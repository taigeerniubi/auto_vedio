"""以有界循环执行模型工具调用并在校验后原子提交工程。"""
import json
import logging
from time import monotonic
from dataclasses import asdict

from pydantic import ValidationError

from ..tools import ToolError
from ..http_status import error_status
from ..observability import log_context, log_event, validation_details
from .contracts import Command, Limits, ToolCall
from .prompts import TERMINALS, context, redact
from .providers import configured_session
from .repository import RunRepository


class Agent:
    def __init__(self, store, session_factory=configured_session, limits=None, clock=monotonic):
        """组合工程存储、模型适配和执行预算。

        Args:
            store: 工程与草稿存储。
            session_factory: 接收工程上下文的模型会话工厂。
            limits: 可选的循环预算。
            clock: 可注入的单调时钟。
        """
        self.store = store
        self.runs = RunRepository(store)
        self.session_factory = session_factory
        self.limits = limits or Limits.from_env()
        self.clock = clock

    def execute(self, pid: str, command: Command):
        """执行去重后的编辑请求，任何非完成分支都不会提交草稿。

        Args:
            pid: 项目编号。
            command: 用户指令、选区、基础版本及请求编号。

        Returns:
            包含状态、版本结果和中文事件的运行记录。

        Raises:
            ToolError: 请求创建阶段的版本冲突或幂等冲突。
        """
        run, created = self.runs.begin(pid, command)
        if not created:
            return run
        with log_context(运行编号=run["run_id"], 项目编号=pid, 指令编号=command.request_id):
            return self._execute_run(pid, command, run)

    def _execute_run(self, pid, command, run):
        """执行已经取得所有权的运行并记录关联异常。

        Args:
            pid: 工程编号。
            command: 用户编辑指令。
            run: 已持久化的运行记录。

        Returns:
            最终运行记录。
        """
        rid, did = run["run_id"], run["draft_id"]
        deadline = self.clock() + self.limits.seconds if self.limits.seconds is not None else None
        session = None
        try:
            self.runs.event(rid, "开始执行")
            saved = self.runs.checkpoint(rid) or {}
            original = saved.get("instruction", command.instruction)
            annotation = run.get("annotation")
            model_command = command.model_copy(update={
                "instruction": original, "session_id": run["session_id"],
                # 续跑依赖已保存的原任务和工具历史，不让当前浏览器选区改变恢复目标。
                "selected_clip_ids": ([] if saved.get("source_run_id") else
                                      [clip["clip_id"] for clip in annotation["clips"]] if annotation else command.selected_clip_ids),
                "playhead_frame": annotation["frame"] if annotation else command.playhead_frame,
            })
            ctx = context(self.store, self.store.draft(did), model_command)
            if annotation:
                ctx["画面批注"] = annotation
            if saved.get("source_run_id"):
                ctx["中断任务"] = {"原始要求": original, "已执行工具": saved.get("trace", []),
                    "恢复方式": "从保存的草稿继续，不要重复已成功的编辑。旧版未保存的分析结果可以重新查询。"}
                self.runs.event(rid, "恢复任务", source_run_id=saved["source_run_id"])
            results = saved.get("results", [])
            unresolved, seen = set(saved.get("unresolved", [])), set(saved.get("seen", []))
            pending = [ToolCall(**item) for item in saved.get("pending", [])]
            trace = saved.get("trace", [])
            # 初始化失败也保留原始要求和当前草稿。
            self.runs.save_checkpoint(rid, {**saved, "instruction": original})
            session = self.session_factory(ctx)
            if saved.get("session_state") and hasattr(session, "restore_state"):
                session.restore_state(saved["session_state"])
            if hasattr(session, "check_active"):
                session.check_active = lambda: self.check_active(pid, rid, deadline)
            if hasattr(session, "on_retry"):
                session.on_retry = lambda attempt, maximum: self.runs.event(
                    rid, "重试模型", 原因=f"模型请求未完成，正在重试（{attempt - 1}/{maximum - 1}）")

            def checkpoint():
                self.runs.save_checkpoint(rid, {
                    "instruction": original, "results": results, "unresolved": sorted(unresolved),
                    "seen": sorted(seen), "pending": [asdict(call) for call in pending], "trace": trace,
                    "session_state": session.export_state() if hasattr(session, "export_state") else None,
                })

            calls_used, errors = 0, 0
            for round_number in range(1, self.limits.rounds + 1):
                self.check_active(pid, rid, deadline)
                if pending:
                    calls = pending.copy()
                else:
                    checkpoint()
                    self.runs.event(rid, "请求模型", 轮次=round_number)
                    calls = session.step(results, deadline - self.clock() if deadline is not None else None)
                    self.check_active(pid, rid, deadline)
                    if not calls:
                        raise ToolError("MODEL_NO_ACTION", "模型未返回工具调用，草稿未提交")
                    if any(c.call_id in seen for c in calls) or len({c.call_id for c in calls}) != len(calls):
                        raise ToolError("MODEL_PROTOCOL_ERROR", "模型重复使用了工具调用编号")
                    if len(calls) > 1 and any(c.name in TERMINALS for c in calls):
                        raise ToolError("MIXED_TERMINAL", "结束动作不能与其他工具同时执行")
                    seen.update(c.call_id for c in calls)
                    results = []
                    pending = calls.copy()
                    checkpoint()
                if calls_used + len(calls) > self.limits.calls:
                    raise ToolError("CALL_LIMIT", "已达到工具调用预算")
                for call in calls:
                    self.check_active(pid, rid, deadline)
                    calls_used += 1
                    asset_fields = {}
                    arguments = {}
                    try:
                        arguments = json.loads(call.arguments)
                        if not isinstance(arguments, dict):
                            raise ValueError("工具参数必须为对象")
                        if call.name in TERMINALS:
                            args = TERMINALS[call.name].model_validate(arguments)
                            if call.name == "finish":
                                if unresolved:
                                    raise ToolError("UNRESOLVED_ERRORS", "仍有失败的工具未修复：" + "、".join(sorted(unresolved)))
                                validation = self.store.call(did, "validate_draft", {})["data"]
                                if not validation["valid"]:
                                    raise ToolError("INVALID_DRAFT", "草稿校验未通过")
                                diff = self.store.call(did, "get_draft_diff", {})["data"]
                                self.check_active(pid, rid, deadline)
                                self.store.commit(did, run_id=rid, run_result={"summary": args.summary, "diff": diff,
                                                  "rounds": round_number, "tool_calls": calls_used, "validation": validation})
                                self.runs.event(rid, "已完成提交", 轮次=round_number, 调用次数=calls_used)
                                return self.runs.get(pid, rid)
                            return self.runs.stop(pid, rid, "needs_input" if call.name == "ask_user" else "unsupported", args.model_dump())
                        seq = self.store.draft(did)["seq"]
                        if call.name in {"query_audio", "analyze_asset", "inspect_segment"}:
                            aid = arguments.get("asset_id")
                            asset = self.store.assets(pid).get(aid) if isinstance(aid, str) else None
                            if asset:
                                asset_fields = {"asset_id": asset.asset_id, "asset_name": asset.name}
                        self.runs.event(rid, "开始工具", 轮次=round_number, 工具=call.name, **asset_fields)
                        result = self.store.call(did, call.name, arguments, f"{rid}-{calls_used}", seq, run_id=rid)
                    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                        if isinstance(exc, ToolError):
                            # 校验拒绝的结束动作不能在恢复时原样重放；给模型反馈后继续修复。
                            if call.name == "finish" and exc.code in {"UNRESOLVED_ERRORS", "INVALID_DRAFT"}:
                                feedback = {"ok": False, "error_code": exc.code, "summary": str(exc)}
                                results.append((call.call_id, feedback))
                                trace.append({"tool": call.name, "arguments": arguments, "result": feedback})
                                pending = pending[1:]
                                checkpoint()
                            raise
                        if isinstance(exc, ValidationError):
                            details = validation_details(exc.errors())
                        elif isinstance(exc, json.JSONDecodeError):
                            details = [{"原因": exc.msg, "行号": exc.lineno, "列号": exc.colno}]
                        else:
                            details = [{"原因": "参数不是有效的 JSON 对象"}]
                        result = {"ok": False, "error_code": "INVALID_ARGUMENTS", "summary": "工具参数必须是符合 Schema 的 JSON 对象", "details": details}
                    if result["ok"]:
                        unresolved.discard(call.name)
                    else:
                        if call.name not in TERMINALS:
                            unresolved.add(call.name)
                        errors += 1
                    self.runs.event(rid, "工具执行", 轮次=round_number, 工具=call.name,
                                    cache_hit=bool(result.get("data", {}).get("cache_hit")) if call.name == "query_audio" else False,
                                    changed_ids=result.get("changed_ids", []), draft_seq=result.get("draft_seq"),
                                    状态="成功" if result["ok"] else "失败", 错误代码=result.get("error_code"),
                                    原因=result.get("summary") if not result["ok"] else None, 参数错误=result.get("details"), **asset_fields)
                    results.append((call.call_id, redact(result)))
                    trace.append({"tool": call.name, "arguments": redact(arguments), "result": redact(result)})
                    pending = pending[1:]
                    checkpoint()
                    if errors >= self.limits.errors:
                        raise ToolError("ERROR_LIMIT", "工具错误次数已达到预算")
            raise ToolError("ROUND_LIMIT", f"已达到本次 {self.limits.rounds} 轮规划上限，已保存任务进度，可点击“继续任务”从中断处继续")
        except ToolError as exc:
            log_event("智能体执行异常", level=logging.ERROR if error_status(exc.code) >= 500 else logging.WARNING,
                      error=exc, 运行编号=rid, 项目编号=pid, 错误代码=exc.code,
                      secrets=(command.instruction,))
            return self.runs.stop(pid, rid, "failed", {"error_code": exc.code, "summary": str(exc), **exc.details})
        except Exception as exc:
            log_event("智能体未处理异常", level=logging.ERROR, error=exc, 运行编号=rid, 项目编号=pid,
                      错误代码="AGENT_INTERNAL_ERROR", secrets=(command.instruction,))
            return self.runs.stop(pid, rid, "failed", {"error_code": "AGENT_INTERNAL_ERROR", "summary": "执行器发生异常，草稿未提交"})
        finally:
            if session:
                try:
                    session.close()
                except Exception as exc:
                    log_event("模型连接清理异常", level=logging.ERROR, error=exc, 运行编号=rid, secrets=(command.instruction,))
                    self.runs.event(rid, "连接清理失败")

    def check_active(self, pid, rid, deadline):
        """在模型请求、工具执行和提交前检查取消与时间预算。

        Args:
            pid: 项目编号。
            rid: 运行编号。
            deadline: 单调时钟计算的截止时间。

        Raises:
            ToolError: 运行已经取消或超过总时间预算。
        """
        if self.runs.get(pid, rid)["status"] != "running":
            raise ToolError("RUN_STOPPED", "运行已取消或结束")
        if deadline is not None and self.clock() >= deadline:
            raise ToolError("TIME_LIMIT", "已达到运行时间预算")

    def cancel(self, pid, rid):
        """取消运行，已经提交的结果保持不变。

        Args:
            pid: 项目编号。
            rid: 运行编号。

        Returns:
            取消或已结束的运行记录。
        """
        return self.runs.stop(pid, rid, "cancelled", {"summary": "用户取消了运行"})
