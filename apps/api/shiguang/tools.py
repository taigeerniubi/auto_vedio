"""注册带参数校验的确定性工具并执行工程快照变更。"""
from __future__ import annotations

from copy import deepcopy
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field

from .models import (Anchor, AudioItem, Clip, Duck, Frame, Gain, Model, Motion,
                     PositiveFrame, Snapshot, TextItem, Transform, Transition, Unit,
                     timeline, validate_snapshot)


class ToolError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


class Empty(Model):
    pass


class ClipId(Model):
    clip_id: str


class SequenceArgs(Model):
    clips: list[Clip] = Field(min_length=1, max_length=40)


class TrimArgs(ClipId):
    source_in_frame: Frame = 0
    source_out_frame: PositiveFrame | None = None
    still_duration_frames: PositiveFrame | None = None


class SplitArgs(ClipId):
    local_frame: PositiveFrame


class MoveArgs(ClipId):
    before_clip_id: str | None = None


class InsertArgs(Model):
    clip: Clip
    before_clip_id: str | None = None


class ReplaceArgs(ClipId):
    replacement: Clip


class PlaybackArgs(ClipId):
    rate_num: Annotated[int, Field(strict=True, gt=0)] = 1
    rate_den: Annotated[int, Field(strict=True, gt=0)] = 1
    reverse: bool = False


class TransformArgs(ClipId):
    transform: Transform


class MotionArgs(ClipId):
    motion: Motion | None


class TransitionArgs(Model):
    left_clip_id: str
    right_clip_id: str
    duration_frames: Frame
    preset_id: Literal["dissolve"] = "dissolve"
    preset_version: Literal[1] = 1


class ColorArgs(ClipId):
    preset_id: Literal["warm", "cool", "monochrome"] | None
    preset_version: Literal[1] = 1


class AddTextArgs(Model):
    text: str = Field(min_length=1, max_length=1000)
    anchor: Anchor
    style_preset: Literal["title", "subtitle"] = "title"
    x: Unit = 0.5
    y: Unit = 0.8
    font_size: Annotated[float, Field(ge=8, le=200)] = 48
    color: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")


class UpdateTextArgs(Model):
    text_id: str
    value: AddTextArgs


class TextId(Model):
    text_id: str


class AddAudioArgs(Model):
    asset_id: str
    anchor: Anchor
    source_in_frame: Frame = 0
    track: Literal[1, 2] = 1
    gain_db: Gain = -12


class AudioId(Model):
    audio_id: str


class GainArgs(Model):
    target: Literal["audio", "original"] = "audio"
    target_id: str
    gain_db: Gain


class FadeArgs(AudioId):
    fade_in_frames: Frame = 0
    fade_out_frames: Frame = 0


class DuckArgs(AudioId):
    clip_id: str
    gain_db: Annotated[float, Field(ge=-60, le=0)] = -12
    ramp_frames: Frame = 6


class ToolSpec:
    def __init__(self, arguments, description, handler=None):
        self.arguments = arguments
        self.description = description
        self.handler = handler


TOOLS: dict[str, ToolSpec] = {}


def tool(name, arguments, description):
    def decorate(fn):
        TOOLS[name] = ToolSpec(arguments, description, fn)
        return fn
    return decorate


def find_clip(s, cid):
    c = next((c for c in s.clips if c.clip_id == cid), None)
    if not c:
        raise ToolError("CLIP_NOT_FOUND", f"片段不存在：{cid}")
    return c


def find_audio(s, aid):
    a = next((a for a in s.audio_items if a.audio_id == aid), None)
    if not a:
        raise ToolError("AUDIO_NOT_FOUND", f"配乐不存在：{aid}")
    return a


def insertion_index(s, cid):
    return len(s.clips) if cid is None else s.clips.index(find_clip(s, cid))


def discard_invalid_transitions(s):
    pairs = list(zip([c.clip_id for c in s.clips], [c.clip_id for c in s.clips][1:]))
    old = len(s.transitions)
    s.transitions = [t for t in s.transitions if (t.left_clip_id, t.right_clip_id) in pairs]
    return ["因相邻关系改变，已移除失效转场"] if old != len(s.transitions) else []


@tool("assemble_sequence", SequenceArgs, "按顺序组装视频和图片，替换当前草稿并清空已有包装和配乐。")
def assemble(s, a):
    s.clips = deepcopy(a.clips)
    s.transitions, s.text_items, s.audio_items = [], [], []
    return [c.clip_id for c in s.clips], []


@tool("trim_clip", TrimArgs, "裁切视频源帧区间，或设置图片显示帧数；依赖效果越界时拒绝修改。")
def trim(s, a):
    c = find_clip(s, a.clip_id)
    if c.kind == "video":
        if a.still_duration_frames is not None:
            raise ToolError("INVALID_ARGUMENTS", "视频不接受图片时长")
        c.source_in_frame, c.source_out_frame = a.source_in_frame, a.source_out_frame
    else:
        if a.source_in_frame or a.source_out_frame is not None:
            raise ToolError("INVALID_ARGUMENTS", "图片不接受视频源区间")
        c.still_duration_frames = a.still_duration_frames
    return [c.clip_id], []


@tool("split_clip", SplitArgs, "在本地输出帧处分割，生成两个新 ID；有绑定效果或非整数源边界时明确拒绝。")
def split(s, a):
    c = find_clip(s, a.clip_id)
    if not 0 < a.local_frame < c.duration:
        raise ToolError("SPLIT_BOUNDS", "分割点必须在片段内部")
    if c.motion or any(i.anchor.clip_id == c.clip_id for i in [*s.text_items, *s.audio_items]) or any(t.left_clip_id == c.clip_id or t.right_clip_id == c.clip_id for t in s.transitions) or any(d.clip_id == c.clip_id for x in s.audio_items for d in x.ducks):
        raise ToolError("DEPENDENT_EFFECTS", "分割前请移除该片段的绑定文字、配乐、转场、压低配乐及运镜")
    left, right = c.model_copy(deep=True), c.model_copy(deep=True)
    left.clip_id, right.clip_id = uid("clip"), uid("clip")
    left.derived_from_clip_id = right.derived_from_clip_id = c.clip_id
    if c.kind == "image":
        left.still_duration_frames, right.still_duration_frames = a.local_frame, c.duration - a.local_frame
    else:
        if (a.local_frame * c.rate_num) % c.rate_den:
            raise ToolError("UNALIGNED_SPLIT", "分割点不能对应整数源帧，请选择相邻有效边界")
        offset = a.local_frame * c.rate_num // c.rate_den
        if c.reverse:
            left.source_in_frame = c.source_out_frame - offset
            right.source_out_frame = left.source_in_frame
        else:
            left.source_out_frame = c.source_in_frame + offset
            right.source_in_frame = left.source_out_frame
        if left.duration + right.duration != c.duration:
            raise ToolError("UNALIGNED_SPLIT", "分割会改变取整后的时长，请选择其他边界")
    i = s.clips.index(c)
    s.clips[i:i + 1] = [left, right]
    return [left.clip_id, right.clip_id], []


@tool("delete_clip", ClipId, "删除片段，连带删除其专属文字、音频、转场及配乐压低规则。")
def delete(s, a):
    c = find_clip(s, a.clip_id)
    s.clips.remove(c)
    s.text_items = [t for t in s.text_items if t.anchor.clip_id != c.clip_id]
    s.audio_items = [t for t in s.audio_items if t.anchor.clip_id != c.clip_id]
    for x in s.audio_items:
        x.ducks = [d for d in x.ducks if d.clip_id != c.clip_id]
    warnings = discard_invalid_transitions(s)
    return [c.clip_id], warnings


@tool("move_clip", MoveArgs, "移动到指定片段之前；before_clip_id=null 表示末尾。")
def move(s, a):
    c = find_clip(s, a.clip_id)
    if a.before_clip_id == c.clip_id:
        return [c.clip_id], []
    if a.before_clip_id:
        find_clip(s, a.before_clip_id)
    s.clips.remove(c)
    s.clips.insert(insertion_index(s, a.before_clip_id), c)
    return [c.clip_id], discard_invalid_transitions(s)


@tool("insert_clip", InsertArgs, "插入一个视频或图片引用，不修改原素材。")
def insert(s, a):
    if any(c.clip_id == a.clip.clip_id for c in s.clips):
        raise ToolError("DUPLICATE_CLIP_ID", "片段 ID 已存在")
    s.clips.insert(insertion_index(s, a.before_clip_id), a.clip.model_copy(deep=True))
    return [a.clip.clip_id], discard_invalid_transitions(s)


@tool("replace_clip", ReplaceArgs, "替换素材和参数，保留原片段 ID；关联效果必须仍然合法。")
def replace(s, a):
    c = find_clip(s, a.clip_id)
    replacement = a.replacement.model_copy(deep=True)
    replacement.clip_id = c.clip_id
    s.clips[s.clips.index(c)] = replacement
    return [c.clip_id], []


@tool("set_playback", PlaybackArgs, "设置 0.5–2 倍速和倒放；渲染前由 FFmpeg 生成精确长度的派生媒体。")
def playback(s, a):
    c = find_clip(s, a.clip_id)
    c.rate_num, c.rate_den, c.reverse = a.rate_num, a.rate_den, a.reverse
    return [c.clip_id], []


@tool("set_transform", TransformArgs, "设置 cover/contain、锚点、缩放和基础旋转；cover 自动补足旋转覆盖范围。")
def transform(s, a):
    c = find_clip(s, a.clip_id)
    c.transform = a.transform
    return [c.clip_id], []


@tool("apply_motion_preset", MotionArgs, "应用缓推、缓拉、四向平移；motion=null 清除运镜。")
def motion(s, a):
    c = find_clip(s, a.clip_id)
    c.motion = a.motion
    return [c.clip_id], []


@tool("set_transition", TransitionArgs, "设置相邻片段的叠化；duration_frames=0 清除。")
def transition(s, a):
    find_clip(s, a.left_clip_id)
    find_clip(s, a.right_clip_id)
    s.transitions = [t for t in s.transitions if (t.left_clip_id, t.right_clip_id) != (a.left_clip_id, a.right_clip_id)]
    if a.duration_frames:
        s.transitions.append(Transition(**a.model_dump()))
    return [a.left_clip_id, a.right_clip_id], []


@tool("set_color_preset", ColorArgs, "应用暖色、冷色、黑白预设；preset_id=null 清除。")
def color(s, a):
    find_clip(s, a.clip_id).color_preset = a.preset_id
    return [a.clip_id], []


@tool("add_text", AddTextArgs, "添加可绑定片段或成片时间的文字。")
def add_text(s, a):
    t = TextItem(text_id=uid("text"), **a.model_dump())
    s.text_items.append(t)
    return [t.text_id], []


@tool("update_text", UpdateTextArgs, "更新指定文字及样式，不修改视频中的人声。")
def update_text(s, a):
    old = next((t for t in s.text_items if t.text_id == a.text_id), None)
    if not old:
        raise ToolError("TEXT_NOT_FOUND", "文字不存在")
    s.text_items[s.text_items.index(old)] = TextItem(text_id=a.text_id, **a.value.model_dump())
    return [a.text_id], []


@tool("remove_text", TextId, "移除指定文字。")
def remove_text(s, a):
    if not any(t.text_id == a.text_id for t in s.text_items):
        raise ToolError("TEXT_NOT_FOUND", "文字不存在")
    s.text_items = [t for t in s.text_items if t.text_id != a.text_id]
    return [a.text_id], []


@tool("add_audio", AddAudioArgs, "添加独立配乐或音效，使用两条可叠加音轨。")
def add_audio(s, a):
    x = AudioItem(audio_id=uid("audio"), **a.model_dump())
    s.audio_items.append(x)
    return [x.audio_id], []


@tool("remove_audio", AudioId, "移除指定配乐及其淡变、压低规则。")
def remove_audio(s, a):
    x = find_audio(s, a.audio_id)
    s.audio_items.remove(x)
    return [x.audio_id], []


@tool("set_audio_gain", GainArgs, "以 dB 设置配乐或片段原声音量。")
def gain(s, a):
    if a.target == "original":
        find_clip(s, a.target_id).original_audio_gain_db = a.gain_db
    else:
        find_audio(s, a.target_id).gain_db = a.gain_db
    return [a.target_id], []


@tool("set_audio_fade", FadeArgs, "设置配乐淡入淡出长度，单位为输出帧。")
def fade(s, a):
    x = find_audio(s, a.audio_id)
    x.fade_in_frames, x.fade_out_frames = a.fade_in_frames, a.fade_out_frames
    return [x.audio_id], []


@tool("duck_music_for_clip", DuckArgs, "片段期间压低指定配乐，规则随片段移动。")
def duck(s, a):
    find_clip(s, a.clip_id)
    x = find_audio(s, a.audio_id)
    x.ducks = [d for d in x.ducks if d.clip_id != a.clip_id]
    x.ducks.append(Duck(clip_id=a.clip_id, gain_db=a.gain_db, ramp_frames=a.ramp_frames))
    return [a.audio_id, a.clip_id], []


for name, description in {
    "get_project_state": "读取草稿序号、基础版本、工程及派生时间线。",
    "list_assets": "列出当前项目已标准化的素材和真实元数据。",
    "list_presets": "列出当前已实现的预设、版本和参数范围。",
    "validate_draft": "校验源区间、转场、锚点、时长及媒体引用。",
    "get_draft_diff": "比较草稿与其基础版本，返回变更对象。",
}.items():
    TOOLS[name] = ToolSpec(Empty, description)


def definitions():
    """导出所有工具的描述、参数契约和草稿写入标记。

    Returns:
        供 API、模型适配层和契约文件使用的工具列表。
    """
    return [{"name": k, "description": v.description, "writes_draft": v.handler is not None,
             "parameters": v.arguments.model_json_schema()} for k, v in TOOLS.items()]


def presets():
    """列出当前支持的效果预设及版本。

    Returns:
        按效果类型组织的预设信息。
    """
    return {"version": 1, "motion": {"ids": ["slow_push_in", "slow_pull_out", "pan_left", "pan_right", "pan_up", "pan_down"],
            "intensity": {"exclusive_min": 0, "max": 0.5}, "easing": ["linear", "ease_in_out"],
            "fit_required": "cover", "coverage": "覆盖范围在共享合成函数中解析计算，避免黑边"},
            "color": ["warm", "cool", "monochrome"], "transition": ["dissolve"], "text": ["title", "subtitle"]}
