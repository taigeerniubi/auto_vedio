"""素材分析、预设目录与语音输入的本机接口。"""
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import APIRouter, Request, Query, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from .analysis import AnalysisService, analysis_records, search_segments
from .audio_query import AudioQuestion
from .http_status import error_status
from .media import ARTIFACTS, contained, ffmpeg, probe
from .models import Model
from .perception_provider import perception_status
from .tools import ToolError, presets


class AnalysisRequest(Model):
    visual: bool = True
    speech: bool = True
    force: bool = False


class InspectRequest(Model):
    start_frame: int = Field(ge=0, strict=True)
    end_frame: int = Field(gt=0, strict=True)


def analysis_router(store, service: AnalysisService):
    router = APIRouter(prefix="/api/v1")

    @router.get("/perception-status")
    def status():
        return perception_status()

    @router.get("/presets")
    def preset_list():
        return presets()

    @router.get("/projects/{pid}/analyses")
    def analyses(pid: str):
        store.project(pid)
        return analysis_records(store, pid)

    @router.get("/projects/{pid}/segments")
    def segments(pid: str, query: str = Query(default="", max_length=200)):
        store.project(pid)
        return search_segments(store, pid, query)

    @router.get("/projects/{pid}/assets/{aid}/analysis")
    def get_analysis(pid: str, aid: str):
        return service.get(pid, aid)

    @router.post("/projects/{pid}/assets/{aid}/analysis")
    def analyze(pid: str, aid: str, req: AnalysisRequest, tasks: BackgroundTasks, background: bool = False):
        if background:
            asset, record = service.reserve(pid, aid, **req.model_dump())
            tasks.add_task(service.complete, asset, record)
            return record
        record = service.analyze(pid, aid, **req.model_dump())
        failed = [value for kind, value in record["stages"].items() if getattr(req, kind) and value["status"] in {"failed", "partial"}]
        if failed:
            problem = failed[0]
            return JSONResponse(status_code=error_status(problem["reason_code"]), content={"ok": False,
                "error_code": problem["reason_code"], "summary": problem.get("message", "部分分析失败，已保存就绪结果"), "analysis": record})
        return record

    @router.post("/projects/{pid}/assets/{aid}/audio-query")
    def query_audio(pid: str, aid: str, req: AudioQuestion):
        return service.query_audio(pid, aid, req)

    @router.post("/projects/{pid}/assets/{aid}/inspect")
    def inspect(pid: str, aid: str, req: InspectRequest):
        return service.inspect(pid, aid, req.start_frame, req.end_frame)

    @router.get("/projects/{pid}/assets/{aid}/analysis-frames/{analysis_id}/{frame_id}")
    def frame(pid: str, aid: str, analysis_id: str, frame_id: str):
        service.asset(pid, aid)
        with store.connect() as con:
            row = con.execute("SELECT data FROM analysis_versions WHERE id=? AND project_id=? AND asset_id=?", (analysis_id, pid, aid)).fetchone()
        if not row:
            raise ToolError("ANALYSIS_NOT_FOUND", "分析记录不存在")
        import json
        sample = next((f for f in json.loads(row[0])["frame_samples"] if f["frame_id"] == frame_id), None)
        if not sample:
            raise ToolError("FRAME_NOT_FOUND", "证据帧不存在")
        return FileResponse(contained(ARTIFACTS / sample["image_key"], ARTIFACTS / "analysis"), media_type="image/jpeg")

    @router.post("/projects/{pid}/voice-inputs")
    async def voice(pid: str, request: Request):
        store.project(pid)
        provider = service.provider_factory("speech")
        with TemporaryDirectory(prefix="shiguang-voice-") as folder:
            source = Path(folder) / "input.audio"
            count = 0
            with source.open("wb") as output:
                async for chunk in request.stream():
                    count += len(chunk)
                    if count > 12 * 1024 * 1024:
                        raise ToolError("MEDIA_TOO_LARGE", "录音不能超过 12 MB")
                    output.write(chunk)
            if not count:
                raise ToolError("INVALID_ARGUMENTS", "录音为空")
            def transcribe():
                data = probe(source)
                seconds = float(data.get("format", {}).get("duration", 0))
                if seconds > 125:
                    raise ToolError("INVALID_ARGUMENTS", "单次口述最长两分钟")
                target = Path(folder) / "voice.wav"
                ffmpeg("-i", source, "-t", 125, "-vn", "-ac", 1, "-ar", 16000, "-c:a", "pcm_s16le", target)
                # WebM 可能没有 duration，解码后再检查，不能静默截断长录音。
                import wave
                with wave.open(str(target)) as audio:
                    if audio.getnframes() / audio.getframerate() > 120.5:
                        raise ToolError("INVALID_ARGUMENTS", "单次口述最长两分钟")
                text = provider.transcribe(target, timed=False)["text"].strip()
                if not text or len(text) > 8000:
                    raise ToolError("VOICE_EMPTY", "未识别到可用的剪辑指令，请重新录音或输入文字")
                return {"text": text}
            return await run_in_threadpool(transcribe)

    return router
