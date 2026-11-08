"""负责素材导入、帧与音频派生、Remotion 渲染和输出验收。"""
from __future__ import annotations

from hashlib import file_digest
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
from queue import Empty, Queue
from threading import Thread
from time import monotonic
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
               "-af", "aresample=48000,asetpts=PTS-STARTPTS", "-c:v", "libx264", "-preset", "veryfast", "-crf", 18,
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", 48000, "-movflags", "+faststart", output)
    else:
        ffmpeg("-i", src, "-vn", "-ar", 48000, "-ac", 2, "-c:a", "pcm_s16le", output)
    # 自行生成的 MP4 已记录准确帧数；避免导入时为计数再完整解码一次。
    master = probe(output)
    v = next((s for s in master["streams"] if s["codec_type"] == "video"), None)
    frames = v.get("nb_frames") if v else None
    if kind == "video" and (not str(frames).isdigit() or int(frames) <= 0):
        master = probe(output, count=True)
        v = next(s for s in master["streams"] if s["codec_type"] == "video")
        frames = v["nb_read_frames"]
    with output.open("rb") as source:
        digest = file_digest(source, "sha256").hexdigest()
    asset = Asset(asset_id=aid, project_id=pid, kind=kind, name=src.name, path=str(output.resolve()),
                  sha256=digest, width=v["width"] if v else 0, height=v["height"] if v else 0,
                  total_frames=(int(frames) if kind == "video" else math.floor(float(master["format"].get("duration", 0)) * 30)),
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


def _export_scale(snapshot, resolution):
    """按标准档位缩放并限制最长边，避免窄画布产生超大输出。"""
    width, height = snapshot.composition.width, snapshot.composition.height
    bounds = {"720p": (720, 1280), "1080p": (1080, 1920), "2160p": (2160, 3840)}
    if resolution == "source":
        return 1
    if resolution not in bounds:
        raise ToolError("INVALID_EXPORT_OPTIONS", "不支持的导出分辨率")
    short, long = bounds[resolution]
    return min(short / min(width, height), long / max(width, height))


def export_dimensions(snapshot, resolution="source"):
    """按档位选择输出尺寸，保持画幅与画布中的文字、构图比例。"""
    width, height = snapshot.composition.width, snapshot.composition.height
    scale = _export_scale(snapshot, resolution)
    # 与 Remotion 的 H.264 偶数尺寸规则一致；不修改正式工程的画布。
    def scaled_even(value):
        while math.floor(value * scale + .5) % 2:
            value -= 1
        return math.floor(value * scale + .5)
    return scaled_even(width), scaled_even(height)


def prepare_export(snapshot, assets, public_dir):
    """复用已缓存混音、倒放和素材母版，不为普通剪切重复转码视频。"""
    from .preview import cached_resource, prepare_preview
    errors = validate_snapshot(snapshot, assets)
    if errors:
        raise ToolError("INVALID_DRAFT", "; ".join(errors))
    if not snapshot.clips:
        raise ToolError("EMPTY_TIMELINE", "空工程不能渲染")
    pid = assets[snapshot.clips[0].asset_id].project_id
    plan, warnings = prepare_preview(pid, snapshot, assets)
    public_dir.mkdir(parents=True, exist_ok=True)

    def cached_path(url):
        prefix = f"/api/v1/projects/{pid}/previews/"
        parts = url.removeprefix(prefix).split("/")
        if not url.startswith(prefix) or len(parts) != 3 or parts[1] != "files":
            raise ToolError("INVALID_RENDER_RESOURCE", "渲染缓存地址无效")
        root = ARTIFACTS / "previews" / pid
        return contained(root / parts[0] / parts[2], root)

    def publish(source, name):
        dest = public_dir / name
        if not dest.exists():
            try:
                os.link(source, dest)
            except OSError:
                shutil.copyfile(source, dest)
        return name

    for clip, source_clip in zip(plan["clips"], snapshot.clips):
        asset = assets[clip["asset_id"]]
        # 非整数变速必须保留编辑内核的 floor 帧映射，不能交给播放器自行舍入。
        if not clip["reverse"] and source_clip.rate_num != source_clip.rate_den:
            key = {"version": 1, "sha256": asset.sha256, "asset_id": asset.asset_id,
                   **source_clip.model_dump(include={"source_in_frame", "source_out_frame", "rate_num", "rate_den", "reverse"})}
            def retime(folder):
                derive_video(source_clip, asset, folder / "retimed.mp4")
                return []
            clip["file"], _ = cached_resource(pid, key, "retimed.mp4", retime)
            clip["previewPlayback"] = {"trimBefore": 0, "playbackRate": 1}
        derived = clip["reverse"] or source_clip.rate_num != source_clip.rate_den
        source = cached_path(clip["file"]) if derived else contained(asset.path, DATABASE)
        name = f"derived-{source.parent.name}.mp4" if derived else f"asset-{asset.asset_id}-{asset.sha256[:16]}{source.suffix}"
        clip["file"] = publish(source, name)
    mix = cached_path(plan["mix_file"])
    plan["mix_file"] = publish(mix, f"mix-{mix.parent.name}.wav")
    return plan, warnings


def _render_process(argv, on_progress, timeout, stderr_path):
    """读取渲染器的逐行进度，并在失败或超时后终止整个子进程树。"""
    queue = Queue()
    process = None
    reader = None
    with stderr_path.open("wb") as err:
        try:
            process = subprocess.Popen([str(a) for a in argv], stdout=subprocess.PIPE, stderr=err,
                                       text=True, encoding="utf-8", errors="replace", shell=False,
                                       **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}))
            def read_lines():
                try:
                    for line in process.stdout:
                        queue.put(line)
                finally:
                    queue.put(None)
            reader = Thread(target=read_lines, daemon=True)
            reader.start()
            deadline = monotonic() + timeout
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise ToolError("MEDIA_TIMEOUT", f"媒体渲染超时：{timeout} 秒")
                try:
                    line = queue.get(timeout=min(.5, remaining))
                except Empty:
                    continue
                if line is None:
                    break
                try:
                    event = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if isinstance(event, dict) and event.get("event") == "render_progress":
                    on_progress({key: value for key, value in event.items() if key != "event"})
            try:
                code = process.wait(timeout=max(.1, deadline - monotonic()))
            except subprocess.TimeoutExpired as exc:
                raise ToolError("MEDIA_TIMEOUT", f"媒体渲染超时：{timeout} 秒") from exc
            if code:
                err.flush()
                raise ToolError("MEDIA_PROCESS_FAILED", stderr_path.read_text(encoding="utf-8", errors="replace")[-5000:])
        finally:
            if process is not None:
                if process.poll() is None:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=15, check=False)
                    else:
                        import signal
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=15)
                if reader is not None:
                    reader.join(timeout=1)
                process.stdout.close()


def render_snapshot(snapshot, assets, output: Path, work: Path | None = None, *,
                    resolution="source", quality="high", on_progress=None):
    """渲染工程并在解码验收通过后发布输出文件。

    Args:
        snapshot: 要导出的工程快照。
        assets: 项目素材映射。
        output: 最终视频路径。
        work: 可选的中间文件目录。
        resolution: 原画布或按短边选择的输出分辨率。
        quality: 画质档位，标准、高质量或节省空间。
        on_progress: 接收阶段和百分比的可选回调。

    Returns:
        输出路径、帧数和媒体校验信息。
    """
    if quality not in {"standard", "high", "compact"}:
        raise ToolError("INVALID_EXPORT_OPTIONS", "不支持的导出画质")
    width, height = export_dimensions(snapshot, resolution)
    total = timeline(snapshot)[1]
    notify = on_progress or (lambda value: None)
    def progress(stage, percent, rendered=0):
        notify({"stage": stage, "progress": percent, "rendered_frames": rendered, "total_frames": total})
    output = contained(output, ARTIFACTS)
    output.parent.mkdir(parents=True, exist_ok=True)
    work = work or ROOT / ".cache" / "render" / uid("job")
    progress("preparing", 1)
    plan, warnings = prepare_export(snapshot, assets, work / "public")
    scale = _export_scale(snapshot, resolution)
    plan["export_settings"] = {"scale": scale, "quality": quality}
    manifest = work / "manifest.json"
    manifest.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    silent = work / "silent.mp4"
    node = shutil.which("node")
    if not node:
        raise ToolError("RENDERER_UNAVAILABLE", "未找到 Node.js，无法启动视频渲染器")
    progress("bundling", 12)
    _render_process([node, ROOT / "node_modules/tsx/dist/cli.mjs", ROOT / "apps/renderer/render.ts", manifest, silent],
                    notify, timeout=3600, stderr_path=work / "renderer.stderr.log")
    temp = output.with_name(output.stem + ".pending.mp4")
    try:
        progress("muxing", 92, total)
        ffmpeg("-i", silent, "-i", work / "public" / plan["mix_file"], "-map", "0:v:0", "-map", "1:a:0",
               "-c:v", "copy", "-c:a", "aac", "-ar", 48000, "-t", plan["total_frames"] / 30, "-movflags", "+faststart", temp)
        progress("verifying", 96, total)
        verify_output(temp, plan["total_frames"], width, height)
        temp.replace(output)
    finally:
        temp.unlink(missing_ok=True)
    progress("completed", 100, total)
    return {"output": str(output), "frames": plan["total_frames"], "warnings": warnings,
            "width": width, "height": height, "fps": 30, "bytes": output.stat().st_size}


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
