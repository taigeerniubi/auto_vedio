"""验证接口日志落盘、异常定位、并发关联、脱敏和文件轮转。"""
from concurrent.futures import ThreadPoolExecutor
import json
import logging

from fastapi.testclient import TestClient
import httpx

from shiguang.agent.contracts import Limits
from shiguang.agent.providers import ModelConfig, RemoteSession
from shiguang.api import create_app
from shiguang.observability import file_handler, log_context, log_event
from fakes import ScriptSession, tc


def records(folder):
    """读取磁盘上已写出的 JSON 日志行。

    Args:
        folder: 测试日志目录。

    Returns:
        解码后的日志对象列表。
    """
    return [json.loads(line) for line in (folder / "app.log").read_text(encoding="utf-8").splitlines()]


def test_http_failures_and_200_tool_failure_written_to_file(tmp_path):
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
    assert [r.status_code for r in responses] == [404, 422, 404, 405, 200]
    logs = records(folder)
    expected = ["接口业务异常", "接口参数校验失败", "接口HTTP异常", "接口HTTP异常", "工具调用失败"]
    for response, event in zip(responses, expected):
        rid = response.headers["X-Request-ID"]
        matching = [item for item in logs if item.get("请求编号") == rid]
        assert any(item["事件"] == event for item in matching)
        assert any(item["事件"] == "接口请求结束" and item["HTTP状态码"] == response.status_code for item in matching)
        assert all("接口" in item and "请求方法" in item for item in matching)
        if response.status_code != 200:
            assert response.json()["request_id"] == rid
    business = next(item for item in logs if item["事件"] == "接口业务异常")
    assert business["原因"] == "项目不存在" and business["异常链"][0]["调用栈"]
    invalid = next(item for item in logs if item["事件"] == "接口参数校验失败")
    assert invalid["参数错误"] and all("input" not in item for item in invalid["参数错误"])
    text = (folder / "app.log").read_text(encoding="utf-8")
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
    item = next(r for r in records(folder) if r["事件"] == "接口未处理异常")
    assert item["请求编号"] == response.json()["request_id"]
    assert item["异常类型"] == "RuntimeError" and item["原因"] == "读取工程失败"
    assert item["异常链"][1]["异常类型"] == "ValueError"
    assert "底层连接拒绝" in item["异常链"][1]["原因"]
    assert any(frame["函数"] == "crash" and frame["行号"] > 0 for frame in item["异常链"][0]["调用栈"])
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
    model = next(r for r in logs if r["事件"] == "模型HTTP请求失败")
    agent = next(r for r in logs if r["事件"] == "智能体执行异常")
    for item in (model, agent):
        assert item["请求编号"] == response.headers["X-Request-ID"]
        assert item["运行编号"] == result["run_id"]
        assert item["接口"] == "/api/v1/projects/{pid}/commands"
    assert model["服务商请求编号"] == "provider-request-1"
    assert model["服务商错误码"] == "invalid_api_key"
    assert "密钥已失效" in model["服务商原因"]
    text = (folder / "app.log").read_text(encoding="utf-8")
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
    text = (folder / "app.log").read_text(encoding="utf-8")
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
    tool = next(r for r in logs if r["事件"] == "工具调用失败")
    assert "clips" in tool["原因"] and "list_type" in tool["原因"]
    internal = next(r for r in logs if r["事件"] == "智能体未处理异常")
    assert internal["原因"] == "模拟内部连接池失效" and internal["异常链"][0]["调用栈"]
    assert "private-input-for-tool" not in (folder / "app.log").read_text(encoding="utf-8")


def test_concurrent_requests_keep_separate_contexts(tmp_path):
    folder = tmp_path / "logs"
    client = TestClient(create_app(tmp_path / "state.sqlite3", log_dir=folder))
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: client.get("/api/v1/projects/missing"), range(12)))
    ids = {r.headers["X-Request-ID"] for r in responses}
    assert len(ids) == 12
    logs = records(folder)
    for rid in ids:
        matched = [r for r in logs if r["请求编号"] == rid]
        assert [r["事件"] for r in matched] == ["接口业务异常", "接口请求结束"]


def test_rotation_utf8_and_context_reset(tmp_path):
    folder = tmp_path / "logs"
    handler = file_handler(folder)
    handler.maxBytes, handler.backupCount = 600, 2
    with log_context(日志目录=folder):
        with log_context(请求编号="request-before-reset"):
            log_event("上下文测试", level=logging.WARNING)
        log_event("上下文已恢复")
        assert "请求编号" not in records(folder)[-1]
        for number in range(10):
            log_event("日志轮转测试", 序号=number, 原因="这是中文错误原因" * 8)
    assert (folder / "app.log.1").is_file() and (folder / "app.log.2").is_file()
    assert not (folder / "app.log.3").exists()
    assert records(folder)[-1]["序号"] == 9
    for path in folder.glob("app.log*"):
        assert all(json.loads(line)["事件"] == "日志轮转测试" for line in path.read_text(encoding="utf-8").splitlines())
