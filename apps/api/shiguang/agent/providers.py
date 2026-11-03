"""适配 OpenAI Responses 与 DeepSeek Chat Completions 的工具调用协议。"""
from dataclasses import dataclass, field
import json
import logging
import os
from urllib.parse import urlparse

import httpx

from ..tools import ToolError
from ..observability import log_event
from .contracts import ToolCall
from .prompts import SYSTEM_PROMPT, tool_definitions


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str
    api_key: str = field(repr=False)
    base_url: str = ""

    @classmethod
    def from_env(cls):
        """读取服务、模型名与密钥并检查远程传输配置。

        Returns:
            已校验的模型配置。

        Raises:
            ToolError: 缺少必要配置或服务地址无效。
        """
        config = cls(os.getenv("SHIGUANG_MODEL_PROVIDER", "deepseek").lower(),
                     os.getenv("SHIGUANG_MODEL_NAME", ""), os.getenv("SHIGUANG_MODEL_API_KEY", ""),
                     os.getenv("SHIGUANG_MODEL_BASE_URL", ""))
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
        self.private_texts = [config.api_key, context.get("用户要求", ""), json.dumps(context, ensure_ascii=False)]
        self.client = httpx.Client(headers={"Authorization": f"Bearer {config.api_key}"}, transport=transport,
                                   follow_redirects=False)
        self.history = [{"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]

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
                    "include": ["reasoning.encrypted_content"], "max_output_tokens": 4096,
                    "tools": [{"type": "function", **t, "strict": False} for t in tool_definitions()]}
            endpoint = "/responses"
        else:
            body = {"model": self.config.model, "messages": self.history, "max_tokens": 4096,
                    "tools": [{"type": "function", "function": t} for t in tool_definitions()]}
            endpoint = "/chat/completions"
        try:
            response = self.client.post(self.config.url + endpoint, json=body, timeout=min(timeout, 60))
            response.raise_for_status()
            data = response.json()
            if openai:
                if data["status"] != "completed":
                    raise ToolError("MODEL_INCOMPLETE", "模型回复未完成，草稿不会提交")
                outputs = data["output"]
                self.history.extend(outputs)
                self.private_texts.extend(x.get("encrypted_content", "") for x in outputs if isinstance(x, dict))
                calls = [ToolCall(x["call_id"], x["name"], x["arguments"]) for x in outputs if x["type"] == "function_call"]
            else:
                choice = data["choices"][0]
                if choice["finish_reason"] not in {"stop", "tool_calls"}:
                    raise ToolError("MODEL_INCOMPLETE", "模型回复被截断，草稿不会提交")
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
                details = {"服务商原因": "响应不是标准 JSON 错误，未记录原始正文"}
            self.log_failure("模型HTTP请求失败", exc, HTTP状态码=code,
                             服务商请求编号=exc.response.headers.get("x-request-id") or exc.response.headers.get("request-id"), **details)
            raise ToolError("MODEL_HTTP_ERROR", f"模型服务返回 HTTP {code}，请检查配置或配额") from exc
        except httpx.RequestError as exc:
            self.log_failure("模型网络请求失败", exc)
            raise ToolError("MODEL_NETWORK_ERROR", "无法连接模型服务") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            if isinstance(exc, ToolError):
                self.log_failure("模型返回未完成结果", exc)
                raise
            # JSON 解码异常仅记录位置，原始响应正文不写入日志。
            if isinstance(exc, json.JSONDecodeError):
                log_event("模型JSON解析失败", level=logging.ERROR, 模型服务=self.config.provider, 模型=self.config.model,
                          行号=exc.lineno, 列号=exc.colno, 原因=exc.msg)
            else:
                self.log_failure("模型协议解析失败", exc)
            raise ToolError("MODEL_PROTOCOL_ERROR", "模型返回了无法解析的工具调用协议") from exc

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
