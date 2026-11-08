"""验证转场工具兼容性、边界处理及横竖屏真实导出的视觉结果。"""
from typing import get_args

import numpy as np
import pytest
from PIL import Image, ImageDraw

from shiguang import media
from shiguang.models import Anchor, Asset, AudioItem, Clip, Composition, Motion, Snapshot, TextItem, Transform, Transition, TransitionPreset, timeline
from shiguang.tools import TOOLS, definitions, presets
from conftest import call, clip


@pytest.mark.parametrize("preset", get_args(TransitionPreset))
def test_presets_use_existing_tool_and_shorten_dependencies(context, preset):
    store, pid, did = context
    assert call(store, did, "assemble_sequence", {"clips": [clip(), clip("c2", "b")]})["ok"]
    assert call(store, did, "add_audio", {"asset_id": "music", "anchor": {"end_frame": 120}})["ok"]
    assert call(store, did, "add_text", {"text": "title", "anchor": {"end_frame": 120}})["ok"]
    before = store.commit(did)["snapshot"]
    did = store.create_draft(pid)["id"]
    result = call(store, did, "set_transition", {
        "left_clip_id": "c1", "right_clip_id": "c2", "preset_id": preset, "duration_frames": 12,
    })
    assert result["ok"], result
    after = store.commit(did)
    s = after["snapshot"]
    assert s["transitions"][0]["preset_id"] == preset
    assert timeline(Snapshot.model_validate(s))[1] == 108
    assert s["audio_items"][0]["anchor"]["end_frame"] == 108
    assert s["text_items"][0]["anchor"]["end_frame"] == 108
    assert store.history(pid, "undo", after["revision"])["snapshot"] == before


def test_transition_catalog_schema_and_legacy_defaults(context):
    assert len(TOOLS) == 37
    schema = next(item for item in definitions() if item["name"] == "set_transition")["parameters"]
    assert schema["properties"]["preset_id"]["enum"] == presets()["transition"] == list(get_args(TransitionPreset))
    assert Transition(left_clip_id="a", right_clip_id="b", duration_frames=12).preset_id == "dissolve"
    store, _, did = context
    call(store, did, "assemble_sequence", {"clips": [clip(), clip("c2", "b")]})
    call(store, did, "add_audio", {"asset_id": "music", "anchor": {"end_frame": 120}})
    before = store.draft(did)
    for preset, frames in [("invented", 12), ("slide_left", 60)]:
        result = call(store, did, "set_transition", {"left_clip_id": "c1", "right_clip_id": "c2", "preset_id": preset, "duration_frames": frames})
        assert not result["ok"]
        assert store.draft(did) == before
    assert call(store, did, "set_transition", {"left_clip_id": "c1", "right_clip_id": "c2", "preset_id": "wipe_down", "duration_frames": 12})["ok"]
    assert call(store, did, "set_transition", {"left_clip_id": "c1", "right_clip_id": "c2", "duration_frames": 0})["ok"]
    assert store.draft(did)["snapshot"]["transitions"] == []


@pytest.mark.media
@pytest.mark.parametrize("width,height", [(320, 180), (180, 320)])
def test_all_transition_exports_have_expected_pixels(tmp_path, monkeypatch, width, height):
    monkeypatch.setattr(media, "DATABASE", tmp_path)
    colors = [(220, 30, 30), (30, 30, 220)]
    assets = {}
    for index, color in enumerate(colors):
        path = tmp_path / f"scene-{index}.png"
        image = Image.new("RGB", (width, height), color)
        ImageDraw.Draw(image).rectangle((width // 8 - 3, 0, width // 8 + 3, height), fill=(30, 220, 30))
        image.save(path)
        aid = f"scene{index}"
        assets[aid] = Asset(asset_id=aid, project_id="transition-test", kind="image", name=aid,
                            path=str(path), sha256=aid, width=width, height=height)
    ids = list(get_args(TransitionPreset))
    s = Snapshot(composition=Composition(width=width, height=height), clips=[
        Clip(clip_id=f"c{i}", asset_id=f"scene{i % 2}", kind="image", still_duration_frames=30)
        for i in range(len(ids) + 1)
    ], transitions=[Transition(left_clip_id=f"c{i}", right_clip_id=f"c{i + 1}", preset_id=preset, duration_frames=12)
                    for i, preset in enumerate(ids)])
    output = media.ARTIFACTS / "transition-acceptance" / f"presets-{width}x{height}.mp4"
    media.render_snapshot(s, assets, output, work=tmp_path / "render")
    raw = media.run([media.FFMPEG, "-v", "error", "-i", output, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], binary=True)
    frames = np.frombuffer(raw, dtype=np.uint8).reshape((-1, height, width, 3))
    rows, total = timeline(s)
    assert len(frames) == total
    for index, preset in enumerate(ids):
        start = rows[index + 1]["start_frame"]
        midpoint = frames[start + 6].astype(float)
        outgoing, incoming = colors[index % 2], colors[(index + 1) % 2]
        def pixel(x, y):
            return midpoint[int(height * y), int(width * x)]
        def close(actual, expected):
            assert np.max(np.abs(actual - expected)) < 16, (preset, actual, expected)
        # 入场前后都应回到完整镜头；出场包装不能污染相邻转场。
        close(frames[start - 1, height // 2, width // 2].astype(float), outgoing)
        close(frames[start + 12, height // 2, width // 2].astype(float), incoming)
        if preset == "dissolve":
            close(pixel(.5, .5), (np.array(outgoing) + incoming) / 2)
        elif preset == "fade_black":
            assert midpoint.mean() < 5
        elif preset == "fade_white":
            assert midpoint.mean() > 248
        elif preset == "iris_open":
            close(pixel(.5, .5), incoming)
            close(pixel(.02, .02), outgoing)
        elif preset.startswith(("slide_", "wipe_")):
            direction = preset.split("_")[1]
            point = {"left": (.75, .5), "right": (.25, .5), "up": (.5, .75), "down": (.5, .25)}[direction]
            close(pixel(*point), incoming)
            close(pixel(1 - point[0], 1 - point[1]), outgoing)
        elif preset.startswith("flip_"):
            # 翻面到一半接近侧面；不是普通叠化。
            assert np.mean(np.max(midpoint, axis=2) < 15) > .8
        elif preset.startswith("cube_"):
            assert np.mean(np.max(midpoint, axis=2) > 70) > .3
            assert np.mean(np.max(midpoint, axis=2) < 15) > .01
        elif preset == "clock_wipe":
            close(pixel(.75, .5), incoming)
            close(pixel(.25, .5), outgoing)
        elif preset == "push_cut":
            # 切换点闪光之后必须回归下一镜头，不能永久放大。
            assert np.mean(midpoint) > np.mean(incoming)
        elif preset == "film_burn":
            assert midpoint.mean() > 100
        elif preset == "cross_zoom" or preset.startswith("blur_slide_") or preset == "glitch":
            assert midpoint.mean() > 30
            assert np.mean(np.abs(midpoint - outgoing)) > 20
            assert np.mean(np.abs(midpoint - incoming)) > 20
        if preset == "slide_left":
            close(pixel(.625, .5), (30, 220, 30))  # 标记随画面移动，不能只是擦除。
        if preset == "wipe_left":
            close(pixel(.625, .5), incoming)


@pytest.mark.media
def test_shader_export_uses_current_video_frames_and_preserves_text_audio(tmp_path, monkeypatch):
    """真实视频逐帧变色，覆盖裁切、变速、倒放、运镜和叠加文字，防止高级转场冻结视频。"""
    monkeypatch.setattr(media, "DATABASE", tmp_path)
    width, height = 320, 180
    for i in range(60):
        image = Image.new("RGB", (width, height), (30 + i * 3, 50, 190 - i * 2))
        ImageDraw.Draw(image).rectangle((12, 50, 24, 160), fill=(40, 220, 70))
        image.save(tmp_path / f"frame-{i:02d}.png")
    source, music = tmp_path / "source.mp4", tmp_path / "music.wav"
    media.ffmpeg("-framerate", 30, "-i", tmp_path / "frame-%02d.png", "-c:v", "libx264", "-crf", 12, "-pix_fmt", "yuv420p", source)
    media.ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-ar", 48000, "-ac", 2, music)
    assets = {
        "video": Asset(asset_id="video", project_id="shader-test", kind="video", name="video", path=str(source),
                       sha256="video", width=width, height=height, total_frames=60),
        "music": Asset(asset_id="music", project_id="shader-test", kind="audio", name="music", path=str(music),
                       sha256="music", total_frames=180, has_audio=True),
    }
    ids = ["cross_zoom", "blur_slide_left", "blur_slide_right", "blur_slide_up", "blur_slide_down", "film_burn", "glitch"]
    clips = [Clip(clip_id=f"v{i}", asset_id="video", kind="video", source_in_frame=i,
                  source_out_frame=i + (48 if i % 2 else 24), rate_num=2 if i % 2 else 1, reverse=bool(i % 2),
                  transform=Transform(rotation_deg=8 if i % 2 else 0),
                  motion=Motion(preset_id="slow_push_in", local_end_frame=24, intensity=.2))
             for i in range(8)]
    s = Snapshot(composition=Composition(width=width, height=height), clips=clips,
                 transitions=[Transition(left_clip_id=f"v{i}", right_clip_id=f"v{i+1}", preset_id=p, duration_frames=8)
                              for i, p in enumerate(ids)])
    rows, total = timeline(s)
    s.text_items = [TextItem(text_id="label", text="FRAME TEST", font_size=22, y=.12, anchor=Anchor(end_frame=total))]
    s.audio_items = [AudioItem(audio_id="music", asset_id="music", anchor=Anchor(end_frame=total))]
    output = media.ARTIFACTS / "transition-acceptance" / "video-shaders.mp4"
    work = tmp_path / "render"
    media.render_snapshot(s, assets, output, work=work)

    def decode(path):
        raw = media.run([media.FFMPEG, "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], binary=True)
        return np.frombuffer(raw, dtype=np.uint8).reshape((-1, height, width, 3)).astype(float)

    frames = decode(output)
    derived = [decode(work / "public" / f"render-v{i}.mp4") for i in range(8)]
    center = (height // 2, width // 2)
    for i, preset in enumerate(ids):
        start = rows[i + 1]["start_frame"]
        before = derived[i][16, *center]
        after = derived[i + 1][8, *center]
        assert np.max(np.abs(frames[start, *center] - before)) < 12, preset
        assert np.max(np.abs(frames[start + 8, *center] - after)) < 12, preset
        # 文字独立于转场画面，亮白字不能被漏光/错位/模糊吞掉。
        title = frames[start + 4, 10:32, 65:255]
        assert np.mean(np.min(title, axis=2) > 225) > .05, preset
        assert np.mean(np.abs(frames[start] - frames[start + 4])) > 1, preset
    # 缩放模糊中点的底色应来自两段视频的当前帧，而非首帧或上一次跳转。
    midpoint = rows[1]["start_frame"] + 4
    expected = (derived[0][20, *center] + derived[1][4, *center]) / 2
    assert np.max(np.abs(frames[midpoint, *center] - expected)) < 12
    audio = media.run([media.FFMPEG, "-v", "error", "-i", output, "-vn", "-f", "f32le", "-"], binary=True)
    assert np.sqrt(np.mean(np.frombuffer(audio, dtype=np.float32) ** 2)) > .005
