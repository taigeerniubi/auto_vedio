"""定义智能体请求、循环预算和模型调用契约。"""
from dataclasses import dataclass
from typing import Annotated, Protocol

from pydantic import Field

from ..models import Model


class Command(Model):
    request_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    instruction: str = Field(min_length=1, max_length=8000)
    expected_revision: Annotated[int, Field(strict=True, ge=0)]
    selected_clip_ids: list[str] = Field(default_factory=list, max_length=40)


class Finish(Model):
    summary: str = Field(min_length=1, max_length=2000)


class AskUser(Model):
    question: str = Field(min_length=1, max_length=2000)


class Unsupported(Model):
    reason: str = Field(min_length=1, max_length=2000)


@dataclass(frozen=True)
class Limits:
    rounds: int = 8
    calls: int = 32
    errors: int = 3
    seconds: float = 180

    def __post_init__(self):
        """拒绝无效或非有限的循环预算。"""
        import math
        if min(self.rounds, self.calls, self.errors, self.seconds) <= 0 or not math.isfinite(self.seconds):
            raise ValueError("循环预算必须为有限正数")


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: str


class Session(Protocol):
    def step(self, results: list[tuple[str, dict]], timeout: float) -> list[ToolCall]:
        """将工具结果交给模型并取得下一批调用。

        Args:
            results: 调用编号及对应结果。
            timeout: 本轮请求的剩余时间，单位为秒。

        Returns:
            模型产生的工具调用列表。
        """
        ...

    def close(self) -> None:
        """释放当前会话持有的网络连接。"""
        ...
