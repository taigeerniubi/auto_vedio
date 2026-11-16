"""验证接口日志落盘、异常定位、并发关联、脱敏和跨启动追加。"""
from concurrent.futures import ThreadPoolExecutor
import json
import logging

from fastapi.testclient import TestClient
import httpx

from shiguang.agent.contracts import Limits
from shiguang.agent.providers import ModelConfig, RemoteSession
from shiguang.api import create_app
from shiguang.observability import file_handler, log_context, log_event, sanitize
from fakes import ScriptSession, tc


def records(folder):
    """读取磁盘上已写出的 JSON 日志行。

    Args:
        folder: 测试日志目录。

    Returns:
        解码后的日志对象列表。
    """
    return [json.loads(line) for line in (folder / "backend.log").read_text(encoding="utf-8").splitlines()]


def test_only_numeric_usage_counters_are_exempt_from_token_redaction():
    """确保数字用量可诊断，认证 token 和伪装为计数的文本仍然脱敏。"""
    data = sanitize({"input_tokens": 2000, "output_tokens": 16384, "max_output_tokens": 16384,
                     "reasoning_tokens": "private-token", "total_tokens": {"secret": "private-token"},
                     "access_token": "private-token", "token": 1234})
    assert data["input_tokens"] == 2000 and data["output_tokens"] == 16384
    assert data["max_output_tokens"] == 16384
    assert all(data[key] == "[REDACTED]" for key in ("reasoning_tokens", "total_tokens", "access_token", "token"))


def test_http_failures_and_tool_failure_written_to_file(tmp_path):
    folder = tmp_path / "logs"
    client = TestClient(create_app(tmp_path / "state.sqlite3", log_dir=folder))
    pid = client.post("/api/v1/projects", json={"name": "日志验收"}).json()["project_id"]
    prefix = f"/api/v1/projects/{pid}"
    did = client.post(prefix + "/drafts").json()["id"]
    responses = [
        client.get("/api/v1/projects/missing"),
        client.post("/api/v1/projects", json={"name": "", "password": "private-input-not-to-log"}),
        client.get("/missing?api_key=private-query-not-to-log", headers={"Authorization": "Bearer private-header-not-to-log"}),
        client.delete("/health"),
        client.post(prefix + f"/drafts/{did}/tools", json={"tool": "invented_tool"}),
    ]
    assert [r.status_code for r in responses] == [404, 422, 404, 405, 400]
    logs = records(folder)
    expected = ["request_rejected", "request_validation_failed", "http_error", "http_error", "tool_failed"]
    for response, event in zip(responses, expected):
        rid = response.headers["X-Request-ID"]
        matching = [item for item in logs if item.get("request_id") == rid]
        assert any(item["event"] == event for item in matching)
        assert any(item["event"] == "request_completed" and item["status_code"] == response.status_code for item in matching)
        assert all("route" in item and "method" in item for item in matching)
        if response.status_code != 200:
            assert response.json()["request_id"] == rid
    business = next(item for item in logs if item["event"] == "request_rejected")
    assert business["reason"] == "项目不存在" and business["exception_chain"][0]["stack"]
    invalid = next(item for item in logs if item["event"] == "request_validation_failed")
    assert invalid["validation_errors"] and all("input" not in item for item in invalid["validation_errors"])
    text = (folder / "backend.log").read_text(encoding="utf-8")
    for private in ("private-input-not-to-log", "private-query-not-to-log", "private-header-not-to-log"):
        assert private not in text


def test_unexpected_exception_has_cause_stack_and_safe_500(tmp_path, monkeypatch):
    folder = tmp_path / "logs"
    secret = "configured-secret-for-log-test"
    monkeypatch.setenv("SHIGUANG_MODEL_API_KEY", secret)
    app = create_app(tmp_path / "state.sqlite3", log_dir=folder)

    @app.get("/test/crash")
    def crash():
        """模拟底层异常被业务异常包装的调用链。"""
        try:
            raise ValueError("底层连接拒绝，凭据=" + secret)
        except ValueError as error:
            raise RuntimeError("读取工程失败") from error

    response = TestClient(app).get("/test/crash")
    assert response.status_code == 500
    assert secret not in response.text and "读取工程失败" not in response.text
    item = next(r for r in records(folder) if r["event"] == "request_failed")
    assert item["request_id"] == response.json()["request_id"]
    assert item["exception_type"] == "RuntimeError" and item["reason"] == "读取工程失败"
    assert item["exception_chain"][1]["exception_type"] == "ValueError"
    assert "底层连接拒绝" in item["exception_chain"][1]["reason"]
    assert any(frame["function"] == "crash" and frame["line"] > 0 for frame in item["exception_chain"][0]["stack"])
    assert secret not in json.dumps(item, ensure_ascii=False)


def test_agent_error_and_provider_details_share_request_and_run(tmp_path):
    folder = tmp_path / "logs"
    secret, instruction = "direct-config-key-not-env", "不得写入日志的用户完整指令"

    def handle(request):
        """模拟包含敏感文本的服务商错误响应。

        Args:
            request: 模型发出的 HTTP 请求。

        Returns:
            带标准错误对象的鉴权失败响应。
        """
        return httpx.Response(401, headers={"x-request-id": "provider-request-1"}, json={"error": {
            "code": "invalid_api_key", "type": "authentication_error", "param": "model",
            "message": f"密钥已失效 {secret}；原指令：{instruction}"}, "debug": "private-debug-not-to-log"})

    def factory(ctx):
        """构建模拟网络但真实协议解析的模型会话。

        Args:
            ctx: 工程与指令上下文。

        Returns:
            使用模拟 HTTP 的模型会话。
        """
        return RemoteSession(ModelConfig("deepseek", "test-model", secret), ctx, httpx.MockTransport(handle))

    client = TestClient(create_app(tmp_path / "state.sqlite3", session_factory=factory, log_dir=folder))
    pid = client.post("/api/v1/projects", json={"name": "模型日志测试"}).json()["project_id"]
    response = client.post(f"/api/v1/projects/{pid}/commands", json={"request_id": "model-log-1", "instruction": instruction, "expected_revision": 0})
    result = response.json()
    assert result["status"] == "failed" and result["result"]["error_code"] == "MODEL_HTTP_ERROR"
    logs = records(folder)
    model = next(r for r in logs if r["event"] == "model_http_error")
    agent = next(r for r in logs if r["event"] == "agent_failed")
    for item in (model, agent):
        assert item["request_id"] == response.headers["X-Request-ID"]
        assert item["run_id"] == result["run_id"]
        assert item["route"] == "/api/v1/projects/{pid}/commands"
    assert model["provider_request_id"] == "provider-request-1"
    assert model["provider_error_code"] == "invalid_api_key"
    assert "密钥已失效" in model["provider_reason"]
    text = (folder / "backend.log").read_text(encoding="utf-8")
    assert all(private not in text for private in (secret, instruction, "private-debug-not-to-log"))
    assert "authentication_error" not in response.text


def test_provider_cause_redacted_again_in_agent_logger(tmp_path):
    folder = tmp_path / "logs"
    secret = "direct-secret-in-network-exception"

    def broken(request):
        """模拟包含直接配置密钥的底层连接异常。

        Args:
            request: 模型请求。

        Raises:
            httpx.ConnectError: 预设的网络连接失败。
        """
        raise httpx.ConnectError("连接拒绝 " + secret, request=request)

    client = TestClient(create_app(tmp_path / "state.sqlite3", log_dir=folder,
        session_factory=lambda ctx: RemoteSession(ModelConfig("openai", "test-model", secret), ctx, httpx.MockTransport(broken))))
    pid = client.post("/api/v1/projects", json={"name": "异常链脱敏"}).json()["project_id"]
    result = client.post(f"/api/v1/projects/{pid}/commands", json={"request_id": "network-1", "instruction": "测试", "expected_revision": 0}).json()
    assert result["result"]["error_code"] == "MODEL_NETWORK_ERROR"
    text = (folder / "backend.log").read_text(encoding="utf-8")
    assert "连接拒绝" in text and secret not in text


def test_agent_internal_error_and_invalid_tool_parameters_are_diagnostic(tmp_path):
    folder = tmp_path / "logs"

    def broken(_):
        """模拟模型循环内部的非预期故障。

        Args:
            _: 上轮工具结果。

        Raises:
            RuntimeError: 用于验证调用栈的内部故障。
        """
        raise RuntimeError("模拟内部连接池失效")

    sessions = iter([ScriptSession([[tc("assemble_sequence", {"clips": "private-input-for-tool"})]]), ScriptSession([broken])])
    client = TestClient(create_app(tmp_path / "state.sqlite3", log_dir=folder, agent_limits=Limits(errors=1), session_factory=lambda _: next(sessions)))
    pid = client.post("/api/v1/projects", json={"name": "工具日志测试"}).json()["project_id"]
    for request_id in ("invalid-tool", "internal-error"):
        response = client.post(f"/api/v1/projects/{pid}/commands", json={"request_id": request_id, "instruction": "测试", "expected_revision": 0})
        assert response.json()["status"] == "failed"
    logs = records(folder)
    tool = next(r for r in logs if r["event"] == "tool_failed")
    assert "clips" in tool["reason"] and "list_type" in tool["reason"]
    internal = next(r for r in logs if r["event"] == "agent_internal_error")
    assert internal["reason"] == "模拟内部连接池失效" and internal["exception_chain"][0]["stack"]
    assert "private-input-for-tool" not in (folder / "backend.log").read_text(encoding="utf-8")


def test_concurrent_requests_keep_separate_contexts(tmp_path):
    folder = tmp_path / "logs"
    client = TestClient(create_app(tmp_path / "state.sqlite3", log_dir=folder))
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: client.get("/api/v1/projects/missing"), range(12)))
    ids = {r.headers["X-Request-ID"] for r in responses}
    assert len(ids) == 12
    logs = records(folder)
    for rid in ids:
        matched = [r for r in logs if r["request_id"] == rid]
        assert [r["event"] for r in matched] == ["request_rejected", "request_completed"]


def test_append_utf8_and_context_reset(tmp_path):
    folder = tmp_path / "logs"
    handler = file_handler(folder)
    with log_context(日志目录=folder):
        with log_context(请求编号="request-before-reset"):
            log_event("context_test", level=logging.WARNING)
        log_event("context_reset")
        assert "request_id" not in records(folder)[-1]
        for number in range(10):
            log_event("append_test", 序号=number, 原因="这是中文错误原因" * 8)
        # 关闭再打开文件处理器，模拟后端重启后继续追加。
        handler.close()
        log_event("after_restart")
    assert len(records(folder)) == 13
    assert records(folder)[-1]["event"] == "after_restart"
    assert records(folder)[-2]["index"] == 9
    assert records(folder)[0]["request_id"] == "request-before-reset"
    assert sorted(path.name for path in folder.iterdir()) == ["backend.log"]


def test_managed_backend_only_emits_once_to_console(tmp_path, monkeypatch, caplog):
    """启动器接管时不由后端重复写盘，终端保留完整结构化事件。"""
    monkeypatch.setenv("SHIGUANG_LOG_RELAY", "1")
    with log_context(log_dir=tmp_path / "logs"):
        log_event("managed_event")
    assert not (tmp_path / "logs").exists()
    rows = [json.loads(record.message) for record in caplog.records]
    assert [row["event"] for row in rows] == ["managed_event"]
