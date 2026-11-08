"""解码真实导出，验证所有风格、浮点调色及封面与变速倒放视频的一致性。"""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from shiguang import media
from shiguang.color_models import COLOR_PRESETS, ColorGrade
from shiguang.covers import render_cover
from shiguang.models import Asset, Clip, Composition, Cover, Snapshot, Transform, Transition, timeline


def decode(path, width=320, height=180):
    raw = media.run([media.FFMPEG, "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], binary=True)
    return np.frombuffer(raw, dtype=np.uint8).reshape((-1, height, width, 3)).astype(float)


@pytest.mark.media
def test_color_presets_and_controls_render_actual_pixels(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "DATABASE", tmp_path)
    source = tmp_path / "chart.png"
    image = Image.new("RGB", (320, 180))
    for i, color in enumerate([(128, 128, 128), (150, 55, 40), (40, 140, 60), (35, 70, 150)]):
        ImageDraw.Draw(image).rectangle((i * 80, 0, (i+1)*80-1, 179), fill=color)
    image.save(source)
    asset = Asset(asset_id="chart", project_id="color-media", kind="image", name="chart", path=str(source), sha256="chart", width=320, height=180)
    variants = [{}, {"color_grade": {"exposure": 1}}, {"color_grade": {"exposure": 3, "contrast": .1}},
                {"color_grade": {"hsl": {"red": {"saturation": -1}}}},
                {"color_grade": {"wheels": {"offset": {"hue": 180, "saturation": .4}}}},
                {"color_grade": {"curves": {"red": [{"x": 0, "y": 0}, {"x": .5, "y": .8}, {"x": 1, "y": 1}]}}}]
    variants += [{"color_preset": p["id"]} for p in COLOR_PRESETS]
    snapshot = Snapshot(composition=Composition(width=320, height=180), clips=[
        Clip(clip_id=f"c{i}", asset_id="chart", kind="image", still_duration_frames=6, **variant) for i, variant in enumerate(variants)
    ])
    output = media.ARTIFACTS / "grading-acceptance" / "all-color-presets.mp4"
    media.render_snapshot(snapshot, {"chart": asset}, output, tmp_path / "render")
    frames = decode(output)
    sample = [frames[i*6+3, 90, [40, 120, 200, 280], :] for i in range(len(variants))]
    assert np.max(abs(sample[0][0]-128)) < 4
    assert sample[1][0][0] > 170
    assert 145 < sample[2][0][0] < 155
    assert abs(sample[3][1][0]-sample[3][1][1]) < 25
    assert sample[4][0][1] > sample[4][0][0] + 20
    assert sample[5][0][0] > 195 and abs(sample[5][0][1]-128) < 4
    for preset, pixels in zip(COLOR_PRESETS, sample[6:]):
        assert np.abs(pixels-sample[0]).max() > 2, preset["id"]
        assert pixels.mean() > 20, preset["id"]


@pytest.mark.media
def test_cover_matches_graded_video_with_speed_reverse_transform_and_transition(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "DATABASE", tmp_path)
    for frame in range(40):
        image = Image.new("RGB", (320, 180), (50+frame*3, 75+frame, 130-frame))
        ImageDraw.Draw(image).rectangle((frame*5, 50, frame*5+25, 110), fill=(210, 180, 30))
        image.save(tmp_path / f"frame-{frame:03}.png")
    source = tmp_path / "source.mp4"
    media.ffmpeg("-framerate", 30, "-i", tmp_path / "frame-%03d.png", "-c:v", "libx264", "-crf", 12, "-pix_fmt", "yuv420p", source)
    asset = Asset(asset_id="video", project_id="cover-media", kind="video", name="video", path=str(source), sha256="video", width=320, height=180, total_frames=40)
    snapshot = Snapshot(composition=Composition(width=320, height=180), clips=[
        Clip(clip_id="fast", asset_id="video", kind="video", source_in_frame=4, source_out_frame=40, rate_num=2,
             color_grade=ColorGrade(exposure=.4), transform=Transform(scale=.8, scale_x=.65, scale_y=1.1, position_x=-.05, rotation_deg=8)),
        Clip(clip_id="reverse", asset_id="video", kind="video", source_in_frame=3, source_out_frame=33, reverse=True,
             color_preset="teal_orange", color_preset_intensity=.6),
    ], transitions=[Transition(left_clip_id="fast", right_clip_id="reverse", duration_frames=6, preset_id="cross_zoom")])
    output = media.ARTIFACTS / "grading-acceptance" / "graded-video.mp4"
    media.render_snapshot(snapshot, {"video": asset}, output, tmp_path / "video-render")
    frames = decode(output)
    for frame in [5, 15, 27]:
        snapshot.cover = Cover(frame=frame, shade=0)
        picture = media.ARTIFACTS / "grading-acceptance" / f"video-cover-{frame}.png"
        render_cover(snapshot, {"video": asset}, picture, tmp_path / f"cover-{frame}")
        pixels = np.asarray(Image.open(picture).convert("RGB")).astype(float)
        difference = np.abs(pixels-frames[frame])
        assert difference.mean() < 3, (frame, difference.mean())
        assert np.percentile(difference, 95) < 9, (frame, np.percentile(difference, 95))
