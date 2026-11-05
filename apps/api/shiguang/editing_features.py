"""注册成片风格、画幅与自动字幕工具。"""
from math import ceil
from typing import Literal
from pydantic import Field
from .models import Composition, Model, Motion, TextItem, Anchor, Transition
from .tools import TOOLS, ToolSpec, ToolError, tool
from .audio_query import QueryAudioArgs

STYLES = [
    {"id": "calm_travel", "name": "舒缓旅行", "description": "较长镜头、轻柔叠化、暖色和少量缓推", "shot_seconds": [4, 7]},
    {"id": "bright_daily", "name": "轻快日常", "description": "短镜头、直接切换、原色与适量平移", "shot_seconds": [2, 4]},
    {"id": "ambient", "name": "保留现场声", "description": "克制运镜、保留原声、压低配乐", "shot_seconds": [5, 9]},
]

class StyleArgs(Model):
    preset_id: Literal["calm_travel", "bright_daily", "ambient"]

@tool("apply_style_preset", StyleArgs, "对现有镜头应用整片风格：运镜、色调、转场、配乐音量。先按风格组织镜头长度；此工具不改变取段。")
def apply_style(s, a):
    if not s.clips:
        raise ToolError("EMPTY_TIMELINE", "请先组装画面片段")
    s.transitions = []
    for index, clip in enumerate(s.clips):
        clip.color_preset = "warm" if a.preset_id == "calm_travel" else None
        clip.motion = None
        if a.preset_id != "ambient" and index % 3 == 0 and clip.transform.fit == "cover":
            clip.motion = Motion(preset_id="slow_push_in" if a.preset_id == "calm_travel" else "pan_right",
                                 local_end_frame=clip.duration, intensity=.08)
        if a.preset_id == "ambient":
            clip.original_audio_gain_db = 0
        if index and a.preset_id == "calm_travel":
            previous = s.clips[index - 1]
            length = min(15, (previous.duration - 1) // 3, (clip.duration - 1) // 3)
            if length:
                s.transitions.append(Transition(left_clip_id=previous.clip_id, right_clip_id=clip.clip_id, duration_frames=length))
    # 转场会缩短成片；时间线锚点超出的配乐与标题同步收短。
    from .models import timeline
    total = timeline(s)[1]
    for collection in (s.audio_items, s.text_items):
        collection[:] = [i for i in collection if i.anchor.kind != "timeline" or i.anchor.start_frame < total]
        for item in collection:
            if item.anchor.kind == "timeline":
                item.anchor.end_frame = min(item.anchor.end_frame, total)
    for item in s.audio_items:
        item.gain_db = -24 if a.preset_id == "ambient" else -16 if a.preset_id == "calm_travel" else -12
        item.fade_in_frames = item.fade_out_frames = min(30, (item.anchor.end_frame - item.anchor.start_frame) // 2)
    return [c.clip_id for c in s.clips], []

@tool("set_composition", Composition, "调整横竖屏和画布尺寸；预览与导出使用相同画幅。")
def composition(s, a):
    s.composition = a
    return [], []

class SubtitleArgs(Model):
    clip_ids: list[str] = Field(default_factory=list, max_length=40)

def generate_subtitles(s, args, records):
    """将句级对白映射成可随片段移动的字幕。

    Args:
        s: 隔离草稿，失败时由外层事务放弃。
        args: 需要生成字幕的片段编号。
        records: 当前工程中的素材分析结果。

    Returns:
        新增字幕编号和需要试听校正的提示。
    """
    selected = set(args.clip_ids) or {c.clip_id for c in s.clips if c.kind == "video"}
    if not selected or selected - {c.clip_id for c in s.clips}:
        raise ToolError("CLIP_NOT_FOUND", "请选择有效的视频片段")
    added = []
    for clip in s.clips:
        if clip.clip_id not in selected:
            continue
        if clip.kind != "video" or clip.reverse:
            raise ToolError("UNSUPPORTED_OPERATION", "自动字幕只支持正放视频；倒放片段不会伪造对白字幕")
        record = records.get(clip.asset_id)
        if not record or record["stages"]["speech"]["status"] not in {"succeeded", "skipped"}:
            raise ToolError("ANALYSIS_REQUIRED", "请先完成所选视频的语音分析")
        prefix = f"asr_{clip.clip_id}_"
        s.text_items = [t for t in s.text_items if not t.text_id.startswith(prefix)]
        for index, line in enumerate(record["speech"]["utterances"]):
            lo = max(clip.source_in_frame, line["start_ms"] * 30 // 1000)
            hi = min(clip.source_out_frame, ceil(line["end_ms"] * 30 / 1000))
            if lo >= hi:
                continue
            start = ceil((lo - clip.source_in_frame) * clip.rate_den / clip.rate_num)
            end = min(clip.duration, ceil((hi - clip.source_in_frame) * clip.rate_den / clip.rate_num))
            if start >= end:
                continue
            item = TextItem(text_id=prefix + str(index), text=line["text"],
                anchor=Anchor(kind="clip", clip_id=clip.clip_id, start_frame=start, end_frame=end),
                style_preset="subtitle", y=.88, font_size=max(16, min(s.composition.width, s.composition.height) * .045))
            s.text_items.append(item)
            added.append(item.text_id)
    return added, ["句级字幕已按裁切和变速映射；请试听检查被裁切句子的文字边界。"]

TOOLS["generate_subtitles"] = ToolSpec(SubtitleArgs, "根据已完成的语音分析生成片段绑定字幕；空 clip_ids 表示所有视频。重复生成替换自动字幕，保留手动标题。", generate_subtitles)

class SearchArgs(Model):
    query: str = Field(default="", max_length=200)

class AssetArgs(Model):
    asset_id: str

class AnalyzeArgs(AssetArgs):
    visual: bool = True
    speech: bool = True

class InspectArgs(AssetArgs):
    start_frame: int = Field(ge=0, strict=True)
    end_frame: int = Field(gt=0, strict=True)

for name, args, description in [
    ("query_audio", QueryAudioArgs, "根据任意问题听取音频素材或视频原声。可选 start_ms/end_ms 为素材母版毫秒；回答和候选锚点是模型估计，仅查询，不改草稿。"),
    ("search_segments", SearchArgs, "按画面描述、标签或对白关键词检索已分析片段；返回真实源帧区间，空词列出候选。"),
    ("get_asset_analysis", AssetArgs, "读取素材的视觉证据、对白时间戳和各阶段状态。"),
    ("analyze_asset", AnalyzeArgs, "通过已配置视觉/语音模型分析素材，缓存已完成阶段；调用可能耗时。"),
    ("inspect_segment", InspectArgs, "对最多 30 秒的视频区间密集抽取九帧精查；不是逐帧动作定位。"),
]:
    TOOLS[name] = ToolSpec(args, description)
