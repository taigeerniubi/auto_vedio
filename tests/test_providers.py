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
        assert len(tools) == 28
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
