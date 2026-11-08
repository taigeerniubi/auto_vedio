"""验证失败任务的持久化续跑、历史上下文和项目版本隔离。"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from shiguang.agent.contracts import Command
from shiguang.agent.loop import Agent
from shiguang.agent.providers import ModelConfig, RemoteSession
from shiguang.api import create_app
from shiguang.store import Store
from shiguang.tools import ToolError
from conftest import call, clip
from fakes import ScriptSession, tc


ORIGINAL = "把素材 a 剪成酒吧之夜风格，先加入时间线再调色"


def request(request_id, instruction=ORIGINAL, **kwargs):
    """为独立运行构造请求，保留稳定的幂等编号。"""
    return Command(request_id=request_id, instruction=instruction, expected_revision=0, **kwargs)


def fail_after_edit(store, pid, session_id=None):
    """模拟完成一次写工具后，下一轮规划网络超时。"""
    def timeout(_):
        raise ToolError("MODEL_TIMEOUT", "模型请求超时")

    session = ScriptSession([
        [tc("assemble_sequence", {"clips": [clip()]}, "initial-assemble")],
        timeout,
    ])
    result = Agent(store, lambda _: session).execute(pid, request("interrupted", session_id=session_id))
    assert result["status"] == "failed" and result["result"]["error_code"] == "MODEL_TIMEOUT"
    assert store.project(pid)["revision"] == 0
    return result


def test_resume_after_restart_preserves_draft_results_and_original_intent(context):
    """数据库重开后直接在已有镜头上继续调色，不重放初剪写入。"""
    store, pid, _ = context
    interrupted = fail_after_edit(store, pid)
    reopened = Store(store.db)
    captured = []

    def next_step(results):
        assert results and results[0][0] == "initial-assemble"
        assert results[0][1]["ok"] is True
        return [tc("set_color_preset", {"clip_id": "c1", "preset_id": "warm"}, "resume-color")]

    resumed_session = ScriptSession([next_step, [tc("finish", {"summary": "完成酒吧风格剪辑"}, "resume-finish")]])

    def factory(ctx):
        captured.append(ctx)
        return resumed_session

    agent = Agent(reopened, factory)
    command = request("resume-explicit", "继续上次任务", resume_run_id=interrupted["run_id"])
    completed = agent.execute(pid, command)
    assert completed["status"] == "completed", completed
    assert completed["run_id"] != interrupted["run_id"]
    assert completed["draft_id"] != interrupted["draft_id"]
    assert ORIGINAL in json.dumps(captured[0]["中断任务"], ensure_ascii=False)
    assert captured[0]["工程"]["clips"][0]["clip_id"] == "c1"
    clips = reopened.project(pid)["snapshot"]["clips"]
    assert len(clips) == 1 and clips[0]["color_preset"] == "warm"
    assert reopened.project(pid)["revision"] == 1
    assert agent.runs.get(pid, interrupted["run_id"])["status"] == "failed"
    assert not any(event.get("工具") == "assemble_sequence" for event in completed["events"])
    assert agent.execute(pid, command) == completed
    assert len(captured) == 1


@pytest.mark.parametrize("instruction", ["继续", "重试", "继续上次任务", "重试上次任务"])
def test_natural_resume_finds_failed_task_behind_clarification(context, instruction):
    """失败之后曾询问澄清，也仍能找到同一会话未完成的原任务。"""
    store, pid, _ = context
    interrupted = fail_after_edit(store, pid)
    clarification = Agent(store, lambda _: ScriptSession([
        [tc("ask_user", {"question": "请补充剪辑要求"}, "clarification")],
    ])).execute(pid, request("clarification", "请问现在是什么情况"))
    assert clarification["status"] == "needs_input"
    captured = []

    def factory(ctx):
        captured.append(ctx)
        return ScriptSession([[tc("finish", {"summary": "接着原任务完成"}, "resume-finish")]])

    result = Agent(store, factory).execute(pid, request("resume-natural", instruction))
    assert result["status"] == "completed", result
    assert ORIGINAL in json.dumps(captured[0]["中断任务"], ensure_ascii=False)
    assert store.project(pid)["snapshot"]["clips"][0]["clip_id"] == "c1"
    assert store.draft(interrupted["draft_id"])["status"] == "abandoned"


def test_resume_rejects_modified_project_without_losing_manual_edit(context):
    """基础版本发生变化时停止恢复，不能覆盖用户之后的手动剪辑。"""
    store, pid, manual = context
    interrupted = fail_after_edit(store, pid)
    assert call(store, manual, "assemble_sequence", {"clips": [clip("manual")]})["ok"]
    store.commit(manual)
    agent = Agent(store, lambda _: pytest.fail("Version conflict must precede any model call"))
    with pytest.raises(ToolError) as error:
        agent.execute(pid, request("resume-stale", "继续", resume_run_id=interrupted["run_id"])
                      .model_copy(update={"expected_revision": 1}))
    assert error.value.code == "RESUME_CONFLICT"
    assert [c["clip_id"] for c in store.project(pid)["snapshot"]["clips"]] == ["manual"]


def test_resume_cannot_cross_session_or_project(context):
    """显式恢复目标也必须属于当前项目和会话。"""
    store, pid, _ = context
    interrupted = fail_after_edit(store, pid)
    agent = Agent(store, lambda _: pytest.fail("Invalid resume target must not invoke the model"))
    other_session = agent.runs.create_session(pid)["session_id"]
    with pytest.raises(ToolError):
        agent.execute(pid, request("wrong-session", "继续", session_id=other_session,
                                   resume_run_id=interrupted["run_id"]))
    other_pid = store.create_project("Other")["project_id"]
    with pytest.raises(ToolError):
        agent.execute(other_pid, request("wrong-project", "继续", resume_run_id=interrupted["run_id"]))
    assert store.project(pid)["revision"] == 0
    assert store.project(other_pid)["revision"] == 0


def test_natural_continue_in_another_session_does_not_restore_failed_draft(context):
    """新会话的继续不能隐式继承其他会话的失败任务。"""
    store, pid, _ = context
    fail_after_edit(store, pid)
    captured = []

    def factory(ctx):
        captured.append(ctx)
        return ScriptSession([[tc("ask_user", {"question": "想继续哪项编辑？"})]])

    agent = Agent(store, factory)
    sid = agent.runs.create_session(pid)["session_id"]
    result = agent.execute(pid, request("other-session-continue", "继续", session_id=sid))
    assert result["status"] == "needs_input"
    assert not captured[0].get("中断任务")
    assert captured[0]["工程"]["clips"] == []


def test_newer_completed_run_prevents_implicit_old_task_resume(context):
    """同会话后来已有完成的请求时，不把含糊的继续指向更早失败任务。"""
    store, pid, _ = context
    fail_after_edit(store, pid)
    completed = Agent(store, lambda _: ScriptSession([
        [tc("finish", {"summary": "已回答最新问题"})],
    ])).execute(pid, request("later-completed", "告诉我当前画幅"))
    assert completed["status"] == "completed"
    captured = []

    def factory(ctx):
        captured.append(ctx)
        return ScriptSession([[tc("ask_user", {"question": "继续什么？"})]])

    result = Agent(store, factory).execute(pid, request("continue-after-completion", "继续"))
    assert result["status"] == "needs_input"
    assert not captured[0].get("中断任务")
    assert store.project(pid)["snapshot"]["clips"] == []


def test_resume_executes_remaining_tool_batch_without_repeating_completed_tools(context, monkeypatch):
    """同轮多个工具中途失败时，续跑只执行剩余调用。"""
    store, pid, _ = context
    original_call = store.call
    executed = []
    should_fail = [True]

    def interrupted_call(did, name, arguments, *args, **kwargs):
        if name in {"assemble_sequence", "set_color_preset"}:
            executed.append(name)
        if name == "set_color_preset" and should_fail[0]:
            should_fail[0] = False
            raise ToolError("MODEL_TIMEOUT", "模拟工具中断")
        return original_call(did, name, arguments, *args, **kwargs)

    monkeypatch.setattr(store, "call", interrupted_call)
    interrupted = Agent(store, lambda _: ScriptSession([[
        tc("assemble_sequence", {"clips": [clip()]}, "batch-assemble"),
        tc("set_color_preset", {"clip_id": "c1", "preset_id": "warm"}, "batch-color"),
    ]])).execute(pid, request("batch-interrupted"))
    assert interrupted["status"] == "failed"
    observed = []

    def finish_batch(results):
        observed.extend(results)
        return [tc("finish", {"summary": "继续完成同轮编辑"}, "batch-finish")]

    result = Agent(store, lambda _: ScriptSession([finish_batch])).execute(
        pid, request("batch-resume", "重试", resume_run_id=interrupted["run_id"]))
    assert result["status"] == "completed", result
    assert executed == ["assemble_sequence", "set_color_preset", "set_color_preset"]
    assert [item[0] for item in observed] == ["batch-assemble", "batch-color"]
    assert len(store.project(pid)["snapshot"]["clips"]) == 1
    assert store.project(pid)["snapshot"]["clips"][0]["color_preset"] == "warm"


@pytest.mark.parametrize("provider", ["deepseek", "openai"])
def test_remote_resume_restores_wire_history_once_without_persisting_key(context, provider):
    """真实适配器重建后沿用原协议上下文，失败请求的工具结果只追加一次。"""
    store, pid, _ = context
    sent = []
    config = ModelConfig(provider, "resume-test-model", "private-resume-api-key", retries=0)

    def response(name, arguments, call_id):
        if provider == "openai":
            return httpx.Response(200, json={"status": "completed", "output": [{
                "type": "function_call", "call_id": call_id, "name": name,
                "arguments": json.dumps(arguments),
            }]})
        return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "tool_calls": [{"id": call_id, "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)}}],
        }}]})

    def handler(http_request):
        sent.append(json.loads(http_request.content))
        if len(sent) == 1:
            return response("assemble_sequence", {"clips": [clip()]}, "remote-assemble")
        if len(sent) == 2:
            raise httpx.ReadTimeout("temporary interruption", request=http_request)
        return response("finish", {"summary": "从断点完成"}, "remote-finish")

    def factory(ctx):
        return RemoteSession(config, ctx, httpx.MockTransport(handler))

    interrupted = Agent(store, factory).execute(pid, request("remote-initial"))
    assert interrupted["status"] == "failed", interrupted
    with store.connect() as connection:
        checkpoint_text = connection.execute("SELECT data FROM agent_checkpoints WHERE run_id=?",
                                             (interrupted["run_id"],)).fetchone()[0]
    assert config.api_key not in checkpoint_text
    assert ORIGINAL in checkpoint_text
    resumed = Agent(Store(store.db), factory).execute(pid, request(
        "remote-resume", "继续", resume_run_id=interrupted["run_id"]))
    assert resumed["status"] == "completed", resumed
    history_key = "input" if provider == "openai" else "messages"
    def normalized(history):
        # SQLite 使用稳定键排序；工具 JSON 的字段顺序不是协议语义。
        result = []
        for message in history:
            message = dict(message)
            if message.get("type") == "function_call_output":
                message["output"] = json.loads(message["output"])
            elif message.get("role") == "tool":
                message["content"] = json.loads(message["content"])
            result.append(message)
        return result
    assert normalized(sent[2][history_key]) == normalized(sent[1][history_key])
    outputs = [message for message in sent[2][history_key]
               if message.get("type") == "function_call_output" or message.get("role") == "tool"]
    assert len(outputs) == 1
    assert outputs[0].get("call_id", outputs[0].get("tool_call_id")) == "remote-assemble"
    assert len(store.project(pid)["snapshot"]["clips"]) == 1


def test_legacy_failed_run_without_checkpoint_keeps_draft_and_original_instruction(context):
    """旧版本没有完整模型记录时，也能从持久化草稿和原始指令恢复。"""
    store, pid, _ = context
    interrupted = fail_after_edit(store, pid)
    with store.connect() as connection:
        connection.execute("DELETE FROM agent_checkpoints WHERE run_id=?", (interrupted["run_id"],))
    captured = []

    def factory(ctx):
        captured.append(ctx)
        return ScriptSession([[tc("finish", {"summary": "旧任务继续完成"}, "legacy-finish")]])

    resumed = Agent(Store(store.db), factory).execute(pid, request("legacy-resume", "继续"))
    assert resumed["status"] == "completed", resumed
    assert ORIGINAL in json.dumps(captured[0]["中断任务"], ensure_ascii=False)
    assert captured[0]["工程"]["clips"][0]["clip_id"] == "c1"
    assert len(store.project(pid)["snapshot"]["clips"]) == 1


def test_resume_after_rejected_finish_returns_feedback_and_repairs_unresolved_tool(context):
    """结束动作被未修复错误拒绝后，续跑交给模型修复而非反复重放 finish。"""
    store, pid, _ = context
    agent = Agent(store, lambda _: ScriptSession([
        [tc("assemble_sequence", {"clips": [clip()]}, "before-rejected-finish")],
        [tc("trim_clip", {"clip_id": "c1", "source_out_frame": 900}, "bad-trim")],
        [tc("finish", {"summary": "忽略了错误的结束"}, "rejected-finish")],
    ]))
    interrupted = agent.execute(pid, request("finish-rejected"))
    assert interrupted["result"]["error_code"] == "UNRESOLVED_ERRORS"
    assert store.project(pid)["revision"] == 0
    checkpoint = agent.runs.checkpoint(interrupted["run_id"])
    assert checkpoint["pending"] == []
    assert checkpoint["results"][-1][0] == "rejected-finish"
    assert checkpoint["results"][-1][1]["error_code"] == "UNRESOLVED_ERRORS"

    def repair(results):
        assert results[-1][0] == "rejected-finish"
        assert results[-1][1]["error_code"] == "UNRESOLVED_ERRORS"
        return [tc("trim_clip", {"clip_id": "c1", "source_out_frame": 30}, "repaired-trim")]

    recovered = Agent(store, lambda _: ScriptSession([
        repair, [tc("finish", {"summary": "已修复裁切并完成"}, "accepted-finish")],
    ])).execute(pid, request("finish-rejected-resume", "继续", resume_run_id=interrupted["run_id"]))
    assert recovered["status"] == "completed", recovered
    assert store.project(pid)["snapshot"]["clips"][0]["source_out_frame"] == 30


def test_app_startup_marks_interrupted_run_failed_and_preserves_checkpoint_for_resume(context, tmp_path):
    """服务启动时将遗留 running 任务标记可恢复失败，完整断点仍可通过 API 接续。"""
    store, pid, _ = context
    agent = Agent(store)
    run, created = agent.runs.begin(pid, request("process-interrupted"))
    assert created
    result = store.call(run["draft_id"], "assemble_sequence", {"clips": [clip()]},
                        "before-process-stop", 0, run_id=run["run_id"])
    assert result["ok"]
    agent.runs.save_checkpoint(run["run_id"], {
        "instruction": ORIGINAL, "results": [["before-process-stop", result]],
        "pending": [], "seen": ["before-process-stop"], "unresolved": [],
        "trace": [{"tool": "assemble_sequence", "result": result}], "session_state": None,
    })
    saved = agent.runs.checkpoint(run["run_id"])
    captured = []

    def factory(ctx):
        captured.append(ctx)
        return ScriptSession([[tc("finish", {"summary": "恢复服务中断的任务"}, "after-restart")]])

    app = create_app(store.db, session_factory=factory, log_dir=tmp_path / "restart-logs")
    assert agent.runs.get(pid, run["run_id"])["status"] == "running"
    with TestClient(app) as client:
        interrupted = client.get(f"/api/v1/projects/{pid}/runs/{run['run_id']}").json()
        assert interrupted["status"] == "failed"
        assert interrupted["result"]["error_code"] == "RUN_INTERRUPTED"
        assert app.state.agent.runs.checkpoint(run["run_id"]) == saved
        resume = request("process-resume", "继续", resume_run_id=run["run_id"])
        response = client.post(f"/api/v1/projects/{pid}/commands", json=resume.model_dump())
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "completed"
    assert ORIGINAL in json.dumps(captured[0]["中断任务"], ensure_ascii=False)
    assert len(store.project(pid)["snapshot"]["clips"]) == 1
