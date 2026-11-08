"""验证本机工具接口的参数校验和项目隔离。"""
from fastapi.testclient import TestClient
from shiguang.api import create_app


def test_local_api_and_project_isolation(tmp_path):
    client = TestClient(create_app(tmp_path / "api.sqlite3"))
    assert client.get("/health").json()["status"] == "ok"
    assert len(client.get("/api/v1/tools").json()["tools"]) == 37
    p = client.post("/api/v1/projects", json={"name": "trip"}).json()["project_id"]
    other = client.post("/api/v1/projects", json={"name": "other"}).json()["project_id"]
    draft = client.post(f"/api/v1/projects/{p}/drafts").json()["id"]
    assert client.get(f"/api/v1/projects/{other}/drafts/{draft}").status_code == 404
    result = client.post(f"/api/v1/projects/{p}/drafts/{draft}/tools", json={"tool": "validate_draft"}).json()
    assert result["ok"] and result["data"]["valid"]
    assert client.post(f"/api/v1/projects/{p}/assets/import", json={"path": "../package.json"}).status_code == 400
    assert client.post(f"/api/v1/projects/{p}/history/fake", json={"expected_revision": 0}).status_code == 400
