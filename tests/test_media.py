"""验收 HTTP 编辑、草稿提交和 1080p 真实导出的完整链路。"""
from pathlib import Path
import shutil

from fastapi.testclient import TestClient
import pytest

from shiguang.api import create_app
from shiguang.media import ARTIFACTS, DATABASE, probe, verify_output
from shiguang import media
from shiguang.store import Store


@pytest.mark.media
@pytest.mark.parametrize("count_missing", [False, True])
def test_import_preserves_frame_count_without_redecoding_when_metadata_exists(tmp_path, monkeypatch, count_missing):
    """元数据有帧数时直接使用，缺失时回退；两种路径均与真实解码一致。"""
    source = tmp_path / "source.mp4"
    media.ffmpeg("-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24:duration=1",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", source)
    original_probe = media.probe
    calls = []

    def observed_probe(path, count=False):
        calls.append((Path(path).name, count))
        result = original_probe(path, count=count)
        if count_missing and Path(path).name == "master.mp4" and not count:
            result["streams"][0]["nb_frames"] = "N/A"
        return result

    monkeypatch.setattr(media, "probe", observed_probe)
    store = Store(tmp_path / "state.sqlite3")
    pid = store.create_project("Import frame verification")["project_id"]
    asset = media.import_media(store, pid, source.name, tmp_path)
    video = next(s for s in original_probe(asset.path, count=True)["streams"] if s["codec_type"] == "video")
    assert asset.total_frames == int(video["nb_read_frames"]) == 30
    assert video["codec_name"] == "h264" and video["pix_fmt"] == "yuv420p"
    assert video["r_frame_rate"] == "30/1"
    assert (("master.mp4", True) in calls) == count_missing


@pytest.mark.media
@pytest.mark.skipif(not (DATABASE / "test-inputs/frame-red.mp4").exists(), reason="Run scripts/media_acceptance.py to generate fixtures")
def test_http_to_1080p_export(tmp_path):
    client = TestClient(create_app(tmp_path / "integration.sqlite3"))
    p = client.post("/api/v1/projects", json={"name": "1080p API integration"})
    assert p.status_code == 200
    pid = p.json()["project_id"]
    aids = []
    for filename in ["frame-red.mp4", "frame-blue-24fps.mp4", "portrait-silent.mp4", "music.wav"]:
        r = client.post(f"/api/v1/projects/{pid}/assets/import", json={"path": "test-inputs/" + filename})
        assert r.status_code == 200, r.text
        aids.append(r.json()["asset_id"])
    did = client.post(f"/api/v1/projects/{pid}/drafts").json()["id"]
    seq = 0
    def call(tool, arguments):
        nonlocal seq
        response = client.post(f"/api/v1/projects/{pid}/drafts/{did}/tools", json={"tool": tool, "arguments": arguments, "op_id": f"http-{seq}", "expected_seq": seq})
        result = response.json()
        assert result["ok"], result
        seq = result["draft_seq"]
        return result
    call("assemble_sequence", {"clips": [{"clip_id": f"c{i}", "asset_id": aid, "kind": "video", "source_out_frame": 30,
                                          "original_audio_gain_db": -12} for i, aid in enumerate(aids[:3])]})
    call("set_transition", {"left_clip_id": "c0", "right_clip_id": "c1", "duration_frames": 6})
    call("set_transition", {"left_clip_id": "c1", "right_clip_id": "c2", "duration_frames": 6})
    call("apply_motion_preset", {"clip_id": "c1", "motion": {"preset_id": "slow_push_in", "local_end_frame": 30}})
    call("add_text", {"text": "拾光 · 慢慢走", "anchor": {"end_frame": 78}, "font_size": 64})
    aid = call("add_audio", {"asset_id": aids[3], "anchor": {"end_frame": 78}, "gain_db": -18})["changed_ids"][0]
    call("set_audio_fade", {"audio_id": aid, "fade_in_frames": 6, "fade_out_frames": 6})
    call("duck_music_for_clip", {"audio_id": aid, "clip_id": "c1", "gain_db": -12, "ramp_frames": 4})
    revision = client.post(f"/api/v1/projects/{pid}/drafts/{did}/commit").json()["revision"]
    response = client.post(f"/api/v1/projects/{pid}/render", json={"expected_revision": revision})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["revision"] == revision and result["frames"] == 78
    output = Path(result["output"])
    verify_output(output, 78, 1920, 1080)
    target = ARTIFACTS / "tool-acceptance/integration-1080p.mp4"
    shutil.copyfile(output, target)
    assert client.post(f"/api/v1/projects/{pid}/render", json={"expected_revision": 0}).status_code == 409
    print({"事件": "1080p 集成导出完成", "输出文件": str(target)})
