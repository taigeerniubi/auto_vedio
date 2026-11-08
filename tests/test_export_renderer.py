"""验证导出档位、缓存复用、进度及剪切变速的真实逐帧一致性。"""
from pathlib import Path
import sys
from time import monotonic

import numpy as np
import pytest

from shiguang import media
from shiguang.models import Asset, Clip, Composition, Snapshot
from shiguang.tools import ToolError


@pytest.mark.parametrize("width,height,resolution,expected", [
    (1920, 1080, "source", (1920, 1080)),
    (1920, 1080, "720p", (1280, 720)),
    (1080, 1920, "1080p", (1080, 1920)),
    (1080, 1920, "2160p", (2160, 3840)),
    (1024, 768, "720p", (960, 720)),
    (1920, 64, "2160p", (3840, 128)),
])
def test_export_resolution_keeps_canvas(width, height, resolution, expected):
    snapshot = Snapshot(composition=Composition(width=width, height=height))
    assert media.export_dimensions(snapshot, resolution) == expected
    assert (snapshot.composition.width, snapshot.composition.height) == (width, height)


def test_export_rejects_unknown_options(tmp_path):
    with pytest.raises(ToolError, match="分辨率"):
        media.export_dimensions(Snapshot(), "480p")
    with pytest.raises(ToolError, match="画质"):
        media.render_snapshot(Snapshot(), {}, tmp_path / "out.mp4", quality="unknown")


def test_renderer_reports_progress_and_retains_process_error(tmp_path):
    progress = []
    code = "import json,sys; print(json.dumps({'event':'render_progress','stage':'rendering','progress':55}),flush=True); sys.stderr.write('renderer failed'); sys.exit(7)"
    with pytest.raises(ToolError, match="renderer failed") as failure:
        media._render_process([sys.executable, "-c", code], progress.append, 5, tmp_path / "stderr.log")
    assert failure.value.code == "MEDIA_PROCESS_FAILED"
    assert progress == [{"stage": "rendering", "progress": 55}]


def test_renderer_timeout_terminates_process(tmp_path):
    started = monotonic()
    with pytest.raises(ToolError) as failure:
        media._render_process([sys.executable, "-c", "import time; time.sleep(30)"],
                              lambda _: None, .2, tmp_path / "stderr.log")
    assert failure.value.code == "MEDIA_TIMEOUT"
    assert monotonic() - started < 10


@pytest.mark.media
def test_export_reuses_mix_and_preserves_trim_speed_reverse_frames(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "DATABASE", tmp_path)
    monkeypatch.setattr(media, "ARTIFACTS", tmp_path / "artifacts")
    source = tmp_path / "master.mp4"
    raw = tmp_path / "source.rgb"
    pixels = np.stack([np.full((90, 160, 3), 20 + frame * 8, np.uint8) for frame in range(30)])
    raw.write_bytes(pixels.tobytes())
    media.ffmpeg("-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "160x90", "-r", "30", "-i", raw,
                 "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", source)
    asset = Asset(asset_id="video", project_id="export-test", kind="video", name="Frames",
                  path=str(source), sha256="frame-fixture", width=160, height=90, total_frames=30, has_audio=False)
    configs = [(5, 11, 1, 1, False), (3, 15, 2, 1, False), (4, 8, 1, 2, False),
               (7, 13, 1, 1, True), (4, 16, 2, 1, True)]
    clips = [Clip(clip_id=f"clip-{i}", asset_id="video", kind="video", source_in_frame=start,
                  source_out_frame=end, rate_num=num, rate_den=den, reverse=reverse)
             for i, (start, end, num, den, reverse) in enumerate(configs)]
    snapshot = Snapshot(composition=Composition(width=320, height=180), clips=clips)
    derived = []
    original_derive = media.derive_video
    def derive(clip, asset, output):
        derived.append(clip.clip_id)
        original_derive(clip, asset, output)
    monkeypatch.setattr(media, "derive_video", derive)
    first, _ = media.prepare_export(snapshot, {"video": asset}, tmp_path / "first")
    second, _ = media.prepare_export(snapshot, {"video": asset}, tmp_path / "second")
    assert first == second
    assert sorted(derived) == ["clip-1", "clip-2", "clip-3", "clip-4"]
    assert first["clips"][0]["previewPlayback"] == {"trimBefore": 5, "playbackRate": 1}
    progress = []
    output = tmp_path / "artifacts" / "frames-720p.mp4"
    result = media.render_snapshot(snapshot, {"video": asset}, output, tmp_path / "work",
                                   resolution="720p", quality="standard", on_progress=progress.append)
    assert result["width"] == 1280 and result["height"] == 720 and result["fps"] == 30
    assert result["bytes"] == output.stat().st_size
    assert sorted(derived) == ["clip-1", "clip-2", "clip-3", "clip-4"]
    assert {p["stage"] for p in progress} >= {"preparing", "bundling", "rendering", "muxing", "verifying", "completed"}
    assert [p["progress"] for p in progress] == sorted(p["progress"] for p in progress)
    assert progress[-1]["progress"] == 100
    assert any(p["rendered_frames"] > 0 and p["progress"] < 92 for p in progress)
    decoded = media.run([media.FFMPEG, "-v", "error", "-i", output, "-vf", "scale=1:1", "-an",
                         "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"], binary=True)
    values = np.frombuffer(decoded, dtype=np.uint8).reshape(-1, 3).mean(axis=1)
    expected = []
    for clip in clips:
        for frame in range(clip.duration):
            offset = frame * clip.rate_num // clip.rate_den
            source_frame = clip.source_out_frame - 1 - offset if clip.reverse else clip.source_in_frame + offset
            expected.append(20 + source_frame * 8)
    assert len(values) == len(expected)
    assert np.max(np.abs(values - expected)) <= 4, (values.tolist(), expected)
