"""用真实抽帧、音频解码与导出验证分析链路；模型响应为固定测试数据。"""
import base64
import json
from pathlib import Path
import wave

from fastapi.testclient import TestClient
import httpx
import numpy as np
import pytest

from shiguang.api import create_app
from shiguang.media import ARTIFACTS, DATABASE, FFMPEG, render_snapshot, run
from shiguang.models import Snapshot
from shiguang.perception_provider import PerceptionConfig, PerceptionProvider


@pytest.mark.media
def test_real_frames_voice_and_subtitle_export(tmp_path):
    observations = []
    def respond(request):
        observations.append(request.url.path)
        if request.url.path.endswith("chat/completions"):
            data = json.loads(request.content)
            blocks = data["messages"][1]["content"]
            ids = [block["text"].split()[-1] for block in blocks if block["type"] == "text"]
            images = [base64.b64decode(block["image_url"]["url"].split(",")[1]) for block in blocks if block["type"] == "image_url"]
            assert len(images) == 3 and all(image.startswith(b"\xff\xd8") for image in images)
            result = {"description": "测试网格画面", "tags": ["测试"], "subjects": [], "actions": [], "shot_scale": "wide", "apparent_motion": "unknown", "issues": [], "needs_review": False, "review_reasons": []}
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}]})
        assert b"RIFF" in request.content
        return httpx.Response(200, json={"text": "测试字幕", "language": "zh", "segments": [{"start": .2, "end": 1.5, "text": "TEST SUBTITLE"}]})
    def provider(kind):
        return PerceptionProvider(PerceptionConfig(kind, "fake-protocol-test", "https://test.invalid/v1", "test-key"), httpx.MockTransport(respond))
    app = create_app(tmp_path / "media.sqlite3", log_dir=tmp_path / "logs", perception_factory=provider)
    client = TestClient(app)
    pid = client.post("/api/v1/projects", json={"name": "分析媒体验收", "composition": {"width": 320, "height": 180}}).json()["project_id"]
    root = f"/api/v1/projects/{pid}"
    asset = client.post(root + "/assets/import", json={"path": "test-inputs/frame-red.mp4"}).json()
    aid = asset["asset_id"]
    response = client.post(root + f"/assets/{aid}/analysis?background=true", json={})
    assert response.status_code == 200, response.text
    record = client.get(root + f"/assets/{aid}/analysis").json()
    assert all(stage["status"] == "succeeded" for stage in record["stages"].values())
    frame = record["frame_samples"][0]
    assert client.get(root + f"/assets/{aid}/analysis-frames/{record['analysis_id']}/{frame['frame_id']}").status_code == 200
    did = client.post(root + "/drafts").json()["id"]
    def tool(name, args):
        draft = app.state.store.draft(did)
        result = app.state.store.call(did, name, args, "op-" + str(draft["seq"]), draft["seq"])
        assert result["ok"], result
    tool("assemble_sequence", {"clips": [{"clip_id": "v", "asset_id": aid, "kind": "video", "source_in_frame": 15, "source_out_frame": 60}]})
    baseline = Snapshot.model_validate(app.state.store.draft(did)["snapshot"])
    tool("generate_subtitles", {})
    snapshot = Snapshot.model_validate(app.state.store.draft(did)["snapshot"])
    assert snapshot.text_items[0].anchor.start_frame == 0
    assert snapshot.text_items[0].anchor.end_frame == 30
    output = ARTIFACTS / "feature-acceptance" / "subtitles.mp4"
    reference = ARTIFACTS / "feature-acceptance" / "without-subtitles.mp4"
    result = render_snapshot(snapshot, app.state.store.assets(pid), output)
    render_snapshot(baseline, app.state.store.assets(pid), reference)
    assert result["frames"] == 45
    def pixels(path):
        return np.frombuffer(run([FFMPEG, "-v", "error", "-i", path, "-an", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"], binary=True), dtype=np.uint8).reshape(45, 180, 320, 3)
    changed, original = pixels(output), pixels(reference)
    # 字幕显示区有真实像素变化，时间范围外恢复无字幕画面。
    assert np.abs(changed[10, 145:175].astype(float) - original[10, 145:175]).mean() > 2
    assert np.abs(changed[40].astype(float) - original[40]).mean() < 2
    voice = client.post(root + "/voice-inputs", content=(DATABASE / "test-inputs/original.wav").read_bytes())
    assert voice.status_code == 200 and voice.json()["text"] == "测试字幕"
    assert app.state.store.project(pid)["revision"] == 0  # 转写不会自动提交剪辑。
    (output.parent / "analysis-report.json").write_text(json.dumps({"model": "mock responses, no live model", "frames": 45, "subtitle_range": [0, 30], "protocol_calls": observations}, indent=2), encoding="utf-8")
    # 点选中心使用无黑边的原比例缩略图，避免竖屏素材坐标落在填充边上。
    portrait = client.post(root + "/assets/import", json={"path": "test-inputs/portrait-silent.mp4"}).json()["asset_id"]
    thumb = client.get(root + f"/assets/{portrait}/thumbnail?pad=false")
    from PIL import Image
    from io import BytesIO
    size = Image.open(BytesIO(thumb.content)).size
    assert size[0] < size[1] and size[1] == 180
