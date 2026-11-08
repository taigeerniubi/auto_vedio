"""验证后台导出的设置、持久化进度、工程隔离及冻结版本。"""
import json
from threading import Event
from time import monotonic, sleep

from fastapi.testclient import TestClient
import pytest

from shiguang import media
from shiguang import workspace_api
from shiguang.api import create_app
from shiguang.export_jobs import ExportService
from shiguang.tools import ToolError
from conftest import call, clip


def export_client(context, monkeypatch, renderer):
    store, pid, did = context
    monkeypatch.setattr(media, "ARTIFACTS", store.db.parent / "artifacts")
    monkeypatch.setattr(workspace_api, "ARTIFACTS", store.db.parent / "artifacts")
    assert call(store, did, "assemble_sequence", {"clips": [clip()]})["ok"]
    store.commit(did)
    app = create_app(store.db, export_renderer=renderer, log_dir=store.db.parent / "logs")
    return TestClient(app), store, pid


def wait_done(client, root, export_id):
    deadline = monotonic() + 4
    while monotonic() < deadline:
        job = client.get(f"{root}/jobs/{export_id}").json()
        if job["status"] in {"completed", "failed"}:
            return job
        sleep(.01)
    pytest.fail("Background export did not finish")


def completed_output(snapshot, assets, output, **options):
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"test-mp4")
    options["on_progress"]({"stage": "completed", "progress": 100, "rendered_frames": 60})
    return {"output": str(output), "frames": 60, "width": 1280, "height": 720,
            "fps": 30, "bytes": 8, "warnings": []}


def test_background_job_progress_freezes_revision_and_deduplicates(context, monkeypatch):
    started, release = Event(), Event()
    captured = []

    def render(snapshot, assets, output, **options):
        captured.append((snapshot, assets, options))
        options["on_progress"]({"stage": "rendering", "progress": 37, "rendered_frames": 15})
        started.set()
        assert release.wait(4)
        return completed_output(snapshot, assets, output, **options)

    client, store, pid = export_client(context, monkeypatch, render)
    root = f"/api/v1/projects/{pid}/exports"
    assert client.get(root + "/jobs/latest").json() is None
    data = {"expected_revision": 1, "resolution": "720p", "quality": "compact", "filename": "酒吧成片.mp4"}
    response = client.post(root, json=data)
    assert response.status_code == 202
    job_id = response.json()["export_id"]
    try:
        assert started.wait(2)
        progress = client.get(f"{root}/jobs/{job_id}").json()
        assert (progress["stage"], progress["progress"], progress["rendered_frames"], progress["total_frames"]) == ("rendering", 37, 15, 60)
        assert progress["options"] == {key: data[key] for key in ("resolution", "quality", "filename")}
        assert "snapshot" not in progress and "assets" not in progress
        assert client.post(root, json=data).json()["export_id"] == job_id
        assert client.post(root, json={**data, "quality": "high"}).status_code == 409
        assert client.get(f"{root}/jobs/{job_id}/download").status_code == 409
        # 导出期间允许新编辑，但后台始终使用提交导出时的快照。
        draft = store.create_draft(pid)
        assert call(store, draft["id"], "trim_clip", {"clip_id": "c1", "source_out_frame": 30})["ok"]
        store.commit(draft["id"])
        assert captured[0][0].clips[0].source_out_frame == 60
        assert captured[0][2]["resolution"] == "720p" and captured[0][2]["quality"] == "compact"
    finally:
        release.set()
    result = wait_done(client, root, job_id)
    assert result["status"] == "completed", result
    assert result["progress"] == 100 and result["revision"] == 1
    assert store.project(pid)["revision"] == 2
    download = client.get(result["result"]["download_url"])
    assert download.status_code == 200 and download.content == b"test-mp4"
    assert "%E9%85%92%E5%90%A7%E6%88%90%E7%89%87.mp4" in download.headers["content-disposition"]
    assert ExportService(store).latest(pid) == result
    other = store.create_project("Other")["project_id"]
    assert client.get(f"/api/v1/projects/{other}/exports/jobs/{job_id}").status_code == 404
    assert client.get(f"/api/v1/projects/{other}/exports/jobs/{job_id}/download").status_code == 404


@pytest.mark.parametrize("bad", [
    {"resolution": "1440p"}, {"quality": "lossless"}, {"expected_revision": True},
    {"filename": "../outside.mp4"}, {"filename": r"D:\outside.mp4"},
    {"filename": "CON.mp4"}, {"filename": ""}, {"filename": "bad?.mp4"},
])
def test_export_settings_reject_invalid_input_without_starting(context, monkeypatch, bad):
    calls = []
    client, _, pid = export_client(context, monkeypatch, lambda *a, **k: calls.append(a))
    response = client.post(f"/api/v1/projects/{pid}/exports", json={"expected_revision": 1, **bad})
    assert response.status_code == 422
    assert calls == []


def test_export_checks_revision_and_empty_timeline(context, monkeypatch):
    client, store, pid = export_client(context, monkeypatch, completed_output)
    root = f"/api/v1/projects/{pid}/exports"
    assert client.post(root, json={"expected_revision": 0}).status_code == 409
    empty = store.create_project("Empty")["project_id"]
    result = client.post(f"/api/v1/projects/{empty}/exports", json={"expected_revision": 0})
    assert result.status_code == 400 and result.json()["error_code"] == "EMPTY_TIMELINE"
    assert client.get(root + "/jobs/latest").json() is None


def test_export_failure_is_persisted_and_retry_creates_new_job(context, monkeypatch):
    def failed(*args, **kwargs):
        kwargs["on_progress"]({"stage": "rendering", "progress": 21})
        raise ToolError("MEDIA_PROCESS_FAILED", "Failed subprocess secret=private-value")

    client, _, pid = export_client(context, monkeypatch, failed)
    root = f"/api/v1/projects/{pid}/exports"
    submitted = client.post(root, json={"expected_revision": 1}).json()
    job = wait_done(client, root, submitted["export_id"])
    assert job["status"] == "failed" and job["progress"] == 21
    assert job["error"]["code"] == "MEDIA_PROCESS_FAILED"
    assert "private-value" not in job["error"]["message"] and job["result"] is None
    assert client.get(f"{root}/jobs/{job['export_id']}/download").status_code == 409
    retry = client.post(root, json={"expected_revision": 1}).json()
    assert retry["export_id"] != job["export_id"]
    wait_done(client, root, retry["export_id"])


def test_export_startup_marks_interrupted_job_and_keeps_completed_history(context, monkeypatch):
    client, store, pid = export_client(context, monkeypatch, completed_output)
    root = f"/api/v1/projects/{pid}/exports"
    first = client.post(root, json={"expected_revision": 1, "filename": "custom"}).json()
    first = wait_done(client, root, first["export_id"])
    second = client.post(root, json={"expected_revision": 1}).json()
    second = wait_done(client, root, second["export_id"])
    assert first["options"]["filename"] == "custom.mp4"
    with store.connect() as con:
        data = {**second, "status": "running", "stage": "rendering", "progress": 42, "result": None}
        con.execute("UPDATE export_jobs SET status='running',data=? WHERE id=?", (json.dumps(data), second["export_id"]))
    restarted = create_app(store.db, export_renderer=lambda *a, **k: pytest.fail("Must not auto-replay exports"),
                           log_dir=store.db.parent / "logs")
    with TestClient(restarted) as fresh:
        latest = fresh.get(root + "/jobs/latest").json()
        assert latest["status"] == "failed" and latest["error"]["code"] == "EXPORT_INTERRUPTED"
        assert latest["progress"] == 42
        assert fresh.get(f"{root}/jobs/{first['export_id']}").json()["status"] == "completed"


def test_legacy_download_cannot_bypass_pending_job_gate(context, monkeypatch):
    started, release = Event(), Event()

    def renderer(snapshot, assets, output, **options):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"unverified")
        output.with_suffix(".pending.mp4").write_bytes(b"partial")
        started.set()
        assert release.wait(4)
        return completed_output(snapshot, assets, output, **options)

    client, store, pid = export_client(context, monkeypatch, renderer)
    root = f"/api/v1/projects/{pid}/exports"
    job = client.post(root, json={"expected_revision": 1}).json()
    try:
        assert started.wait(2)
        assert client.get(f"{root}/{job['export_id']}.mp4").status_code == 404
        assert client.get(f"{root}/{job['export_id']}.pending.mp4").status_code == 404
        legacy = media.ARTIFACTS / "exports" / pid / "revision-1-legacy.mp4"
        legacy.write_bytes(b"verified-legacy")
        assert client.get(root + "/" + legacy.name).content == b"verified-legacy"
    finally:
        release.set()
    done = wait_done(client, root, job["export_id"])
    assert done["status"] == "completed"
    assert client.get(f"{root}/{job['export_id']}.mp4").content == b"test-mp4"
