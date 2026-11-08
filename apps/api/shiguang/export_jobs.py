"""持久化本机导出任务、冻结工程版本并向页面提供真实渲染进度。"""
from contextvars import copy_context
from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
import re
from threading import Thread
from typing import Annotated, Literal

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import Field, field_validator

from . import media
from .models import Asset, Model, Snapshot, timeline, validate_snapshot
from .observability import log_context, log_event, sanitize
from .store import dumps
from .tools import ToolError, uid


def _filename(value):
    """规范化下载名称，禁止将文件名当作本机路径使用。"""
    name = value.strip()
    if not name or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith((".", " ")):
        raise ValueError("文件名不能包含路径或系统保留字符")
    if not name.lower().endswith(".mp4"):
        name += ".mp4"
    stem = name[:-4]
    if not stem or stem in {".", ".."} or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", stem):
        raise ValueError("文件名不可使用系统保留名称")
    return name


class ExportRequest(Model):
    expected_revision: Annotated[int, Field(strict=True, ge=0)]
    resolution: Literal["source", "720p", "1080p", "2160p"] = "source"
    quality: Literal["standard", "high", "compact"] = "standard"
    filename: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value):
        return _filename(value) if value is not None else None


def _now():
    return datetime.now(timezone.utc).isoformat()


class ExportService:
    def __init__(self, store, renderer=None):
        """组合持久化存储和可注入的本机渲染函数。"""
        self.store = store
        self.renderer = renderer or media.render_snapshot
        self.root = media.ARTIFACTS / "exports"
        with store.connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS export_jobs(
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    status TEXT NOT NULL, settings TEXT NOT NULL,
                    snapshot TEXT NOT NULL, assets TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS export_jobs_project ON export_jobs(project_id,status);
            """)

    def recover_interrupted(self):
        """服务重启后明确标记未完成任务，不把旧进度误报为仍在导出。"""
        with self.store.connect() as con:
            for row in con.execute("SELECT id,data FROM export_jobs WHERE status IN ('queued','running')").fetchall():
                data = json.loads(row["data"])
                data.update(status="failed", stage="failed", updated_at=_now(),
                            error={"code": "EXPORT_INTERRUPTED", "message": "导出因服务停止而中断，请重新导出"})
                con.execute("UPDATE export_jobs SET status='failed',data=? WHERE id=?", (dumps(data), row["id"]))

    def get(self, pid, export_id):
        """读取指定工程的导出状态，不公开冻结的素材路径与快照。"""
        self.store.project(pid)
        with self.store.connect() as con:
            row = con.execute("SELECT data FROM export_jobs WHERE project_id=? AND id=?", (pid, export_id)).fetchone()
        if not row:
            raise ToolError("EXPORT_NOT_FOUND", "导出任务不存在于当前工程")
        return json.loads(row[0])

    def latest(self, pid):
        """恢复页面刷新前最近一次导出进度或结果。"""
        self.store.project(pid)
        with self.store.connect() as con:
            row = con.execute("SELECT data FROM export_jobs WHERE project_id=? ORDER BY rowid DESC LIMIT 1", (pid,)).fetchone()
        return json.loads(row[0]) if row else None

    def submit(self, pid, request):
        """原子冻结快照与素材，提交后立即返回可轮询的导出任务。"""
        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            project = con.execute("SELECT name,head FROM projects WHERE id=?", (pid,)).fetchone()
            if not project:
                raise ToolError("PROJECT_NOT_FOUND", "项目不存在")
            if project["head"] != request.expected_revision:
                raise ToolError("REVISION_CONFLICT", "项目已变更，请刷新后重新导出")
            name = request.filename
            if name is None:
                default = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", project["name"]).strip(" .")[:100] or "成片"
                try:
                    name = _filename(default)
                except ValueError:
                    name = "成片.mp4"
            options = {"resolution": request.resolution, "quality": request.quality, "filename": name}
            settings = dumps({"revision": project["head"], **options})
            active = con.execute("SELECT settings,data FROM export_jobs WHERE project_id=? AND status IN ('queued','running') ORDER BY rowid DESC LIMIT 1", (pid,)).fetchone()
            if active:
                if active["settings"] == settings:
                    return json.loads(active["data"])
                raise ToolError("EXPORT_CONFLICT", "该工程已有导出任务，请等待完成后再更改导出设置")
            source = con.execute("SELECT snapshot FROM revisions WHERE project_id=? AND revision=?", (pid, project["head"])).fetchone()[0]
            snapshot = Snapshot.model_validate_json(source)
            assets = self.store.assets(pid, con)
            errors = validate_snapshot(snapshot, assets)
            if errors:
                raise ToolError("INVALID_DRAFT", "; ".join(errors))
            _, total = timeline(snapshot)
            if not total:
                raise ToolError("EMPTY_TIMELINE", "时间线为空，无法导出成片")
            export_id, now = uid("export"), _now()
            record = {"export_id": export_id, "project_id": pid, "revision": project["head"],
                      "status": "queued", "stage": "queued", "progress": 0,
                      "rendered_frames": 0, "total_frames": total, "options": options,
                      "created_at": now, "updated_at": now, "result": None, "error": None}
            con.execute("INSERT INTO export_jobs VALUES(?,?,?,?,?,?,?)", (
                export_id, pid, "queued", settings, snapshot.model_dump_json(),
                dumps({aid: asset.model_dump() for aid, asset in assets.items()}), dumps(record)))
        context = copy_context()
        try:
            Thread(target=context.run, args=(self._run, pid, export_id), daemon=True,
                   name=f"shiguang-{export_id}").start()
        except Exception as exc:
            self._fail(pid, export_id, exc)
        return self.get(pid, export_id)

    def _update(self, pid, export_id, **fields):
        """原子合并进度，避免已完成状态被迟到的渲染通知覆盖。"""
        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT data FROM export_jobs WHERE project_id=? AND id=?", (pid, export_id)).fetchone()
            if not row:
                return
            record = json.loads(row[0])
            if record["status"] in {"completed", "failed"}:
                return
            record.update(fields, updated_at=_now())
            con.execute("UPDATE export_jobs SET status=?,data=? WHERE id=?", (record["status"], dumps(record), export_id))

    def _run(self, pid, export_id):
        """后台执行已冻结的工程，成功验收后才公开下载入口。"""
        with log_context(project_id=pid, export_id=export_id):
            try:
                with self.store.connect() as con:
                    row = con.execute("SELECT snapshot,assets,data FROM export_jobs WHERE id=?", (export_id,)).fetchone()
                record = json.loads(row["data"])
                snapshot = Snapshot.model_validate_json(row["snapshot"])
                assets = {key: Asset.model_validate(value) for key, value in json.loads(row["assets"]).items()}
                self._update(pid, export_id, status="running", stage="preparing", started_at=_now())
                log_event("export_started", resolution=record["options"]["resolution"], quality=record["options"]["quality"],
                          revision=record["revision"], total_frames=record["total_frames"])
                output = media.contained(self.root / pid / f"{export_id}.mp4", self.root)
                progress = 0.0

                def report(update):
                    nonlocal progress
                    fields = {}
                    stage = update.get("stage")
                    if stage in {"preparing", "bundling", "rendering", "muxing", "verifying", "completed"}:
                        fields["stage"] = "verifying" if stage == "completed" else stage
                    value = update.get("progress")
                    if isinstance(value, (int, float)) and math.isfinite(value):
                        progress = max(progress, min(99.9, max(0, value)))
                        fields["progress"] = progress
                    value = update.get("rendered_frames")
                    if isinstance(value, int) and not isinstance(value, bool):
                        fields["rendered_frames"] = max(0, min(record["total_frames"], value))
                    self._update(pid, export_id, **fields)

                rendered = self.renderer(snapshot, assets, output,
                    resolution=record["options"]["resolution"], quality=record["options"]["quality"], on_progress=report)
                if not output.is_file():
                    raise ToolError("EXPORT_VERIFICATION_FAILED", "导出未生成可下载的成片文件")
                result = {**rendered, "revision": record["revision"], "filename": record["options"]["filename"],
                          "download_url": f"/api/v1/projects/{pid}/exports/jobs/{export_id}/download"}
                self._update(pid, export_id, status="completed", stage="completed", progress=100,
                             rendered_frames=record["total_frames"], result=result)
                log_event("export_completed", revision=record["revision"], total_frames=record["total_frames"])
            except Exception as exc:
                self._fail(pid, export_id, exc)

    def _fail(self, pid, export_id, error):
        """保留失败进度，返回短错误说明并将详细原因写入诊断日志。"""
        code = error.code if isinstance(error, ToolError) else "EXPORT_FAILED"
        if isinstance(error, ToolError) and code not in {"MEDIA_PROCESS_FAILED", "ENCODE_FAILED"}:
            message = sanitize(str(error))[:1000]
        else:
            message = "导出失败，请重试；详细原因已记录到本机日志"
        self._update(pid, export_id, status="failed", stage="failed", error={"code": code, "message": message})
        log_event("export_failed", level=logging.ERROR, error=error, project_id=pid, export_id=export_id, error_code=code)

    def download(self, pid, export_id):
        """仅为已成功验收的任务返回最终文件与用户选择的下载名称。"""
        job = self.get(pid, export_id)
        if job["status"] != "completed":
            raise ToolError("EXPORT_CONFLICT", "导出尚未完成，暂时无法下载")
        output = media.contained(self.root / pid / f"{export_id}.mp4", self.root)
        if not output.is_file():
            raise ToolError("EXPORT_NOT_FOUND", "导出文件不存在，请重新导出")
        return output, job["result"]["filename"]


def export_router(service):
    """注册导出设置、后台进度恢复与验收后的下载接口。"""
    router = APIRouter(prefix="/api/v1")

    @router.post("/projects/{pid}/exports", status_code=202)
    def submit(pid: str, req: ExportRequest):
        return service.submit(pid, req)

    @router.get("/projects/{pid}/exports/jobs/latest")
    def latest(pid: str):
        return service.latest(pid)

    @router.get("/projects/{pid}/exports/jobs/{export_id}")
    def get(pid: str, export_id: str):
        return service.get(pid, export_id)

    @router.get("/projects/{pid}/exports/jobs/{export_id}/download")
    def download(pid: str, export_id: str):
        path, filename = service.download(pid, export_id)
        return FileResponse(path, media_type="video/mp4", filename=filename)

    return router
