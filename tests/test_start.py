"""验证启动器配置读取、密钥脱敏和端口冲突保护。"""
import importlib.util
import json
import logging
from pathlib import Path
import socket
from concurrent.futures import ThreadPoolExecutor
import time

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


@pytest.fixture
def isolated_launcher(tmp_path, monkeypatch):
    """隔离启动器文件与处理器，避免测试之间复用关闭的终端流。"""
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    monkeypatch.setattr(launcher.LOGGER, "handlers", [])
    monkeypatch.setattr(launcher.LOGGER, "propagate", True)
    yield tmp_path
    for handler in launcher.LOGGER.handlers:
        handler.close()


def test_launcher_logs_append_and_split_services(isolated_launcher):
    folder = isolated_launcher / "logs"
    for run in range(2):
        launcher.configure_logging()
        launcher.log("launcher_run", run=run)
        launcher.relay_output("backend", '{"event":"backend_event","level":"ERROR"}')
        launcher.relay_output("frontend", "前端消息")
    backend = [json.loads(line) for line in (folder / "backend.log").read_text(encoding="utf-8").splitlines()]
    frontend = [json.loads(line) for line in (folder / "frontend.log").read_text(encoding="utf-8").splitlines()]
    assert [row["event"] for row in backend] == ["launcher_run", "backend_event"] * 2
    assert [row["message"] for row in frontend] == ["前端消息"] * 2
    assert sorted(path.name for path in folder.iterdir()) == ["backend.log", "frontend.log"]


def test_settings_cli_overrides_environment_and_defaults():
    parser = launcher.argument_parser()
    env = {"SHIGUANG_HOST": "localhost", "SHIGUANG_API_PORT": "9000", "SHIGUANG_WEB_PORT": "5174"}
    assert launcher.service_settings(parser.parse_args([]), {}) == ("127.0.0.1", 8000, 5173)
    assert launcher.service_settings(parser.parse_args([]), env) == ("localhost", 9000, 5174)
    settings = launcher.service_settings(parser.parse_args(["--host", "0.0.0.0", "--api-port", "9100", "--web-port", "5180"]), env)
    assert settings == ("0.0.0.0", 9100, 5180)
    assert settings.api_url == "http://127.0.0.1:9100"
    assert settings.web_url == "http://127.0.0.1:5180"
    assert launcher.ServiceSettings("::", 9100, 5180).api_url == "http://[::1]:9100"


@pytest.mark.parametrize("value", ["0", "65536", "-1", "text"])
def test_invalid_ports_rejected(value):
    with pytest.raises(SystemExit):
        launcher.argument_parser().parse_args(["--api-port", value])
    with pytest.raises(launcher.argparse.ArgumentTypeError):
        launcher.service_settings(launcher.argument_parser().parse_args([]), {"SHIGUANG_API_PORT": value})


def test_identical_ports_rejected():
    args = launcher.argument_parser().parse_args(["--api-port", "9000", "--web-port", "9000"])
    with pytest.raises(ValueError, match="不能相同"):
        launcher.service_settings(args, {})


def test_custom_ports_checked(monkeypatch):
    addresses = []
    monkeypatch.setattr(socket.socket, "bind", lambda self, address: addresses.append(address))
    launcher.check_ports("127.0.0.1", 9100, 5180)
    assert addresses == [("127.0.0.1", 9100), ("127.0.0.1", 5180)]


def test_smoke_test_wires_settings_and_cleans_services(isolated_launcher, monkeypatch):
    env = {"SHIGUANG_VISION_API_KEY": "private-vision", "SHIGUANG_MODEL_BASE_URL": "https://old.example/v1"}
    for kind in ("MODEL", "VISION", "SPEECH", "AUDIO"):
        monkeypatch.setenv(f"SHIGUANG_{kind}_API_KEY", "")
    monkeypatch.setattr(launcher, "load_environment", lambda _: env)
    monkeypatch.setattr(launcher, "check_dependencies", lambda: (Path("python"), "node"))
    ports, services, checks, stopped = [], [], [], []
    monkeypatch.setattr(launcher, "check_ports", lambda *args: ports.append(args))
    def start_service(name, command, environment):
        services.append((name, command, dict(environment)))
        return name, None, None
    monkeypatch.setattr(launcher, "start_service", start_service)
    monkeypatch.setattr(launcher, "wait_ready", lambda services, **kw: checks.append(kw["urls"]))
    monkeypatch.setattr(launcher, "stop_services", lambda services: stopped.extend(services))
    assert launcher.main(["--smoke-test", "--api-port", "9100", "--web-port", "5180",
                          "--model-base-url", "https://new.example/v1"]) == 0
    assert ports == [("127.0.0.1", 9100, 5180)]
    assert "9100" in services[0][1] and "5180" in services[1][1]
    assert services[0][2]["SHIGUANG_LOG_RELAY"] == "1"
    assert services[0][2]["SHIGUANG_MODEL_BASE_URL"] == "https://new.example/v1"
    assert services[1][2]["SHIGUANG_API_URL"] == "http://127.0.0.1:9100"
    assert "SHIGUANG_VISION_API_KEY" not in services[1][2]
    assert "SHIGUANG_MODEL_BASE_URL" not in services[1][2]
    assert checks[-1] == {"http://127.0.0.1:9100/health", "http://127.0.0.1:5180/", "http://127.0.0.1:5180/health"}
    assert len(stopped) == 2
    assert launcher.running_state(isolated_launcher / ".runtime") is None


def test_background_matches_run_identity_with_python_wrapper(isolated_launcher, monkeypatch):
    """Windows 虚拟环境包装器和真正的启动器 PID 不同也能确认就绪。"""
    state = {}
    class Process:
        pid = 100

        def poll(self):
            return None

    def spawn(command, **options):
        assert "--background" not in command
        assert "--api-port" in command
        state.update(pid=200, launch_id=options["env"]["SHIGUANG_LAUNCH_ID"], status="ready",
                     api_url="http://127.0.0.1:9100", web_url="http://127.0.0.1:5180")
        return Process()

    monkeypatch.setattr(launcher.subprocess, "Popen", spawn)
    monkeypatch.setattr(launcher, "running_state", lambda _: state or None)
    assert launcher.start_background(["start", "--background", "--no-browser", "--api-port", "9100"]) == 0


def test_run_lock_and_stale_state(tmp_path):
    control = launcher.RunControl(tmp_path, web_url="http://127.0.0.1:5180")
    try:
        assert launcher.running_state(tmp_path)["run_id"] == control.state["run_id"]
        with pytest.raises(RuntimeError, match="已经启动"):
            launcher.RunControl(tmp_path)
        # 旧运行的停止请求不能影响当前实例。
        (tmp_path / "stop-old-run.request").touch()
        control.check_stop()
        control.publish(status="ready")
        assert launcher.running_state(tmp_path)["status"] == "ready"
    finally:
        control.close()
    (tmp_path / "launcher.json").write_text('{"pid":123,"run_id":"old-run"}', encoding="utf-8")
    assert launcher.running_state(tmp_path) is None
    assert launcher.stop_running(tmp_path) is False


def test_stop_command_waits_for_owner_cleanup(tmp_path):
    control = launcher.RunControl(tmp_path)
    with ThreadPoolExecutor(max_workers=1) as pool:
        stopped = pool.submit(launcher.stop_running, tmp_path, 3)
        try:
            deadline = time.monotonic() + 2
            while not control.stop_path.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            with pytest.raises(KeyboardInterrupt):
                control.check_stop()
            assert not stopped.done()
        finally:
            control.close()
        assert stopped.result(timeout=3) is True
    assert launcher.running_state(tmp_path) is None
