"""抽帧、语音转写、证据校验与可恢复的素材分析存储。"""
import base64
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from .media import ARTIFACTS, DATABASE, contained, ffmpeg
from .models import Model, Unit
from .perception_provider import configured_perception
from .tools import ToolError, uid
from .audio_query import query_audio


class Subject(Model):
    label: str = Field(min_length=1, max_length=200)
    frame_id: str
    bbox: tuple[Unit, Unit, Unit, Unit] | None = None

    @model_validator(mode="after")
    def bounds(self):
        if self.bbox and (self.bbox[0] + self.bbox[2] > 1 or self.bbox[1] + self.bbox[3] > 1):
            raise ValueError("主体坐标越界")
        return self


class Action(Model):
    description: str = Field(min_length=1, max_length=300)
    evidence_frame_ids: list[str] = Field(min_length=2, max_length=9)


class Issue(Model):
    type: Literal["blur", "dark", "bright", "occluded", "subject_cutoff"]
    evidence_frame_ids: list[str] = Field(min_length=1, max_length=9)


class Visual(Model):
    description: str = Field(min_length=1, max_length=1000)
    tags: list[str] = Field(max_length=20)
    subjects: list[Subject] = Field(max_length=15)
    actions: list[Action] = Field(max_length=15)
    shot_scale: Literal["wide", "medium", "close_up", "mixed", "unknown"]
    apparent_motion: Literal["still", "horizontal", "vertical", "zoom_like", "unstable", "mixed", "unknown"]
    issues: list[Issue] = Field(max_length=15)
    needs_review: bool
    review_reasons: list[str] = Field(max_length=10)


def validate_visual(value, frame_ids):
    """校验模型字段与本次实际观察的证据帧。

    Args:
        value: 模型返回的结构化描述。
        frame_ids: 实际发送的帧编号。

    Returns:
        合法的视觉描述字典。
    """
    try:
        visual = Visual.model_validate(value)
        refs = [s.frame_id for s in visual.subjects]
        for observation in [*visual.actions, *visual.issues]:
            refs.extend(observation.evidence_frame_ids)
        if not set(refs) <= set(frame_ids):
            raise ValueError("证据帧不属于本次采样")
        if any(len(set(a.evidence_frame_ids)) < 2 for a in visual.actions):
            raise ValueError("动作缺少连续帧证据")
        if visual.needs_review != bool(visual.review_reasons):
            raise ValueError("复核状态与原因不一致")
        return visual.model_dump()
    except (ValueError, ValidationError):
        raise ToolError("MODEL_ANALYSIS_INVALID", "视觉结果未通过字段、坐标或证据帧校验") from None


def analysis_records(store, pid, con=None):
    if con is None:
        with store.connect() as connection:
            return analysis_records(store, pid, connection)
    return {row["asset_id"]: json.loads(row["data"]) for row in con.execute(
        "SELECT asset_id,data FROM asset_analysis WHERE project_id=?", (pid,))}


def search_segments(store, pid, query="", con=None):
    """在项目内按关键词查找已就绪的画面和对白。

    Args:
        store: 项目存储。
        pid: 项目编号。
        query: 空格分隔的关键词；空值列出画面候选。
        con: 可选的已有事务连接。

    Returns:
        最多一百条带源区间的候选。
    """
    terms = query.casefold().split()
    matches = []
    for aid, record in analysis_records(store, pid, con).items():
        for segment in record.get("segments", []):
            visual = segment.get("visual")
            if not visual:
                continue
            text = json.dumps(visual, ensure_ascii=False).casefold()
            if all(term in text for term in terms):
                matches.append({"asset_id": aid, "analysis_id": record["analysis_id"], **segment})
        for speech in record.get("speech", {}).get("utterances", []):
            if query and all(term in speech["text"].casefold() for term in terms):
                matches.append({"asset_id": aid, "analysis_id": record["analysis_id"],
                    "start_frame": speech["start_ms"] * 30 // 1000,
                    "end_frame": math.ceil(speech["end_ms"] * 30 / 1000), "speech": speech["text"]})
    return matches[:100]


class AnalysisService:
    def __init__(self, store, provider_factory=configured_perception):
        self.store = store
        self.provider_factory = provider_factory

    def recover_interrupted(self):
        """启动时释放上次进程中断的分析，不自动重放模型请求。"""
        with self.store.connect() as con:
            for row in con.execute("SELECT project_id,asset_id,data FROM asset_analysis").fetchall():
                record = json.loads(row["data"])
                interrupted = False
                for stage in record["stages"].values():
                    if stage["status"] == "running":
                        stage.update(status="failed", reason_code="ANALYSIS_INTERRUPTED", message="上次分析因服务停止而中断，请继续未完成阶段")
                        interrupted = True
                if record.get("status") == "running":
                    record["status"] = "failed"
                    interrupted = True
                if interrupted:
                    con.execute("UPDATE asset_analysis SET data=? WHERE project_id=? AND asset_id=?",
                                (json.dumps(record, ensure_ascii=False), row["project_id"], row["asset_id"]))

    def asset(self, pid, aid):
        self.store.project(pid)
        asset = self.store.assets(pid).get(aid)
        if not asset:
            raise ToolError("ASSET_NOT_FOUND", "该工程中没有此素材")
        return asset

    def get(self, pid, aid):
        self.asset(pid, aid)
        return analysis_records(self.store, pid).get(aid)

    def query_audio(self, pid, aid, question):
        return query_audio(self.asset(pid, aid), question, self.provider_factory)

    def save(self, pid, aid, record, *, publish=False):
        with self.store.connect() as con:
            con.execute("INSERT INTO asset_analysis VALUES(?,?,?) ON CONFLICT(project_id,asset_id) DO UPDATE SET data=excluded.data",
                        (pid, aid, json.dumps(record, ensure_ascii=False)))
            if publish:
                con.execute("INSERT INTO analysis_versions VALUES(?,?,?,?)",
                    (record["analysis_id"], pid, aid, json.dumps(record, ensure_ascii=False)))

    def blank(self, asset):
        return {"schema_version": 1, "analysis_id": uid("analysis"), "asset_id": asset.asset_id, "status": "pending",
            "media": {"master_sha256": asset.sha256, "width": asset.width, "height": asset.height,
                      "fps": 30, "total_frames": asset.total_frames, "has_audio": asset.has_audio},
            "stages": {k: {"status": "pending", "reason_code": None} for k in ("visual", "speech")},
            "provenance": {"pipeline_version": "1.0", "visual_model": None, "visual_prompt_version": "1",
                           "asr_model": None, "created_at": datetime.now(timezone.utc).isoformat()},
            "frame_samples": [], "segments": [],
            "speech": {"presence": "unknown", "language": None, "utterances": []}}

    def analyze(self, pid, aid, *, visual=True, speech=True, force=False):
        asset, record = self.reserve(pid, aid, visual=visual, speech=speech, force=force)
        return self.complete(asset, record)

    def reserve(self, pid, aid, *, visual=True, speech=True, force=False):
        """在响应前占用分析任务，避免重复提交产生并发模型请求。"""
        asset = self.asset(pid, aid)
        if not visual and not speech:
            raise ToolError("INVALID_ARGUMENTS", "至少选择一种分析")
        # 持久化占用标记，避免双击或并发运行重复产生模型费用。
        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT data FROM asset_analysis WHERE project_id=? AND asset_id=?", (pid, aid)).fetchone()
            record = json.loads(row[0]) if row else self.blank(asset)
            if record.get("status") == "running" or any(s["status"] == "running" for s in record["stages"].values()):
                raise ToolError("ANALYSIS_CONFLICT", "该素材正在分析，请等待完成")
            record["analysis_id"] = uid("analysis")
            record["status"] = "running"
            for kind, enabled in (("visual", visual), ("speech", speech)):
                if enabled and (force or record["stages"][kind]["status"] not in {"succeeded", "skipped"}):
                    record["stages"][kind] = {"status": "running", "reason_code": None}
                    if force and kind == "visual":
                        record["segments"], record["frame_samples"] = [], []
            con.execute("INSERT INTO asset_analysis VALUES(?,?,?) ON CONFLICT(project_id,asset_id) DO UPDATE SET data=excluded.data",
                        (pid, aid, json.dumps(record, ensure_ascii=False)))
        record["provenance"]["created_at"] = datetime.now(timezone.utc).isoformat()
        return asset, record

    def complete(self, asset, record):
        """处理已占用任务，逐阶段保存进度并发布不可变结果。"""
        pid, aid = asset.project_id, asset.asset_id
        for kind in ("visual", "speech"):
            if record["stages"][kind]["status"] != "running":
                continue
            try:
                if kind == "visual" and asset.kind == "audio" or kind == "speech" and not asset.has_audio:
                    record["stages"][kind] = {"status": "skipped", "reason_code": "no_video" if kind == "visual" else "no_audio_track"}
                    if kind == "speech":
                        record["speech"] = {"presence": "not_detected", "language": None, "utterances": []}
                else:
                    provider = self.provider_factory("vision" if kind == "visual" else "speech")
                    if kind == "visual":
                        record["provenance"]["visual_model"] = provider.config.model
                        self.visual(asset, record, provider)
                    else:
                        record["provenance"]["asr_model"] = provider.config.model
                        record["speech"] = self.speech(asset, provider)
                    record["stages"][kind] = {"status": "succeeded", "reason_code": None}
            except Exception as exc:
                from .observability import log_event
                import logging
                log_event("analysis_stage_failed", level=logging.ERROR, error=exc, project_id=pid, asset_id=aid, stage=kind)
                code = exc.code if isinstance(exc, ToolError) else "ANALYSIS_INTERNAL_ERROR"
                record["stages"][kind] = {"status": "partial" if kind == "visual" and any(s.get("visual") for s in record["segments"]) else "failed",
                                          "reason_code": code, "message": str(exc) if isinstance(exc, ToolError) else "素材分析失败，请重试"}
                if kind == "speech":
                    record["speech"] = {"presence": "unknown", "language": None, "utterances": []}
            self.save(pid, aid, record)
        failed = any(s["status"] in {"failed", "partial"} for s in record["stages"].values())
        ready = any(s["status"] in {"succeeded", "skipped"} for s in record["stages"].values())
        record["status"] = ("partial" if ready else "failed") if failed else "succeeded"
        self.save(pid, aid, record, publish=True)
        return record

    def visual(self, asset, record, provider):
        total = max(1, asset.total_frames)
        window = max(300, math.ceil(total / 24))
        if not record["segments"]:
            for start in range(0, total, window):
                end = min(total, start + window)
                record["segments"].append({"segment_id": f"segment_{start}", "start_frame": start, "end_frame": end,
                                           "frame_ids": [], "status": "pending", "visual": None})
        for segment in record["segments"]:
            if segment["status"] == "succeeded":
                continue
            start, end = segment["start_frame"], segment["end_frame"]
            try:
                frames, visual = self.describe_range(asset, start, end, provider, 3, record["analysis_id"])
                segment.update(frame_ids=[f["frame_id"] for f in frames], status="succeeded", visual=visual)
                record["frame_samples"].extend(frames)
                self.save(asset.project_id, asset.asset_id, record)
            except Exception:
                segment["status"] = "failed"
                raise

    def describe_range(self, asset, start, end, provider, samples, token):
        """抽取指定源区间的真实画面并请求视觉描述。

        Args:
            asset: 已标准化的项目素材。
            start: 起始帧，包含该帧。
            end: 结束帧，不包含该帧。
            provider: 独立视觉服务。
            samples: 最多抽取的帧数。
            token: 本次分析的存储目录标识。

        Returns:
            抽帧清单和已通过证据校验的描述。
        """
        path = contained(asset.path, DATABASE)
        folder = contained(ARTIFACTS / "analysis" / asset.project_id / asset.asset_id / token, ARTIFACTS / "analysis")
        folder.mkdir(parents=True, exist_ok=True)
        indices = [0] if asset.kind == "image" else sorted({start + (end - start - 1) * i // max(1, samples - 1) for i in range(samples)})
        frames, images = [], []
        for index in indices:
            frame_id = f"frame_{index}"
            target = folder / f"{frame_id}.jpg"
            ffmpeg("-ss", str(index / 30), "-i", path, "-frames:v", 1, "-vf", "scale=768:768:force_original_aspect_ratio=decrease", target)
            frames.append({"frame_id": frame_id, "frame_index": index, "image_key": target.relative_to(ARTIFACTS).as_posix()})
            images.append((frame_id, "data:image/jpeg;base64," + base64.b64encode(target.read_bytes()).decode()))
        value = validate_visual(provider.describe(images, Visual.model_json_schema()), [f["frame_id"] for f in frames])
        return frames, value

    def inspect(self, pid, aid, start, end):
        asset = self.asset(pid, aid)
        if asset.kind != "video" or not 0 <= start < end <= asset.total_frames or end - start > 900:
            raise ToolError("INVALID_ARGUMENTS", "精查区间须位于视频内，最长 30 秒")
        provider = self.provider_factory("vision")
        token = uid("inspection")
        frames, visual = self.describe_range(asset, start, end, provider, 9, token)
        result = {"analysis_id": token, "asset_id": aid, "start_frame": start, "end_frame": end,
                  "frame_samples": frames, "visual": visual}
        with self.store.connect() as con:
            con.execute("INSERT INTO analysis_versions VALUES(?,?,?,?)", (token, pid, aid, json.dumps(result, ensure_ascii=False)))
        return result

    def speech(self, asset, provider):
        """分块识别对白，并恢复到统一母版时间位置。

        Args:
            asset: 含音轨的标准化素材。
            provider: 独立语音服务。

        Returns:
            带毫秒区间、语言和检测状态的对白。
        """
        utterances, language = [], None
        # 每块最多五分钟，16kHz 单声道 PCM，小于常见 25 MB 上传限制。
        with TemporaryDirectory(prefix="shiguang-speech-") as temp:
            for start in range(0, asset.total_frames, 9000):
                frames = min(9000, asset.total_frames - start)
                target = Path(temp) / "speech.wav"
                ffmpeg("-ss", str(start / 30), "-i", contained(asset.path, DATABASE), "-t", str(frames / 30),
                       "-vn", "-ar", 16000, "-ac", 1, "-c:a", "pcm_s16le", target)
                result = provider.transcribe(target)
                language = result.get("language") or language
                previous = 0
                for item in result.get("segments", []):
                    try:
                        lo, hi = float(item["start"]), float(item["end"])
                        text = item["text"].strip()
                        if not (math.isfinite(lo) and math.isfinite(hi) and 0 <= lo < hi <= frames / 30 + .05 and lo >= previous):
                            raise ValueError("invalid time")
                        if not text or len(text) > 1000:
                            raise ValueError("invalid text")
                        previous = lo
                    except (KeyError, TypeError, ValueError):
                        raise ToolError("MODEL_ANALYSIS_INVALID", "语音返回的句子或时间戳越界") from None
                    utterances.append({"utterance_id": uid("utterance"), "start_ms": round(start / 30 * 1000 + lo * 1000),
                        "end_ms": min(math.floor(asset.total_frames / 30 * 1000), round(start / 30 * 1000 + hi * 1000)), "text": text})
        return {"presence": "detected" if utterances else "not_detected", "language": language, "utterances": utterances}
