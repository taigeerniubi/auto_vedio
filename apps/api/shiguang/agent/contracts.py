"""定义智能体请求、循环预算和模型调用契约。"""
from dataclasses import dataclass
import os
from typing import Annotated, Protocol

from pydantic import Field, model_validator

from ..models import Model


class AnnotationRegion(Model):
    """相对成片画布的区域；不包含播放器留白或浏览器像素坐标。"""
    x: Annotated[float, Field(strict=True, ge=0, lt=1)]
    y: Annotated[float, Field(strict=True, ge=0, lt=1)]
    width: Annotated[float, Field(strict=True, gt=0, le=1)]
    height: Annotated[float, Field(strict=True, gt=0, le=1)]

    @model_validator(mode="after")
    def contained(self):
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("批注区域必须在成片画布内")
        return self


class FrameAnnotation(Model):
    frame: Annotated[int, Field(strict=True, ge=0)]
    region: AnnotationRegion


class Command(Model):
    request_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    session_id: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    new_session: bool = Field(default=False, strict=True)
    resume_run_id: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    instruction: str = Field(min_length=1, max_length=8000)
    expected_revision: Annotated[int, Field(strict=True, ge=0)]
    selected_clip_ids: list[str] = Field(default_factory=list, max_length=40)
    playhead_frame: Annotated[int, Field(strict=True, ge=0)] = 0
    annotation: FrameAnnotation | None = None

    @model_validator(mode="after")
    def annotation_is_new_request(self):
        if self.annotation and self.resume_run_id:
            raise ValueError("恢复任务使用原批注，不能同时提交新批注")
        return self


class Finish(Model):
    summary: str = Field(min_length=1, max_length=8000,
                         description="直接展示给用户的完整答复。问答必须写出结论、依据和建议，不得仅写‘已说明’或‘已回答’的工作摘要。")


class AskUser(Model):
    question: str = Field(min_length=1, max_length=2000)


class Unsupported(Model):
    reason: str = Field(min_length=1, max_length=2000)


@dataclass(frozen=True)
class Limits:
    rounds: int = 24
    calls: int = 32
    errors: int = 3
    seconds: float | None = None

    @classmethod
    def from_env(cls):
        """读取可配置的规划轮次；总运行时间默认不设截止时间。"""
        try:
            rounds = int(os.getenv("SHIGUANG_AGENT_MAX_ROUNDS", str(cls.rounds)))
            if rounds <= 0:
                raise ValueError
        except ValueError as exc:
            raise ValueError("SHIGUANG_AGENT_MAX_ROUNDS must be a positive integer") from exc
        try:
            seconds = float(os.getenv("SHIGUANG_AGENT_TIMEOUT_SECONDS", "0"))
            return cls(rounds=rounds, seconds=None if seconds == 0 else seconds)
        except ValueError as exc:
            raise ValueError("SHIGUANG_AGENT_TIMEOUT_SECONDS must be a finite non-negative number") from exc

    def __post_init__(self):
        """拒绝无效或非有限的循环预算。"""
        import math
        if min(self.rounds, self.calls, self.errors) <= 0 or (
            self.seconds is not None and (self.seconds <= 0 or not math.isfinite(self.seconds))
        ):
            raise ValueError("循环预算必须为有限正数")


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: str


class Session(Protocol):
    def step(self, results: list[tuple[str, dict]], timeout: float | None) -> list[ToolCall]:
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
