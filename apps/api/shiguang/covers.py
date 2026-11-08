"""只抽取封面所需源帧，再通过共享合成器输出调色和转场后的封面。"""
import json
from pathlib import Path
import shutil

from . import media
from .models import timeline, validate_snapshot
from .tools import ToolError, uid


def render_cover(snapshot, assets, output: Path, work: Path | None = None):
    """导出封面；保持原始素材和成片时间线不变。

    Args:
        snapshot: 已保存且带封面设置的工程快照。
        assets: 工程所属素材。
        output: artifacts 内的 PNG 输出路径。
        work: 可选的隔离渲染工作目录。

    Returns:
        图片尺寸与输出路径。
    """
    if not snapshot.cover:
        raise ToolError("COVER_NOT_SET", "请先保存封面设置")
    errors = validate_snapshot(snapshot, assets)
    if errors:
        raise ToolError("INVALID_DRAFT", "; ".join(errors))
    cover = snapshot.cover
    output = media.contained(output, media.ARTIFACTS)
    output.parent.mkdir(parents=True, exist_ok=True)
    work = work or media.ROOT / ".cache" / "covers" / uid("job")
    public = work / "public"
    public.mkdir(parents=True, exist_ok=True)
    rows, total = timeline(snapshot)
    plan = {**snapshot.model_dump(), "total_frames": max(total, 1), "clips": [], "mix_file": ""}
    image = None
    if cover.source == "image":
        asset = assets[cover.asset_id]
        shutil.copyfile(media.contained(asset.path, media.DATABASE), public / "cover.png")
        image = {"file": "cover.png", "width": asset.width, "height": asset.height}
    else:
        active = set()
        for clip, row in zip(snapshot.clips, rows):
            if not row["start_frame"] <= cover.frame < row["end_frame"]:
                continue
            active.add(clip.clip_id)
            asset = assets[clip.asset_id]
            source = media.contained(asset.path, media.DATABASE)
            name = f"{clip.clip_id}.png"
            if clip.kind == "image":
                shutil.copyfile(source, public / name)
            else:
                offset = (cover.frame - row["start_frame"]) * clip.rate_num // clip.rate_den
                source_frame = clip.source_out_frame - 1 - offset if clip.reverse else clip.source_in_frame + offset
                source_frame = max(clip.source_in_frame, min(clip.source_out_frame - 1, source_frame))
                media.ffmpeg("-ss", source_frame / 30, "-i", source, "-frames:v", 1, "-update", 1, public / name)
            plan["clips"].append({**clip.model_dump(), **row, "kind": "image", "file": name, "width": asset.width, "height": asset.height})
        plan["transitions"] = [t.model_dump() for t in snapshot.transitions if t.left_clip_id in active and t.right_clip_id in active]
        plan["text_items"] = [t.model_dump() for t in snapshot.text_items if t.anchor.kind == "timeline" or t.anchor.clip_id in active]
    manifest = work / "manifest.json"
    manifest.write_text(json.dumps({"plan": plan, "cover": cover.model_dump(), "image": image}, ensure_ascii=False), encoding="utf-8")
    pending = output.with_name(output.stem + ".pending.png")
    try:
        media.run([shutil.which("node"), media.ROOT / "node_modules/tsx/dist/cli.mjs", media.ROOT / "apps/renderer/still.ts", manifest, pending], timeout=180)
        from PIL import Image
        with Image.open(pending) as picture:
            if picture.size != (snapshot.composition.width, snapshot.composition.height):
                raise ToolError("COVER_SIZE_MISMATCH", "封面尺寸校验失败")
            picture.verify()
        pending.replace(output)
    finally:
        pending.unlink(missing_ok=True)
    return {"output": str(output), "width": snapshot.composition.width, "height": snapshot.composition.height}
