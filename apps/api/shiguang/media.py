"""负责素材导入、帧与音频派生、Remotion 渲染和输出验收。"""
from __future__ import annotations

from hashlib import file_digest
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import wave

import imageio_ffmpeg
import numpy as np

from .models import Asset, Snapshot, anchor_range, timeline, validate_snapshot
from .tools import ToolError, uid

ROOT = Path(__file__).resolve().parents[3]
DATABASE = ROOT / "database"
ARTIFACTS = ROOT / "artifacts"
FFMPEG = os.environ.get("FFMPEG_PATH") or imageio_ffmpeg.get_ffmpeg_exe()
_compositors = list((ROOT / "node_modules" / "@remotion").glob("compositor-*/ffprobe*"))
FFPROBE = os.environ.get("FFPROBE_PATH") or (str(_compositors[0]) if _compositors else "ffprobe")


def run(argv, timeout=180, binary=False):
    try:
        proc = subprocess.run([str(a) for a in argv], capture_output=True, timeout=timeout, shell=False)
    except subprocess.TimeoutExpired as exc:
        raise ToolError("MEDIA_TIMEOUT", f"媒体进程超时：{timeout} 秒") from exc
    if proc.returncode:
        raise ToolError("MEDIA_PROCESS_FAILED", proc.stderr.decode("utf-8", errors="replace")[-5000:])
    return proc.stdout if binary else proc.stdout.decode("utf-8", errors="replace")


def ffmpeg(*args, timeout=180):
    return run([FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args], timeout=timeout)


def contained(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    if not path.is_relative_to(root):
        raise ToolError("PATH_OUTSIDE_ROOT", f"路径必须位于 {root.name} 目录内")
    return path


def probe(path, count=False):
    args = [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json"]
    if count:
        args += ["-count_frames"]
    return json.loads(run([*args, str(path)]))


def import_media(store, pid, relative_path, database=DATABASE):
    """从素材目录导入文件并归一化媒体元数据。

    Args:
        store: 工程素材存储。
        pid: 所属项目编号。
        relative_path: 相对素材目录的路径。
        database: 允许读取的素材根目录。

    Returns:
        已登记的素材对象。
    """
    store.project(pid)
    src = contained(Path(database) / relative_path, database)
    if not src.is_file() or src.stat().st_size > 1024 ** 3:
        raise ToolError("INVALID_MEDIA_FILE", "素材不存在或大于 1 GB")
    data = probe(src)
    streams = data["streams"]
    video = next((s for s in streams if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    audio = next((s for s in streams if s["codec_type"] == "audio"), None)
    image = src.suffix.lower() in {".png", ".jpg", ".jpeg"}
    kind = "image" if image else "video" if video else "audio" if audio else None
    if not kind or (kind != "image" and float(data["format"].get("duration", 0)) > 1200):
        raise ToolError("UNSUPPORTED_MEDIA", "仅接受可探测的图片、视频及音频，视频/音频最长 20 分钟")
    if video and (video.get("color_transfer") in {"smpte2084", "arib-std-b67"} or video.get("color_primaries") == "bt2020"):
        raise ToolError("HDR_REQUIRES_CONVERSION", "请先将 HDR 素材转换为 SDR")
    if video and video["width"] * video["height"] > 3840 * 2160:
        raise ToolError("INPUT_DIMENSION_LIMIT", "本轮支持最高 4K 输入")
    aid = uid("asset")
    folder = contained(Path(database) / "managed" / pid / aid, database)
    folder.mkdir(parents=True)
    ext = {"image": ".png", "video": ".mp4", "audio": ".wav"}[kind]
    output = folder / ("master" + ext)
    if kind == "image":
        ffmpeg("-i", src, "-frames:v", 1, output)
    elif kind == "video":
        ffmpeg("-i", src, "-map", "0:v:0", "-map", "0:a:0?", "-vf", "fps=30,setpts=PTS-STARTPTS,scale=trunc(iw/2)*2:trunc(ih/2)*2",
               "-af", "aresample=48000,asetpts=PTS-STARTPTS", "-c:v", "libx264", "-preset", "fast", "-crf", 18,
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", 48000, "-movflags", "+faststart", output)
    else:
        ffmpeg("-i", src, "-vn", "-ar", 48000, "-ac", 2, "-c:a", "pcm_s16le", output)
    master = probe(output, count=True)
    v = next((s for s in master["streams"] if s["codec_type"] == "video"), None)
    with output.open("rb") as source:
        digest = file_digest(source, "sha256").hexdigest()
    asset = Asset(asset_id=aid, project_id=pid, kind=kind, name=src.name, path=str(output.resolve()),
                  sha256=digest, width=v["width"] if v else 0, height=v["height"] if v else 0,
                  total_frames=(int(v["nb_read_frames"]) if kind == "video" else math.floor(float(master["format"].get("duration", 0)) * 30)),
                  has_audio=any(s["codec_type"] == "audio" for s in master["streams"]))
    store.add_asset(asset)
    return asset


def read_audio(path):
    with wave.open(str(path), "rb") as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (2, 2, 48000):
            raise ToolError("AUDIO_FORMAT", "内部音频须为 48kHz 双声道 16bit PCM")
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2).astype(np.float32) / 32768


def write_audio(path, samples):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes((np.clip(samples, -.98, .98) * 32767).astype("<i2").tobytes())


def derive_video(clip, asset, output):
    """按精确帧映射生成变速或倒放视频，并限制中间解码大小。

    Args:
        clip: 含入出点、速度和方向的片段。
        asset: 输入视频素材。
        output: 派生视频的输出路径。
    """
    trim = f"trim=start_frame={clip.source_in_frame}:end_frame={clip.source_out_frame},setpts=PTS-STARTPTS"
    if clip.rate_num == clip.rate_den and not clip.reverse:
        ffmpeg("-i", asset.path, "-vf", trim, "-an", "-frames:v", clip.duration,
               "-c:v", "libx264", "-preset", "fast", "-crf", 16, "-pix_fmt", "yuv420p", output)
        return
    frames = clip.source_out_frame - clip.source_in_frame
    expected_bytes = frames * asset.width * asset.height * 3
    if expected_bytes > 1024 ** 3:
        raise ToolError("DERIVATION_LIMIT", "本轮变速/倒放的单段解码预算为 1 GB；请缩短源区间")
    raw = output.with_suffix(".rgb")
    try:
        ffmpeg("-i", asset.path, "-vf", trim, "-an", "-pix_fmt", "rgb24", "-f", "rawvideo", raw)
        if raw.stat().st_size != expected_bytes:
            raise ToolError("DECODE_FRAME_MISMATCH", "解码帧数与源区间不一致")
        mapped = np.memmap(raw, dtype=np.uint8, mode="r", shape=(frames, asset.height, asset.width, 3))
        log = output.with_suffix(".encode.log")
        with log.open("wb") as err:
            process = subprocess.Popen([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                                        "-s", f"{asset.width}x{asset.height}", "-r", "30", "-i", "pipe:0", "-an", "-c:v", "libx264",
                                        "-preset", "fast", "-crf", "16", "-pix_fmt", "yuv420p", str(output)], stdin=subprocess.PIPE, stderr=err)
            try:
                for k in range(clip.duration):
                    index = min(frames - 1, k * clip.rate_num // clip.rate_den)
                    if clip.reverse:
                        index = frames - 1 - index
                    process.stdin.write(mapped[index].tobytes())
                process.stdin.close()
                if process.wait(timeout=180):
                    raise ToolError("ENCODE_FAILED", log.read_text(errors="replace"))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                del mapped
    finally:
        raw.unlink(missing_ok=True)


def fit_audio(samples, n):
    out = np.zeros((n, 2), dtype=np.float32)
    count = min(n, len(samples))
    out[:count] = samples[:count]
    return out


def prepare_render(snapshot: Snapshot, assets: dict[str, Asset], public_dir: Path, tag: str, *, audio_only=False):
    """生成渲染清单、派生视频和统一音频混音。

    Args:
        snapshot: 已校验的工程快照。
        assets: 可用素材映射。
        public_dir: 渲染器可读取的工作目录。
        tag: 用于隔离派生文件的标记。
        audio_only: 仅准备混音，浏览器预览直接复用素材母版。

    Returns:
        包含混音文件名的渲染清单及处理警告。
    """
    errors = validate_snapshot(snapshot, assets)
    if errors:
        raise ToolError("INVALID_DRAFT", "; ".join(errors))
    rows, total = timeline(snapshot)
    if total == 0:
        raise ToolError("EMPTY_TIMELINE", "空工程不能渲染")
    public_dir.mkdir(parents=True, exist_ok=True)
    n = total * 1600
    mix = np.zeros((n, 2), dtype=np.float32)
    plan = snapshot.model_dump()
    prepared = []
    for clip, row in zip(snapshot.clips, rows):
        a = assets[clip.asset_id]
        contained(a.path, DATABASE)
        filename = f"{tag}-{clip.clip_id}." + ("png" if clip.kind == "image" else "mp4")
        dest = public_dir / filename
        if audio_only:
            pass
        elif clip.kind == "image":
            shutil.copyfile(a.path, dest)
        else:
            derive_video(clip, a, dest)
        prepared.append({**clip.model_dump(), **row, "file": filename, "width": a.width, "height": a.height})
        if clip.kind == "video" and a.has_audio:
            wav = public_dir / f"{tag}-{clip.clip_id}-original.wav"
            filters = [f"atrim=start_sample={clip.source_in_frame * 1600}:end_sample={clip.source_out_frame * 1600}", "asetpts=PTS-STARTPTS"]
            if clip.reverse:
                filters += ["areverse"]
            filters += [f"atempo={clip.rate_num / clip.rate_den}", "apad", f"atrim=end_sample={clip.duration * 1600}"]
            ffmpeg("-i", a.path, "-vn", "-af", ",".join(filters), "-ar", 48000, "-ac", 2, "-c:a", "pcm_s16le", wav)
            samples = fit_audio(read_audio(wav), clip.duration * 1600)
            envelope = np.ones(len(samples), dtype=np.float32)
            for t in snapshot.transitions:
                length = t.duration_frames * 1600
                if t.right_clip_id == clip.clip_id:
                    envelope[:length] *= np.linspace(0, 1, length)
                if t.left_clip_id == clip.clip_id:
                    envelope[-length:] *= np.linspace(1, 0, length)
            start = row["start_frame"] * 1600
            mix[start:start + len(samples)] += samples * envelope[:, None] * 10 ** (clip.original_audio_gain_db / 20)
    by_id = {r["clip_id"]: r for r in rows}
    for item in snapshot.audio_items:
        a = assets[item.asset_id]
        contained(a.path, DATABASE)
        start, end = anchor_range(item.anchor, snapshot)
        count = (end - start) * 1600
        source = read_audio(a.path)
        samples = fit_audio(source[item.source_in_frame * 1600:item.source_in_frame * 1600 + count], count)
        envelope = np.ones(count, dtype=np.float32) * 10 ** (item.gain_db / 20)
        fade_in, fade_out = item.fade_in_frames * 1600, item.fade_out_frames * 1600
        if fade_in:
            envelope[:fade_in] *= np.linspace(0, 1, fade_in)
        if fade_out:
            envelope[-fade_out:] *= np.linspace(1, 0, fade_out)
        duck_envelope = np.ones(count, dtype=np.float32)
        absolute_samples = np.arange(count, dtype=np.float64) + start * 1600
        for d in item.ducks:
            target = by_id[d.clip_id]
            low = target["start_frame"] * 1600
            high = target["end_frame"] * 1600
            gain = 10 ** (d.gain_db / 20)
            mask = (absolute_samples >= low) & (absolute_samples < high)
            effect = np.ones(count, dtype=np.float32)
            if d.ramp_frames:
                ramp = d.ramp_frames * 1600
                enter = np.clip((absolute_samples - low) / ramp, 0, 1)
                leave = np.clip((high - 1 - absolute_samples) / ramp, 0, 1)
                effect[mask] = 1 - (1 - gain) * np.minimum(enter, leave)[mask]
            else:
                effect[mask] = gain
            duck_envelope = np.minimum(duck_envelope, effect)
        mix[start * 1600:end * 1600] += samples * (envelope * duck_envelope)[:, None]
    mix_name = f"{tag}-mix.wav"
    warnings = ["混音峰值超过 0.98，已限幅"] if np.max(np.abs(mix)) > .98 else []
    write_audio(public_dir / mix_name, mix)
    plan.update({"clips": prepared, "total_frames": total, "mix_file": mix_name})
    return plan, warnings


def render_snapshot(snapshot, assets, output: Path, work: Path | None = None):
    """渲染工程并在解码验收通过后发布输出文件。

    Args:
        snapshot: 要导出的工程快照。
        assets: 项目素材映射。
        output: 最终视频路径。
        work: 可选的中间文件目录。

    Returns:
        输出路径、帧数和媒体校验信息。
    """
    output = contained(output, ARTIFACTS)
    output.parent.mkdir(parents=True, exist_ok=True)
    work = work or ROOT / ".cache" / "render" / uid("job")
    plan, warnings = prepare_render(snapshot, assets, work / "public", "render")
    manifest = work / "manifest.json"
    manifest.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    silent = work / "silent.mp4"
    node = shutil.which("node")
    run([node, ROOT / "node_modules/tsx/dist/cli.mjs", ROOT / "apps/renderer/render.ts", manifest, silent], timeout=600)
    temp = output.with_name(output.stem + ".pending.mp4")
    try:
        ffmpeg("-i", silent, "-i", work / "public" / plan["mix_file"], "-map", "0:v:0", "-map", "1:a:0",
               "-c:v", "copy", "-c:a", "aac", "-ar", 48000, "-t", plan["total_frames"] / 30, "-movflags", "+faststart", temp)
        verify_output(temp, plan["total_frames"], snapshot.composition.width, snapshot.composition.height)
        temp.replace(output)
    finally:
        temp.unlink(missing_ok=True)
    return {"output": str(output), "frames": plan["total_frames"], "warnings": warnings}


def verify_output(path, frames, width, height):
    """验证导出文件的帧数、编码、尺寸和音视频可解码性。

    Args:
        path: 待验收的视频路径。
        frames: 预期帧数。
        width: 预期宽度。
        height: 预期高度。

    Returns:
        通过校验的视频元数据。
    """
    info = probe(path, count=True)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    if (int(video["nb_read_frames"]), video["width"], video["height"], video["codec_name"], video["r_frame_rate"]) != (frames, width, height, "h264", "30/1") or not audio or audio["codec_name"] != "aac" or int(audio["sample_rate"]) != 48000 or abs(float(audio["duration"]) - frames / 30) > 1 / 30:
        raise ToolError("EXPORT_VERIFICATION_FAILED", "输出帧数、尺寸或编解码不正确")
    # 发布前完整解码音视频，避免仅验证容器头信息。
    ffmpeg("-xerror", "-i", path, "-f", "null", "-")
    return info
