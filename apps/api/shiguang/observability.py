"""统一生成脱敏的中文结构化日志并写入可轮转的日志文件。"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
from threading import RLock

LOG_DIR = Path(__file__).resolve().parents[3] / "logs"
_CONTEXT = ContextVar("shiguang_log_context", default={})
_FILES = {}
_LOCK = RLock()
LOGGER = logging.getLogger("shiguang")
LOGGER.setLevel(logging.INFO)
if not LOGGER.handlers:
    LOGGER.addHandler(logging.StreamHandler())


def sanitize(value, secrets=()):
    """递归脱敏凭据并限制单个文本字段大小。

    Args:
        value: 日志或对外错误信息。
        secrets: 当前模型会话中已知的额外敏感文本。

    Returns:
        可安全序列化的字段副本。
    """
    known = [s for s in (*secrets, os.getenv("SHIGUANG_MODEL_API_KEY", "")) if isinstance(s, str) and s]
    if isinstance(value, dict):
        return {str(k): "[已脱敏]" if re.search(r"authorization|api[_-]?key|password|cookie|secret|token|密钥|密码", str(k), re.I)
                else sanitize(v, known) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [sanitize(v, known) for v in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    for secret in sorted(set(known), key=len, reverse=True):
        text = text.replace(secret, "[已脱敏]")
    text = re.sub(r"(?i)Bearer\s+[^\s\"',;]+", "Bearer [已脱敏]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[已脱敏]", text)
    text = re.sub(r"(?i)((?:api[_-]?key|password|token|secret|密码|密钥)[\"']?\s*[:=]\s*[\"']?)[^\s\"'&,;}]+", r"\1[已脱敏]", text)
    text = re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1[已脱敏]@", text)
    return text[:6000] + ("…[已截断]" if len(text) > 6000 else "")


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
    return sanitize([{"字段": list(e["loc"]), "错误类型": e["type"], "原因": e["msg"]} for e in errors[:50]])


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
    """取得同一进程内共享的 UTF-8 轮转文件写入器。

    Args:
        directory: 日志目录。

    Returns:
        单文件 5 MiB、保留五份历史文件的写入器。
    """
    path = Path(directory).resolve() / "app.log"
    with _LOCK:
        if path not in _FILES:
            path.parent.mkdir(parents=True, exist_ok=True)
            _FILES[path] = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
        return _FILES[path]


def log_event(event, *, level=logging.INFO, error=None, secrets=(), logger="shiguang", **fields):
    """同时输出中文 JSON 到控制台和日志文件。

    Args:
        event: 事件名称。
        level: 日志级别。
        error: 可选的异常对象。
        secrets: 需要额外脱敏的内容。
        logger: 用于区分组件的日志名称。
        **fields: 事件详情，不应包含完整请求体或模型上下文。
    """
    context = dict(_CONTEXT.get())
    directory = context.pop("日志目录", LOG_DIR)
    scope = context.pop("_http_scope", None)
    if scope is not None:
        context["接口"] = getattr(scope.get("route"), "path", "未匹配路由")
    labels = {logging.DEBUG: "调试", logging.INFO: "信息", logging.WARNING: "警告", logging.ERROR: "错误", logging.CRITICAL: "严重"}
    item = {"时间": datetime.now(timezone.utc).isoformat(), "级别": labels.get(level, str(level)),
            **context, "事件": event, **fields}
    if error is not None:
        item.update(exception_details(error, secrets))
    message = json.dumps(sanitize(item, secrets), ensure_ascii=False, allow_nan=False)
    record = logging.LogRecord(logger, level, "", 0, message, (), None)
    file_handler(directory).handle(record)
    logging.getLogger(logger).log(level, message)
