"""使用模拟 HTTP 验证两种模型协议、工具反馈和错误脱敏。"""
import json

import httpx
import pytest

from shiguang.agent.contracts import Command, Limits
from shiguang.agent.prompts import context as build_context
from shiguang.agent.providers import ModelConfig, RemoteSession
from shiguang.perception_provider import PerceptionConfig, PerceptionProvider
from shiguang.tools import ToolError


@pytest.mark.parametrize("provider", ["openai", "deepseek"])
@pytest.mark.parametrize("visible", [True, False])
def test_finish_preserves_public_answer_and_never_exposes_reasoning(provider, visible):
    """公开答复与 finish 同时返回时展示实际答案，内部思考不能代替缺失答复。"""
    answer = "可以裁切配乐、调整音量和淡入淡出。\n\n这段前奏约 15 秒较轻，可以考虑从主节奏开始。"
    summary = "确认了配乐结构，并说明了修改方法。"
    args = json.dumps({"summary": summary}, ensure_ascii=False)
    if provider == "openai":
        data = {"status": "completed", "output": [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "private-reasoning-marker"}],
             "encrypted_content": "private-encrypted-marker"},
            {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": answer if visible else ""}]},
            {"type": "function_call", "call_id": "finish-public", "name": "finish", "arguments": args},
        ]}
    else:
        data = {"choices": [{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": answer if visible else "", "reasoning_content": "private-reasoning-marker",
            "tool_calls": [{"id": "finish-public", "type": "function", "function": {"name": "finish", "arguments": args}}],
        }}]}
    session = RemoteSession(ModelConfig(provider, "answer-test", "fake-key"), {},
                            httpx.MockTransport(lambda _: httpx.Response(200, json=data)))
    try:
        calls = session.step([], 5)
        assert len(calls) == 1 and calls[0].name == "finish" and calls[0].call_id == "finish-public"
        assert json.loads(calls[0].arguments)["summary"] == (answer if visible else summary)
        assert "private-" not in calls[0].arguments
    finally:
        session.close()


def test_public_reply_does_not_rewrite_invalid_finish_or_nonterminal_calls():
    """答复保留不能绕过混合结束调用、无效参数或其他工具的控制器检查。"""
    from shiguang.agent.contracts import ToolCall
    finish = ToolCall("finish-1", "finish", '{"summary":"已完成"}')
    tool = ToolCall("tool-1", "list_assets", "{}")
    for calls in [[tool], [finish, tool], [ToolCall("bad-finish", "finish", "{}")]]:
        assert RemoteSession._preserve_visible_reply(calls, "完整公开答复") == calls


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
        assert len(tools) == 40
        history_key = "input" if provider == "openai" else "messages"
        first_history = sent[0][history_key]
        assert sent[1][history_key][:len(first_history)] == first_history
        assert sent[1]["tools"] == tools
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


@pytest.mark.parametrize("provider,model,base_url,breakpoints", [
    ("openai", "gpt-5.6", "", True),
    ("openai", "gpt-5.4", "", False),
    ("openai", "gpt-6.1-sol", "https://compatible.test/v1", False),
    ("deepseek", "test-model", "", False),
])
def test_edit_commands_share_wire_prefix(context, provider, model, base_url, breakpoints):
    """不同指令复用相同前缀，工程更新后发送新状态，兼容端点只收到标准字段。"""
    store, _, draft_id = context
    draft = store.draft(draft_id)
    command = Command(instruction="加字幕", request_id="edit-1", expected_revision=0)
    sent = []

    def handle(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "completed", "output": []} if provider == "openai"
                              else {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": ""}}]})

    contexts = [
        build_context(store, draft, command),
        build_context(store, draft, command.model_copy(update={"instruction": "删除字幕", "playhead_frame": 30})),
        build_context(store, {**draft, "base": 1, "snapshot": {**draft["snapshot"], "clips": [{"clip_id": "c1"}]}}, command),
    ]
    contexts[1]["近期对话"] = [{"指令": "加字幕", "结果": "加了一条字幕"}]
    contexts[1]["工程"] = dict(reversed(contexts[1]["工程"].items()))
    for ctx in contexts:
        session = RemoteSession(ModelConfig(provider, model, "fake-key", base_url), ctx, httpx.MockTransport(handle))
        try:
            session.step([], 5)
        finally:
            session.close()

    assert sent[0]["tools"] == sent[1]["tools"] == sent[2]["tools"]
    messages = [body["input" if provider == "openai" else "messages"] for body in sent]
    assert messages[0][0] == messages[1][0] == messages[2][0]
    sections = []
    for history in messages:
        assert [message["role"] for message in history] == ["system", "user"]
        content = history[1]["content"]
        if breakpoints:
            assert [part.get("prompt_cache_breakpoint") for part in content] == [{"mode": "explicit"}] * 3 + [None]
            content = "\n".join(part["text"] for part in content)
        assert isinstance(content, str)
        sections.append(content.splitlines())
    first, followup, edited = sections
    assert first[:6] == followup[:6]
    assert first[:4] == edited[:4]
    assert [next(iter(json.loads(line))) for line in first[:6]] == ["预设", "分析能力", "素材", "素材分析", "工程", "基础版本"]
    assert [asset["asset_id"] for asset in json.loads(first[2])["素材"]] == ["a", "b", "music", "pic"]
    assert json.loads(edited[4])["工程"]["clips"] == [{"clip_id": "c1"}]
    assert json.loads(followup[6])["近期对话"] == contexts[1]["近期对话"]
    assert json.loads(followup[-2])["播放帧"] == 30
    assert json.loads(followup[-1]) == {"用户要求": "删除字幕"}


@pytest.mark.parametrize("provider,usage,expected", [
    ("openai", {"input_tokens_details": {"cached_tokens": 0}}, {"cached_input_tokens": 0}),
    ("openai", {"input_tokens_details": "invalid"}, {}),
    ("deepseek", {"prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 50},
     {"cached_input_tokens": 0, "cache_miss_tokens": 50}),
    ("deepseek", {"prompt_cache_hit_tokens": True, "prompt_cache_miss_tokens": -1}, {}),
    ("deepseek", {}, {}),
])
def test_cache_usage_distinguishes_zero_and_missing(provider, usage, expected, caplog):
    """成功响应中的零命中可见，缺少或无效的用量不记录。"""
    data = {"status": "completed", "output": []} if provider == "openai" else {
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": ""}}]}
    data["usage"] = usage
    session = RemoteSession(ModelConfig(provider, "test-model", "fake-key"), {},
                            httpx.MockTransport(lambda _: httpx.Response(200, json=data)))
    try:
        with caplog.at_level("INFO", logger="shiguang"):
            session.step([], 5)
        record = next(info for r in caplog.records if (info := json.loads(r.message))["event"] == "model_response_received")
        assert {k: v for k, v in record.items() if k.startswith("cache")} == expected
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
                              "input_tokens_details": {"cached_tokens": 1536, "cache_write_tokens": 0},
                              "output_tokens_details": {"reasoning_tokens": 8000}},
                    "output": [{"type": "function_call", "arguments": "private-partial-tool"}]}
        else:
            data = {"choices": [{"finish_reason": "length", "message": {
                "reasoning_content": "private-thinking", "content": "private-response", "tool_calls": [{"partial": True}]}}],
                "usage": {"prompt_tokens": 2000, "completion_tokens": 8192, "prompt_cache_hit_tokens": 1536,
                          "prompt_cache_miss_tokens": 464,
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
        record = next(json.loads(r.message) for r in caplog.records if json.loads(r.message)["event"] == "model_response_received")
        assert record["cached_input_tokens"] == 1536
        if provider == "openai":
            assert record["cache_write_tokens"] == 0
        else:
            assert record["cache_miss_tokens"] == 464
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


@pytest.mark.parametrize("failure", ["timeout", "network", "rate", "unavailable"])
def test_retry_reuses_request_body_and_keeps_tool_feedback_once(failure):
    """临时错误仅重发本轮请求，历史和工具反馈不会重复追加。"""
    sent, timeouts = [], []
    def handle(request):
        sent.append(json.loads(request.content))
        timeouts.append(request.extensions["timeout"]["read"])
        if len(sent) == 1:
            if failure == "timeout":
                raise httpx.ReadTimeout("temporary", request=request)
            if failure == "network":
                raise httpx.ConnectError("temporary", request=request)
            return httpx.Response(429 if failure == "rate" else 503, json={"error": {"code": "temporary"}})
        return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "tool_calls": [{"id": "next", "function": {"name": "finish", "arguments": "{}"}}]}}]})
    session = RemoteSession(ModelConfig("deepseek", "test", "secret-key"), {}, httpx.MockTransport(handle), wait=lambda _: None)
    try:
        assert session.step([("previous", {"ok": True})], None)[0].call_id == "next"
        assert sent[0] == sent[1]
        assert timeouts == [120, 120]
        assert len([m for m in session.history if m.get("tool_call_id") == "previous"]) == 1
    finally:
        session.close()


@pytest.mark.parametrize("code,error", [(401, {}), (403, {}), (400, {}),
    (429, {"code": "insufficient_quota"}), (429, {"code": "credit_balance_exhausted"})])
def test_permanent_errors_are_not_retried(code, error):
    """鉴权、参数和余额错误不可通过重复付费请求恢复。"""
    sent = []
    def handle(request):
        sent.append(request)
        return httpx.Response(code, json={"error": error})
    session = RemoteSession(ModelConfig("deepseek", "test", "key"), {}, httpx.MockTransport(handle))
    try:
        with pytest.raises(ToolError) as failure:
            session.step([], None)
        assert failure.value.code == "MODEL_HTTP_ERROR"
        assert len(sent) == 1
    finally:
        session.close()


def test_budget_expiry_is_time_limit_and_prevents_retry():
    """总预算耗尽时给出准确错误码，不再进行无意义重试。"""
    now = [10.0]
    sent = []
    def handle(request):
        sent.append(request)
        assert request.extensions["timeout"]["read"] == 2
        now[0] += 2
        raise httpx.ReadTimeout("timed out", request=request)
    session = RemoteSession(ModelConfig("deepseek", "test", "key"), {}, httpx.MockTransport(handle), clock=lambda: now[0])
    try:
        with pytest.raises(ToolError) as failure:
            session.step([], 2)
        assert failure.value.code == "TIME_LIMIT"
        assert len(sent) == 1
    finally:
        session.close()


def test_cancellation_during_retry_backoff_stops_new_request():
    """退避期间可以取消，下一次网络请求不会发出。"""
    sent, cancelled = [], [False]
    def handle(request):
        sent.append(request)
        return httpx.Response(503)
    def check():
        if cancelled[0]:
            raise ToolError("RUN_STOPPED", "cancelled")
    session = RemoteSession(ModelConfig("deepseek", "test", "key"), {}, httpx.MockTransport(handle),
                            wait=lambda _: cancelled.__setitem__(0, True))
    session.check_active = check
    try:
        with pytest.raises(ToolError) as failure:
            session.step([], None)
        assert failure.value.code == "RUN_STOPPED" and len(sent) == 1
    finally:
        session.close()


@pytest.mark.parametrize("provider", ["openai", "deepseek"])
def test_saved_protocol_context_restores_without_credentials(provider):
    """恢复工具调用和供应商状态，检查点不包含模型配置密钥。"""
    first = RemoteSession(ModelConfig(provider, "test", "private-key"), {"用户要求": "继续原来的任务"})
    restored = RemoteSession(ModelConfig(provider, "test", "new-private-key"), {})
    try:
        first.history.append({"role": "assistant", "content": "checkpoint-response"})
        checkpoint = first.export_state()
        assert "private-key" not in json.dumps(checkpoint)
        restored.restore_state(checkpoint)
        assert restored.history == first.history
        checkpoint["history"].clear()
        assert restored.history
        with pytest.raises(ToolError, match="模型配置已变更"):
            restored.restore_state({**first.export_state(), "model": "other"})
    finally:
        first.close()
        restored.close()


def test_runtime_budget_can_be_disabled_or_configured(monkeypatch):
    """默认不再用三分钟切断任务，用户可以显式启用总预算。"""
    monkeypatch.delenv("SHIGUANG_AGENT_TIMEOUT_SECONDS", raising=False)
    assert Limits.from_env().seconds is None
    monkeypatch.setenv("SHIGUANG_AGENT_TIMEOUT_SECONDS", "600")
    assert Limits.from_env().seconds == 600
    for bad in ["-1", "nan", "inf", "no"]:
        monkeypatch.setenv("SHIGUANG_AGENT_TIMEOUT_SECONDS", bad)
        with pytest.raises(ValueError):
            Limits.from_env()


def test_request_policy_env_validation(monkeypatch):
    """网络等待和重试次数可配置，但必须有限且有效。"""
    monkeypatch.setenv("SHIGUANG_MODEL_API_KEY", "key")
    monkeypatch.setenv("SHIGUANG_MODEL_NAME", "test")
    monkeypatch.setenv("SHIGUANG_MODEL_TIMEOUT_SECONDS", "180")
    monkeypatch.setenv("SHIGUANG_MODEL_RETRIES", "2")
    config = ModelConfig.from_env()
    assert config.request_timeout_seconds == 180 and config.retries == 2
    for name, bad in [("SHIGUANG_MODEL_TIMEOUT_SECONDS", "nan"), ("SHIGUANG_MODEL_TIMEOUT_SECONDS", "0"),
                      ("SHIGUANG_MODEL_RETRIES", "4"), ("SHIGUANG_MODEL_RETRIES", "-1")]:
        with monkeypatch.context() as settings:
            settings.setenv(name, bad)
            with pytest.raises(ToolError):
                ModelConfig.from_env()


def test_perception_timeout_is_configurable_and_validated(monkeypatch):
    """长音频等待采用独立可配置值，拒绝无效或无限网络等待。"""
    monkeypatch.setenv("SHIGUANG_AUDIO_API_KEY", "key")
    monkeypatch.setenv("SHIGUANG_PERCEPTION_TIMEOUT_SECONDS", "240")
    config = PerceptionConfig.from_env("audio")
    seen = []
    def handle(request):
        seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json={"ok": True})
    provider = PerceptionProvider(config, httpx.MockTransport(handle))
    assert provider.request("/chat/completions", json={}) == {"ok": True}
    assert seen == [240]
    for bad in ["0", "-1", "inf", "nan", "invalid"]:
        monkeypatch.setenv("SHIGUANG_PERCEPTION_TIMEOUT_SECONDS", bad)
        with pytest.raises(ToolError, match="SHIGUANG_PERCEPTION_TIMEOUT_SECONDS"):
            PerceptionConfig.from_env("audio")
