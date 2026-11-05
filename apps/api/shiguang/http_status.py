"""集中定义业务错误、内部故障和外部调用失败的 HTTP 状态码。"""


def error_status(code: str) -> int:
    """将稳定错误码转换为对应的 HTTP 错误类别。

    Args:
        code: 工具、媒体处理或模型服务的错误码。

    Returns:
        业务错误为 4xx，执行故障及外部调用失败为 5xx。
    """
    if code in {"MODEL_TIMEOUT", "MEDIA_TIMEOUT", "TIME_LIMIT"}:
        return 504
    if code == "MODEL_CONFIG":
        return 503
    if code.startswith("MODEL_") or code in {"MIXED_TERMINAL", "CALL_LIMIT", "ROUND_LIMIT", "ERROR_LIMIT"}:
        return 502
    if code in {"INTERNAL_ERROR", "AGENT_INTERNAL_ERROR", "ANALYSIS_INTERNAL_ERROR", "MEDIA_PROCESS_FAILED", "ENCODE_FAILED",
                "DECODE_FRAME_MISMATCH", "EXPORT_VERIFICATION_FAILED", "AUDIO_FORMAT"}:
        return 500
    if code.endswith("NOT_FOUND"):
        return 404
    if "CONFLICT" in code or code in {"DRAFT_CLOSED", "RUN_STOPPED"}:
        return 409
    if code == "MEDIA_TOO_LARGE":
        return 413
    if code == "REQUEST_VALIDATION_ERROR":
        return 422
    return 400
