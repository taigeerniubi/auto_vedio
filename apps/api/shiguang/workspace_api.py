"""提供前端所需的工程列表、素材浏览、预览资源和文件下载接口。"""
import json
import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from .media import ARTIFACTS, DATABASE, contained, ffmpeg, import_media, prepare_render
from .models import Model, Snapshot
from .tools import ToolError, uid


class PreviewRequest(Model):
    expected_revision: Annotated[int, Field(strict=True, ge=0)]


def workspace_router(store):
    """创建与工程存储绑定的工作台路由。

    Args:
        store: 工程与素材存储。

    Returns:
        可安装到 FastAPI 的路由集合。
    """
    router = APIRouter(prefix="/api/v1")

    @router.get("/projects")
    def projects():
        """列出工程名称和当前版本。

        Returns:
            最近创建的工程优先排列的列表。
        """
        with store.connect() as c:
            return [dict(r) for r in c.execute("SELECT id AS project_id,name,head AS revision FROM projects ORDER BY rowid DESC LIMIT 200")]

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
        """扫描素材目录中的可导入文件，跳过托管副本和隐藏目录。

        Returns:
            最多五百个文件的相对路径、名称与大小。
        """
        found = []
        for root, directories, filenames in os.walk(DATABASE, followlinks=False):
            directories[:] = sorted(d for d in directories if d != "managed" and not d.startswith("."))
            for name in sorted(filenames):
                file = Path(root) / name
                if file.suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm", ".png", ".jpg", ".jpeg", ".wav", ".mp3", ".m4a", ".aac"}:
                    continue
                if not file.resolve().is_relative_to(DATABASE.resolve()) or not file.is_file():
                    continue
                found.append({"path": file.relative_to(DATABASE).as_posix(), "name": name, "size": file.stat().st_size})
                if len(found) >= 500:
                    return found
        return found

    @router.post("/projects/{pid}/assets/upload", status_code=201)
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
        name = Path(filename.replace("\\", "/")).name
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
    def thumbnail(pid: str, aid: str):
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
        target = contained(ARTIFACTS / "thumbnails" / f"{aid}.jpg", ARTIFACTS / "thumbnails")
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(uid("thumb") + ".jpg")
            try:
                ffmpeg("-i", path, "-frames:v", 1, "-vf", "scale=320:180:force_original_aspect_ratio=decrease,pad=320:180:(ow-iw)/2:(oh-ih)/2", temp)
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
        token = uid("preview")
        folder = contained(ARTIFACTS / "previews" / pid / token, ARTIFACTS / "previews")
        plan, warnings = prepare_render(Snapshot.model_validate(project["snapshot"]), store.assets(pid), folder, token)
        allowed = [clip["file"] for clip in plan["clips"]] + [plan["mix_file"]]
        (folder / "allowed.json").write_text(json.dumps(allowed), encoding="utf-8")
        prefix = f"/api/v1/projects/{pid}/previews/{token}/files/"
        for clip in plan["clips"]:
            clip["file"] = prefix + clip["file"]
        plan["mix_file"] = prefix + plan["mix_file"]
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
        if not path.is_file() or path.suffix != ".mp4":
            raise ToolError("EXPORT_NOT_FOUND", "导出文件不存在")
        return FileResponse(path, media_type="video/mp4", filename=filename)

    return router
