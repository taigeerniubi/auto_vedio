"""适配 OpenAI Responses 与 DeepSeek Chat Completions 的工具调用协议。"""
from dataclasses import dataclass, field
import json
import logging
import os
import re
from urllib.parse import urlparse

import httpx

from ..tools import ToolError
from ..observability import log_event, sanitize
from .contracts import ToolCall
from .prompts import SYSTEM_PROMPT, context_parts, tool_definitions


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str
    api_key: str = field(repr=False)
    base_url: str = ""
    max_output_tokens: int = 16384

    @classmethod
    def from_env(cls):
        """读取服务、模型名与密钥并检查远程传输配置。

        Returns:
            已校验的模型配置。

        Raises:
            ToolError: 缺少必要配置或服务地址无效。
        """
        try:
            max_output_tokens = int(os.getenv("SHIGUANG_MODEL_MAX_OUTPUT_TOKENS", "16384"))
        except ValueError as exc:
            raise ToolError("MODEL_CONFIG", "SHIGUANG_MODEL_MAX_OUTPUT_TOKENS must be an integer between 1024 and 65536") from exc
        config = cls(os.getenv("SHIGUANG_MODEL_PROVIDER", "deepseek").lower(),
                     os.getenv("SHIGUANG_MODEL_NAME", ""), os.getenv("SHIGUANG_MODEL_API_KEY", ""),
                     os.getenv("SHIGUANG_MODEL_BASE_URL", ""), max_output_tokens)
        config.validate()
        return config

    def validate(self):
        """检查服务类型、模型名称、密钥和安全地址。

        Raises:
            ToolError: 配置不完整或地址不是 HTTPS。
        """
        if self.provider not in {"openai", "deepseek"}:
            raise ToolError("MODEL_CONFIG", "模型服务必须是 openai 或 deepseek")
        if not self.model.strip() or not self.api_key.strip():
            raise ToolError("MODEL_CONFIG", "请配置 SHIGUANG_MODEL_NAME 和 SHIGUANG_MODEL_API_KEY")
        if type(self.max_output_tokens) is not int or not 1024 <= self.max_output_tokens <= 65536:
            raise ToolError("MODEL_CONFIG", "SHIGUANG_MODEL_MAX_OUTPUT_TOKENS must be an integer between 1024 and 65536")
        url = urlparse(self.url)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ToolError("MODEL_CONFIG", "模型地址必须是不含凭据、查询参数或片段的 HTTPS 地址")

    @property
    def url(self):
        """取得不带末尾斜杠的服务地址。

        Returns:
            配置地址或服务官方默认地址。
        """
        return (self.base_url or {"openai": "https://api.openai.com/v1", "deepseek": "https://api.deepseek.com"}.get(self.provider, "")).rstrip("/")

    @property
    def supports_cache_breakpoints(self) -> bool:
        """官方 Responses 的 GPT-5.6 及后续版本支持显式前缀断点。"""
        if self.provider != "openai" or self.url != "https://api.openai.com/v1":
            return False
        version = re.match(r"^gpt-(\d+)(?:\.(\d+))?(?:-|$)", self.model)
        return version is not None and (int(version[1]), int(version[2] or 0)) >= (5, 6)


class RemoteSession:
    def __init__(self, config: ModelConfig, context: dict, transport=None):
        """创建独立会话并保留必要的工具协议历史。

        Args:
            config: 已配置的模型服务。
            context: 脱敏后的工程上下文。
            transport: 测试时可注入的 HTTP 传输层。
        """
        config.validate()
        self.config = config
        prefix, suffix = context_parts(context)
        prompt_text = "\n".join(part for part in [*prefix, suffix] if part) or "{}"
        content = prompt_text
        if prefix and config.supports_cache_breakpoints:
            # 为三段可复用内容保留断点，最新工具反馈仍可使用默认的隐式断点。
            content = [{"type": "input_text", "text": part, "prompt_cache_breakpoint": {"mode": "explicit"}} for part in prefix]
            if suffix:
                content.append({"type": "input_text", "text": suffix})
        self.private_texts = [config.api_key, context.get("用户要求", ""), prompt_text, *prefix, suffix]
        definitions = tool_definitions()
        if config.provider == "openai":
            self.tools = [{"type": "function", **tool, "strict": False} for tool in definitions]
        else:
            self.tools = [{"type": "function", "function": tool} for tool in definitions]
        self.client = httpx.Client(headers={"Authorization": f"Bearer {config.api_key}"}, transport=transport,
                                   follow_redirects=False)
        self.history = [{"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": content}]

    def step(self, results: list[tuple[str, dict]], timeout: float) -> list[ToolCall]:
        """发送一轮请求并解析工具调用，服务错误不会泄露响应正文。

        Args:
            results: 上轮工具调用编号与执行结果。
            timeout: 本轮网络操作超时秒数。

        Returns:
            可供控制器校验的原始工具调用。

        Raises:
            ToolError: 网络失败、鉴权失败或响应格式异常。
        """
        openai = self.config.provider == "openai"
        for call_id, result in results:
            payload = json.dumps(result, ensure_ascii=False, allow_nan=False)
            self.history.append({"type": "function_call_output", "call_id": call_id, "output": payload} if openai
                                else {"role": "tool", "tool_call_id": call_id, "content": payload})
        if openai:
            body = {"model": self.config.model, "input": self.history, "store": False,
                    "include": ["reasoning.encrypted_content"], "max_output_tokens": self.config.max_output_tokens,
                    "tools": self.tools}
            endpoint = "/responses"
        else:
            body = {"model": self.config.model, "messages": self.history, "max_tokens": self.config.max_output_tokens,
                    "tools": self.tools}
            endpoint = "/chat/completions"
        try:
            response = self.client.post(self.config.url + endpoint, json=body, timeout=min(timeout, 60))
            response.raise_for_status()
            data = response.json()
            if openai:
                diagnostics = self.response_diagnostics(data, response, openai=True)
                if data["status"] != "completed":
                    self.raise_incomplete(diagnostics)
                outputs = data["output"]
                self.history.extend(outputs)
                self.private_texts.extend(x.get("encrypted_content", "") for x in outputs if isinstance(x, dict))
                calls = [ToolCall(x["call_id"], x["name"], x["arguments"]) for x in outputs if x["type"] == "function_call"]
            else:
                choice = data["choices"][0]
                diagnostics = self.response_diagnostics(data, response, openai=False)
                if choice["finish_reason"] not in {"stop", "tool_calls"}:
                    self.raise_incomplete(diagnostics)
                message = choice["message"]
                self.private_texts.extend([message.get("reasoning_content") or "", message.get("content") or ""])
                # 保留思考模型继续工具调用所需的字段，但不写入日志或运行记录。
                self.history.append({k: v for k, v in message.items() if k in {"role", "content", "tool_calls", "reasoning_content"}})
                calls = [ToolCall(x["id"], x["function"]["name"], x["function"]["arguments"]) for x in (message.get("tool_calls") or [])]
            if any(not isinstance(c.call_id, str) or not c.call_id or not isinstance(c.name, str) or not isinstance(c.arguments, str) for c in calls):
                raise ValueError("无效工具调用结构")
            if len({c.call_id for c in calls}) != len(calls):
                raise ValueError("工具调用编号重复")
            return calls
        except httpx.TimeoutException as exc:
            self.log_failure("模型请求超时", exc)
            raise ToolError("MODEL_TIMEOUT", "模型请求超时，草稿不会提交") from exc
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            details = {}
            try:
                problem = exc.response.json().get("error", {})
                if isinstance(problem, dict):
                    details = {"服务商错误码": problem.get("code"), "服务商错误类型": problem.get("type"),
                               "服务商原因": problem.get("message"), "服务商参数": problem.get("param")}
            except (ValueError, AttributeError):
                details = {"服务商原因": "Non-JSON error response; raw body omitted."}
            self.log_failure("模型HTTP请求失败", exc, HTTP状态码=code,
                             服务商请求编号=exc.response.headers.get("x-request-id") or exc.response.headers.get("request-id"), **details)
            provider_code = details.get("服务商错误码")
            provider_type = details.get("服务商错误类型")
            if provider_code in {"credit_balance_exhausted", "insufficient_quota"} or provider_type == "insufficient_quota":
                reason = "API credits or quota exhausted. Add API credits or check the project's billing limits."
            elif code == 401:
                reason = "API authentication failed. Check the API key and ensure it belongs to the configured provider."
            elif code == 403:
                reason = "API access denied. Check account permissions and model access."
            elif code == 429:
                reason = "API rate limit exceeded. Wait before retrying or reduce request frequency."
            else:
                reason = "The model provider rejected the request."
            provider_message = details.get("服务商原因")
            if isinstance(provider_message, str) and provider_message:
                reason += " " + provider_message[:1500]
            summary = sanitize(f"{self.config.provider}: {reason} (upstream HTTP {code})", self.private_texts)
            diagnostic = sanitize({"upstream_status": code, "provider_error_code": provider_code,
                                   "provider_request_id": exc.response.headers.get("x-request-id") or exc.response.headers.get("request-id")},
                                  self.private_texts)
            raise ToolError("MODEL_HTTP_ERROR", summary, details=diagnostic) from exc
        except httpx.RequestError as exc:
            self.log_failure("模型网络请求失败", exc)
            raise ToolError("MODEL_NETWORK_ERROR", "无法连接模型服务") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            if isinstance(exc, ToolError):
                self.log_failure("模型返回未完成结果", exc, **exc.details)
                raise
            # JSON 解码异常仅记录位置，原始响应正文不写入日志。
            if isinstance(exc, json.JSONDecodeError):
                log_event("模型JSON解析失败", level=logging.ERROR, 模型服务=self.config.provider, 模型=self.config.model,
                          行号=exc.lineno, 列号=exc.colno, 原因=exc.msg)
            else:
                self.log_failure("模型协议解析失败", exc)
            raise ToolError("MODEL_PROTOCOL_ERROR", "模型返回了无法解析的工具调用协议") from exc

    def response_diagnostics(self, data, response, *, openai):
        """仅提取完成原因和数字用量，不记录模型正文或推理内容。

        Args:
            data: 已解码的服务商响应。
            response: HTTP 响应，用于获取请求编号。
            openai: 是否使用 OpenAI Responses 协议。

        Returns:
            可安全写入日志及失败结果的诊断字段。
        """
        usage = data.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        if openai:
            incomplete = data.get("incomplete_details") or {}
            reason = incomplete.get("reason", data.get("status")) if isinstance(incomplete, dict) else data.get("status")
            output_details = usage.get("output_tokens_details") or {}
        else:
            reason = data["choices"][0].get("finish_reason")
            output_details = usage.get("completion_tokens_details") or {}
        details = {"finish_reason": str(reason)[:100], "max_output_tokens": self.config.max_output_tokens,
                   "provider_request_id": response.headers.get("x-request-id") or response.headers.get("request-id")}
        for target, source in (("input_tokens", "input_tokens" if openai else "prompt_tokens"),
                               ("output_tokens", "output_tokens" if openai else "completion_tokens"),
                               ("total_tokens", "total_tokens")):
            if type(usage.get(source)) is int:
                details[target] = usage[source]
        if isinstance(output_details, dict) and type(output_details.get("reasoning_tokens")) is int:
            details["reasoning_tokens"] = output_details["reasoning_tokens"]
        if openai:
            input_details = usage.get("input_tokens_details")
            cache_counts = input_details if isinstance(input_details, dict) else {}
            cache_fields = (("cached_input_tokens", "cached_tokens"), ("cache_write_tokens", "cache_write_tokens"))
        else:
            cache_counts = usage
            cache_fields = (("cached_input_tokens", "prompt_cache_hit_tokens"), ("cache_miss_tokens", "prompt_cache_miss_tokens"))
        for target, source in cache_fields:
            count = cache_counts.get(source)
            if type(count) is int and count >= 0:
                details[target] = count
        details = sanitize(details, self.private_texts)
        log_event("model_response_received", provider=self.config.provider, model=self.config.model, **details)
        return details

    def raise_incomplete(self, details):
        """按真实结束原因解释未完成结果，不执行残缺工具调用。

        Args:
            details: 已脱敏的响应诊断字段。

        Raises:
            ToolError: 模型未正常完成，当前草稿不会提交。
        """
        reason = details["finish_reason"]
        if reason in {"length", "max_output_tokens"}:
            count = details.get("output_tokens")
            reached = isinstance(count, int) and count >= self.config.max_output_tokens
            message = (f"Model output reached the configured {self.config.max_output_tokens}-token limit. " if reached else
                       f"Model generation hit a length limit (output budget: {self.config.max_output_tokens} tokens); "
                       "the provider did not confirm whether output or context capacity was exhausted. ")
            message += "Increase SHIGUANG_MODEL_MAX_OUTPUT_TOKENS or split the edit into smaller steps."
        elif reason == "content_filter":
            message = "The model provider filtered this response. This is not a context-length error."
        elif reason in {"insufficient_system_resource", "aborted"}:
            message = "The model provider interrupted generation. Retry the instruction later."
        else:
            message = f"Model response did not complete (finish_reason={reason})."
        raise ToolError("MODEL_INCOMPLETE", message + " Draft was not committed.", details=details)

    def log_failure(self, event, error, **fields):
        """记录脱敏后的服务商错误和网络异常位置。

        Args:
            event: 中文事件名称。
            error: 原始异常对象。
            **fields: 白名单提取的服务商错误信息。
        """
        # 外层执行器继续记录异常链时也必须使用相同的脱敏范围。
        error._log_secrets = tuple(self.private_texts)
        log_event(event, level=logging.ERROR, error=error, secrets=self.private_texts,
                  模型服务=self.config.provider, 模型=self.config.model, **fields)

    def close(self):
        """关闭当前模型会话的 HTTP 连接。"""
        self.client.close()


def configured_session(context: dict) -> RemoteSession:
    """按当前环境配置创建模型会话。

    Args:
        context: 脱敏后的工程上下文。

    Returns:
        OpenAI 或 DeepSeek 模型会话。
    """
    return RemoteSession(ModelConfig.from_env(), context)
