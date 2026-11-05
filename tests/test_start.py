"""验证启动器配置读取、密钥脱敏和端口冲突保护。"""
import importlib.util
import logging
from pathlib import Path
import socket

import pytest

SPEC = importlib.util.spec_from_file_location("project_start", Path(__file__).resolve().parents[1] / "start.py")
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


def test_environment_quotes_and_process_priority(tmp_path, monkeypatch):
    """确保密钥中的特殊字符按原样读取，环境变量优先于文件。"""
    path = tmp_path / ".env"
    path.write_text('\ufeff# 本机配置\nSHIGUANG_MODEL_API_KEY="test#literal=$value" # 注释\n'
                    'SHIGUANG_MODEL_NAME=file-model\nEMPTY=\n', encoding="utf-8")
    monkeypatch.delenv("SHIGUANG_MODEL_API_KEY", raising=False)
    monkeypatch.setenv("SHIGUANG_MODEL_NAME", "env-model")
    env = launcher.load_environment(path)
    assert env["SHIGUANG_MODEL_API_KEY"] == "test#literal=$value"
    assert env["SHIGUANG_MODEL_NAME"] == "env-model"
    assert env["EMPTY"] == ""


def test_invalid_environment_never_echoes_secret(tmp_path):
    """确保错误配置行仅报告位置，不回显内容。"""
    path = tmp_path / ".env"
    path.write_text('SHIGUANG_MODEL_API_KEY="private-test-secret', encoding="utf-8")
    with pytest.raises(ValueError) as failure:
        launcher.load_environment(path)
    assert "第 1 行" in str(failure.value)
    assert "private-test-secret" not in str(failure.value)


def test_child_logs_redact_configured_key(monkeypatch, caplog):
    """确保第三方进程输出中的密钥也会脱敏。"""
    monkeypatch.setenv("SHIGUANG_MODEL_API_KEY", "private-test-secret")
    with caplog.at_level(logging.INFO, logger="shiguang.start"):
        launcher.log("服务输出", 内容="请求失败 private-test-secret")
    assert "private-test-secret" not in caplog.text
    assert "REDACTED" in caplog.text


def test_relay_keeps_flat_json_and_error_severity(monkeypatch, caplog):
    """确保后端结构化错误不会再次嵌套或降级为 INFO。"""
    import json
    monkeypatch.setenv("SHIGUANG_MODEL_API_KEY", "private-test-secret")
    with caplog.at_level(logging.INFO, logger="shiguang.start"):
        launcher.relay_output("backend", json.dumps({"timestamp": "now", "level": "ERROR",
            "event": "model_http_error", "reason": "private-test-secret rejected", "status_code": 429}))
        launcher.relay_output("frontend", "Error: connect ECONNREFUSED 127.0.0.1:8000")
    rows = [json.loads(record.message) for record in caplog.records]
    assert all(record.levelno == logging.ERROR for record in caplog.records)
    assert rows[0]["event"] == "model_http_error" and rows[0]["status_code"] == 429
    assert "message" not in rows[0] and "private-test-secret" not in caplog.text


def test_busy_port_fails_without_stopping_owner(monkeypatch):
    """模拟端口被占用，确保预检直接报错而不干预现有程序。"""
    def denied_bind(self, address):
        """模拟系统拒绝绑定端口。

        Args:
            self: 待绑定的套接字。
            address: 目标地址。
        """
        raise OSError("address already in use")

    monkeypatch.setattr(socket.socket, "bind", denied_bind)
    with pytest.raises(RuntimeError, match="8000"):
        launcher.check_ports()
