"""用本机文件锁和停止请求管理本项目的启动器，不按端口或陈旧 PID 杀进程。"""
import json
import os
from pathlib import Path
import time
from uuid import uuid4


def try_lock(stream) -> bool:
    """尝试持有进程退出后由系统自动释放的文件锁。"""
    stream.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def running_state(directory: Path) -> dict | None:
    """只在启动器仍持有锁时读取状态，忽略崩溃遗留的状态文件。"""
    path = directory / "launcher.lock"
    if not path.exists():
        return None
    with path.open("r+b") as stream:
        if try_lock(stream):
            return None
        try:
            return json.loads((directory / "launcher.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            # 锁已获取但状态尚未发布，调用方可以短暂重试。
            return {"status": "starting"}


class RunControl:
    """持有一次运行的锁、状态和停止请求；不同运行使用独立标识。"""

    def __init__(self, directory: Path, **state):
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory
        self.stream = (directory / "launcher.lock").open("a+b")
        if self.stream.tell() == 0:
            self.stream.write(b"\0")
            self.stream.flush()
        if not try_lock(self.stream):
            self.stream.close()
            raise RuntimeError("本项目已经启动，请使用 npm run status 或 npm stop")
        self.state = {"pid": os.getpid(), "run_id": uuid4().hex, "status": "starting", **state}
        self.stop_path = directory / f"stop-{self.state['run_id']}.request"
        try:
            self.publish()
        except BaseException:
            self.stream.close()
            raise

    def publish(self, **changes) -> None:
        """原子更新状态，避免查询命令读到半份 JSON。"""
        self.state.update(changes)
        temporary = self.directory / "launcher.json.tmp"
        temporary.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.directory / "launcher.json")

    def check_stop(self) -> None:
        """由拥有子进程的启动器处理停止请求，沿用统一清理流程。"""
        if self.stop_path.exists():
            raise KeyboardInterrupt

    def close(self) -> None:
        """清理当前运行的状态，再释放文件锁。"""
        try:
            (self.directory / "launcher.json").unlink(missing_ok=True)
            self.stop_path.unlink(missing_ok=True)
        finally:
            self.stream.close()


def stop_running(directory: Path, timeout: float = 60) -> bool:
    """请求当前启动器停止并等待清理完成，无运行实例时直接返回。"""
    deadline = time.monotonic() + timeout
    while True:
        state = running_state(directory)
        if state is None:
            return False
        if state.get("run_id"):
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("启动器状态尚未就绪，请稍后重试停止命令")
        time.sleep(0.1)
    request = directory / f"stop-{state['run_id']}.request"
    request.touch()
    while time.monotonic() < deadline:
        current = running_state(directory)
        if current is None or current.get("run_id") != state["run_id"]:
            request.unlink(missing_ok=True)
            return True
        time.sleep(0.1)
    raise RuntimeError("关闭超时，停止请求已保留；请查看 logs/backend.log")
