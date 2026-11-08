"""按用户问题听取一段素材，校验模型标记并换算为母版毫秒。"""
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Annotated, Literal
import wave

from pydantic import Field, StringConstraints, ValidationError, model_validator

from .media import DATABASE, contained, ffmpeg
from .models import Model
from .observability import validation_details
from .tools import ToolError


Millis = Annotated[int, Field(strict=True, ge=0)]
Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class AudioQuestion(Model):
    query: Question
    start_ms: Millis = 0
    end_ms: Millis | None = None
    include_timestamps: bool = Field(default=True, strict=True,
        description="风格、情绪、乐器等整体听感问题设为 false；寻找声音发生位置时设为 true。")
    force_refresh: bool = Field(default=False, strict=True,
        description="仅在用户明确要求重新听取或刷新分析时设为 true；默认复用同一素材与问题的结构化结果。")


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


class AudioSummary(Model):
    status: Literal["matched", "not_found", "uncertain"]
    answer: str = Field(min_length=1, max_length=2000)


class AudioAnswer(AudioSummary):
    matches: list[AudioMark] = Field(max_length=100)


_query_locks = {}
_query_locks_guard = Lock()


@contextmanager
def _query_lock(key):
    """同一进程中相同问题只允许一个付费请求，等待者复用已完成结果。"""
    with _query_locks_guard:
        entry = _query_locks.setdefault(key, [Lock(), 0])
        entry[1] += 1
    try:
        with entry[0]:
            yield
    finally:
        with _query_locks_guard:
            entry[1] -= 1
            if entry[1] == 0:
                del _query_locks[key]


def audio_query_records(store, pid, *, asset_id=None, limit=100):
    """读取当前素材指纹对应的完整音频观察，供模型先复用已知答案和母版时间。"""
    assets = store.assets(pid)
    with store.connect() as con:
        rows = con.execute("""SELECT asset_id,asset_sha256,data,created_at FROM audio_query_cache
            WHERE project_id=? AND (? IS NULL OR asset_id=?) ORDER BY created_at DESC LIMIT ?""",
            (pid, asset_id, asset_id, limit)).fetchall()
    observations = []
    for row in rows:
        asset = assets.get(row["asset_id"])
        if not asset or asset.sha256 != row["asset_sha256"]:
            continue
        saved = json.loads(row["data"])
        observations.append({"query": saved["query"], **saved["result"],
                             "provenance": saved["provenance"], "created_at": row["created_at"]})
    return observations


def cached_query_audio(store, asset, question, provider_factory):
    """按素材、模型、精确问题和查询区间缓存校验通过的结构化答案。

    Args:
        store: 当前工程数据库。
        asset: 已校验归属的源素材。
        question: 问题、母版区间及是否需要时间标记。
        provider_factory: 创建音频模型适配器的工厂。

    Returns:
        结构化答案及本次是否命中缓存；失败回答不会写入缓存。
    """
    if not asset.has_audio or asset.kind not in {"audio", "video"}:
        raise ToolError("NO_AUDIO", "该素材没有可查询的音轨")
    source_end = round(asset.total_frames * 1000 / 30)
    end = source_end if question.end_ms is None else question.end_ms
    if not 0 <= question.start_ms < end <= source_end:
        raise ToolError("INVALID_ARGUMENTS", f"查询范围须在素材的 0–{source_end} 毫秒内，且起点早于终点")
    provider = provider_factory("audio")
    config = getattr(provider, "config", None)
    provenance = {"pipeline_version": 1,
                  "provider": f"{type(provider).__module__}.{type(provider).__qualname__}",
                  "model": getattr(config, "model", ""), "base_url": getattr(config, "base_url", "")}
    # API key 和本地路径均不参与持久化元数据；换模型或源文件指纹后重新分析。
    identity = {"project_id": asset.project_id, "asset_id": asset.asset_id, "sha256": asset.sha256,
                "source_end_ms": source_end, "query": question.query.strip(), "start_ms": question.start_ms,
                "end_ms": end, "include_timestamps": question.include_timestamps, **provenance}
    key = sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with _query_lock((str(store.db.resolve()), key)):
        with store.connect() as con:
            cached = con.execute("SELECT data FROM audio_query_cache WHERE cache_key=?", (key,)).fetchone()
        if cached and not question.force_refresh:
            return {**json.loads(cached[0])["result"], "cache_hit": True}
        result = query_audio(asset, question, lambda _: provider)
        record = {"query": question.query, "result": result, "provenance": provenance}
        with store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if not con.execute("SELECT 1 FROM projects WHERE id=?", (asset.project_id,)).fetchone():
                raise ToolError("PROJECT_NOT_FOUND", "项目不存在或已被删除")
            con.execute("INSERT OR REPLACE INTO audio_query_cache VALUES(?,?,?,?,?,?)",
                        (key, asset.project_id, asset.asset_id, asset.sha256,
                         json.dumps(record, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))
        return {**result, "cache_hit": False}


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
        # 整体听感无需时间标记；使用独立响应结构，避免无关锚点导致整份回答被拒绝。
        answer_model = AudioAnswer if question.include_timestamps else AudioSummary
        raw = provider.query_audio(audio, question.query, duration, answer_model.model_json_schema())
    try:
        result = answer_model.model_validate(raw)
    except ValidationError as exc:
        details = validation_details(exc.errors())
        location = ".".join(str(part) for part in details[0]["field"])
        raise ToolError("MODEL_ANALYSIS_INVALID", f"音频模型回答格式不正确（{location or '回答'}）：{details[0]['reason']}",
                        details={"validation_errors": details}) from None
    marks = result.matches if isinstance(result, AudioAnswer) else []
    if result.status == "not_found" and marks:
        raise ToolError("MODEL_ANALYSIS_INVALID", "音频模型称未找到声音，却返回了时间标记，请重新查询",
                        details={"validation_errors": [{"field": ["matches"], "error_type": "status_conflict",
                                              "reason": "not_found requires empty matches"}]})
    for index, mark in enumerate(marks):
        if mark.end_ms > duration:
            raise ToolError("MODEL_ANALYSIS_INVALID", "音频模型返回的时间标记超出查询区间，请缩小范围后重试",
                            details={"validation_errors": [{"field": ["matches", index, "end_ms"],
                                                  "error_type": "audio_range", "reason": f"end_ms must be <= {duration}"}]})
    matches = []
    for mark in sorted(marks, key=lambda item: item.anchor_ms):
        matches.append({**mark.model_dump(), "start_ms": start + mark.start_ms,
                        "end_ms": start + mark.end_ms, "anchor_ms": start + mark.anchor_ms})
    return {"asset_id": asset.asset_id, "timebase": "source_ms", "range": {"start_ms": start, "end_ms": start + duration},
            "timing": "model_estimate" if question.include_timestamps else "not_requested",
            "status": result.status, "answer": result.answer, "matches": matches}
