"""加载本机配置并统一启动、监控和关闭拾光前后端服务。"""
import argparse
from io import TextIOWrapper
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from types import FrameType
from typing import Callable, NamedTuple, TypeAlias
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4
import webbrowser

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "apps" / "api"))
from shiguang.observability import sanitize
from shiguang.log_format import ConsoleFormatter, english_record
from scripts.service_control import RunControl, running_state, stop_running

LOGGER = logging.getLogger("shiguang.start")
# 保留每项服务的进程和日志线程类型，避免后续生命周期操作失去类型信息。
Service: TypeAlias = tuple[str, subprocess.Popen[str], threading.Thread]


class ServiceSettings(NamedTuple):
    """共享端口配置，保证服务、代理、健康检查和浏览器地址一致。"""
    host: str
    api_port: int
    web_port: int

    def url(self, port: int) -> str:
        host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(self.host, self.host)
        return f"http://{'[' + host + ']' if ':' in host else host}:{port}"

    @property
    def api_url(self) -> str:
        return self.url(self.api_port)

    @property
    def web_url(self) -> str:
        return self.url(self.web_port)


def port_number(value: str) -> int:
    """拒绝无效端口及会随机分配端口的零值。"""
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("端口必须是 1–65535 的整数") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("端口必须是 1–65535 的整数")
    return port


def service_settings(args: argparse.Namespace, env: dict[str, str]) -> ServiceSettings:
    """命令行优先于环境变量；环境变量已优先于 .env。"""
    settings = ServiceSettings(
        args.host or env.get("SHIGUANG_HOST") or "127.0.0.1",
        args.api_port or port_number(env.get("SHIGUANG_API_PORT") or "8000"),
        args.web_port or port_number(env.get("SHIGUANG_WEB_PORT") or "5173"),
    )
    if settings.api_port == settings.web_port:
        raise ValueError("前后端端口不能相同，请分别设置 --api-port 和 --web-port")
    return settings


def load_environment(path: Path) -> dict[str, str]:
    """读取单行键值配置，已设置的进程环境变量优先。

    Args:
        path: 本机配置文件路径。

    Returns:
        可传给后端的环境变量，不执行插值或命令。

    Raises:
        ValueError: 配置行无效或引号未闭合。
    """
    values = {}
    if path.exists():
        for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            key, separator, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise ValueError(f".env 第 {number} 行格式无效，请使用 名称=值")
            if value.startswith(("'", '"')):
                match = re.fullmatch(r"(['\"])(.*?)\1\s*(?:#.*)?", value)
                if not match:
                    raise ValueError(f".env 第 {number} 行引号未闭合或存在多余内容")
                value = match.group(2)
            else:
                value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
            values[key] = value
    values.update(os.environ)
    values.update(PYTHONUTF8="1", PYTHONUNBUFFERED="1", NO_COLOR="1")
    return values


def configure_logging() -> None:
    """按服务追加到两个固定 UTF-8 文件，重复启动不截断或轮转。"""
    (ROOT / "logs").mkdir(exist_ok=True)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    for handler in list(LOGGER.handlers):
        handler.close()
        LOGGER.removeHandler(handler)
    console = logging.StreamHandler()
    console.setFormatter(ConsoleFormatter())
    LOGGER.addHandler(console)
    for service in ("backend", "frontend"):
        handler = logging.FileHandler(ROOT / "logs" / f"{service}.log", mode="a", encoding="utf-8")
        handler.addFilter(lambda record, name=service: record.service == name)
        LOGGER.addHandler(handler)


def emit_record(item: dict) -> None:
    """按原始级别输出已脱敏记录，避免再次包裹后端 JSON。

    Args:
        item: 含英文级别与事件字段的结构化日志。
    """
    item = sanitize(english_record(item))
    level = getattr(logging, str(item.get("level", "INFO")), logging.INFO)
    LOGGER.log(level if isinstance(level, int) else logging.INFO,
               json.dumps(item, ensure_ascii=False, allow_nan=False),
               extra={"service": item.get("service", "backend")})


def log(event: str, *, level: int = logging.INFO, **fields: object) -> None:
    """将启动器事件归入后端日志，子进程输出按服务分流。

    Args:
        event: 中文事件名称。
        level: 标准日志级别。
        **fields: 事件信息，不应传入完整环境变量。
    """
    emit_record({"timestamp": datetime.now(timezone.utc).isoformat(), "level": logging.getLevelName(level),
                 "event": event, **fields})


def relay_output(name: str, line: str) -> None:
    """透传结构化日志并识别第三方输出中的标准错误级别。

    Args:
        name: 子进程的服务名称。
        line: 一行未经脱敏的标准输出。
    """
    line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line).strip()
    if not line:
        return
    try:
        parsed = json.loads(line)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict) and ("event" in parsed or "事件" in parsed):
        emit_record({**parsed, "service": name})
        return
    level = logging.ERROR if re.search(r"\b(ERROR|CRITICAL|Traceback|\w*Error)\b|proxy error", line) else (
        logging.WARNING if re.search(r"\b(WARNING|WARN)\b", line) else logging.INFO)
    log("service_output", level=level, service=name, message=line)


def check_dependencies() -> tuple[Path, str]:
    """检查项目解释器和前端依赖是否已经安装。

    Returns:
        项目 Python 路径与 Node 可执行文件路径。

    Raises:
        RuntimeError: 依赖缺失或后端依赖无法导入。
    """
    python = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    node = shutil.which("node")
    if not python.is_file():
        raise RuntimeError("未找到项目 Python 环境，请先运行 uv sync --locked")
    if not node:
        raise RuntimeError("未找到 Node.js，请安装 Node.js 并重新打开终端")
    if not (ROOT / "node_modules/vite/bin/vite.js").is_file():
        raise RuntimeError("未找到前端依赖，请先运行 npm ci")
    result = subprocess.run([str(python), "-c", "import uvicorn, fastapi, httpx, numpy, imageio_ffmpeg"],
                            cwd=ROOT, capture_output=True, timeout=20)
    if result.returncode:
        raise RuntimeError("后端依赖不完整，请运行 uv sync --locked")
    return python, node


def check_ports(host: str = "127.0.0.1", api_port: int = 8000, web_port: int = 5173) -> None:
    """检查所选监听地址与端口，避免覆盖或停止其他程序。

    Raises:
        RuntimeError: 前后端端口不可绑定。
    """
    for port in (api_port, web_port):
        for family, kind, proto, _, address in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
            with socket.socket(family, kind, proto) as probe:
                if sys.platform == "win32":
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                try:
                    probe.bind(address)
                except OSError as exc:
                    raise RuntimeError(f"地址 {host}:{port} 已占用或不可用，请使用自定义端口后重试") from exc


def capture_output(name: str, process: subprocess.Popen[str]) -> None:
    """持续收集单个服务的输出并脱敏记录。

    Args:
        name: 服务中文名称。
        process: 已开启输出管道的进程。

    Raises:
        RuntimeError: 子进程未开启标准输出管道。
    """
    output = process.stdout
    if output is None:
        raise RuntimeError(f"{name}未开启标准输出管道，无法收集日志")
    with output:
        for line in output:
            relay_output(name, line)


def start_service(name: str, command: list[str], env: dict[str, str]) -> Service:
    """创建独立进程组和日志读取线程。

    Args:
        name: 服务中文名称。
        command: 不经过 shell 的参数列表。
        env: 该服务的环境变量。

    Returns:
        服务名称、子进程与日志线程。
    """
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               encoding="utf-8", errors="replace", creationflags=creationflags,
                               start_new_session=sys.platform != "win32")
    reader = threading.Thread(target=capture_output, args=(name, process), daemon=True)
    reader.start()
    log("service_started", service=name, pid=process.pid)
    return name, process, reader


def check_children(services: list[Service]) -> None:
    """发现子进程异常退出时通知统一清理逻辑。

    Args:
        services: 当前启动器创建的服务列表。

    Raises:
        RuntimeError: 任一服务提前退出。
    """
    for name, process, _ in services:
        if process.poll() is not None:
            raise RuntimeError(f"{name}已退出，退出码 {process.returncode}，详情见 logs/{name}.log")


def wait_ready(services: list[Service], timeout: float = 60, *, urls: set[str] | None = None,
               check_stop: Callable[[], None] | None = None) -> None:
    """验证后端、前端页面和前端代理均能响应。

    Args:
        services: 当前服务列表。
        timeout: 最长等待秒数。
        urls: 指定检查地址，默认同时检查前后端和代理。
        check_stop: 允许启动过程中响应另一个终端发出的停止请求。

    Raises:
        RuntimeError: 服务退出或启动超时。
    """
    pending = set(urls) if urls is not None else {
        "http://127.0.0.1:8000/health", "http://127.0.0.1:5173/", "http://127.0.0.1:5173/health"}
    client = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while pending and time.monotonic() < deadline:
        if check_stop:
            check_stop()
        check_children(services)
        for url in tuple(pending):
            try:
                with client.open(url, timeout=1) as response:
                    if response.status == 200 and (not url.endswith("/health") or json.load(response).get("status") == "ok"):
                        pending.remove(url)
            except (URLError, OSError, ValueError):
                pass
        if pending:
            time.sleep(0.2)
    check_children(services)
    if pending:
        raise RuntimeError("服务启动超时，未就绪地址：" + "、".join(sorted(pending)))


def stop_services(services: list[Service]) -> None:
    """停止本次创建的进程组并收集日志尾部。

    Args:
        services: 本启动器拥有的服务列表，不包含外部进程。
    """
    for name, process, reader in reversed(services):
        try:
            if process.poll() is None:
                if sys.platform == "win32":
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=8)
        except (OSError, subprocess.TimeoutExpired):
            if process.poll() is None:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, timeout=10)
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
        finally:
            reader.join(timeout=2)
            log("service_stopped", service=name, exit_code=process.poll())


def request_stop(signum: int, frame: FrameType | None) -> None:
    """将终端退出信号转为统一清理流程。

    Args:
        signum: 操作系统信号编号。
        frame: 信号产生时的执行帧。
    """
    raise KeyboardInterrupt


def start_background(arguments: list[str]) -> int:
    """隐藏后台启动器窗口，确认前后端及代理就绪后才返回成功。"""
    if running_state(ROOT / ".runtime") is not None:
        raise RuntimeError("本项目已经启动，请使用 npm run status")
    options = {"start_new_session": sys.platform != "win32"}
    if sys.platform == "win32":
        # 隐藏独立控制台，同时保留 CTRL_BREAK 关闭子进程的能力。
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        options.update(creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup)
    launch_id = uuid4().hex
    environment = {**os.environ, "SHIGUANG_LAUNCH_ID": launch_id}
    process = subprocess.Popen([sys.executable, "-X", "utf8", str(ROOT / "start.py"),
                                *(arg for arg in arguments if arg != "--background")],
                               cwd=ROOT, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, **options)
    print("正在后台启动，日志位于 logs/backend.log 和 logs/frontend.log……", flush=True)
    deadline = time.monotonic() + 150
    ready = False
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("后台启动失败，详情见 logs/backend.log 和 logs/frontend.log")
            state = running_state(ROOT / ".runtime")
            # Windows 的 .venv/python.exe 可能转发到另一 PID，用运行标识关联子启动器。
            if state and state.get("launch_id") == launch_id and state.get("status") == "ready":
                print(f"后台启动成功：{state['web_url']}\n接口文档：{state['api_url']}/docs\n"
                      "关闭：npm stop（PowerShell 可用 npm.cmd stop）", flush=True)
                ready = True
                return 0
            time.sleep(0.2)
        raise RuntimeError("后台启动超时，详情见 logs/backend.log 和 logs/frontend.log")
    finally:
        if not ready and process.poll() is None:
            state = running_state(ROOT / ".runtime")
            if state and state.get("launch_id") == launch_id:
                stop_running(ROOT / ".runtime")
            else:
                # 尚未发布状态时只终止本命令创建的启动器。
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, timeout=10)
                else:
                    process.terminate()
                process.wait(timeout=10)


def argument_parser() -> argparse.ArgumentParser:
    """声明前台、后台及自动化脚本共用的命令行接口。"""
    parser = argparse.ArgumentParser(description="启动、关闭或查询拾光前后端服务，默认动作为 start")
    parser.add_argument("command", nargs="?", choices=("start", "stop", "status"), default="start")
    parser.add_argument("--host", help="监听地址，默认 127.0.0.1，也可设置 SHIGUANG_HOST")
    parser.add_argument("--api-port", "--backend-port", type=port_number, help="后端端口，默认 8000")
    parser.add_argument("--web-port", "--frontend-port", type=port_number, help="前端端口，默认 5173")
    parser.add_argument("--model-base-url", help="本次运行的模型接口地址，覆盖 SHIGUANG_MODEL_BASE_URL")
    parser.add_argument("--background", action="store_true", help="后台启动，就绪后返回终端")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--smoke-test", action="store_true", help="启动并验证服务后自动关闭，不调用模型")
    return parser


def main(argv: list[str] | None = None) -> int:
    """加载配置、启动服务并在退出时清理本次创建的进程。

    Returns:
        正常停止返回零，启动或运行失败返回一。
    """
    arguments = sys.argv[1:] if argv is None else argv
    parser = argument_parser()
    args = parser.parse_args(arguments)
    if args.background and args.smoke_test:
        parser.error("--background 与 --smoke-test 不能同时使用")
    if args.command != "start" and any((args.host, args.api_port, args.web_port, args.model_base_url,
                                         args.background, args.no_browser, args.smoke_test)):
        parser.error("启动参数只能用于 start；stop/status 自动读取当前实例的配置")
    services: list[Service] = []
    control = None
    signals = {}
    try:
        if args.command == "stop":
            stopped = stop_running(ROOT / ".runtime")
            print("前后端服务已关闭。" if stopped else "本项目当前没有运行中的启动器。")
            return 0
        if args.command == "status":
            state = running_state(ROOT / ".runtime")
            print(json.dumps(state or {"status": "stopped"}, ensure_ascii=False, indent=2))
            return 0 if state else 1
        if args.background:
            return start_background(arguments)
        configure_logging()
        env = load_environment(ROOT / ".env")
        settings = service_settings(args, env)
        if args.model_base_url is not None:
            env["SHIGUANG_MODEL_BASE_URL"] = args.model_base_url
        # 仅供日志脱敏，不输出配置文件或模型密钥。
        for kind in ("MODEL", "VISION", "SPEECH", "AUDIO"):
            key = f"SHIGUANG_{kind}_API_KEY"
            os.environ[key] = env.get(key, "")
        control = RunControl(ROOT / ".runtime", host=settings.host, api_url=settings.api_url,
                             web_url=settings.web_url, launch_id=env.pop("SHIGUANG_LAUNCH_ID", None))
        for signum in (signal.SIGINT, signal.SIGTERM, *((signal.SIGBREAK,) if sys.platform == "win32" else ())):
            signals[signum] = signal.signal(signum, request_stop)
        python, node = check_dependencies()
        control.check_stop()
        check_ports(*settings)
        for name in ("database", "logs"):
            (ROOT / name).mkdir(exist_ok=True)
        if not env.get("SHIGUANG_MODEL_API_KEY", "").strip():
            log("model_not_configured", level=logging.WARNING,
                reason="Set SHIGUANG_MODEL_API_KEY in .env to enable AI editing. Manual editing is available.")
        # 统一由启动器落盘，后端只向管道输出，避免同一事件被重复写入。
        env["SHIGUANG_LOG_CONSOLE"] = "json"
        env["SHIGUANG_LOG_RELAY"] = "1"
        services.append(start_service("backend", [str(python), "-X", "utf8", "-m", "uvicorn", "shiguang.api:app",
                                                "--app-dir", "apps/api", "--host", settings.host,
                                                "--port", str(settings.api_port),
                                                "--no-access-log"], env))
        # 后端先就绪，避免启动检查触发前端代理的 ECONNREFUSED 噪声。
        wait_ready(services, urls={settings.api_url + "/health"}, check_stop=control.check_stop)
        # 前端开发进程无需接触模型密钥；API 调用统一由后端完成。
        frontend_env = {k: v for k, v in env.items()
                        if not k.startswith(tuple(f"SHIGUANG_{kind}_" for kind in ("MODEL", "VISION", "SPEECH", "AUDIO")))}
        frontend_env["SHIGUANG_API_URL"] = settings.api_url
        services.append(start_service("frontend", [node, str(ROOT / "node_modules/vite/bin/vite.js"),
                                                "--config", "apps/web/vite.config.ts", "--host", settings.host,
                                                "--port", str(settings.web_port)], frontend_env))
        wait_ready(services, urls={settings.api_url + "/health", settings.web_url + "/", settings.web_url + "/health"},
                   check_stop=control.check_stop)
        control.check_stop()
        control.publish(status="ready")
        log("workspace_ready", url=settings.web_url, api_docs=settings.api_url + "/docs",
            instruction="Press Ctrl+C or run npm stop to stop both services",
            backend_log="logs/backend.log", frontend_log="logs/frontend.log")
        if args.smoke_test:
            log("startup_check_passed", checks="backend health, frontend page, API proxy")
            return 0
        if not args.no_browser:
            webbrowser.open(settings.web_url)
        while True:
            control.check_stop()
            check_children(services)
            time.sleep(0.5)
    except KeyboardInterrupt:
        log("shutdown_requested")
        return 0
    except Exception as exc:
        if LOGGER.handlers:
            log("launcher_failed", level=logging.ERROR, exception_type=type(exc).__name__, reason=str(exc))
        else:
            print(f"错误：{sanitize(str(exc))}", file=sys.stderr)
        return 1
    finally:
        # 清理期间忽略重复 Ctrl+C，确保两个子进程都得到停止处理。
        for signum in signals:
            signal.signal(signum, signal.SIG_IGN)
        try:
            if control:
                try:
                    control.publish(status="stopping")
                except OSError as exc:
                    log("state_update_failed", level=logging.WARNING, reason=str(exc))
            stop_services(services)
        finally:
            if control:
                control.close()
            for signum, handler in signals.items():
                signal.signal(signum, handler)
            for handler in list(LOGGER.handlers):
                handler.close()
                LOGGER.removeHandler(handler)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
