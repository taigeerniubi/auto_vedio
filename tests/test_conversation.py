"""验证对话持久化、项目隔离、幂等分页和旧版本记录兼容。"""
from fastapi.testclient import TestClient

from shiguang.api import create_app
from shiguang.agent.contracts import Command
from shiguang.agent.repository import RunRepository
from conftest import call, clip
from fakes import ScriptSession, tc


def test_conversation_survives_reload_and_pages_without_duplicates(tmp_path):
    """验证用户指令按项目保存，服务重建后分页读取且不进入诊断日志。"""
    db, logs = tmp_path / "history.db", tmp_path / "logs"
    client = TestClient(create_app(db, log_dir=logs,
        session_factory=lambda _: ScriptSession([[tc("ask_user", {"question": "请选择一个素材"})]])))
    pid = client.post("/api/v1/projects", json={"name": "History"}).json()["project_id"]
    other = client.post("/api/v1/projects", json={"name": "Other"}).json()["project_id"]
    prefix = f"/api/v1/projects/{pid}"
    requests = [{"request_id": f"history-{index}", "expected_revision": 0,
                 "instruction": f"private-history-instruction-{index}"} for index in range(4)]
    for request in requests:
        assert client.post(prefix + "/commands", json=request).status_code == 200
    client.post(prefix + "/commands", json=requests[-1])
    # 使用新应用实例模拟重启，旧记录通过迁移后的持久化表恢复。
    reopened = TestClient(create_app(db, log_dir=logs))
    first = reopened.get(prefix + "/runs?limit=2").json()
    assert [item["instruction"] for item in first["items"]] == [r["instruction"] for r in requests[2:]]
    assert all(item["created_at"] and item["result"]["question"] == "请选择一个素材" for item in first["items"])
    second = reopened.get(prefix + f"/runs?limit=2&before={first['next_cursor']}").json()
    assert [item["instruction"] for item in second["items"]] == [r["instruction"] for r in requests[:2]]
    assert second["next_cursor"] is None
    assert len({item["run_id"] for page in [first, second] for item in page["items"]}) == 4
    assert reopened.get(f"/api/v1/projects/{other}/runs").json()["items"] == []
    assert reopened.get("/api/v1/projects/missing/runs").status_code == 404
    assert reopened.get(prefix + "/runs?limit=101").status_code == 422
    assert reopened.get(prefix + "/runs?before=0").status_code == 422
    assert "private-history-instruction" not in (logs / "app.log").read_text(encoding="utf-8")


def test_legacy_run_has_result_without_invented_instruction(tmp_path):
    """旧版本运行仍可查看结果，但不会伪造未保存的用户原文。"""
    app = create_app(tmp_path / "legacy.db", log_dir=tmp_path / "logs",
        session_factory=lambda _: ScriptSession([[tc("ask_user", {"question": "历史结果"})]]))
    client = TestClient(app)
    pid = client.post("/api/v1/projects", json={"name": "Legacy"}).json()["project_id"]
    run = client.post(f"/api/v1/projects/{pid}/commands", json={"request_id": "old-run",
        "expected_revision": 0, "instruction": "原来的指令"}).json()
    with app.state.store.connect() as connection:
        connection.execute("DELETE FROM agent_run_inputs WHERE run_id=?", (run["run_id"],))
        connection.execute("DELETE FROM agent_run_sessions WHERE run_id=?", (run["run_id"],))
    item = client.get(f"/api/v1/projects/{pid}/runs").json()["items"][0]
    assert item["instruction"] is None and item["created_at"] is None
    assert item["result"]["question"] == "历史结果"
    default = client.get(f"/api/v1/projects/{pid}/sessions").json()[0]
    assert client.get(f"/api/v1/projects/{pid}/runs", params={"session_id": default["session_id"]}).json()["items"][0]["run_id"] == run["run_id"]


def test_sessions_keep_project_and_isolate_history_and_model_context(context, tmp_path):
    """新会话隔离模型上下文，历史可切回且项目素材和时间线不变。"""
    store, pid, did = context
    call(store, did, "insert_clip", {"clip": clip()})
    store.commit(did)
    before = store.project(pid)
    captured = []

    def factory(ctx):
        captured.append(ctx)
        return ScriptSession([[tc("finish", {"summary": "已读取当前工程"})]])

    client = TestClient(create_app(store.db, log_dir=tmp_path / "logs", session_factory=factory))
    base = f"/api/v1/projects/{pid}"
    assert client.get(base + "/sessions").json() == []
    first = client.post(base + "/sessions").json()

    def send(request_id, instruction, session_id):
        response = client.post(base + "/commands", json={"request_id": request_id, "instruction": instruction,
            "expected_revision": before["revision"], "session_id": session_id})
        assert response.status_code == 200, response.text
        return response.json()

    old_run = send("old", "旧会话的要求", first["session_id"])
    created = client.post(base + "/sessions")
    assert created.status_code == 200
    second = created.json()
    assert second["session_id"] != first["session_id"]
    assert client.get(base).json() == before
    assert len(client.get(base + "/assets").json()) == 4
    assert client.get(base + "/runs", params={"session_id": second["session_id"]}).json()["items"] == []

    send("new", "新会话的要求", second["session_id"])
    assert captured[-1]["近期对话"] == []
    assert captured[-1]["工程"] == before["snapshot"]
    assert len(captured[-1]["素材"]) == 4
    send("new-followup", "继续新会话", second["session_id"])
    assert [item["指令"] for item in captured[-1]["近期对话"]] == ["新会话的要求"]
    send("old-followup", "继续旧会话", first["session_id"])
    assert [item["指令"] for item in captured[-1]["近期对话"]] == ["旧会话的要求"]
    assert old_run["session_id"] == first["session_id"]

    sessions = client.get(base + "/sessions").json()
    assert [s["session_id"] for s in sessions] == [first["session_id"], second["session_id"]]
    assert [s["title"] for s in sessions] == ["旧会话的要求", "新会话的要求"]
    assert all(s["has_messages"] and s["updated_at"] >= s["created_at"] for s in sessions)

    reopened = TestClient(create_app(store.db, log_dir=tmp_path / "logs"))
    assert len(reopened.get(base + "/sessions").json()) == 2
    page = reopened.get(base + "/runs", params={"session_id": second["session_id"], "limit": 1}).json()
    assert [r["instruction"] for r in page["items"]] == ["继续新会话"]
    older = reopened.get(base + "/runs", params={"session_id": second["session_id"], "before": page["next_cursor"]}).json()
    assert [r["instruction"] for r in older["items"]] == ["新会话的要求"]
    assert older["next_cursor"] is None
    other = client.post("/api/v1/projects", json={"name": "Other"}).json()["project_id"]
    foreign = f"/api/v1/projects/{other}"
    assert client.get(foreign + "/runs", params={"session_id": second["session_id"]}).status_code == 404
    assert client.post(foreign + "/commands", json={"request_id": "foreign", "instruction": "test",
        "expected_revision": 0, "session_id": second["session_id"]}).status_code == 404


def test_new_session_cannot_hide_an_active_run(tmp_path):
    """运行期间拒绝新建，刷新会话列表仍可定位并取消原运行。"""
    app = create_app(tmp_path / "active.db", log_dir=tmp_path / "logs")
    client = TestClient(app)
    pid = client.post("/api/v1/projects", json={"name": "Active"}).json()["project_id"]
    base = f"/api/v1/projects/{pid}"
    repo = RunRepository(app.state.store)
    run, _ = repo.begin(pid, Command(request_id="active", instruction="test", expected_revision=0))
    assert client.post(base + "/sessions").status_code == 409
    sessions = client.get(base + "/sessions").json()
    assert len(sessions) == 1 and sessions[0]["running"] is True
    assert sessions[0]["session_id"] == run["session_id"]
    repo.stop(pid, run["run_id"], "cancelled", {"summary": "cancelled"})
    assert client.post(base + "/sessions").status_code == 200


def test_session_titles_come_from_first_message_including_legacy(tmp_path):
    """首条消息折叠换行后作为持久标题，兼容旧映射且不被后续消息覆盖。"""
    app = create_app(tmp_path / "titles.db", log_dir=tmp_path / "logs")
    client = TestClient(app)
    pid = client.post("/api/v1/projects", json={"name": "Titles"}).json()["project_id"]
    base = f"/api/v1/projects/{pid}"
    assert client.get(base + "/sessions").json() == []
    repo = RunRepository(app.state.store)
    for index, instruction in enumerate(["  给开头添加标题\n再加配乐  ", "修改后续片段"]):
        run, _ = repo.begin(pid, Command(request_id=f"title-{index}", instruction=instruction, expected_revision=0))
        repo.stop(pid, run["run_id"], "cancelled", {"summary": "cancelled"})
        with app.state.store.connect() as connection:
            connection.execute("DELETE FROM agent_run_sessions WHERE run_id=?", (run["run_id"],))
    reopened = TestClient(create_app(app.state.store.db, log_dir=tmp_path / "logs"))
    assert reopened.get(base + "/sessions").json()[0]["title"] == "给开头添加标题 再加配乐"
    new = client.post(base + "/sessions").json()
    assert new["title"] == "新会话" and new["has_messages"] is False
