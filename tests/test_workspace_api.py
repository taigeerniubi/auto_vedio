"""验证前端辅助接口的素材隔离、预览文件白名单和实际上传。"""
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from shiguang.api import create_app
from shiguang.media import DATABASE


def test_workspace_lists_and_empty_preview(tmp_path, monkeypatch):
    client = TestClient(create_app(tmp_path / "web.sqlite3", log_dir=tmp_path / "logs"))
    monkeypatch.delenv("SHIGUANG_MODEL_API_KEY", raising=False)
    assert client.get("/api/v1/projects").json() == []
    pid = client.post("/api/v1/projects", json={"name": "前端接口测试"}).json()["project_id"]
    assert client.get("/api/v1/projects").json()[0]["name"] == "前端接口测试"
    status = client.get("/api/v1/model-status").json()
    assert status["configured"] is False and "api_key" not in status
    library = client.get("/api/v1/library").json()
    assert all(not x["path"].startswith("managed/") and not Path(x["path"]).is_absolute() for x in library)
    base = f"/api/v1/projects/{pid}"
    assert client.post(base + "/preview", json={"expected_revision": 1}).status_code == 409
    assert client.post(base + "/preview", json={"expected_revision": 0}).json()["error_code"] == "EMPTY_TIMELINE"
    assert client.get(base + "/assets/missing/file").status_code == 404
    assert client.get(base + "/exports/missing.mp4").status_code == 404
    assert client.post(base + "/assets/upload?filename=script.exe", content=b"bad").status_code == 400
    assert client.post(base + "/assets/upload?filename=C%3Ainvalid.png", content=b"bad").status_code == 400


@pytest.mark.media
def test_uploaded_image_and_prepared_preview_are_project_scoped(tmp_path):
    client = TestClient(create_app(tmp_path / "web-media.sqlite3", log_dir=tmp_path / "logs"))
    pid = client.post("/api/v1/projects", json={"name": "浏览器上传验收", "composition": {"width": 320, "height": 180}}).json()["project_id"]
    other = client.post("/api/v1/projects", json={"name": "另一个工程"}).json()["project_id"]
    source = BytesIO()
    Image.new("RGB", (320, 180), (80, 120, 160)).save(source, format="PNG")
    response = client.post(f"/api/v1/projects/{pid}/assets/upload?filename=browser-test.png", content=source.getvalue(), headers={"Content-Type": "application/octet-stream"})
    assert response.status_code == 201, response.text
    aid = response.json()["asset_id"]
    base = f"/api/v1/projects/{pid}"
    assert Path(response.json()["path"]).is_relative_to(DATABASE)
    assert client.get(base + f"/assets/{aid}/thumbnail").headers["content-type"] == "image/jpeg"
    asset = client.get(base + f"/assets/{aid}/file", headers={"Range": "bytes=0-19"})
    assert asset.status_code == 206 and len(asset.content) == 20
    assert client.get(f"/api/v1/projects/{other}/assets/{aid}/file").status_code == 404
    did = client.post(base + "/drafts").json()["id"]
    result = client.post(base + f"/drafts/{did}/tools", json={"tool": "insert_clip", "arguments": {"clip": {"clip_id": "image1", "asset_id": aid, "kind": "image", "still_duration_frames": 30}}, "op_id": "insert-image", "expected_seq": 0})
    assert result.json()["ok"]
    client.post(base + f"/drafts/{did}/commit")
    preview = client.post(base + "/preview", json={"expected_revision": 1})
    assert preview.status_code == 200, preview.text
    plan = preview.json()["plan"]
    assert plan["total_frames"] == 30
    url = plan["clips"][0]["file"]
    assert client.get(url).status_code == 200
    assert client.get(plan["mix_file"]).status_code == 200
    assert client.get(url.rsplit('/', 1)[0] + "/allowed.json").status_code == 404
    assert client.get(url.replace(pid, other)).status_code == 404
