"""定义以整数帧和左闭右开区间表示的剪辑工程契约。"""
from __future__ import annotations

from math import ceil, isfinite
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

Frame = Annotated[StrictInt, Field(ge=0)]
PositiveFrame = Annotated[StrictInt, Field(gt=0)]
Unit = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Gain = Annotated[float, Field(ge=-60, le=12, allow_inf_nan=False)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, allow_inf_nan=False)


class Composition(Model):
    width: Annotated[StrictInt, Field(ge=64, le=1920)] = 1920
    height: Annotated[StrictInt, Field(ge=64, le=1920)] = 1080
    fps: Literal[30] = 30
    audio_sample_rate: Literal[48000] = 48000
    color_space: Literal["sdr_bt709"] = "sdr_bt709"

    @model_validator(mode="after")
    def even_size(self):
        """检查视频宽高能否用于 H.264 编码。

        Returns:
            尺寸合法的合成配置。

        Raises:
            ValueError: 宽或高不是偶数。
        """
        if self.width % 2 or self.height % 2:
            raise ValueError("H.264 output dimensions must be even")
        return self


class Transform(Model):
    fit: Literal["cover", "contain"] = "cover"
    anchor_x: Unit = 0.5
    anchor_y: Unit = 0.5
    scale: Annotated[float, Field(ge=1, le=4)] = 1
    rotation_deg: Annotated[float, Field(ge=-15, le=15)] = 0


class Motion(Model):
    preset_id: Literal["slow_push_in", "slow_pull_out", "pan_left", "pan_right", "pan_up", "pan_down"]
    preset_version: Literal[1] = 1
    local_start_frame: Frame = 0
    local_end_frame: PositiveFrame
    anchor_x: Unit = 0.5
    anchor_y: Unit = 0.5
    intensity: Annotated[float, Field(gt=0, le=0.5)] = 0.12
    easing: Literal["linear", "ease_in_out"] = "ease_in_out"


class Clip(Model):
    clip_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
    asset_id: str
    kind: Literal["video", "image"]
    source_in_frame: Frame = 0
    source_out_frame: PositiveFrame | None = None
    still_duration_frames: PositiveFrame | None = None
    rate_num: Annotated[StrictInt, Field(gt=0)] = 1
    rate_den: Annotated[StrictInt, Field(gt=0)] = 1
    reverse: bool = False
    original_audio_gain_db: Gain = 0
    transform: Transform = Field(default_factory=Transform)
    motion: Motion | None = None
    color_preset: Literal["warm", "cool", "monochrome"] | None = None
    derived_from_clip_id: str | None = None

    @property
    def duration(self) -> int:
        if self.kind == "image":
            return self.still_duration_frames or 0
        return ceil(((self.source_out_frame or 0) - self.source_in_frame) * self.rate_den / self.rate_num)


class Transition(Model):
    left_clip_id: str
    right_clip_id: str
    preset_id: Literal["dissolve"] = "dissolve"
    preset_version: Literal[1] = 1
    duration_frames: PositiveFrame


class Anchor(Model):
    kind: Literal["timeline", "clip"] = "timeline"
    clip_id: str | None = None
    start_frame: Frame = 0
    end_frame: PositiveFrame


class TextItem(Model):
    text_id: str
    text: str = Field(min_length=1, max_length=1000)
    anchor: Anchor
    style_preset: Literal["title", "subtitle"] = "title"
    x: Unit = 0.5
    y: Unit = 0.8
    font_size: Annotated[float, Field(ge=8, le=200)] = 48
    color: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")


class Duck(Model):
    clip_id: str
    gain_db: Annotated[float, Field(ge=-60, le=0)] = -12
    ramp_frames: Frame = 6


class AudioItem(Model):
    audio_id: str
    asset_id: str
    anchor: Anchor
    source_in_frame: Frame = 0
    track: Literal[1, 2] = 1
    gain_db: Gain = -12
    fade_in_frames: Frame = 0
    fade_out_frames: Frame = 0
    ducks: list[Duck] = Field(default_factory=list)


class Snapshot(Model):
    schema_version: Literal[1] = 1
    composition: Composition = Field(default_factory=Composition)
    clips: list[Clip] = Field(default_factory=list)
    transitions: list[Transition] = Field(default_factory=list)
    text_items: list[TextItem] = Field(default_factory=list)
    audio_items: list[AudioItem] = Field(default_factory=list)


class Asset(Model):
    asset_id: str
    project_id: str
    kind: Literal["video", "image", "audio"]
    name: str
    path: str
    sha256: str
    width: int = 0
    height: int = 0
    total_frames: int = 0
    has_audio: bool = False
    status: Literal["ready"] = "ready"


def timeline(snapshot: Snapshot) -> tuple[list[dict], int]:
    """计算包含转场重叠的时间线位置。

    Args:
        snapshot: 当前工程快照。

    Returns:
        各片段的时间线区间和总帧数。
    """
    transitions = {(t.left_clip_id, t.right_clip_id): t.duration_frames for t in snapshot.transitions}
    rows, cursor = [], 0
    for i, clip in enumerate(snapshot.clips):
        if i:
            cursor -= transitions.get((snapshot.clips[i - 1].clip_id, clip.clip_id), 0)
        rows.append({"clip_id": clip.clip_id, "start_frame": cursor, "end_frame": cursor + clip.duration,
                     "duration_frames": clip.duration, "asset_id": clip.asset_id})
        cursor += clip.duration
    return rows, cursor


def anchor_range(anchor: Anchor, snapshot: Snapshot) -> tuple[int, int]:
    """将片段相对锚点转换为时间线帧区间。

    Args:
        anchor: 时间线或片段锚点。
        snapshot: 用于解析片段位置的工程快照。

    Returns:
        左闭右开的绝对帧区间。
    """
    if anchor.kind == "timeline":
        return anchor.start_frame, anchor.end_frame
    row = next((r for r in timeline(snapshot)[0] if r["clip_id"] == anchor.clip_id), None)
    if row is None:
        raise ValueError(f"Unknown anchor clip: {anchor.clip_id}")
    return row["start_frame"] + anchor.start_frame, row["start_frame"] + anchor.end_frame


def validate_snapshot(s: Snapshot, assets: dict[str, Asset]) -> list[str]:
    """检查工具参数之外的跨片段和素材约束。

    Args:
        s: 待检查的工程快照。
        assets: 当前项目可用素材的编号映射。

    Returns:
        约束错误代码列表，空列表表示通过。
    """
    errors: list[str] = []
    ids = [c.clip_id for c in s.clips]
    if len(set(ids)) != len(ids):
        errors.append("DUPLICATE_CLIP_ID")
    if len(ids) > 40:
        errors.append("CLIP_LIMIT")
    for collection, key in [(s.text_items, "text_id"), (s.audio_items, "audio_id")]:
        values = [getattr(v, key) for v in collection]
        if len(values) != len(set(values)):
            errors.append(f"DUPLICATE_{key.upper()}")
    by_id = {c.clip_id: c for c in s.clips}
    overlaps = {c.clip_id: 0 for c in s.clips}
    pairs = list(zip(ids, ids[1:]))
    seen = set()
    for t in s.transitions:
        pair = (t.left_clip_id, t.right_clip_id)
        if pair not in pairs or pair in seen:
            errors.append("INVALID_TRANSITION_PAIR")
        seen.add(pair)
        for cid in pair:
            if cid in overlaps:
                overlaps[cid] += t.duration_frames
    for c in s.clips:
        a = assets.get(c.asset_id)
        if not a or a.kind != c.kind:
            errors.append(f"INVALID_ASSET:{c.clip_id}")
            continue
        if c.kind == "video":
            if c.still_duration_frames is not None or c.source_out_frame is None or not 0 <= c.source_in_frame < c.source_out_frame <= a.total_frames:
                errors.append(f"SOURCE_BOUNDS:{c.clip_id}")
        elif c.still_duration_frames is None or c.source_in_frame != 0 or c.source_out_frame is not None or c.reverse or c.rate_num != c.rate_den:
            errors.append(f"IMAGE_TIMING:{c.clip_id}")
        rate = c.rate_num / c.rate_den
        if not isfinite(rate) or not 0.5 <= rate <= 2:
            errors.append(f"PLAYBACK_RANGE:{c.clip_id}")
        if c.duration <= overlaps[c.clip_id]:
            errors.append(f"TRANSITION_TOO_LONG:{c.clip_id}")
        if c.motion:
            m = c.motion
            if not 0 <= m.local_start_frame < m.local_end_frame <= c.duration:
                errors.append(f"MOTION_BOUNDS:{c.clip_id}")
            if c.transform.fit != "cover":
                errors.append(f"MOTION_REQUIRES_COVER:{c.clip_id}")
    _, total = timeline(s)
    for item in [*s.text_items, *s.audio_items]:
        anchor = item.anchor
        if anchor.kind == "clip":
            clip = by_id.get(anchor.clip_id or "")
            end = clip.duration if clip else 0
        else:
            end = total
            if anchor.clip_id is not None:
                errors.append("TIMELINE_ANCHOR_HAS_CLIP")
        if not 0 <= anchor.start_frame < anchor.end_frame <= end:
            errors.append("ANCHOR_BOUNDS")
        if isinstance(item, AudioItem):
            a = assets.get(item.asset_id)
            duration = anchor.end_frame - anchor.start_frame
            if not a or a.kind != "audio" or item.source_in_frame + duration > a.total_frames:
                errors.append("AUDIO_SOURCE_BOUNDS")
            if item.fade_in_frames + item.fade_out_frames > duration:
                errors.append("AUDIO_FADE_BOUNDS")
            if len({d.clip_id for d in item.ducks}) != len(item.ducks) or any(d.clip_id not in by_id for d in item.ducks):
                errors.append("INVALID_DUCK_TARGET")
    if total > 180 * 30:
        errors.append("DURATION_LIMIT")
    return errors
