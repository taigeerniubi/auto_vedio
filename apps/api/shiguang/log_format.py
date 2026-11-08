"""将内部进度事件转换为英文日志，并生成便于终端阅读的诊断文本。"""
import json
import logging

# 界面进度事件保持中文；日志边界统一英文，兼容已经持久化的事件字段。
FIELD_NAMES = {
    "时间": "timestamp", "级别": "level", "事件": "event", "请求编号": "request_id",
    "请求方法": "method", "接口": "route", "HTTP状态码": "status_code", "耗时毫秒": "duration_ms",
    "运行编号": "run_id", "项目编号": "project_id", "指令编号": "command_id", "草稿编号": "draft_id",
    "工具": "tool", "操作编号": "operation_id", "错误代码": "error_code", "原因": "reason",
    "异常类型": "exception_type", "异常链": "exception_chain", "调用栈": "stack",
    "文件": "file", "行号": "line", "列号": "column", "函数": "function", "字段": "field",
    "错误类型": "error_type", "参数错误": "validation_errors", "轮次": "round", "调用次数": "tool_calls",
    "模型服务": "provider", "模型": "model", "服务商请求编号": "provider_request_id",
    "服务商错误码": "provider_error_code", "服务商错误类型": "provider_error_type",
    "服务商原因": "provider_reason", "服务商参数": "provider_param", "状态": "status",
    "序号": "index", "服务": "service", "内容": "message", "进程编号": "pid", "退出码": "exit_code",
    "页面": "url", "接口文档": "api_docs", "操作": "instruction", "启动日志": "startup_log",
    "业务日志": "app_log", "检查": "checks",
}
EVENT_NAMES = {
    "恢复任务": "agent_resumed", "重试模型": "model_retrying",
    "开始工具": "tool_started",
    "接口未处理异常": "request_failed", "接口请求结束": "request_completed",
    "接口业务异常": "request_rejected", "接口参数校验失败": "request_validation_failed",
    "接口HTTP异常": "http_error", "工具调用失败": "tool_failed",
    "智能体执行异常": "agent_failed", "智能体未处理异常": "agent_internal_error",
    "模型连接清理异常": "model_cleanup_failed", "模型请求超时": "model_timeout",
    "模型HTTP请求失败": "model_http_error", "模型网络请求失败": "model_network_error",
    "模型返回未完成结果": "model_incomplete", "模型JSON解析失败": "model_json_error",
    "模型协议解析失败": "model_protocol_error", "开始执行": "agent_started", "请求模型": "model_requested",
    "已完成提交": "agent_committed", "工具执行": "tool_executed", "连接清理失败": "cleanup_failed",
    "执行失败": "run_failed", "已取消": "run_cancelled", "等待澄清": "input_required",
    "能力不支持": "unsupported_operation", "运行结束": "run_finished",
    "服务输出": "service_output", "服务已创建": "service_started", "服务已停止": "service_stopped",
    "模型尚未配置": "model_not_configured", "工作台已就绪": "workspace_ready",
    "启动验收通过": "startup_check_passed", "正在关闭服务": "shutdown_requested",
    "启动或运行失败": "launcher_failed",
}


def english_record(value):
    """规范化已知日志字段，不改写用户输入和底层异常原文。

    Args:
        value: 待转换的日志记录或嵌套结构。

    Returns:
        使用英文标准字段、事件和级别的结构。
    """
    if isinstance(value, dict):
        item = {FIELD_NAMES.get(str(k), str(k)): english_record(v) for k, v in value.items()}
        if "event" in item:
            item["event"] = EVENT_NAMES.get(item["event"], item["event"])
        for key, labels in {
            "level": {"信息": "INFO", "警告": "WARNING", "错误": "ERROR", "调试": "DEBUG", "严重": "CRITICAL"},
            "status": {"成功": "success", "失败": "failed"},
            "service": {"前端": "frontend", "后端": "backend"},
        }.items():
            if isinstance(item.get(key), str):
                item[key] = labels.get(item[key], item[key])
        return item
    if isinstance(value, (list, tuple)):
        return [english_record(v) for v in value]
    return value


class ConsoleFormatter(logging.Formatter):
    """把结构化日志显示为标准级别、请求概要和异常调用栈。"""

    def format(self, record):
        """生成不含嵌套 JSON 字符串的终端文本。

        Args:
            record: 含脱敏 JSON 消息的日志记录。

        Returns:
            易读的英文概要及可选调用栈。
        """
        try:
            item = json.loads(record.getMessage())
        except (TypeError, ValueError):
            return f"{record.levelname}: {record.getMessage()}"
        if not isinstance(item, dict):
            return f"{record.levelname}: {record.getMessage()}"
        header = f"{item.get('timestamp', '')} {record.levelname:<7} {item.get('event', 'log')}"
        method, path = item.get("method"), item.get("path", item.get("route"))
        if method and path:
            header += f" {method} {path}"
        for key in ("status_code", "duration_ms", "service", "pid", "exit_code", "tool", "provider", "model",
                    "finish_reason", "max_output_tokens", "input_tokens", "output_tokens", "reasoning_tokens"):
            if key in item:
                header += f" {key}={item[key]}"
        # 原因优先显示，不要求使用者先理解 error_code。
        reason = item.get("provider_reason") or item.get("reason") or item.get("message")
        if reason:
            header += f" | {reason}"
        for key in ("error_code", "request_id", "run_id", "url", "api_docs", "instruction", "checks"):
            if item.get(key):
                header += f" {key}={item[key]}"
        lines = [header]
        for cause in reversed(item.get("exception_chain", [])):
            lines.append("Traceback (most recent call last):")
            for frame in cause.get("stack", []):
                lines.append(f'  File "{frame["file"]}", line {frame["line"]}, in {frame["function"]}')
            lines.append(f'{cause["exception_type"]}: {cause["reason"]}')
        return "\n".join(lines)
