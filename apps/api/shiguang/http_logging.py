"""关联 HTTP 请求日志并统一记录接口异常和参数校验失败。"""
import logging
from time import monotonic
from uuid import uuid4

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from .observability import file_handler, log_context, log_event, sanitize, validation_details
from .tools import ToolError


class RequestLogMiddleware:
    def __init__(self, app, directory):
        """初始化日志中间件并提前检查日志目录可写。

        Args:
            app: 下游 ASGI 应用。
            directory: 日志保存目录。
        """
        self.app = app
        self.directory = directory
        file_handler(directory)

    async def __call__(self, scope, receive, send):
        """为 HTTP 请求生成跟踪编号并捕获未处理异常。

        Args:
            scope: ASGI 连接上下文。
            receive: 请求消息接收函数。
            send: 响应消息发送函数。
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid4().hex
        scope.setdefault("state", {})["trace_id"] = request_id
        started, status, response_started = monotonic(), 500, False

        async def tracked_send(message):
            """在响应头中附加请求编号并记录状态。

            Args:
                message: 下游发出的 ASGI 响应消息。
            """
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                response_started, status = True, message["status"]
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"]
                message = {**message, "headers": headers + [(b"x-request-id", request_id.encode())]}
            await send(message)

        with log_context(请求编号=request_id, 请求方法=scope["method"], 日志目录=self.directory, _http_scope=scope):
            try:
                await self.app(scope, receive, tracked_send)
            except Exception as exc:
                route = getattr(scope.get("route"), "path", "未匹配路由")
                log_event("接口未处理异常", level=logging.ERROR, error=exc, 接口=route, HTTP状态码=500)
                if response_started:
                    raise
                response = JSONResponse(status_code=500, content={"ok": False, "error_code": "INTERNAL_ERROR",
                    "summary": "服务器内部异常，请凭 request_id 查询日志", "request_id": request_id})
                await response(scope, receive, tracked_send)
            finally:
                route = getattr(scope.get("route"), "path", "未匹配路由")
                log_event("接口请求结束", 接口=route, HTTP状态码=status, 耗时毫秒=round((monotonic() - started) * 1000, 2))


def install_error_handlers(app):
    """注册业务、参数与框架异常处理器。

    Args:
        app: 需要统一错误日志的 FastAPI 应用。
    """
    @app.exception_handler(ToolError)
    async def tool_error(request, error):
        """记录业务错误并返回原有错误码。

        Args:
            request: 当前 HTTP 请求。
            error: 已知业务异常。

        Returns:
            含请求编号的错误响应。
        """
        status = 404 if error.code.endswith("NOT_FOUND") else 409 if "CONFLICT" in error.code else 400
        log_event("接口业务异常", level=logging.WARNING, error=error, 接口=request.scope["route"].path,
                  错误代码=error.code, HTTP状态码=status)
        return JSONResponse(status_code=status, content={"ok": False, "error_code": error.code,
            "summary": sanitize(str(error)), "request_id": request.state.trace_id})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        """记录参数约束信息而不记录原始请求体。

        Args:
            request: 当前 HTTP 请求。
            error: 请求校验异常。

        Returns:
            不包含原始输入值的 422 响应。
        """
        details = validation_details(error.errors())
        log_event("接口参数校验失败", level=logging.WARNING, 接口=request.scope["route"].path,
                  错误代码="REQUEST_VALIDATION_ERROR", HTTP状态码=422, 参数错误=details)
        return JSONResponse(status_code=422, content={"ok": False, "error_code": "REQUEST_VALIDATION_ERROR",
            "summary": "请求参数不符合接口约束", "detail": details, "request_id": request.state.trace_id})

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        """记录路由不存在、方法错误等 HTTP 框架异常。

        Args:
            request: 当前 HTTP 请求。
            error: HTTP 框架异常。

        Returns:
            保留状态码和必要响应头的错误响应。
        """
        detail = sanitize(error.detail)
        log_event("接口HTTP异常", level=logging.WARNING, 接口=getattr(request.scope.get("route"), "path", "未匹配路由"),
                  HTTP状态码=error.status_code, 原因=detail)
        return JSONResponse(status_code=error.status_code, headers=error.headers,
                            content={"detail": detail, "request_id": request.state.trace_id})
