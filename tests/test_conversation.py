"""验证对话持久化、项目隔离、幂等分页和旧版本记录兼容。"""
from fastapi.testclient import TestClient

from shiguang.api import create_app
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
    item = client.get(f"/api/v1/projects/{pid}/runs").json()["items"][0]
    assert item["instruction"] is None and item["created_at"] is None
    assert item["result"]["question"] == "历史结果"
