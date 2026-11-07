"""复用素材母版及按内容缓存的混音和倒放文件，避免每次编辑重新转码。"""
from hashlib import sha256
import json
from threading import Lock

from . import media
from .models import timeline, validate_snapshot
from .tools import ToolError, uid

# 固定数量的锁避免相同内容并发生成，也避免随工程数量无限增长。
_locks = [Lock() for _ in range(32)]


def cached_resource(pid, key, filename, produce):
    """原子发布缓存资源，失败时不留下可读取的半成品。

    Args:
        pid: 所属工程。
        key: 影响资源内容的完整参数。
        filename: 资源文件名。
        produce: 在临时目录生成资源并返回警告的函数。

    Returns:
        稳定的资源地址和生成警告。
    """
    token = sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()
    root = media.contained(media.ARTIFACTS / "previews" / pid, media.ARTIFACTS / "previews")
    folder = root / token
    with _locks[int(token[:8], 16) % len(_locks)]:
        if not (folder / "allowed.json").is_file():
            pending = root / uid("pending")
            pending.mkdir(parents=True)
            try:
                warnings = produce(pending)
                (pending / "warnings.json").write_text(json.dumps(warnings), encoding="utf-8")
                (pending / "allowed.json").write_text(json.dumps([filename]), encoding="utf-8")
                pending.replace(folder)
            finally:
                # 仅清理当前函数创建的、已经校验位于预览目录内的临时目录。
                if pending.exists():
                    media.shutil.rmtree(media.contained(pending, root))
        warnings = json.loads((folder / "warnings.json").read_text(encoding="utf-8"))
    return f"/api/v1/projects/{pid}/previews/{token}/files/{filename}", warnings


def prepare_preview(pid, snapshot, assets):
    """为浏览器构造播放清单，只为音频变化和新倒放区间生成媒体。

    Args:
        pid: 工程编号。
        snapshot: 当前正式工程快照。
        assets: 工程母版素材。

    Returns:
        与导出共用画面合成规则的清单和混音警告。
    """
    errors = validate_snapshot(snapshot, assets)
    if errors:
        raise ToolError("INVALID_DRAFT", "; ".join(errors))
    rows, total = timeline(snapshot)
    if not total:
        raise ToolError("EMPTY_TIMELINE", "Cannot preview an empty timeline.")
    sound_clips = []
    for clip, row in zip(snapshot.clips, rows):
        asset = assets[clip.asset_id]
        if clip.kind == "video" and asset.has_audio:
            sound_clips.append({**row, "sha256": asset.sha256, **clip.model_dump(include={
                "source_in_frame", "source_out_frame", "rate_num", "rate_den", "reverse", "original_audio_gain_db",
            })})
    sound_key = {
        "version": 1, "total": total, "clips": sound_clips,
        "transitions": [t.model_dump() for t in snapshot.transitions],
        "audio": [a.model_dump() for a in snapshot.audio_items],
        # 片段锚点及压低规则依赖所有镜头的位置。
        "timeline": rows if snapshot.audio_items else [],
        "sources": {a.asset_id: assets[a.asset_id].sha256 for a in snapshot.audio_items},
    }

    def mix(folder):
        _, warnings = media.prepare_render(snapshot, assets, folder, "audio", audio_only=True)
        return warnings

    mix_url, warnings = cached_resource(pid, sound_key, "audio-mix.wav", mix)
    prepared = []
    for clip, row in zip(snapshot.clips, rows):
        asset = assets[clip.asset_id]
        media.contained(asset.path, media.DATABASE)
        file = f"/api/v1/projects/{pid}/assets/{asset.asset_id}/file"
        playback = {"trimBefore": clip.source_in_frame, "playbackRate": clip.rate_num / clip.rate_den}
        if clip.reverse:
            key = {"version": 1, "sha256": asset.sha256, "asset_id": asset.asset_id,
                   **clip.model_dump(include={"source_in_frame", "source_out_frame", "rate_num", "rate_den", "reverse"})}

            def reverse(folder):
                media.derive_video(clip, asset, folder / "reverse.mp4")
                return []

            file, _ = cached_resource(pid, key, "reverse.mp4", reverse)
            playback = {"trimBefore": 0, "playbackRate": 1}
        prepared.append({**clip.model_dump(), **row, "file": file, "width": asset.width,
                         "height": asset.height, "previewPlayback": playback})
    return {**snapshot.model_dump(), "clips": prepared, "total_frames": total, "mix_file": mix_url}, warnings
