"""统一生成脱敏的英文结构化日志并追加到终端和固定文件。"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
from threading import RLock
from .log_format import ConsoleFormatter, english_record

LOG_DIR = Path(__file__).resolve().parents[3] / "logs"
_CONTEXT = ContextVar("shiguang_log_context", default={})
_FILES = {}
_LOCK = RLock()
LOGGER = logging.getLogger("shiguang")
LOGGER.setLevel(logging.INFO)
TOKEN_COUNT_FIELDS = {"max_output_tokens", "input_tokens", "output_tokens", "reasoning_tokens", "total_tokens",
                      "cached_input_tokens", "cache_write_tokens", "cache_miss_tokens"}
if not LOGGER.handlers:
    console = logging.StreamHandler()
    if os.getenv("SHIGUANG_LOG_CONSOLE") != "json":
        console.setFormatter(ConsoleFormatter())
    LOGGER.addHandler(console)


def sanitize(value, secrets=()):
    """递归脱敏凭据并限制单个文本字段大小。

    Args:
        value: 日志或对外错误信息。
        secrets: 当前模型会话中已知的额外敏感文本。

    Returns:
        可安全序列化的字段副本。
    """
    known = [s for s in (*secrets, *(os.getenv(f"SHIGUANG_{kind}_API_KEY", "") for kind in ("MODEL", "VISION", "SPEECH", "AUDIO"))) if isinstance(s, str) and s]
    if isinstance(value, dict):
        return {str(k): "[REDACTED]" if re.search(r"authorization|api[_-]?key|password|cookie|secret|token|密钥|密码", str(k), re.I)
                and not (str(k) in TOKEN_COUNT_FIELDS and type(v) is int and v >= 0)
                else sanitize(v, known) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [sanitize(v, known) for v in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    for secret in sorted(set(known), key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)Bearer\s+[^\s\"',;]+", "Bearer [REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"(?i)((?:api[_-]?key|password|token|secret|密码|密钥)[\"']?\s*[:=]\s*[\"']?)[^\s\"'&,;}]+", r"\1[REDACTED]", text)
    text = re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1[REDACTED]@", text)
    return text[:6000] + ("...[TRUNCATED]" if len(text) > 6000 else "")


def exception_details(error, secrets=()):
    """提取异常链及代码位置，省略源码行和局部变量。

    Args:
        error: 捕获的异常。
        secrets: 需要额外移除的密钥或会话内容。

    Returns:
        包含原因、异常类型和调用栈的中文字段。
    """
    chain, seen, private = [], set(), list(secrets)
    current = error
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        private.extend(getattr(current, "_log_secrets", ()))
        frames, tb = [], current.__traceback__
        while tb is not None:
            frames.append({"文件": tb.tb_frame.f_code.co_filename, "行号": tb.tb_lineno, "函数": tb.tb_frame.f_code.co_name})
            tb = tb.tb_next
        chain.append({"异常类型": type(current).__name__, "原因": str(current), "调用栈": frames[-40:]})
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    return sanitize({"异常类型": type(error).__name__, "原因": str(error), "异常链": chain}, private)


def validation_details(errors):
    """保留参数错误的位置与约束，移除原始输入和校验上下文。

    Args:
        errors: Pydantic 或 FastAPI 的错误列表。

    Returns:
        不包含输入值的中文错误条目。
    """
    return sanitize([{"field": list(e["loc"]), "error_type": e["type"], "reason": e["msg"]} for e in errors[:50]])


@contextmanager
def log_context(**fields):
    """绑定当前请求上下文并在结束后恢复，避免并发请求串号。

    Args:
        **fields: 请求编号、接口或日志目录等上下文字段。

    Yields:
        已绑定的日志上下文。
    """
    token = _CONTEXT.set({**_CONTEXT.get(), **fields})
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def file_handler(directory):
    """取得同一进程内共享的 UTF-8 追加文件写入器。

    Args:
        directory: 日志目录。

    Returns:
        固定 backend.log 写入器，重启不覆盖、不新建带时间戳的日志。
    """
    path = Path(directory).resolve() / "backend.log"
    with _LOCK:
        if path not in _FILES:
            path.parent.mkdir(parents=True, exist_ok=True)
            _FILES[path] = logging.FileHandler(path, mode="a", encoding="utf-8")
        return _FILES[path]


def log_event(event, *, level=logging.INFO, error=None, secrets=(), logger="shiguang", **fields):
    """向文件输出英文 JSON，向终端输出易读文本和异常链。

    Args:
        event: 事件名称。
        level: 日志级别。
        error: 可选的异常对象。
        secrets: 需要额外脱敏的内容。
        logger: 用于区分组件的日志名称。
        **fields: 事件详情，不应包含完整请求体或模型上下文。
    """
    context = dict(_CONTEXT.get())
    directory = context.pop("log_dir", context.pop("日志目录", LOG_DIR))
    scope = context.pop("_http_scope", None)
    if scope is not None:
        context["route"] = getattr(scope.get("route"), "path", "unmatched")
    item = {"timestamp": datetime.now(timezone.utc).isoformat(), "level": logging.getLevelName(level),
            **context, "event": event, **fields}
    if error is not None:
        item.update(exception_details(error, secrets))
    message = json.dumps(sanitize(english_record(item), secrets), ensure_ascii=False, allow_nan=False)
    record = logging.LogRecord(logger, level, "", 0, message, (), None)
    if os.getenv("SHIGUANG_LOG_RELAY") != "1":
        file_handler(directory).handle(record)
    logging.getLogger(logger).log(level, message)
