"""生成可复现的测试素材并逐工具渲染视频、测量效果和输出验收报告。"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

import numpy as np
from PIL import Image, ImageDraw

from shiguang.media import (ARTIFACTS, DATABASE, FFMPEG, ffmpeg, import_media, prepare_render,
                            probe, read_audio, render_snapshot, run, verify_output, write_audio)
from shiguang.models import Composition, Snapshot
from shiguang.store import Store
from shiguang.tools import TOOLS

INPUTS = DATABASE / "test-inputs"
OUT = ARTIFACTS / "tool-acceptance"
WORK = ROOT / ".cache" / "media-acceptance"


def generate_fixtures():
    INPUTS.mkdir(parents=True, exist_ok=True)
    for name, fps, frames, base in [("frame-red", 30, 60, "red"), ("frame-blue-24fps", 24, 48, "blue")]:
        raw = INPUTS / f"{name}.rgb"
        with raw.open("wb") as f:
            for n in range(frames):
                color = (40 + n * 2, 24, 24) if base == "red" else (24, 40, 100 + n * 2)
                im = Image.new("RGB", (640, 360), color)
                d = ImageDraw.Draw(im)
                d.rectangle((0, 180, 639, 359), fill=(45, 60, 70))
                for x in range(0, 640, 40):
                    d.line((x, 180, x, 359), fill=(130, 145, 150), width=1)
                for y in range(180, 360, 40):
                    d.line((0, y, 639, y), fill=(130, 145, 150), width=1)
                d.rectangle((30 + n * 3, 230, 70 + n * 3, 270), fill=(70, 240, 100))
                d.text((250, 90), f"{name} / FRAME {n:03d}", fill="white", font_size=22)
                f.write(im.tobytes())
        tone = np.arange(96000) / 48000
        wave = .08 * np.sin(2 * np.pi * 880 * tone)
        # 每半秒设置一次 10 毫秒脉冲，用于独立检查音视频同步。
        pulse = ((np.arange(96000) % 24000) < 480).astype(float) * .12
        write_audio(INPUTS / "original.wav", np.column_stack([wave + pulse, wave + pulse]))
        ffmpeg("-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "640x360", "-r", fps, "-i", raw,
               "-i", INPUTS / "original.wav", "-c:v", "libx264", "-preset", "fast", "-crf", 12,
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", 2, INPUTS / f"{name}.mp4")
        raw.unlink()
    im = Image.new("RGB", (640, 360), (90, 100, 110))
    d = ImageDraw.Draw(im)
    for x in range(0, 640, 40):
        for y in range(0, 360, 40):
            d.rectangle((x, y, x + 39, y + 39), fill=(50 + x // 4 % 160, 60 + y // 2 % 160, 80 + (x + y) // 5 % 150))
    d.rectangle((280, 140, 360, 220), fill=(250, 250, 250))
    d.text((284, 170), "CENTER", fill=(0, 0, 0), font_size=16)
    im.save(INPUTS / "grid.png")
    # 竖屏无声素材用于检查画面比例和缺少音轨时的处理。
    portrait = im.resize((360, 640))
    portrait.save(INPUTS / "portrait.png")
    ffmpeg("-loop", 1, "-i", INPUTS / "portrait.png", "-t", 1, "-r", 30,
           "-c:v", "libx264", "-pix_fmt", "yuv420p", INPUTS / "portrait-silent.mp4")
    t = np.arange(4 * 48000) / 48000
    song = .2 * np.sin(2 * np.pi * 440 * t)
    write_audio(INPUTS / "music.wav", np.column_stack([song, song]))
    ffmpeg("-i", INPUTS / "music.wav", "-c:a", "libmp3lame", "-b:a", "128k", INPUTS / "music.mp3")


def read_frames(path, width=320, height=180):
    raw = run([FFMPEG, "-v", "error", "-i", path, "-an", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"], binary=True)
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, height, width, 3)


def output_audio(path):
    wav = path.with_suffix(".decoded.wav")
    ffmpeg("-i", path, "-vn", "-ar", 48000, "-ac", 2, "-c:a", "pcm_s16le", wav)
    return read_audio(wav)[:, 0]


def amplitude(samples, hz, start, end):
    x = samples[int(start * 48000):int(end * 48000)]
    times = np.arange(len(x)) / 48000
    return float(2 * abs(np.sum(x * np.exp(-2j * np.pi * hz * times))) / len(x))


def case_clip(cid="c1", asset="red", start=0, end=60, silent=False):
    return {"clip_id": cid, "asset_id": ASSETS[asset].asset_id, "kind": "video", "source_in_frame": start,
            "source_out_frame": end, "original_audio_gain_db": -60 if silent else 0}


def image_clip(frames=60, fit="cover"):
    return {"clip_id": "grid", "asset_id": ASSETS["grid"].asset_id, "kind": "image", "still_duration_frames": frames,
            "transform": {"fit": fit}}


def execute(did, name, args):
    seq = STORE.draft(did)["seq"]
    result = STORE.call(did, name, args, f"op-{seq}", seq)
    assert result["ok"], (name, result)
    return result


CASES = []
def add_case(name, base, operations=(), tools=None):
    did = STORE.create_draft(PID)["id"]
    execute(did, "assemble_sequence", {"clips": base})
    values = {}
    for tool, args in operations:
        if callable(args):
            args = args(values)
        result = execute(did, tool, args)
        values[tool] = result["changed_ids"]
    snapshot = Snapshot.model_validate(STORE.draft(did)["snapshot"])
    plan, warnings = prepare_render(snapshot, STORE.assets(PID), WORK / "public", name)
    path = WORK / f"{name}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    CASES.append({"name": name, "tools": tools or [name], "manifest": str(path), "plan": plan,
                  "output": str(OUT / f"{name}.mp4"), "warnings": warnings})


def build_cases(smoke=False):
    pair = [case_clip(end=30), case_clip("c2", "blue", end=30)]
    add_case("assemble_sequence", pair)
    if smoke:
        return
    add_case("trim_clip", [case_clip()], [("trim_clip", {"clip_id": "c1", "source_in_frame": 15, "source_out_frame": 45})])
    add_case("split_clip", [case_clip()], [("split_clip", {"clip_id": "c1", "local_frame": 30})])
    add_case("delete_clip", pair, [("delete_clip", {"clip_id": "c2"})])
    add_case("move_clip", pair, [("move_clip", {"clip_id": "c2", "before_clip_id": "c1"})])
    add_case("insert_clip", pair, [("insert_clip", {"clip": image_clip(15), "before_clip_id": "c2"})])
    add_case("replace_clip", pair, [("replace_clip", {"clip_id": "c1", "replacement": case_clip("ignored", "blue", end=30)})])
    for label, num, den, reverse in [("fast", 2, 1, False), ("slow", 1, 2, False), ("rational", 3, 2, False), ("reverse", 1, 1, True), ("reverse_slow", 1, 2, True)]:
        add_case("playback_" + label, [case_clip()], [("set_playback", {"clip_id": "c1", "rate_num": num, "rate_den": den, "reverse": reverse})], ["set_playback"])
    add_case("image_baseline", [image_clip()], tools=[])
    add_case("set_transform", [image_clip()], [("set_transform", {"clip_id": "grid", "transform": {"scale": 1.4, "anchor_x": .35, "anchor_y": .45, "rotation_deg": 12}})])
    for preset in ["slow_push_in", "slow_pull_out", "pan_left", "pan_right", "pan_up", "pan_down"]:
        add_case("motion_" + preset, [image_clip()], [("apply_motion_preset", {"clip_id": "grid", "motion": {"preset_id": preset, "local_end_frame": 60, "intensity": .4}})], ["apply_motion_preset"])
    add_case("set_transition", pair, [("set_transition", {"left_clip_id": "c1", "right_clip_id": "c2", "duration_frames": 10})])
    for preset in ["warm", "cool", "monochrome"]:
        add_case("color_" + preset, [image_clip()], [("set_color_preset", {"clip_id": "grid", "preset_id": preset})], ["set_color_preset"])
    text = {"text": "海边 · 拾光", "anchor": {"start_frame": 15, "end_frame": 45}, "font_size": 26, "y": .5}
    add_case("add_text", [image_clip()], [("add_text", text)])
    add_case("update_text", [image_clip()], [("add_text", text), ("update_text", lambda v: {"text_id": v["add_text"][0], "value": {**text, "text": "NEW TITLE", "color": "#ff4040"}})])
    add_case("remove_text", [image_clip()], [("add_text", text), ("remove_text", lambda v: {"text_id": v["add_text"][0]})])
    audio = {"asset_id": ASSETS["music"].asset_id, "anchor": {"end_frame": 60}, "gain_db": 0}
    add_case("add_audio", [case_clip(silent=True)], [("add_audio", audio)])
    add_case("remove_audio", [case_clip(silent=True)], [("add_audio", audio), ("remove_audio", lambda v: {"audio_id": v["add_audio"][0]})])
    add_case("set_audio_gain", [case_clip(silent=True)], [("add_audio", audio), ("set_audio_gain", lambda v: {"target_id": v["add_audio"][0], "gain_db": -12})])
    add_case("original_gain", [case_clip()], [("set_audio_gain", {"target": "original", "target_id": "c1", "gain_db": -6})], ["set_audio_gain"])
    add_case("set_audio_fade", [case_clip(silent=True)], [("add_audio", audio), ("set_audio_fade", lambda v: {"audio_id": v["add_audio"][0], "fade_in_frames": 15, "fade_out_frames": 15})])
    silent_pair = [case_clip(end=30, silent=True), case_clip("c2", "blue", end=30, silent=True)]
    add_case("duck_music_for_clip", silent_pair, [("add_audio", audio), ("duck_music_for_clip", lambda v: {"audio_id": v["add_audio"][0], "clip_id": "c2", "gain_db": -12, "ramp_frames": 0})])
    add_case("portrait_silent", [case_clip(asset="portrait", end=30)], tools=[])
    # 独立的 9:16 工程检查输出元数据及横屏素材填充效果。
    add_case("portrait_output", [case_clip()], tools=[])
    CASES[-1]["plan"]["composition"].update(width=180, height=320)
    Path(CASES[-1]["manifest"]).write_text(json.dumps(CASES[-1]["plan"]), encoding="utf-8")


def validate_effects():
    videos = {c["name"]: read_frames(Path(c["output"]), c["plan"]["composition"]["width"], c["plan"]["composition"]["height"]) for c in CASES}
    metrics = {}
    def sample(name, frame):
        return videos[name][frame, 10:20, 10:20].mean(axis=(0, 1))
    assert sample("assemble_sequence", 0)[0] > sample("assemble_sequence", 0)[2]
    assert sample("assemble_sequence", 30)[2] > sample("assemble_sequence", 30)[0]
    if len(CASES) == 1:
        return {"assemble_sequence": {"order": "red → blue", "frames": len(videos["assemble_sequence"])}}
    source_red = read_frames(Path(ASSETS["red"].path), 640, 360)[:, 20:40, 20:40].mean(axis=(1, 2))
    for name, indices in {
        "trim_clip": np.arange(15, 45), "split_clip": np.arange(60),
        "playback_fast": np.arange(30) * 2, "playback_slow": np.arange(120) // 2,
        "playback_rational": np.arange(40) * 3 // 2, "playback_reverse": 59 - np.arange(60),
        "playback_reverse_slow": 59 - np.arange(120) // 2,
    }.items():
        observed = videos[name][:, 10:20, 10:20].mean(axis=(1, 2))
        error = float(np.max(np.abs(observed - source_red[indices])))
        assert error <= 7, (name, error)
        metrics[name] = {"frame_mapping_max_rgb_error": error, "frames_checked": len(indices)}
    assert len(videos["delete_clip"]) == 30
    assert sample("delete_clip", 29)[0] > sample("delete_clip", 29)[2]
    assert sample("move_clip", 0)[2] > sample("move_clip", 0)[0]
    assert sample("move_clip", 30)[0] > sample("move_clip", 30)[2]
    assert len(videos["insert_clip"]) == 75
    assert np.mean(np.abs(videos["insert_clip"][35].astype(float) - videos["image_baseline"][0])) < 2
    assert sample("replace_clip", 0)[2] > sample("replace_clip", 0)[0]
    baseline = videos["image_baseline"][0].astype(float)
    transform = videos["set_transform"][0]
    assert np.mean(np.abs(transform.astype(float) - baseline)) > 15
    assert min(transform[0, 0].sum(), transform[-1, -1].sum(), transform[0, -1].sum(), transform[-1, 0].sum()) > 60
    metrics["set_transform"] = {"mean_pixel_change": float(np.mean(np.abs(transform.astype(float) - baseline))), "black_corners": False}
    for preset in ["slow_push_in", "slow_pull_out", "pan_left", "pan_right", "pan_up", "pan_down"]:
        frames = videos["motion_" + preset]
        difference = float(np.mean(np.abs(frames[0].astype(float) - frames[-1].astype(float))))
        assert difference > 10, (preset, difference)
        # 所有帧均须覆盖视口，包括旋转和裁剪的边界情况。
        assert frames[:, [0, -1]][:, :, [0, -1]].sum(axis=-1).min() > 50
        metrics["motion_" + preset] = {"first_last_pixel_change": difference, "edge_coverage": "passed"}
    # 相反方向的预设应沿同一路径反向运动。
    for a, b in [("slow_push_in", "slow_pull_out"), ("pan_left", "pan_right"), ("pan_up", "pan_down")]:
        error = np.mean(np.abs(videos["motion_" + a][0].astype(float) - videos["motion_" + b][-1].astype(float)))
        assert error < 2.5, (a, b, error)
    assert len(videos["set_transition"]) == 50
    # 重叠中点由前片段第 25 帧与后片段第 5 帧各以一半透明度混合。
    mixed = sample("set_transition", 25)
    red = sample("assemble_sequence", 25)
    blue = sample("assemble_sequence", 35)
    error = float(np.max(np.abs(mixed - (red + blue) / 2)))
    assert error < 8, error
    metrics["set_transition"] = {"duration_frames": 50, "midpoint_blend_error": error}
    mono = videos["color_monochrome"][0].astype(float)
    assert np.mean(abs(mono[..., 0] - mono[..., 1])) < 2
    for name in ["color_warm", "color_cool", "color_monochrome"]:
        assert np.mean(np.abs(videos[name][0].astype(float) - baseline)) > 3
    for name in ["add_text", "update_text"]:
        delta = np.mean(abs(videos[name][25].astype(float) - baseline))
        assert delta > 2
        assert np.mean(abs(videos[name][0].astype(float) - baseline)) < 2
        assert np.mean(abs(videos[name][45].astype(float) - baseline)) < 2
        metrics[name] = {"visible_interval_frames": "[15,45)", "active_frame_difference": float(delta)}
    assert np.mean(abs(videos["remove_text"][25].astype(float) - baseline)) < 2
    sounds = {name: output_audio(OUT / (name + ".mp4")) for name in ["assemble_sequence", "add_audio", "remove_audio", "set_audio_gain", "original_gain", "set_audio_fade", "duck_music_for_clip"]}
    base_amp = amplitude(sounds["add_audio"], 440, .7, 1.2)
    assert .18 < base_amp < .22, base_amp
    assert amplitude(sounds["remove_audio"], 440, .7, 1.2) < .0003
    gain_ratio = amplitude(sounds["set_audio_gain"], 440, .7, 1.2) / base_amp
    assert abs(20 * np.log10(gain_ratio) + 12) < .3
    metrics["set_audio_gain"] = {"measured_change_db": float(20 * np.log10(gain_ratio))}
    original_amp = amplitude(sounds["original_gain"], 880, .7, .9)
    assert abs(original_amp - .08 * 10 ** (-6 / 20)) < .004
    fade = sounds["set_audio_fade"]
    head_ratio = amplitude(fade, 440, .01, .03) / amplitude(fade, 440, .7, .9)
    tail_ratio = amplitude(fade, 440, 1.97, 1.99) / amplitude(fade, 440, .7, .9)
    assert head_ratio < .08 and tail_ratio < .08
    metrics["set_audio_fade"] = {"start_relative_amplitude": head_ratio, "end_relative_amplitude": tail_ratio}
    duck = sounds["duck_music_for_clip"]
    ratio = amplitude(duck, 440, 1.2, 1.8) / amplitude(duck, 440, .2, .8)
    assert abs(20 * np.log10(ratio) + 12) < .3
    metrics["duck_music_for_clip"] = {"measured_duck_db": float(20 * np.log10(ratio))}
    # 裁剪一秒后，音频脉冲起点误差应保持在 30 fps 的一帧以内。
    signal = sounds["assemble_sequence"]
    local = signal[48000:48000 + 2400]
    first = int(np.argmax(np.abs(local) > .105))
    assert first <= 1600
    metrics["audio_sync"] = {"pulse_offset_samples": first, "limit_samples": 1600}
    return metrics


def write_gallery(report):
    cards = []
    for c in CASES:
        cards.append(f'<article><h2>{c["name"]}</h2><video controls preload="metadata" src="{c["name"]}.mp4"></video><p>{c["plan"]["total_frames"]} frames · 30 fps · {", ".join(c["tools"])}</p></article>')
    html = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>拾光 · 工具效果验收</title>
<style>body{background:#101214;color:#edefee;font:14px system-ui;margin:32px}h1{color:#b8f292}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:20px}article{background:#191c1f;padding:16px;border:1px solid #30363a;border-radius:12px}h2{font:16px monospace}video{width:100%;max-height:250px;background:#000}p{color:#9aa5a7}a{color:#b8f292}</style>
<h1>拾光 · 后端工具效果验收</h1><p>合成素材自动验收；真实旅行素材待复验。所有视频均由实际工具修改工程，再经 Remotion 与 FFmpeg 导出。</p><p><a href="report.json">查看测量数据</a></p><main>'''+''.join(cards)+"</main></html>"
    (OUT / "index.html").write_text(html, encoding="utf-8")
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    started = time.monotonic()
    OUT.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps({"status": "running", "synthetic_only": True,
        "time_utc": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
    def record_failure(exc_type, exc, tb):
        (OUT / "report.json").write_text(json.dumps({"status": "failed", "synthetic_only": True,
            "error": str(exc), "time_utc": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False, indent=2), encoding="utf-8")
        sys.__excepthook__(exc_type, exc, tb)
    sys.excepthook = record_failure
    print(json.dumps({"事件": "生成确定性测试素材"}, ensure_ascii=False), flush=True)
    generate_fixtures()
    STORE = Store(WORK / "state.sqlite3")
    PID = STORE.create_project("Synthetic acceptance", Composition(width=320, height=180))["project_id"]
    ASSETS = {k: import_media(STORE, PID, "test-inputs/" + filename) for k, filename in {
        "red": "frame-red.mp4", "blue": "frame-blue-24fps.mp4", "grid": "grid.png", "music": "music.wav", "portrait": "portrait-silent.mp4"}.items()}
    assert ASSETS["blue"].total_frames == 60, "24fps master must normalize to 60 frames / 2 seconds"
    print(json.dumps({"事件": "构建逐工具验收案例"}, ensure_ascii=False), flush=True)
    build_cases("--smoke" in sys.argv)
    jobs = [{"manifest": c["manifest"], "output": str(WORK / (c["name"] + ".silent.mp4"))} for c in CASES]
    batch = WORK / "jobs.json"
    batch.write_text(json.dumps(jobs), encoding="utf-8")
    print(json.dumps({"事件": "渲染真实视频", "视频数量": len(jobs)}, ensure_ascii=False), flush=True)
    proc = subprocess.run([shutil.which("node"), ROOT / "node_modules/tsx/dist/cli.mjs", ROOT / "apps/renderer/render.ts", "--batch", batch], cwd=ROOT, timeout=1800)
    if proc.returncode:
        (OUT / "report.json").write_text(json.dumps({"status": "failed", "error": "Remotion process failed",
            "exit_code": proc.returncode}), encoding="utf-8")
        sys.exit(proc.returncode)
    print(json.dumps({"事件": "检查解码帧和音频"}, ensure_ascii=False), flush=True)
    for c, job in zip(CASES, jobs):
        plan = c["plan"]
        ffmpeg("-i", job["output"], "-i", WORK / "public" / plan["mix_file"], "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-ar", 48000,
               "-t", plan["total_frames"] / 30, "-movflags", "+faststart", c["output"])
        verify_output(c["output"], plan["total_frames"], plan["composition"]["width"], plan["composition"]["height"])
    metrics = validate_effects()
    verified = sorted({t for c in CASES for t in c["tools"] if t in TOOLS})
    readers = ["get_project_state", "list_assets", "list_presets", "validate_draft", "get_draft_diff"]
    did = STORE.create_draft(PID)["id"]
    execute(did, "assemble_sequence", {"clips": [case_clip()]})
    for name in readers:
        assert STORE.call(did, name, {})["ok"]
    verified = sorted(set(verified + readers))
    report = {"status": "passed", "time_utc": datetime.now(timezone.utc).isoformat(), "synthetic_only": True,
              "real_media_acceptance": "pending", "tools_verified": verified, "tool_count": len(verified),
              "render_count": len(CASES), "elapsed_seconds": round(time.monotonic() - started, 2), "metrics": metrics,
              "cases": [{k: c[k] for k in ["name", "tools", "output", "warnings"]} for c in CASES]}
    if "--smoke" not in sys.argv:
        assert set(verified) == set(TOOLS), (set(TOOLS) - set(verified))
    write_gallery(report)
    print(json.dumps({"状态": "通过", "工具数量": len(verified), "视频数量": len(CASES), "耗时秒数": report["elapsed_seconds"]}, ensure_ascii=False), flush=True)
