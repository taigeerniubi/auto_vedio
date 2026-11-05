"""使用模拟 HTTP 验证两种模型协议、工具反馈和错误脱敏。"""
import json

import httpx
import pytest

from shiguang.agent.providers import ModelConfig, RemoteSession
from shiguang.tools import ToolError


@pytest.mark.parametrize("provider", ["openai", "deepseek"])
def test_protocol_round_trip_preserves_reasoning_and_tool_results(provider):
    sent = []
    reasoning = {"type": "reasoning", "id": "reason-1", "summary": [], "encrypted_content": "opaque-test-state"}
    def handle(request):
        body = json.loads(request.content)
        sent.append(body)
        assert request.headers["authorization"] == "Bearer fake-key"
        if provider == "openai":
            assert request.url.path == "/v1/responses"
            return httpx.Response(200, json={"status": "completed", "output": [reasoning, {"type": "function_call", "call_id": "call-1", "name": "list_assets", "arguments": "{}"}]})
        assert request.url.path == "/chat/completions"
        return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": None,
                    "reasoning_content": "private-test-reasoning", "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "list_assets", "arguments": "{}"}}]}}]})
    config = ModelConfig(provider, "configured-test-model", "fake-key")
    session = RemoteSession(config, {"用户要求": "测试"}, httpx.MockTransport(handle))
    try:
        first = session.step([], 5)
        assert first[0].name == "list_assets"
        session.step([("call-1", {"ok": True, "data": []})], 5)
        tools = sent[0]["tools"]
        assert len(tools) == 36
        if provider == "openai":
            assert sent[0]["store"] is False and tools[0]["strict"] is False
            assert reasoning in sent[1]["input"]
            assert sent[1]["input"][-1] == {"type": "function_call_output", "call_id": "call-1", "output": '{"ok": true, "data": []}'}
        else:
            assert sent[1]["messages"][-2]["reasoning_content"] == "private-test-reasoning"
            assert sent[1]["messages"][-1]["tool_call_id"] == "call-1"
            assert "function" in tools[0]
        assert "fake-key" not in repr(config)
    finally:
        session.close()


@pytest.mark.parametrize("provider", ["openai", "deepseek"])
@pytest.mark.parametrize("mode,expected", [("unauthorized", "MODEL_HTTP_ERROR"), ("rate", "MODEL_HTTP_ERROR"),
    ("timeout", "MODEL_TIMEOUT"), ("network", "MODEL_NETWORK_ERROR"), ("invalid", "MODEL_PROTOCOL_ERROR"), ("truncated", "MODEL_INCOMPLETE")])
def test_errors_are_sanitized(provider, mode, expected):
    def handle(request):
        if mode == "timeout":
            raise httpx.ReadTimeout("secret-error-body", request=request)
        if mode == "network":
            raise httpx.ConnectError("secret-error-body", request=request)
        if mode in {"unauthorized", "rate"}:
            return httpx.Response(401 if mode == "unauthorized" else 429, text="secret-error-body")
        if mode == "invalid":
            return httpx.Response(200, json={"unexpected": "secret-error-body"})
        return httpx.Response(200, json={"status": "incomplete", "output": []} if provider == "openai" else {"choices": [{"finish_reason": "length", "message": {}}]})
    session = RemoteSession(ModelConfig(provider, "model", "fake-key"), {}, httpx.MockTransport(handle))
    try:
        with pytest.raises(ToolError) as error:
            session.step([], 3)
        assert error.value.code == expected
        assert "secret-error-body" not in str(error.value)
    finally:
        session.close()


@pytest.mark.parametrize("config", [ModelConfig("invalid", "model", "key"), ModelConfig("deepseek", "", "key"),
    ModelConfig("openai", "model", ""), ModelConfig("openai", "model", "key", "http://unsafe.test"),
    ModelConfig("deepseek", "model", "key", "https://user:password@example.test")])
def test_invalid_configuration_rejected(config):
    with pytest.raises(ToolError):
        config.validate()


@pytest.mark.parametrize("provider", ["openai", "deepseek"])
def test_output_budget_and_truncation_diagnostics(provider, caplog):
    """验证可配置预算和安全用量记录，残缺工具调用不会返回给执行器。"""
    def handle(request):
        """返回带残缺工具参数的长度截断响应。

        Args:
            request: 模型请求。

        Returns:
            带用量和结束原因的模拟响应。
        """
        body = json.loads(request.content)
        assert body["max_output_tokens" if provider == "openai" else "max_tokens"] == 8192
        if provider == "openai":
            data = {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                    "usage": {"input_tokens": 2000, "output_tokens": 8192,
                              "output_tokens_details": {"reasoning_tokens": 8000}},
                    "output": [{"type": "function_call", "arguments": "private-partial-tool"}]}
        else:
            data = {"choices": [{"finish_reason": "length", "message": {
                "reasoning_content": "private-thinking", "content": "private-response", "tool_calls": [{"partial": True}]}}],
                "usage": {"prompt_tokens": 2000, "completion_tokens": 8192,
                          "completion_tokens_details": {"reasoning_tokens": 8000}, "private": "private-usage"}}
        return httpx.Response(200, json=data)

    session = RemoteSession(ModelConfig(provider, "test-model", "fake-key", max_output_tokens=8192), {}, httpx.MockTransport(handle))
    try:
        with caplog.at_level("INFO", logger="shiguang"), pytest.raises(ToolError) as error:
            session.step([], 5)
        assert error.value.code == "MODEL_INCOMPLETE"
        assert "reached the configured 8192-token limit" in str(error.value)
        assert error.value.details["output_tokens"] == 8192
        assert error.value.details["input_tokens"] == 2000
        assert error.value.details["reasoning_tokens"] == 8000
        assert len(session.history) == 2
        assert all(private not in caplog.text for private in ("private-thinking", "private-response", "private-usage", "private-partial-tool"))
    finally:
        session.close()


@pytest.mark.parametrize("reason,expected", [("content_filter", "filtered"), ("aborted", "interrupted"),
    ("insufficient_system_resource", "interrupted"), ("length", "did not confirm")])
def test_incomplete_reason_is_not_always_called_truncation(reason, expected):
    """验证过滤、服务中断与长度限制分别解释，不无依据判定上下文过长。"""
    session = RemoteSession(ModelConfig("deepseek", "test-model", "fake-key"), {}, httpx.MockTransport(
        lambda _: httpx.Response(200, json={"choices": [{"finish_reason": reason, "message": {}}]})))
    try:
        with pytest.raises(ToolError) as error:
            session.step([], 5)
        assert expected in str(error.value)
        assert error.value.details["finish_reason"] == reason
    finally:
        session.close()


@pytest.mark.parametrize("value", ["0", "65537", "no-number", "1.5"])
def test_invalid_output_budget_is_rejected(monkeypatch, value):
    """验证输出预算配置边界。"""
    monkeypatch.setenv("SHIGUANG_MODEL_API_KEY", "fake-key")
    monkeypatch.setenv("SHIGUANG_MODEL_NAME", "test-model")
    monkeypatch.setenv("SHIGUANG_MODEL_MAX_OUTPUT_TOKENS", value)
    with pytest.raises(ToolError, match="MAX_OUTPUT_TOKENS"):
        ModelConfig.from_env()
