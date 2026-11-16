"""验证业务成功、业务错误和模型调用故障的 HTTP 状态与可读诊断。"""
import json
import logging

from fastapi.testclient import TestClient
import httpx
import pytest

from shiguang.api import create_app
from shiguang.agent.providers import ModelConfig, RemoteSession
from shiguang.http_status import error_status
from shiguang.log_format import ConsoleFormatter
from shiguang.observability import exception_details, english_record
from shiguang.tools import ToolError


def test_success_and_business_errors_use_real_http_status(tmp_path):
    """确保成功为 200、工具失败为 400、参数错误为 422。"""
    client = TestClient(create_app(tmp_path / "state.db", log_dir=tmp_path / "logs"))
    created = client.post("/api/v1/projects", json={"name": "HTTP contract"})
    assert created.status_code == 200
    pid = created.json()["project_id"]
    draft = client.post(f"/api/v1/projects/{pid}/drafts")
    assert draft.status_code == 200
    result = client.post(f"/api/v1/projects/{pid}/drafts/{draft.json()['id']}/tools", json={"tool": "unknown"})
    assert result.status_code == 400 and result.json()["summary"]
    assert result.json()["request_id"] == result.headers["X-Request-ID"]
    invalid = client.post("/api/v1/projects", json={"name": ""})
    assert invalid.status_code == 422
    assert "body.name" in invalid.json()["summary"]


@pytest.mark.parametrize("upstream,code,expected", [
    (401, "invalid_api_key", "authentication failed"),
    (429, "credit_balance_exhausted", "credits or quota exhausted"),
    (429, "rate_limit_exceeded", "rate limit exceeded"),
    (500, "server_error", "provider rejected"),
])
def test_provider_failure_is_502_with_safe_reason(tmp_path, upstream, code, expected):
    """确保服务商错误清晰可见，同时不泄露密钥或完整用户指令。"""
    secret, instruction = "test-private-key", "private instruction not for logging"

    def handle(request):
        """返回服务商的模拟错误。

        Args:
            request: 模型请求。

        Returns:
            含白名单诊断和敏感文本的 HTTP 响应。
        """
        return httpx.Response(upstream, headers={"x-request-id": "upstream-123"}, json={"error": {
            "code": code, "message": f"Example upstream reason {secret} {instruction}"}})

    client = TestClient(create_app(tmp_path / "state.db", log_dir=tmp_path / "logs",
        session_factory=lambda ctx: RemoteSession(ModelConfig("openai", "test-model", secret), ctx, httpx.MockTransport(handle))))
    pid = client.post("/api/v1/projects", json={"name": "Gateway errors"}).json()["project_id"]
    response = client.post(f"/api/v1/projects/{pid}/commands", json={
        "request_id": "cmd-1", "expected_revision": 0, "instruction": instruction})
    data = response.json()
    assert response.status_code == 502 and data["ok"] is False
    assert expected in data["summary"] and "Example upstream reason" in data["summary"]
    assert data["result"]["upstream_status"] == upstream
    assert data["result"]["provider_error_code"] == code
    assert data["result"]["provider_request_id"] == "upstream-123"
    assert secret not in response.text and instruction not in response.text
    assert data["request_id"] == response.headers["X-Request-ID"]
    # 查询失败的运行记录本身仍然是成功读取，不应阻断前端轮询。
    queried = client.get(f"/api/v1/projects/{pid}/runs/{data['run_id']}")
    assert queried.status_code == 200 and queried.json()["status"] == "failed"
    rows = [json.loads(line) for line in (tmp_path / "logs/backend.log").read_text(encoding="utf-8").splitlines()]
    completed = next(r for r in rows if r.get("request_id") == data["request_id"] and r["event"] == "request_completed")
    assert completed["level"] == "ERROR" and completed["status_code"] == 502
    assert completed["path"].endswith("/commands")
    assert all(key.isascii() for r in rows for key in r)


@pytest.mark.parametrize("code,status", [("MEDIA_PROCESS_FAILED", 500), ("MODEL_TIMEOUT", 504),
    ("MODEL_CONFIG", 503), ("PROJECT_NOT_FOUND", 404), ("REVISION_CONFLICT", 409)])
def test_exception_status_mapping(tmp_path, code, status):
    """确保抛出的异常与返回失败结果使用同一套状态分类。"""
    app = create_app(tmp_path / "state.db", log_dir=tmp_path / "logs")

    @app.get("/test/error")
    def fail():
        """抛出用于验证 HTTP 分类的确定性异常。

        Raises:
            ToolError: 当前参数指定的模拟故障。
        """
        raise ToolError(code, "Readable failure reason")

    response = TestClient(app).get("/test/error")
    assert response.status_code == status == error_status(code)
    assert response.json()["summary"] == "Readable failure reason"


def test_console_shows_reason_and_traceback_before_error_code():
    """确保终端直接显示错误原因、源位置和英文级别。"""
    try:
        raise RuntimeError("Example failure")
    except RuntimeError as exc:
        item = english_record({"event": "request_failed", "reason": str(exc), "error_code": "INTERNAL_ERROR",
                               "method": "POST", "path": "/assets/import", "status_code": 500,
                               **exception_details(exc)})
    record = logging.LogRecord("test", logging.ERROR, "", 0, json.dumps(item), (), None)
    output = ConsoleFormatter().format(record)
    assert "ERROR" in output and "POST /assets/import" in output and "status_code=500" in output
    assert output.index("Example failure") < output.index("error_code")
    assert "Traceback" in output and "test_http_status.py" in output
