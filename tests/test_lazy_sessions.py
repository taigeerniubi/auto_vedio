"""验证发送首条消息才创建会话，以及空会话清理不会删除任何运行历史。"""
from fastapi.testclient import TestClient

from shiguang.agent.contracts import Command
from shiguang.agent.repository import RunRepository, default_session_id
from shiguang.api import create_app
from fakes import ScriptSession, tc


def table_counts(store):
    """记录会话与消息相关表数量，检查拒绝请求时事务是否完全回滚。"""
    with store.connect() as con:
        return {name: con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                for name in ("agent_sessions", "agent_runs", "agent_run_inputs", "agent_run_sessions", "drafts")}


def test_reading_new_project_sessions_never_creates_empty_rows(tmp_path):
    app = create_app(tmp_path / "empty.db", log_dir=tmp_path / "logs")
    client = TestClient(app)
    pid = client.post("/api/v1/projects", json={"name": "Empty"}).json()["project_id"]
    for _ in range(3):
        assert client.get(f"/api/v1/projects/{pid}/sessions").json() == []
        assert client.get(f"/api/v1/projects/{pid}/runs").json()["items"] == []
    assert table_counts(app.state.store) == {
        "agent_sessions": 0, "agent_runs": 0, "agent_run_inputs": 0, "agent_run_sessions": 0, "drafts": 0,
    }


def test_first_message_creates_named_session_atomically_and_deduplicates(tmp_path):
    contexts = []

    def factory(context):
        contexts.append(context)
        return ScriptSession([[tc("finish", {"summary": "已收到"})]])

    app = create_app(tmp_path / "first-message.db", log_dir=tmp_path / "logs", session_factory=factory)
    client = TestClient(app)
    pid = client.post("/api/v1/projects", json={"name": "First message"}).json()["project_id"]
    base = f"/api/v1/projects/{pid}"
    command = {"request_id": "first", "new_session": True, "instruction": "第一条剪辑想法", "expected_revision": 0}
    first = client.post(base + "/commands", json=command)
    assert first.status_code == 200, first.text
    first = first.json()
    assert first["session_id"] != default_session_id(pid)
    assert client.post(base + "/commands", json=command).json() == first
    sessions = client.get(base + "/sessions").json()
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == first["session_id"]
    assert sessions[0]["title"] == "第一条剪辑想法"
    assert sessions[0]["has_messages"] is True
    assert contexts[0]["近期对话"] == []
    assert len(contexts) == 1

    second = client.post(base + "/commands", json={**command, "request_id": "second", "instruction": "另一段故事"}).json()
    assert second["session_id"] != first["session_id"]
    assert contexts[-1]["近期对话"] == []
    followup = client.post(base + "/commands", json={"request_id": "followup", "instruction": "补充第一个想法",
        "expected_revision": 0, "session_id": first["session_id"]})
    assert followup.status_code == 200, followup.text
    assert [entry["指令"] for entry in contexts[-1]["近期对话"]] == ["第一条剪辑想法"]
    assert len(client.get(base + "/sessions").json()) == 2


def test_rejected_first_messages_do_not_leave_empty_sessions(tmp_path):
    app = create_app(tmp_path / "rejected.db", log_dir=tmp_path / "logs")
    client = TestClient(app)
    pid = client.post("/api/v1/projects", json={"name": "Rejected"}).json()["project_id"]
    base = f"/api/v1/projects/{pid}"
    before = table_counts(app.state.store)
    command = {"request_id": "new", "new_session": True, "instruction": "新会话内容", "expected_revision": 0}
    for override, status in [
        ({"expected_revision": 99}, 409),
        ({"selected_clip_ids": ["missing"]}, 400),
        ({"session_id": "already-selected"}, 400),
        ({"resume_run_id": "existing-run"}, 400),
        ({"instruction": ""}, 422),
    ]:
        response = client.post(base + "/commands", json={**command, **override})
        assert response.status_code == status, response.text
        assert table_counts(app.state.store) == before


def test_foreign_session_and_active_project_rejections_never_create_extra_sessions(tmp_path):
    app = create_app(tmp_path / "ownership.db", log_dir=tmp_path / "logs")
    client = TestClient(app)
    a = client.post("/api/v1/projects", json={"name": "A"}).json()["project_id"]
    b = client.post("/api/v1/projects", json={"name": "B"}).json()["project_id"]
    repo = RunRepository(app.state.store)
    active, _ = repo.begin(a, Command(request_id="active", new_session=True, instruction="正在编辑", expected_revision=0))
    before = table_counts(app.state.store)
    conflict = client.post(f"/api/v1/projects/{a}/commands", json={"request_id": "conflict", "new_session": True,
        "instruction": "不能并行创建", "expected_revision": 0})
    assert conflict.status_code == 409
    foreign = client.post(f"/api/v1/projects/{b}/commands", json={"request_id": "foreign", "session_id": active["session_id"],
        "instruction": "不能跨项目", "expected_revision": 0})
    assert foreign.status_code == 404
    assert table_counts(app.state.store) == before
    assert client.get(f"/api/v1/projects/{b}/sessions").json() == []


def test_cleanup_only_removes_empty_sessions_preserving_failed_cancelled_active_and_legacy(tmp_path):
    app = create_app(tmp_path / "cleanup.db", log_dir=tmp_path / "logs")
    store = app.state.store
    repo = RunRepository(store)
    pid = store.create_project("History")["project_id"]
    other = store.create_project("Other")["project_id"]
    empty = {repo.create_session(pid)["session_id"], repo.create_session(other)["session_id"]}
    keep, run_ids = set(), []
    for status in ("failed", "cancelled"):
        run, _ = repo.begin(pid, Command(request_id=status, new_session=True, instruction=f"{status} 的原始指令", expected_revision=0))
        repo.stop(pid, run["run_id"], status, {"summary": status})
        keep.add(run["session_id"])
        run_ids.append(run["run_id"])
    legacy, _ = repo.begin(pid, Command(request_id="legacy", instruction="旧版原始记录", expected_revision=0))
    repo.stop(pid, legacy["run_id"], "failed", {"summary": "旧版结果"})
    with store.connect() as con:
        con.execute("DELETE FROM agent_run_sessions WHERE run_id=?", (legacy["run_id"],))
        con.execute("DELETE FROM agent_run_inputs WHERE run_id=?", (legacy["run_id"],))
    keep.add(legacy["session_id"])
    run_ids.append(legacy["run_id"])
    active, _ = repo.begin(pid, Command(request_id="active", new_session=True, instruction="尚在运行", expected_revision=0))
    keep.add(active["session_id"])
    run_ids.append(active["run_id"])
    before = table_counts(store)
    repo.cleanup_empty_sessions()
    with store.connect() as con:
        remaining = {row[0] for row in con.execute("SELECT id FROM agent_sessions")}
        assert remaining == keep and not remaining.intersection(empty)
    after = table_counts(store)
    assert after["agent_sessions"] == before["agent_sessions"] - 2
    assert {k: v for k, v in after.items() if k != "agent_sessions"} == {k: v for k, v in before.items() if k != "agent_sessions"}
    assert {item["run_id"] for item in repo.conversation(pid)["items"]} == set(run_ids)
    assert repo.get(pid, active["run_id"])["status"] == "running"
    assert repo.sessions(other) == []
    repo.cleanup_empty_sessions()
    assert table_counts(store) == after


def test_legacy_unmapped_history_can_recreate_its_own_default_session_only(tmp_path):
    app = create_app(tmp_path / "legacy-default.db", log_dir=tmp_path / "logs")
    store = app.state.store
    repo = RunRepository(store)
    pid = store.create_project("Legacy")["project_id"]
    other = store.create_project("No history")["project_id"]
    run, _ = repo.begin(pid, Command(request_id="legacy", instruction="旧版信息", expected_revision=0))
    repo.stop(pid, run["run_id"], "failed", {"summary": "保留旧结果"})
    with store.connect() as con:
        con.execute("DELETE FROM agent_run_sessions WHERE run_id=?", (run["run_id"],))
        con.execute("DELETE FROM agent_sessions WHERE id=?", (run["session_id"],))
    restored = repo.sessions(pid)
    assert len(restored) == 1 and restored[0]["session_id"] == default_session_id(pid)
    assert restored[0]["title"] == "旧版信息"
    assert repo.sessions(other) == []
