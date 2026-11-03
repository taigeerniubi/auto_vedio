"""验证智能体循环、事务提交、并发取消与失败隔离。"""
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event

from fastapi.testclient import TestClient
import pytest

from shiguang.agent.contracts import Command, Limits
from shiguang.agent.loop import Agent
from shiguang.api import create_app
from shiguang.tools import ToolError
from conftest import clip, call
from fakes import ScriptSession, tc


def command(**kwargs):
    return Command(request_id="request-1", instruction="把素材 a 的前两秒放入时间线", expected_revision=0, **kwargs)


def assemble():
    return tc("assemble_sequence", {"clips": [clip()]})


def finish():
    return tc("finish", {"summary": "已完成工程编辑"})


def test_loop_feedback_commit_dedupe_and_undo(context, caplog):
    store, pid, _ = context
    session = ScriptSession([[assemble()], [tc("set_color_preset", {"clip_id": "c1", "preset_id": "warm"})], [finish()]])
    captured = []
    def factory(ctx):
        captured.append(ctx)
        return session
    agent = Agent(store, factory)
    with caplog.at_level("INFO", logger="shiguang.agent"):
        result = agent.execute(pid, command())
    assert result["status"] == "completed", result
    assert result["result"]["revision"] == 1
    assert result["result"]["validation"]["valid"]
    assert store.project(pid)["snapshot"]["clips"][0]["color_preset"] == "warm"
    assert session.received[1][0][1]["ok"] and session.closed
    assert "path" not in captured[0]["素材"][0] and "sha256" not in captured[0]["素材"][0]
    assert agent.execute(pid, command()) == result and len(captured) == 1
    with pytest.raises(ToolError, match="相同 request_id"):
        agent.execute(pid, command().model_copy(update={"instruction": "不同指令"}))
    assert all("事件" in json.loads(r.message) for r in caplog.records)
    assert store.history(pid, "undo", 1)["snapshot"]["clips"] == []
    assert store.history(pid, "redo", 2)["snapshot"]["clips"][0]["clip_id"] == "c1"


def test_error_feedback_can_be_repaired(context):
    store, pid, _ = context
    def repaired(results):
        assert results[0][1]["error_code"] == "INVALID_DRAFT"
        return [tc("trim_clip", {"clip_id": "c1", "source_out_frame": 30}, "trim-fixed")]
    session = ScriptSession([[assemble()], [tc("trim_clip", {"clip_id": "c1", "source_out_frame": 900})], repaired, [finish()]])
    result = Agent(store, lambda _: session).execute(pid, command())
    assert result["status"] == "completed", result
    assert store.project(pid)["snapshot"]["clips"][0]["source_out_frame"] == 30


@pytest.mark.parametrize("steps,limits,expected", [
    ([[assemble()], [tc("trim_clip", {"clip_id": "c1", "source_out_frame": 900})], [finish()]], None, "UNRESOLVED_ERRORS"),
    ([[assemble(), finish()]], None, "MIXED_TERMINAL"),
    ([[assemble()], []], None, "MODEL_NO_ACTION"),
    ([[assemble()]], Limits(rounds=1), "ROUND_LIMIT"),
    ([[assemble(), tc("list_assets")]], Limits(calls=1), "CALL_LIMIT"),
    ([[tc("invented_tool")]], Limits(errors=1), "ERROR_LIMIT"),
    ([[tc("assemble_sequence", "{broken")]], Limits(errors=1), "ERROR_LIMIT"),
    ([[tc("assemble_sequence", "[]")]], Limits(errors=1), "ERROR_LIMIT"),
    ([[assemble()], [assemble()]], None, "MODEL_PROTOCOL_ERROR"),
])
def test_failures_do_not_commit(context, steps, limits, expected):
    store, pid, _ = context
    session = ScriptSession(steps)
    result = Agent(store, lambda _: session, limits).execute(pid, command())
    assert result["status"] == "failed" and result["result"]["error_code"] == expected, result
    assert store.project(pid)["revision"] == 0
    assert store.draft(result["draft_id"])["status"] == "abandoned"
    assert session.closed


@pytest.mark.parametrize("terminal,args,status", [("ask_user", {"question": "选择哪一段？"}, "needs_input"),
                                                  ("unsupported", {"reason": "尚未实现语音识别"}, "unsupported")])
def test_clarification_or_unsupported_discards_partial_edit(context, terminal, args, status):
    store, pid, _ = context
    session = ScriptSession([[assemble()], [tc(terminal, args)]])
    result = Agent(store, lambda _: session).execute(pid, command())
    assert result["status"] == status and result["result"] == args
    assert store.project(pid)["revision"] == 0
    assert store.draft(result["draft_id"])["status"] == "abandoned"


def test_empty_change_does_not_create_revision(context):
    store, pid, _ = context
    result = Agent(store, lambda _: ScriptSession([[finish()]])).execute(pid, command())
    assert result["status"] == "completed"
    assert result["result"]["revision"] == 0 and not result["result"]["changed"]


def test_revision_conflict_during_loop_preserves_other_change(context):
    store, pid, manual = context
    def concurrent_edit(_):
        call(store, manual, "assemble_sequence", {"clips": [clip("other")]})
        store.commit(manual)
        return [finish()]
    result = Agent(store, lambda _: ScriptSession([[assemble()], concurrent_edit])).execute(pid, command())
    assert result["result"]["error_code"] == "REVISION_CONFLICT"
    assert store.project(pid)["snapshot"]["clips"][0]["clip_id"] == "other"


def test_cancel_dedupe_and_draft_ownership_during_request(context):
    store, pid, _ = context
    entered, release = Event(), Event()
    def block(_):
        entered.set()
        assert release.wait(5)
        return [finish()]
    session = ScriptSession([[assemble()], block])
    agent = Agent(store, lambda _: session)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(agent.execute, pid, command())
        try:
            assert entered.wait(5)
            active = agent.runs.by_request(pid, "request-1")
            assert agent.execute(pid, command())["status"] == "running"
            with pytest.raises(ToolError, match="所属运行"):
                store.commit(active["draft_id"])
            with pytest.raises(ToolError, match="所属运行"):
                call(store, active["draft_id"], "delete_clip", {"clip_id": "c1"})
            assert agent.cancel(pid, active["run_id"])["status"] == "cancelled"
        finally:
            release.set()
        result = future.result(timeout=5)
    assert result["status"] == "cancelled"
    assert store.project(pid)["revision"] == 0


def test_total_deadline_checked_after_model_response(context):
    store, pid, _ = context
    now = [0.0]
    def slow(_):
        now[0] = 11
        return [finish()]
    agent = Agent(store, lambda _: ScriptSession([slow]), Limits(seconds=10), clock=lambda: now[0])
    result = agent.execute(pid, command())
    assert result["result"]["error_code"] == "TIME_LIMIT"
    assert store.project(pid)["revision"] == 0


def test_model_exception_sanitized_and_session_closed(context):
    store, pid, _ = context
    def broken(_):
        raise RuntimeError("secret-key-for-test")
    session = ScriptSession([[assemble()], broken])
    result = Agent(store, lambda _: session).execute(pid, command())
    assert result["result"]["error_code"] == "AGENT_INTERNAL_ERROR"
    assert "secret-key-for-test" not in json.dumps(result) and session.closed
    assert store.project(pid)["revision"] == 0


def test_api_commands_query_isolation_and_missing_configuration(tmp_path, monkeypatch):
    client = TestClient(create_app(tmp_path / "api-agent.sqlite3", session_factory=lambda _: ScriptSession([[finish()]])))
    pid = client.post("/api/v1/projects", json={"name": "智能体测试"}).json()["project_id"]
    other = client.post("/api/v1/projects", json={"name": "其他工程"}).json()["project_id"]
    prefix = f"/api/v1/projects/{pid}"
    result = client.post(prefix + "/commands", json=command().model_dump()).json()
    assert result["status"] == "completed"
    rid = result["run_id"]
    assert client.get(prefix + "/commands/request-1").json() == result
    assert client.get(prefix + f"/runs/{rid}").json() == result
    assert client.post(prefix + f"/runs/{rid}/cancel").json() == result
    assert client.get(f"/api/v1/projects/{other}/runs/{rid}").status_code == 404
    assert client.post(f"/api/v1/projects/{other}/runs/{rid}/cancel").status_code == 404
    bad = command(selected_clip_ids=["missing"]).model_copy(update={"request_id": "bad-selection"})
    assert client.post(prefix + "/commands", json=bad.model_dump()).status_code == 400
    stale = command().model_copy(update={"request_id": "stale", "expected_revision": 1})
    assert client.post(prefix + "/commands", json=stale.model_dump()).status_code == 409
    monkeypatch.delenv("SHIGUANG_MODEL_API_KEY", raising=False)
    live = TestClient(create_app(tmp_path / "api-agent.sqlite3"))
    missing = live.post(prefix + "/commands", json=command().model_copy(update={"request_id": "no-key"}).model_dump()).json()
    assert missing["status"] == "failed" and missing["result"]["error_code"] == "MODEL_CONFIG"
