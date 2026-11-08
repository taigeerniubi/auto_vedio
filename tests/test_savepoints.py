"""验证手动暂存的持久化、工程隔离、恢复及恢复后的撤销重做。"""
from datetime import datetime

from fastapi.testclient import TestClient
import pytest

from shiguang.api import create_app
from shiguang.agent.contracts import Command
from shiguang.agent.repository import RunRepository
from shiguang.store import Store
from conftest import call, clip


@pytest.fixture
def saved_context(context):
    store, pid, did = context
    assert call(store, did, "assemble_sequence", {"clips": [clip()]})["ok"]
    store.commit(did)
    app = create_app(store.db, log_dir=store.db.parent / "logs")
    return TestClient(app), store, pid, f"/api/v1/projects/{pid}"


def save(client, root, revision, request_id):
    response = client.post(root + "/savepoints", json={"expected_revision": revision, "request_id": request_id})
    assert response.status_code == 200, response.text
    return response.json()


def trim(store, pid):
    draft = store.create_draft(pid)
    assert call(store, draft["id"], "trim_clip", {"clip_id": "c1", "source_out_frame": 30})["ok"]
    return store.commit(draft["id"])


def test_manual_save_time_survives_reload_and_is_not_changed_by_autosave(saved_context):
    client, store, pid, root = saved_context
    before = store.project(pid)
    assert before["last_savepoint"] is None
    with store.connect() as con:
        undo_before = con.execute("SELECT undo FROM projects WHERE id=?", (pid,)).fetchone()[0]
    first = save(client, root, 1, "manual-1")
    assert datetime.fromisoformat(first["created_at"]).utcoffset().total_seconds() == 0
    reopened = Store(store.db).project(pid)
    assert reopened["snapshot"] == before["snapshot"] and reopened["revision"] == 1
    assert reopened["last_savepoint"] == first
    with store.connect() as con:
        assert con.execute("SELECT undo FROM projects WHERE id=?", (pid,)).fetchone()[0] == undo_before
        assert con.execute("SELECT COUNT(*) FROM revisions WHERE project_id=?", (pid,)).fetchone()[0] == 2
    trim(store, pid)
    assert store.project(pid)["last_savepoint"] == first
    listed = next(p for p in client.get("/api/v1/projects").json() if p["project_id"] == pid)
    assert listed["revision"] == 2 and listed["last_savepoint"] == first
    second = save(client, root, 2, "manual-2")
    assert second["savepoint_id"] != first["savepoint_id"]
    assert client.get(root + "/savepoints").json() == [second, first]
    # 网络重试只返回原记录，即使随后已有新的自动保存。
    assert save(client, root, 1, "manual-1") == first
    assert client.post(root + "/savepoints", json={"expected_revision": 2, "request_id": "manual-1"}).status_code == 409
    assert client.get(root + "/savepoints").json() == [second, first]


def test_restore_can_be_undone_and_redone_without_removing_sessions_or_assets(saved_context):
    client, store, pid, root = saved_context
    initial = store.project(pid)["snapshot"]
    first = save(client, root, 1, "initial")
    current = trim(store, pid)["snapshot"]
    second = save(client, root, 2, "trimmed")
    session = RunRepository(store).create_session(pid)
    assets_before = store.assets(pid)
    result = client.post(root + f"/savepoints/{first['savepoint_id']}/restore", json={"expected_revision": 2})
    assert result.status_code == 200 and result.json()["changed"] is True
    assert store.project(pid)["snapshot"] == initial
    assert store.project(pid)["revision"] == 3
    assert store.project(pid)["last_savepoint"] == second
    assert store.history(pid, "undo", 3)["snapshot"] == current
    assert store.history(pid, "redo", 4)["snapshot"] == initial
    assert store.assets(pid) == assets_before
    assert RunRepository(store).sessions(pid)[0]["session_id"] == session["session_id"]
    assert client.get(root + "/savepoints").json() == [second, first]
    # 再次恢复相同内容不制造多余的撤销步骤。
    same = client.post(root + f"/savepoints/{first['savepoint_id']}/restore", json={"expected_revision": 5})
    assert same.json()["changed"] is False and same.json()["revision"] == 5


def test_savepoints_are_project_scoped_and_removing_one_keeps_current_edit(saved_context):
    client, store, pid, root = saved_context
    other = store.create_project("另一个项目")["project_id"]
    other_root = f"/api/v1/projects/{other}"
    first = save(client, root, 1, "first")
    second = save(client, root, 1, "second")
    foreign = save(client, other_root, 0, "first")
    before = store.project(pid)
    assert client.get(other_root + "/savepoints").json() == [foreign]
    assert client.post(root + f"/savepoints/{foreign['savepoint_id']}/restore", json={"expected_revision": 1}).status_code == 404
    assert client.delete(root + f"/savepoints/{foreign['savepoint_id']}").status_code == 404
    assert client.delete(root + f"/savepoints/{second['savepoint_id']}").status_code == 200
    after = store.project(pid)
    assert (after["snapshot"], after["revision"]) == (before["snapshot"], before["revision"])
    assert after["last_savepoint"] == first
    store.delete_project(pid, 1)
    with store.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM project_savepoints WHERE project_id=?", (pid,)).fetchone()[0] == 0
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    assert client.get(other_root + "/savepoints").json() == [foreign]


def test_stale_revision_and_running_agent_do_not_change_saved_state(saved_context):
    client, store, pid, root = saved_context
    first = save(client, root, 1, "initial")
    assert client.post(root + "/savepoints", json={"expected_revision": 0, "request_id": "stale"}).status_code == 409
    restore = root + f"/savepoints/{first['savepoint_id']}/restore"
    assert client.post(restore, json={"expected_revision": 0}).status_code == 409
    RunRepository(store).begin(pid, Command(request_id="running", expected_revision=1, instruction="正在剪辑", new_session=True))
    assert client.post(root + "/savepoints", json={"expected_revision": 1, "request_id": "busy"}).status_code == 409
    assert client.post(restore, json={"expected_revision": 1}).status_code == 409
    assert store.project(pid)["revision"] == 1
    assert client.get(root + "/savepoints").json() == [first]


@pytest.mark.parametrize("body", [
    {"expected_revision": True, "request_id": "invalid"},
    {"expected_revision": -1, "request_id": "invalid"},
    {"expected_revision": 1, "request_id": ""},
    {"expected_revision": 1},
])
def test_invalid_savepoint_request_never_writes_a_record(saved_context, body):
    client, _, _, root = saved_context
    assert client.post(root + "/savepoints", json=body).status_code == 422
    assert client.get(root + "/savepoints").json() == []
