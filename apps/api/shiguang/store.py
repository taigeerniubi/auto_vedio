"""使用 SQLite 原子保存工程、草稿、智能体运行及可撤销版本。"""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import logging
from pathlib import Path
import sqlite3

from pydantic import ValidationError

from .models import Asset, Snapshot, timeline, validate_snapshot
from .tools import TOOLS, ToolError, presets, uid
from .observability import log_event, validation_details
from . import editing_features  # 注册需要工程上下文的扩展工具。


def dumps(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class Store:
    def __init__(self, db: Path):
        db.parent.mkdir(parents=True, exist_ok=True)
        self.db = db
        with self.connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, name TEXT, head INTEGER NOT NULL, undo TEXT NOT NULL, redo TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS revisions(project_id TEXT, revision INTEGER, snapshot TEXT NOT NULL, PRIMARY KEY(project_id,revision));
                CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, base INTEGER NOT NULL, seq INTEGER NOT NULL, snapshot TEXT NOT NULL, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS operations(draft_id TEXT, op_id TEXT, hash TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(draft_id,op_id));
                CREATE TABLE IF NOT EXISTS agent_runs(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, request_id TEXT NOT NULL, hash TEXT NOT NULL, draft_id TEXT NOT NULL UNIQUE, base INTEGER NOT NULL, status TEXT NOT NULL, result TEXT NOT NULL, events TEXT NOT NULL, UNIQUE(project_id,request_id));
                CREATE TABLE IF NOT EXISTS agent_run_inputs(run_id TEXT PRIMARY KEY REFERENCES agent_runs(id) ON DELETE CASCADE, instruction TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS asset_analysis(project_id TEXT,asset_id TEXT,data TEXT NOT NULL,PRIMARY KEY(project_id,asset_id));
                CREATE TABLE IF NOT EXISTS analysis_versions(id TEXT PRIMARY KEY,project_id TEXT,asset_id TEXT,data TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.db, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def create_project(self, name, composition=None):
        pid = uid("project")
        s = Snapshot(composition=composition) if composition else Snapshot()
        with self.connect() as c:
            c.execute("INSERT INTO projects VALUES(?,?,0,'[]','[]')", (pid, name))
            c.execute("INSERT INTO revisions VALUES(?,0,?)", (pid, s.model_dump_json()))
        return {"project_id": pid, "revision": 0, "snapshot": s.model_dump()}

    def project(self, pid):
        with self.connect() as c:
            p = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
            if not p:
                raise ToolError("PROJECT_NOT_FOUND", "项目不存在")
            row = c.execute("SELECT snapshot FROM revisions WHERE project_id=? AND revision=?", (pid, p["head"])).fetchone()
        return {"project_id": pid, "name": p["name"], "revision": p["head"], "snapshot": json.loads(row[0])}

    def add_asset(self, asset: Asset):
        self.project(asset.project_id)
        with self.connect() as c:
            c.execute("INSERT INTO assets VALUES(?,?,?)", (asset.asset_id, asset.project_id, asset.model_dump_json()))

    def assets(self, pid, con=None):
        if con is None:
            with self.connect() as c:
                return self.assets(pid, c)
        return {a["id"]: Asset.model_validate_json(a["data"]) for a in con.execute("SELECT * FROM assets WHERE project_id=?", (pid,))}

    def create_draft(self, pid):
        p = self.project(pid)
        did = uid("draft")
        with self.connect() as c:
            c.execute("INSERT INTO drafts VALUES(?,?,?,0,?,'open')", (did, pid, p["revision"], dumps(p["snapshot"])))
        return self.draft(did)

    def draft(self, did):
        with self.connect() as c:
            row = c.execute("SELECT * FROM drafts WHERE id=?", (did,)).fetchone()
        if not row:
            raise ToolError("DRAFT_NOT_FOUND", "草稿不存在")
        return {**dict(row), "snapshot": json.loads(row["snapshot"])}

    def call(self, did, name, arguments, op_id=None, expected_seq=None, *, run_id=None):
        """校验参数并原子执行草稿工具，失败时保留原状态。

        Args:
            did: 草稿编号。
            name: 注册工具名称。
            arguments: 待校验的工具参数。
            op_id: 用于写操作去重的编号。
            expected_seq: 执行器读取到的草稿序号。
            run_id: 智能体专属草稿的所有者编号。

        Returns:
            含状态、变更编号及错误信息的执行结果。
        """
        result = self._execute_tool(did, name, arguments, op_id, expected_seq, run_id=run_id)
        if not result["ok"]:
            log_event("工具调用失败", level=logging.WARNING, 草稿编号=did, 运行编号=run_id, 工具=name,
                      操作编号=op_id, 错误代码=result.get("error_code"), 原因=result.get("summary"))
        return result

    def _execute_tool(self, did, name, arguments, op_id, expected_seq, *, run_id):
        """在草稿事务内执行已注册工具。

        Args:
            did: 草稿编号。
            name: 工具名称。
            arguments: 待校验参数。
            op_id: 幂等操作编号。
            expected_seq: 预期草稿序号。
            run_id: 可选的草稿所有者编号。

        Returns:
            工具结果或可供模型修正的错误。
        """
        if name not in TOOLS:
            return {"ok": False, "error_code": "UNKNOWN_TOOL", "summary": "工具尚未实现", "changed_ids": [], "warnings": []}
        spec = TOOLS[name]
        try:
            args = spec.arguments.model_validate(arguments)
        except ValidationError as exc:
            return {"ok": False, "error_code": "INVALID_ARGUMENTS", "summary": dumps(validation_details(exc.errors())), "changed_ids": [], "warnings": []}
        if name in {"analyze_asset", "inspect_segment", "query_audio"}:
            from .analysis import AnalysisService
            draft = self.draft(did)
            with self.connect() as con:
                self.check_run_owner(con, did, run_id)
            if draft["status"] != "open":
                raise ToolError("DRAFT_CLOSED", "草稿已关闭")
            service = getattr(self, "analysis_service", None) or AnalysisService(self)
            try:
                if name == "query_audio":
                    data = service.query_audio(draft["project_id"], args.asset_id, args)
                    summary = "音频查询完成，标记为素材母版上的估计时间"
                elif name == "analyze_asset":
                    data = service.analyze(draft["project_id"], args.asset_id, visual=args.visual, speech=args.speech)
                    summary = "素材分析结果已更新，请检查各阶段状态"
                else:
                    data = service.inspect(draft["project_id"], args.asset_id, args.start_frame, args.end_frame)
                    summary = "区间精查完成"
                return {"ok": True, "draft_seq": draft["seq"], "data": data, "summary": summary, "warnings": [], "changed_ids": []}
            except ToolError as exc:
                return {"ok": False, "error_code": exc.code, "summary": str(exc), "warnings": [], "changed_ids": []}
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM drafts WHERE id=?", (did,)).fetchone()
            if not row:
                raise ToolError("DRAFT_NOT_FOUND", "草稿不存在")
            s = Snapshot.model_validate_json(row["snapshot"])
            assets = self.assets(row["project_id"], c)
            def failure(code, message):
                return {"ok": False, "draft_seq": row["seq"], "error_code": code, "summary": message, "changed_ids": [], "warnings": []}
            if spec.handler is None:
                if name == "get_project_state":
                    rows, total = timeline(s)
                    data = {"snapshot": s.model_dump(), "timeline": rows, "total_frames": total, "base_revision": row["base"], "capability_version": 1}
                elif name == "list_assets":
                    data = [a.model_dump() for a in assets.values()]
                elif name == "list_presets":
                    data = presets()
                elif name in {"search_segments", "get_asset_analysis"}:
                    from .analysis import analysis_records, search_segments
                    if name == "search_segments":
                        data = search_segments(self, row["project_id"], args.query, c)
                    else:
                        if args.asset_id not in assets:
                            return failure("ASSET_NOT_FOUND", "该工程中没有此素材")
                        data = analysis_records(self, row["project_id"], c).get(args.asset_id)
                elif name == "validate_draft":
                    data = {"errors": validate_snapshot(s, assets), "valid": not validate_snapshot(s, assets)}
                else:
                    base = json.loads(c.execute("SELECT snapshot FROM revisions WHERE project_id=? AND revision=?", (row["project_id"], row["base"])).fetchone()[0])
                    now = s.model_dump()
                    data = {k: {"before": base[k], "after": now[k]} for k in now if now[k] != base[k]}
                return {"ok": True, "draft_seq": row["seq"], "changed_ids": [], "warnings": [], "error_code": None, "summary": spec.description, "data": data}
            self.check_run_owner(c, did, run_id)
            if not op_id or expected_seq is None:
                return failure("EXECUTION_CONTEXT_REQUIRED", "写操作需要执行器提供 op_id 与 expected_seq")
            digest = sha256(dumps({"tool": name, "args": args.model_dump()}).encode()).hexdigest()
            old = c.execute("SELECT * FROM operations WHERE draft_id=? AND op_id=?", (did, op_id)).fetchone()
            if old:
                return json.loads(old["result"]) if old["hash"] == digest else failure("OP_ID_CONFLICT", "相同 op_id 不得携带不同参数")
            if row["status"] != "open":
                return failure("DRAFT_CLOSED", "草稿已提交")
            if row["seq"] != expected_seq:
                return failure("DRAFT_CONFLICT", "草稿已变更，请重新读取")
            try:
                if name == "generate_subtitles":
                    from .analysis import analysis_records
                    changed, warnings = spec.handler(s, args, analysis_records(self, row["project_id"], c))
                else:
                    changed, warnings = spec.handler(s, args)
                # 重新解析可捕获绕过赋值校验的复制和列表变更。
                s = Snapshot.model_validate(s.model_dump())
                errors = validate_snapshot(s, assets)
                if errors:
                    return failure("INVALID_DRAFT", "; ".join(errors))
            except (ToolError, ValueError, ValidationError) as exc:
                message = dumps(validation_details(exc.errors())) if isinstance(exc, ValidationError) else str(exc)
                return failure(getattr(exc, "code", "INVALID_ARGUMENTS"), message)
            result = {"ok": True, "draft_seq": row["seq"] + 1, "changed_ids": changed, "warnings": warnings,
                      "error_code": None, "summary": f"已执行 {name}"}
            c.execute("UPDATE drafts SET snapshot=?,seq=seq+1 WHERE id=?", (s.model_dump_json(), did))
            c.execute("INSERT INTO operations VALUES(?,?,?,?)", (did, op_id, digest, dumps(result)))
            return result

    def check_run_owner(self, con, did, run_id):
        """阻止外部写入智能体草稿或在取消后继续提交。

        Args:
            con: 当前事务连接。
            did: 草稿编号。
            run_id: 调用方的运行编号，手动操作传入空值。

        Raises:
            ToolError: 所有者不匹配或运行已经结束。
        """
        owner = con.execute("SELECT id,status FROM agent_runs WHERE draft_id=?", (did,)).fetchone()
        if owner and owner["id"] != run_id:
            raise ToolError("DRAFT_OWNER_CONFLICT", "智能体草稿仅允许所属运行修改")
        if run_id and (not owner or owner["id"] != run_id or owner["status"] != "running"):
            raise ToolError("RUN_STOPPED", "运行已经结束，不能继续修改草稿")

    def commit(self, did, *, run_id=None, run_result=None):
        """校验基础版本并在同一事务内提交工程和智能体结果。

        Args:
            did: 待提交的草稿编号。
            run_id: 可选的智能体运行编号。
            run_result: 已完成校验的运行摘要和变更信息。

        Returns:
            精确对应本次提交版本的工程状态。

        Raises:
            ToolError: 草稿无效、运行终止或基础版本已过期。
        """
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            d = c.execute("SELECT * FROM drafts WHERE id=?", (did,)).fetchone()
            if not d:
                raise ToolError("DRAFT_NOT_FOUND", "草稿不存在")
            self.check_run_owner(c, did, run_id)
            p = c.execute("SELECT * FROM projects WHERE id=?", (d["project_id"],)).fetchone()
            if d["status"] != "open":
                raise ToolError("DRAFT_CLOSED", "草稿已提交")
            if p["head"] != d["base"]:
                raise ToolError("REVISION_CONFLICT", "基础版本已过期")
            s = Snapshot.model_validate_json(d["snapshot"])
            errors = validate_snapshot(s, self.assets(d["project_id"], c))
            if errors:
                raise ToolError("INVALID_DRAFT", "; ".join(errors))
            base = c.execute("SELECT snapshot FROM revisions WHERE project_id=? AND revision=?", (p["id"], p["head"])).fetchone()[0]
            changed = json.loads(base) != s.model_dump()
            revision = p["head"] + (1 if changed or not run_id else 0)
            if revision != p["head"]:
                undo = json.loads(p["undo"]) + [p["head"]]
                c.execute("INSERT INTO revisions VALUES(?,?,?)", (p["id"], revision, s.model_dump_json()))
                c.execute("UPDATE projects SET head=?,undo=?,redo='[]' WHERE id=?", (revision, dumps(undo), p["id"]))
            c.execute("UPDATE drafts SET status='committed' WHERE id=?", (did,))
            if run_id:
                result = {**(run_result or {}), "revision": revision, "changed": changed}
                c.execute("UPDATE agent_runs SET status='completed',result=? WHERE id=?", (dumps(result), run_id))
        return {"project_id": p["id"], "name": p["name"], "revision": revision, "snapshot": s.model_dump()}

    def history(self, pid, action, expected_revision):
        if action not in {"undo", "redo"}:
            raise ToolError("INVALID_ARGUMENTS", "无效历史动作")
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            p = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
            if not p:
                raise ToolError("PROJECT_NOT_FOUND", "项目不存在")
            if p["head"] != expected_revision:
                raise ToolError("REVISION_CONFLICT", "项目已变更")
            undo, redo = json.loads(p["undo"]), json.loads(p["redo"])
            origin, other = (undo, redo) if action == "undo" else (redo, undo)
            if not origin:
                raise ToolError("EMPTY_HISTORY", "没有可恢复的版本")
            target = origin.pop()
            other.append(p["head"])
            snapshot = c.execute("SELECT snapshot FROM revisions WHERE project_id=? AND revision=?", (pid, target)).fetchone()[0]
            revision = p["head"] + 1
            c.execute("INSERT INTO revisions VALUES(?,?,?)", (pid, revision, snapshot))
            c.execute("UPDATE projects SET head=?,undo=?,redo=? WHERE id=?", (revision, dumps(undo), dumps(redo), pid))
        return self.project(pid)
