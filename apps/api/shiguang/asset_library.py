"""从已登记的导入记录构建素材历史，并复用不可变母版而不重新转码。"""
import json
import os
from pathlib import Path
import shutil

from pydantic import ValidationError

from .media import contained
from .models import Asset
from .tools import ToolError, uid


def _master_path(asset, database):
    """只接受属于该素材登记目录的母版，拒绝外部路径和目录链接。"""
    database = Path(database).resolve()
    managed = contained(database / "managed", database)
    owner = contained(managed / asset.project_id / asset.asset_id, managed)
    path = contained(asset.path, database)
    expected = owner / {"video": "master.mp4", "image": "master.png", "audio": "master.wav"}[asset.kind]
    if path != expected or not path.is_file():
        raise ToolError("MEDIA_NOT_FOUND", "历史素材母版不存在或不再位于原托管目录")
    return path


def library_history(store, database, limit=500):
    """按导入时间倒序列出真实历史，不扫描素材目录中的其他文件。

    Args:
        store: 项目与素材登记存储。
        database: 本机素材根目录。
        limit: 最多返回的去重后素材数量。

    Returns:
        不包含本机路径的历史素材卡片数据。
    """
    records, seen = [], set()
    with store.connect() as con:
        rows = con.execute("""SELECT a.id,a.project_id,a.data FROM assets a
            JOIN projects p ON p.id=a.project_id ORDER BY a.rowid DESC""")
        for row in rows:
            try:
                asset = Asset.model_validate_json(row["data"])
                if (asset.asset_id, asset.project_id) != (row["id"], row["project_id"]):
                    continue
                key = (asset.sha256, asset.kind)
                if key in seen:
                    continue
                path = _master_path(asset, database)
                size = path.stat().st_size
            except (OSError, ToolError, ValidationError, ValueError):
                continue
            seen.add(key)
            records.append({"asset_id": asset.asset_id, "project_id": asset.project_id,
                            "name": asset.name, "kind": asset.kind, "size": size,
                            "width": asset.width, "height": asset.height, "total_frames": asset.total_frames})
            if len(records) >= limit:
                break
    return records


def reuse_asset(store, pid, source_project_id, source_asset_id, database):
    """将历史素材的母版复用到目标工程，不改变时间线或复制分析结果。

    Args:
        store: 项目与素材登记存储。
        pid: 目标工程编号。
        source_project_id: 历史素材所属工程。
        source_asset_id: 历史素材编号。
        database: 本机素材根目录。

    Returns:
        已存在的有效素材，或新登记的独立素材编号。
    """
    folder = target = None
    created_folder = False
    try:
        with store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            project = con.execute("SELECT head FROM projects WHERE id=?", (pid,)).fetchone()
            if not project:
                raise ToolError("PROJECT_NOT_FOUND", "项目不存在")
            row = con.execute("SELECT data FROM assets WHERE project_id=? AND id=?", (source_project_id, source_asset_id)).fetchone()
            if not row:
                raise ToolError("ASSET_NOT_FOUND", "历史素材不存在于指定工程")
            source = Asset.model_validate_json(row["data"])
            if (source.asset_id, source.project_id) != (source_asset_id, source_project_id):
                raise ToolError("ASSET_NOT_FOUND", "历史素材登记信息不一致")
            try:
                master = _master_path(source, database)
            except (ToolError, OSError):
                raise ToolError("MEDIA_NOT_FOUND", "历史素材文件已移动或删除，请从文件重新导入") from None
            current = json.loads(con.execute("SELECT snapshot FROM revisions WHERE project_id=? AND revision=?", (pid, project["head"])).fetchone()[0])
            removed = set(current.get("removed_asset_ids", []))
            for existing in store.assets(pid, con).values():
                if existing.asset_id not in removed and (existing.sha256, existing.kind) == (source.sha256, source.kind):
                    try:
                        _master_path(existing, database)
                    except (ToolError, OSError):
                        continue
                    return existing
            aid = uid("asset")
            managed = contained(Path(database) / "managed", database)
            folder = contained(managed / pid / aid, managed)
            folder.mkdir(parents=True, exist_ok=False)
            created_folder = True
            target = folder / master.name
            try:
                # 母版在导入后只读；硬链接保留独立目录生命周期，跨盘时回退到字节复制。
                try:
                    os.link(master, target)
                except OSError:
                    shutil.copyfile(master, target)
            except FileNotFoundError:
                raise ToolError("MEDIA_NOT_FOUND", "历史素材文件已移动或删除，请从文件重新导入") from None
            except OSError as exc:
                raise ToolError("MEDIA_PROCESS_FAILED", "无法复制历史素材，请检查磁盘空间后重试") from exc
            reused = Asset.model_validate({**source.model_dump(), "asset_id": aid, "project_id": pid, "path": str(target.resolve())})
            con.execute("INSERT INTO assets VALUES(?,?,?)", (aid, pid, reused.model_dump_json()))
        return reused
    except BaseException:
        # 只清理本次创建的单个文件和空目录，不触碰源素材或其他导入记录。
        if target is not None:
            target.unlink(missing_ok=True)
        if created_folder and folder is not None and folder.is_dir():
            folder.rmdir()
        raise
