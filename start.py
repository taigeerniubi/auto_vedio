"""加载本机配置并统一启动、监控和关闭拾光前后端服务。"""
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
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
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
import webbrowser

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "apps" / "api"))
from shiguang.observability import sanitize

LOGGER = logging.getLogger("shiguang.start")


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
    """创建控制台和 UTF-8 轮转日志处理器。"""
    (ROOT / "logs").mkdir(exist_ok=True)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    for handler in list(LOGGER.handlers):
        handler.close()
        LOGGER.removeHandler(handler)
    LOGGER.addHandler(logging.StreamHandler())
    LOGGER.addHandler(RotatingFileHandler(ROOT / "logs" / "start.log", maxBytes=5 * 1024 * 1024,
                                         backupCount=3, encoding="utf-8"))


def log(event: str, **fields) -> None:
    """将中文字段和脱敏后的子进程输出写入启动日志。

    Args:
        event: 中文事件名称。
        **fields: 事件信息，不应传入完整环境变量。
    """
    LOGGER.info(json.dumps(sanitize({"时间": time.strftime("%Y-%m-%d %H:%M:%S"),
                                    "事件": event, **fields}), ensure_ascii=False))


def check_dependencies() -> tuple[Path, str]:
    """检查项目解释器和前端依赖是否已经安装。

    Returns:
        项目 Python 路径与 Node 可执行文件路径。

    Raises:
        RuntimeError: 依赖缺失或后端依赖无法导入。
    """
    python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
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


def check_ports() -> None:
    """检查固定服务端口，避免覆盖或停止其他程序。

    Raises:
        RuntimeError: 前后端端口不可绑定。
    """
    for port in (8000, 5173):
        with socket.socket() as probe:
            if os.name == "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as exc:
                raise RuntimeError(f"端口 {port} 已占用或不可用，请关闭原服务后重试") from exc


def capture_output(name: str, process: subprocess.Popen) -> None:
    """持续收集单个服务的输出并脱敏记录。

    Args:
        name: 服务中文名称。
        process: 已开启输出管道的进程。
    """
    with process.stdout:
        for line in process.stdout:
            line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line).strip()
            if line:
                log("服务输出", 服务=name, 内容=line)


def start_service(name: str, command: list[str], env: dict) -> tuple:
    """创建独立进程组和日志读取线程。

    Args:
        name: 服务中文名称。
        command: 不经过 shell 的参数列表。
        env: 该服务的环境变量。

    Returns:
        服务名称、子进程与日志线程。
    """
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               encoding="utf-8", errors="replace", **options)
    reader = threading.Thread(target=capture_output, args=(name, process), daemon=True)
    reader.start()
    log("服务已创建", 服务=name, 进程编号=process.pid)
    return name, process, reader


def check_children(services: list) -> None:
    """发现子进程异常退出时通知统一清理逻辑。

    Args:
        services: 当前启动器创建的服务列表。

    Raises:
        RuntimeError: 任一服务提前退出。
    """
    for name, process, _ in services:
        if process.poll() is not None:
            raise RuntimeError(f"{name}已退出，退出码 {process.returncode}，详情见 logs/start.log")


def wait_ready(services: list, timeout: float = 60) -> None:
    """验证后端、前端页面和前端代理均能响应。

    Args:
        services: 当前服务列表。
        timeout: 最长等待秒数。

    Raises:
        RuntimeError: 服务退出或启动超时。
    """
    pending = {"http://127.0.0.1:8000/health", "http://127.0.0.1:5173/", "http://127.0.0.1:5173/health"}
    client = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while pending and time.monotonic() < deadline:
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


def stop_services(services: list) -> None:
    """停止本次创建的进程组并收集日志尾部。

    Args:
        services: 本启动器拥有的服务列表，不包含外部进程。
    """
    for name, process, reader in reversed(services):
        try:
            if process.poll() is None:
                if os.name == "nt":
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=8)
        except (OSError, subprocess.TimeoutExpired):
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, timeout=10)
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
        finally:
            reader.join(timeout=2)
            log("服务已停止", 服务=name, 退出码=process.poll())


def request_stop(signum, frame) -> None:
    """将终端退出信号转为统一清理流程。

    Args:
        signum: 操作系统信号编号。
        frame: 信号产生时的执行帧。
    """
    raise KeyboardInterrupt


def main() -> int:
    """加载配置、启动服务并在退出时清理本次创建的进程。

    Returns:
        正常停止返回零，启动或运行失败返回一。
    """
    parser = argparse.ArgumentParser(description="一键启动拾光剪辑工作台，Ctrl+C 关闭前后端")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--smoke-test", action="store_true", help="启动并验证服务后自动关闭，不调用模型")
    args = parser.parse_args()
    configure_logging()
    services = []
    try:
        env = load_environment(ROOT / ".env")
        # 仅供日志脱敏，不输出配置文件或模型密钥。
        os.environ["SHIGUANG_MODEL_API_KEY"] = env.get("SHIGUANG_MODEL_API_KEY", "")
        python, node = check_dependencies()
        check_ports()
        for name in ("database", "logs"):
            (ROOT / name).mkdir(exist_ok=True)
        if not env.get("SHIGUANG_MODEL_API_KEY", "").strip():
            log("模型尚未配置", 原因="请在 .env 填写 SHIGUANG_MODEL_API_KEY；当前可使用手动剪辑")
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, request_stop)
        if os.name == "nt":
            signal.signal(signal.SIGBREAK, request_stop)
        services.append(start_service("后端", [str(python), "-X", "utf8", "-m", "uvicorn", "shiguang.api:app",
                                                "--app-dir", "apps/api", "--host", "127.0.0.1", "--port", "8000"], env))
        # 前端开发进程无需接触模型密钥；API 调用统一由后端完成。
        frontend_env = {k: v for k, v in env.items() if not k.startswith("SHIGUANG_MODEL_")}
        frontend_env["SHIGUANG_API_URL"] = "http://127.0.0.1:8000"
        services.append(start_service("前端", [node, str(ROOT / "node_modules/vite/bin/vite.js"),
                                                "--config", "apps/web/vite.config.ts"], frontend_env))
        wait_ready(services)
        log("工作台已就绪", 页面="http://127.0.0.1:5173", 接口文档="http://127.0.0.1:8000/docs",
            操作="按 Ctrl+C 同时关闭前后端", 启动日志="logs/start.log", 业务日志="logs/app.log")
        if args.smoke_test:
            log("启动验收通过", 检查="后端健康检查、前端页面、代理接口")
            return 0
        if not args.no_browser:
            webbrowser.open("http://127.0.0.1:5173")
        while True:
            check_children(services)
            time.sleep(0.5)
    except KeyboardInterrupt:
        log("正在关闭服务")
        return 0
    except Exception as exc:
        log("启动或运行失败", 异常类型=type(exc).__name__, 原因=str(exc))
        return 1
    finally:
        # 清理期间忽略重复 Ctrl+C，确保两个子进程都得到停止处理。
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        stop_services(services)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
