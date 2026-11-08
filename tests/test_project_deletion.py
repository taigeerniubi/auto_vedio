"""验证工程删除的关联清理、任务保护及跨工程素材隔离。"""
from hashlib import sha256
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from shiguang import workspace_api
from shiguang.agent.contracts import Command
from shiguang.agent.repository import RunRepository
from shiguang.analysis import AnalysisService
from shiguang.api import create_app
from shiguang.models import Asset
from shiguang.tools import ToolError


@pytest.fixture
def setup(tmp_path, monkeypatch):
    media = tmp_path / "media"
    monkeypatch.setattr(workspace_api, "DATABASE", media)
    app = create_app(tmp_path / "delete.sqlite3", log_dir=tmp_path / "logs")
    store = app.state.store
    pid = store.create_project("待删除项目")["project_id"]
    master = media / "managed" / pid / "image" / "master.png"
    master.parent.mkdir(parents=True)
    master.write_bytes(b"original-media")
    asset = Asset(asset_id="image", project_id=pid, kind="image", name="原素材.png",
                  path=str(master), sha256=sha256(master.read_bytes()).hexdigest(), width=64, height=64)
    store.add_asset(asset)
    return TestClient(app), store, pid, asset, tmp_path


def history(store, pid):
    repo = RunRepository(store)
    first, _ = repo.begin(pid, Command(request_id="first", expected_revision=0, new_session=True, instruction="初次剪辑"))
    repo.save_checkpoint(first["run_id"], {"snapshot": store.project(pid)["snapshot"], "instruction": "初次剪辑", "trace": []})
    repo.stop(pid, first["run_id"], "failed", {"error_code": "MODEL_TIMEOUT"})
    second, _ = repo.begin(pid, Command(request_id="resume", expected_revision=0, session_id=first["session_id"],
                                       instruction="继续", resume_run_id=first["run_id"]))
    repo.stop(pid, second["run_id"], "needs_input", {"question": "剪多久？"})
    with store.connect() as con:
        con.execute("INSERT INTO operations VALUES(?,?,?,?)", (first["draft_id"], "read", "hash", "{}"))
        record = json.dumps({"status": "succeeded", "stages": {"visual": {"status": "succeeded"}}})
        con.execute("INSERT INTO asset_analysis VALUES(?,?,?)", (pid, "image", record))
        con.execute("INSERT INTO analysis_versions VALUES(?,?,?,?)", (f"analysis-{pid}", pid, "image", record))
        con.execute("INSERT INTO audio_query_cache VALUES(?,?,?,?,?,?)", (f"cache-{pid}", pid, "image", "sha", "{}", "2026-11-08"))
        con.execute("INSERT INTO export_jobs VALUES(?,?,?,?,?,?,?)", (f"export-{pid}", pid, "completed", "{}", "{}", "{}", "{}"))
    return first, second


def database_dump(store):
    with store.connect() as con:
        return "\n".join(con.iterdump())


def test_delete_cleans_project_history_but_preserves_other_project_and_files(setup):
    client, store, pid, asset, folder = setup
    other = store.create_project("保留项目")["project_id"]
    reused = client.post(f"/api/v1/projects/{other}/assets/reuse", json={
        "source_project_id": pid, "source_asset_id": asset.asset_id,
    }).json()
    removed_runs = history(store, pid)
    kept_runs = history(store, other)
    other_before = store.project(other)
    saved_export = folder / "saved-video.mp4"
    saved_export.write_bytes(b"saved-export")
    response = client.delete(f"/api/v1/projects/{pid}", params={"expected_revision": 0})
    assert response.status_code == 200 and response.json() == {"project_id": pid, "deleted": True}
    assert client.get(f"/api/v1/projects/{pid}").status_code == 404
    assert client.get(f"/api/v1/projects/{pid}/sessions").status_code == 404
    assert client.get(f"/api/v1/projects/{pid}/exports/jobs/latest").status_code == 404
    assert [p["project_id"] for p in client.get("/api/v1/projects").json()] == [other]
    assert store.project(other) == other_before
    assert client.get(f"/api/v1/projects/{other}/assets/{reused['asset_id']}/file").content == b"original-media"
    assert Path(asset.path).read_bytes() == b"original-media"
    assert saved_export.read_bytes() == b"saved-export"
    assert client.get("/api/v1/library").json()[0]["project_id"] == other
    with store.connect() as con:
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        for table in ("revisions", "assets", "drafts", "agent_runs", "agent_sessions", "asset_analysis", "analysis_versions", "audio_query_cache", "export_jobs"):
            assert not con.execute(f"SELECT 1 FROM {table} WHERE project_id=?", (pid,)).fetchone()
        for table in ("agent_checkpoints", "agent_run_inputs", "agent_run_sessions"):
            assert {r[0] for r in con.execute(f"SELECT run_id FROM {table}")} <= {r["run_id"] for r in kept_runs}
        assert con.execute("SELECT source_run_id,new_run_id FROM agent_resumptions").fetchall()[0][:] == tuple(r["run_id"] for r in kept_runs)
        assert [r[0] for r in con.execute("SELECT draft_id FROM operations")] == [kept_runs[0]["draft_id"]]
    assert RunRepository(store).get(other, kept_runs[0]["run_id"])["status"] == "failed"
    for run in removed_runs:
        assert client.get(f"/api/v1/projects/{pid}/runs/{run['run_id']}").status_code == 404
    assert client.delete(f"/api/v1/projects/{pid}", params={"expected_revision": 0}).status_code == 404


@pytest.mark.parametrize("activity", ["agent", "queued-export", "running-export", "analysis", "analysis-stage"])
def test_deletion_refuses_active_work_without_partial_changes(setup, activity):
    client, store, pid, _, _ = setup
    if activity == "agent":
        RunRepository(store).begin(pid, Command(request_id="busy", new_session=True, expected_revision=0, instruction="正在编辑"))
    else:
        with store.connect() as con:
            if activity.endswith("export"):
                con.execute("INSERT INTO export_jobs VALUES(?,?,?,?,?,?,?)", ("active-export", pid, activity.split("-")[0], "{}", "{}", "{}", "{}"))
            else:
                record = {"status": "running" if activity == "analysis" else "partial", "stages": {"visual": {"status": "running"}}}
                con.execute("INSERT INTO asset_analysis VALUES(?,?,?)", (pid, "image", json.dumps(record)))
    before = database_dump(store)
    response = client.delete(f"/api/v1/projects/{pid}", params={"expected_revision": 0})
    assert response.status_code == 409 and response.json()["error_code"] == "PROJECT_DELETE_CONFLICT"
    assert database_dump(store) == before


def test_deletion_requires_matching_revision(setup):
    client, store, pid, _, _ = setup
    before = database_dump(store)
    root = f"/api/v1/projects/{pid}"
    assert client.delete(root).status_code == 422
    assert client.delete(root, params={"expected_revision": -1}).status_code == 422
    response = client.delete(root, params={"expected_revision": 1})
    assert response.status_code == 409 and response.json()["error_code"] == "REVISION_CONFLICT"
    assert database_dump(store) == before


def test_late_writes_cannot_recreate_deleted_project_records(setup):
    _, store, pid, asset, _ = setup
    service = AnalysisService(store)
    store.delete_project(pid, 0)
    for operation in [lambda: store.add_asset(asset), lambda: store.create_draft(pid),
                      lambda: service.save(pid, asset.asset_id, {"status": "succeeded"})]:
        with pytest.raises(ToolError) as failure:
            operation()
        assert failure.value.code == "PROJECT_NOT_FOUND"
    with store.connect() as con:
        for table in ("assets", "drafts", "asset_analysis"):
            assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
