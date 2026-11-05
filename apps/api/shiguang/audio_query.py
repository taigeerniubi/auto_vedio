"""按用户问题听取一段素材，校验模型标记并换算为母版毫秒。"""
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Literal
import wave

from pydantic import Field, StringConstraints, model_validator

from .media import DATABASE, contained, ffmpeg
from .models import Model
from .tools import ToolError


Millis = Annotated[int, Field(strict=True, ge=0)]
Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class AudioQuestion(Model):
    query: Question
    start_ms: Millis = 0
    end_ms: Millis | None = None


class QueryAudioArgs(AudioQuestion):
    asset_id: str


class AudioMark(Model):
    label: str = Field(min_length=1, max_length=120)
    start_ms: Millis
    end_ms: Millis
    anchor_ms: Millis
    anchor_role: Literal["start", "end", "peak", "point"]
    certainty: Literal["low", "medium", "high"]
    evidence: str = Field(min_length=1, max_length=400)

    @model_validator(mode="after")
    def check_anchor(self):
        if not self.start_ms <= self.anchor_ms <= self.end_ms:
            raise ValueError("锚点超出声音区间")
        if self.anchor_role == "start" and self.anchor_ms != self.start_ms:
            raise ValueError("起始锚点必须与起点一致")
        if self.anchor_role == "end" and self.anchor_ms != self.end_ms:
            raise ValueError("结束锚点必须与终点一致")
        return self


class AudioAnswer(Model):
    status: Literal["matched", "not_found", "uncertain"]
    answer: str = Field(min_length=1, max_length=2000)
    matches: list[AudioMark] = Field(max_length=100)


def query_audio(asset, question, provider_factory):
    if not asset.has_audio or asset.kind not in {"audio", "video"}:
        raise ToolError("NO_AUDIO", "该素材没有可查询的音轨")
    source_end = round(asset.total_frames * 1000 / 30)
    start = question.start_ms
    end = source_end if question.end_ms is None else question.end_ms
    if not 0 <= start < end <= source_end:
        raise ToolError("INVALID_ARGUMENTS", f"查询范围须在素材的 0–{source_end} 毫秒内，且起点早于终点")
    provider = provider_factory("audio")
    with TemporaryDirectory(prefix="shiguang-audio-query-") as folder:
        audio = Path(folder) / "excerpt.wav"
        ffmpeg("-ss", start / 1000, "-i", contained(asset.path, DATABASE), "-t", (end - start) / 1000,
               "-map", "0:a:0", "-vn", "-ac", 1, "-ar", 24000, "-c:a", "pcm_s16le", audio)
        with wave.open(str(audio), "rb") as decoded:
            duration = min(end - start, round(decoded.getnframes() * 1000 / decoded.getframerate()))
        if duration <= 0:
            raise ToolError("NO_AUDIO", "所选区间内没有可解码的音频")
        # 长素材使用限码率 MP3，20 分钟仍在百炼 Base64 输入限额内。
        if audio.stat().st_size > 7_400_000:
            compressed = audio.with_suffix(".mp3")
            ffmpeg("-i", audio, "-c:a", "libmp3lame", "-b:a", "48k", compressed)
            audio = compressed
        raw = provider.query_audio(audio, question.query, duration, AudioAnswer.model_json_schema())
    try:
        result = AudioAnswer.model_validate(raw)
        if result.status == "not_found" and result.matches:
            raise ValueError("未找到状态包含了标记")
        if any(mark.end_ms > duration for mark in result.matches):
            raise ValueError("时间超出实际音轨范围")
    except ValueError:
        raise ToolError("MODEL_ANALYSIS_INVALID", "音频回答的结构、时间或锚点与查询区间不符，请缩小范围后再试") from None
    matches = []
    for mark in sorted(result.matches, key=lambda item: item.anchor_ms):
        matches.append({**mark.model_dump(), "start_ms": start + mark.start_ms,
                        "end_ms": start + mark.end_ms, "anchor_ms": start + mark.anchor_ms})
    return {"asset_id": asset.asset_id, "timebase": "source_ms", "range": {"start_ms": start, "end_ms": start + duration},
            "timing": "model_estimate", "status": result.status, "answer": result.answer, "matches": matches}
