"""提供前端所需的工程列表、素材浏览、预览资源和文件下载接口。"""
import json
import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from .media import ARTIFACTS, DATABASE, contained, ffmpeg, import_media
from .preview import prepare_preview
from .models import Model, Snapshot
from .tools import ToolError, uid
from .savepoints import SavepointService, latest_savepoint


class PreviewRequest(Model):
    expected_revision: Annotated[int, Field(strict=True, ge=0)]


class SavepointRequest(PreviewRequest):
    request_id: str = Field(min_length=1, max_length=128)


class ReuseAssetRequest(Model):
    source_project_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    source_asset_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


def workspace_router(store):
    """创建与工程存储绑定的工作台路由。

    Args:
        store: 工程与素材存储。

    Returns:
        可安装到 FastAPI 的路由集合。
    """
    router = APIRouter(prefix="/api/v1")
    savepoints = SavepointService(store)

    @router.get("/projects")
    def projects():
        """列出工程名称和当前版本。

        Returns:
            最近创建的工程优先排列的列表。
        """
        with store.connect() as c:
            rows = c.execute("SELECT id AS project_id,name,head AS revision FROM projects ORDER BY rowid DESC LIMIT 200").fetchall()
            return [{**dict(r), "last_savepoint": latest_savepoint(c, r["project_id"])} for r in rows]

    @router.get("/projects/{pid}/savepoints")
    def list_savepoints(pid: str):
        return savepoints.list(pid)

    @router.post("/projects/{pid}/savepoints")
    def create_savepoint(pid: str, req: SavepointRequest):
        return savepoints.create(pid, req.expected_revision, req.request_id)

    @router.post("/projects/{pid}/savepoints/{savepoint_id}/restore")
    def restore_savepoint(pid: str, savepoint_id: str, req: PreviewRequest):
        return savepoints.restore(pid, savepoint_id, req.expected_revision)

    @router.delete("/projects/{pid}/savepoints/{savepoint_id}")
    def remove_savepoint(pid: str, savepoint_id: str):
        return savepoints.remove(pid, savepoint_id)

    @router.delete("/projects/{pid}")
    def delete_project(pid: str, expected_revision: int = Query(ge=0)):
        """删除用户确认版本的项目，进行中的任务由存储层统一保护。"""
        return store.delete_project(pid, expected_revision)

    @router.get("/model-status")
    def model_status():
        """返回可供界面展示的模型配置状态，不暴露密钥。

        Returns:
            服务名称、模型名和配置是否完整。
        """
        from .agent.providers import ModelConfig
        try:
            config = ModelConfig.from_env()
            return {"configured": True, "provider": config.provider, "model": config.model}
        except ToolError:
            return {"configured": False, "provider": os.getenv("SHIGUANG_MODEL_PROVIDER", "deepseek"), "model": os.getenv("SHIGUANG_MODEL_NAME", "")}

    @router.get("/library")
    def library():
        """只列出曾经导入成功的历史素材，不扫描本机目录。"""
        from .asset_library import library_history
        return library_history(store, DATABASE)

    @router.post("/projects/{pid}/assets/reuse")
    def reuse(pid: str, req: ReuseAssetRequest):
        """从已导入历史复用母版，不重新转码或调用素材分析。"""
        from .asset_library import reuse_asset
        return reuse_asset(store, pid, req.source_project_id, req.source_asset_id, DATABASE).model_dump()

    @router.get("/library/audio")
    def library_audio(path: str = Query(min_length=1, max_length=2000)):
        """直接试听素材目录内的音频，支持范围读取，不导入或转码文件。"""
        relative = Path(path)
        if relative.is_absolute() or any(part.startswith(".") or part.lower() == "managed" for part in relative.parts):
            raise ToolError("INVALID_MEDIA_FILE", "请选择素材目录中的音频文件")
        source = contained(DATABASE / relative, DATABASE)
        if source.suffix.lower() not in {".mp3", ".wav", ".m4a", ".aac"}:
            raise ToolError("UNSUPPORTED_MEDIA", "仅支持试听音频文件")
        if not source.is_file():
            raise ToolError("MEDIA_NOT_FOUND", "音频文件已移动或删除")
        return FileResponse(source)

    @router.post("/projects/{pid}/assets/upload")
    async def upload(pid: str, request: Request, filename: str = Query(min_length=1, max_length=200)):
        """接收浏览器上传的单个素材并导入工程。

        Args:
            pid: 工程编号。
            request: 含二进制文件的请求。
            filename: 原始文件名，仅用于安全命名。

        Returns:
            已导入的素材记录。
        """
        store.project(pid)
        # 先按文本取文件名，避免 Windows Path 自动丢弃 C: 之类的非法前缀。
        name = filename.replace("\\", "/").rsplit("/", 1)[-1]
        if Path(name).suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm", ".png", ".jpg", ".jpeg", ".wav", ".mp3", ".m4a", ".aac"}:
            raise ToolError("UNSUPPORTED_MEDIA", "请选择视频、图片或音频文件")
        if any(ch in name for ch in '<>:"|?*') or name in {".", ".."}:
            raise ToolError("INVALID_FILENAME", "文件名包含不支持的字符")
        folder = DATABASE / "uploads" / uid("upload")
        folder.mkdir(parents=True)
        target, count = folder / name, 0
        try:
            with target.open("wb") as output:
                async for chunk in request.stream():
                    count += len(chunk)
                    if count > 1024 ** 3:
                        raise ToolError("MEDIA_TOO_LARGE", "单个素材不能超过 1 GB")
                    output.write(chunk)
            return (await run_in_threadpool(import_media, store, pid, target.relative_to(DATABASE).as_posix())).model_dump()
        except BaseException:
            target.unlink(missing_ok=True)
            raise

    def asset_file(pid, aid):
        """根据工程归属解析素材文件。

        Args:
            pid: 工程编号。
            aid: 素材编号。

        Returns:
            素材记录及经过目录边界检查的路径。
        """
        store.project(pid)
        asset = store.assets(pid).get(aid)
        if not asset:
            raise ToolError("ASSET_NOT_FOUND", "该工程中没有此素材")
        path = contained(asset.path, DATABASE)
        if not path.is_file():
            raise ToolError("MEDIA_NOT_FOUND", "素材文件已移动或删除")
        return asset, path

    @router.get("/projects/{pid}/assets/{aid}/file")
    def media_file(pid: str, aid: str):
        """返回支持范围请求的素材内容。

        Args:
            pid: 工程编号。
            aid: 素材编号。

        Returns:
            可供浏览器播放的文件响应。
        """
        _, path = asset_file(pid, aid)
        return FileResponse(path)

    @router.get("/projects/{pid}/assets/{aid}/thumbnail")
    def thumbnail(pid: str, aid: str, pad: bool = True):
        """生成并缓存视频或图片的缩略图。

        Args:
            pid: 工程编号。
            aid: 素材编号。

        Returns:
            缩略图文件响应。
        """
        asset, path = asset_file(pid, aid)
        if asset.kind == "audio":
            raise ToolError("THUMBNAIL_NOT_FOUND", "音频没有画面缩略图")
        target = contained(ARTIFACTS / "thumbnails" / f"{aid}{'' if pad else '-source'}.jpg", ARTIFACTS / "thumbnails")
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(uid("thumb") + ".jpg")
            try:
                filters = "scale=320:180:force_original_aspect_ratio=decrease"
                if pad:
                    filters += ",pad=320:180:(ow-iw)/2:(oh-ih)/2"
                ffmpeg("-i", path, "-frames:v", 1, "-vf", filters, temp)
                temp.replace(target)
            finally:
                temp.unlink(missing_ok=True)
        return FileResponse(target, media_type="image/jpeg")

    @router.post("/projects/{pid}/preview")
    def preview(pid: str, req: PreviewRequest):
        """准备与导出共用的媒体资源供浏览器实时合成。

        Args:
            pid: 工程编号。
            req: 要预览的工程版本。

        Returns:
            带受限资源地址的 Remotion 播放清单。
        """
        project = store.project(pid)
        if project["revision"] != req.expected_revision:
            raise ToolError("REVISION_CONFLICT", "工程已变化，请刷新后预览")
        plan, warnings = prepare_preview(pid, Snapshot.model_validate(project["snapshot"]), store.assets(pid))
        return {"revision": project["revision"], "plan": plan, "warnings": warnings}

    @router.get("/projects/{pid}/previews/{token}/files/{filename}")
    def preview_file(pid: str, token: str, filename: str):
        """只提供预览清单中列出的媒体资源。

        Args:
            pid: 工程编号。
            token: 预览编号。
            filename: 清单中的媒体文件名。

        Returns:
            对应媒体文件响应。
        """
        store.project(pid)
        root = contained(ARTIFACTS / "previews" / pid, ARTIFACTS / "previews")
        folder = contained(root / token, root)
        manifest = folder / "allowed.json"
        if not manifest.is_file() or filename not in json.loads(manifest.read_text(encoding="utf-8")):
            raise ToolError("PREVIEW_NOT_FOUND", "预览资源不存在")
        return FileResponse(contained(folder / filename, folder))

    @router.get("/projects/{pid}/exports/{filename}")
    def export_file(pid: str, filename: str):
        """下载属于指定工程的已验收 MP4 成片。

        Args:
            pid: 工程编号。
            filename: 导出文件名。

        Returns:
            可下载的 MP4 文件响应。
        """
        store.project(pid)
        root = contained(ARTIFACTS / "exports" / pid, ARTIFACTS / "exports")
        path = contained(root / filename, root)
        if not path.is_file() or path.suffix != ".mp4" or path.name.endswith(".pending.mp4"):
            raise ToolError("EXPORT_NOT_FOUND", "导出文件不存在")
        # 新后台任务只能在验收完成后下载，旧版成片仍可通过原链接访问。
        with store.connect() as con:
            has_jobs = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='export_jobs'").fetchone()
            job = con.execute("SELECT status FROM export_jobs WHERE project_id=? AND id=?", (pid, path.stem)).fetchone() if has_jobs else None
        if job and job["status"] != "completed":
            raise ToolError("EXPORT_NOT_FOUND", "导出文件尚未验收完成")
        return FileResponse(path, media_type="video/mp4", filename=filename)

    @router.post("/projects/{pid}/cover/export")
    def export_cover(pid: str, req: PreviewRequest):
        from .covers import render_cover
        project = store.project(pid)
        if project["revision"] != req.expected_revision:
            raise ToolError("REVISION_CONFLICT", "工程已变化，请刷新后导出封面")
        output = contained(ARTIFACTS / "covers" / pid / f"cover-r{req.expected_revision}-{uid('image')}.png", ARTIFACTS)
        result = render_cover(Snapshot.model_validate(project["snapshot"]), store.assets(pid), output)
        return {**result, "revision": req.expected_revision, "download_url": f"/api/v1/projects/{pid}/covers/{output.name}"}

    @router.get("/projects/{pid}/covers/{filename}")
    def cover_file(pid: str, filename: str):
        store.project(pid)
        root = contained(ARTIFACTS / "covers" / pid, ARTIFACTS / "covers")
        path = contained(root / filename, root)
        if not path.is_file() or path.suffix != ".png":
            raise ToolError("COVER_NOT_FOUND", "封面图片不存在")
        return FileResponse(path, media_type="image/png", filename=filename)

    return router
