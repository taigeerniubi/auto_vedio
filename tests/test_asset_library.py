"""验证导入历史来自登记记录，跨项目复用母版不会重编码或复活旧时间线。"""
from hashlib import sha256
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from shiguang import workspace_api
from shiguang.api import create_app
from shiguang.models import Asset


@pytest.fixture
def library_context(tmp_path, monkeypatch):
    root = tmp_path / "media"
    root.mkdir()
    monkeypatch.setattr(workspace_api, "DATABASE", root)
    app = create_app(tmp_path / "library.sqlite3", log_dir=tmp_path / "logs")
    client = TestClient(app)
    store = app.state.store
    source = store.create_project("历史来源")["project_id"]
    target = store.create_project("新项目")["project_id"]
    return client, store, root, source, target


def registered(store, root, pid, aid, content=b"test-image", name="历史图片.png"):
    master = root / "managed" / pid / aid / "master.png"
    master.parent.mkdir(parents=True)
    master.write_bytes(content)
    asset = Asset(asset_id=aid, project_id=pid, kind="image", name=name,
                  path=str(master), sha256=sha256(content).hexdigest(), width=64, height=64)
    store.add_asset(asset)
    return asset


def test_empty_registry_ignores_files_in_media_folder(library_context):
    client, _, root, _, _ = library_context
    (root / "never-imported.mp4").write_bytes(b"not registered")
    assert client.get("/api/v1/library").json() == []


def test_history_deduplicates_and_omits_missing_or_outside_masters(library_context, tmp_path):
    client, store, root, source, target = library_context
    registered(store, root, source, "first")
    registered(store, root, source, "different", b"different-image", "另一张.png")
    latest = registered(store, root, target, "latest", name="最近导入.png")
    missing = registered(store, root, target, "missing", b"missing")
    Path(missing.path).unlink()
    external = tmp_path / "external.png"
    external.write_bytes(b"outside")
    store.add_asset(latest.model_copy(update={"asset_id": "outside", "path": str(external), "sha256": "outside"}))
    data = client.get("/api/v1/library").json()
    assert [row["asset_id"] for row in data] == ["latest", "different"]
    assert data[0]["size"] == len(b"test-image")
    assert all("path" not in row and "sha256" not in row for row in data)


def test_reuse_is_project_scoped_deduplicated_and_does_not_transcode(library_context, monkeypatch):
    client, store, root, source, target = library_context
    original = registered(store, root, source, "first")
    def unexpected(*args, **kwargs):
        pytest.fail("Reuse must not invoke import or transcode")
    monkeypatch.setattr(workspace_api, "import_media", unexpected)
    before = store.project(target)
    body = {"source_project_id": source, "source_asset_id": original.asset_id}
    response = client.post(f"/api/v1/projects/{target}/assets/reuse", json=body)
    assert response.status_code == 200, response.text
    reused = response.json()
    assert reused["project_id"] == target and reused["asset_id"] != original.asset_id
    assert Path(reused["path"]).read_bytes() == Path(original.path).read_bytes()
    assert Path(reused["path"]).parent == root / "managed" / target / reused["asset_id"]
    assert store.project(target) == before
    assert client.post(f"/api/v1/projects/{target}/assets/reuse", json=body).json() == reused
    assert len(store.assets(target)) == 1
    with store.connect() as con:
        assert con.execute("SELECT count(*) FROM asset_analysis WHERE project_id=?", (target,)).fetchone()[0] == 0
    body["source_project_id"] = target
    assert client.post(f"/api/v1/projects/{target}/assets/reuse", json=body).status_code == 404


def test_reimport_removed_asset_uses_new_id_without_restoring_edits(library_context):
    client, store, root, source, target = library_context
    original = registered(store, root, source, "first")
    body = {"source_project_id": source, "source_asset_id": original.asset_id}
    endpoint = f"/api/v1/projects/{target}/assets/reuse"
    first = client.post(endpoint, json=body).json()
    draft = store.create_draft(target)
    assert store.call(draft["id"], "remove_asset", {"asset_id": first["asset_id"]}, "remove", 0)["ok"]
    store.commit(draft["id"])
    before = store.project(target)
    second = client.post(endpoint, json=body).json()
    assert second["asset_id"] != first["asset_id"]
    assert store.project(target) == before
    assert before["snapshot"]["clips"] == []
    assert [asset["asset_id"] for asset in client.get(f"/api/v1/projects/{target}/assets").json()] == [second["asset_id"]]


def test_missing_source_and_unsafe_ids_are_rejected(library_context):
    client, store, root, source, target = library_context
    asset = registered(store, root, source, "first")
    Path(asset.path).unlink()
    endpoint = f"/api/v1/projects/{target}/assets/reuse"
    assert client.post(endpoint, json={"source_project_id": source, "source_asset_id": "first"}).status_code == 404
    assert client.post(endpoint, json={"source_project_id": source, "source_asset_id": "../first"}).status_code == 422
    assert store.assets(target) == {}
