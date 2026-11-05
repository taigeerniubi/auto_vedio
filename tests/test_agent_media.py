"""通过模拟模型驱动真实工具并验证智能体导出视频的帧数与像素。"""
import json
from pathlib import Path
import shutil

from fastapi.testclient import TestClient
import numpy as np
import pytest

from shiguang.api import create_app
from shiguang.media import ARTIFACTS, DATABASE, FFMPEG, run as run_process, verify_output
from fakes import ScriptSession, tc


@pytest.mark.media
@pytest.mark.skipif(not (DATABASE / "test-inputs/frame-red.mp4").exists(), reason="需要先生成测试素材")
def test_agent_to_real_video(tmp_path):
    def factory(ctx):
        aid = ctx["素材"][0]["asset_id"]
        return ScriptSession([
            [tc("assemble_sequence", {"clips": [{"clip_id": "agent_clip", "asset_id": aid, "kind": "video", "source_out_frame": 45}]})],
            [tc("trim_clip", {"clip_id": "agent_clip", "source_in_frame": 15, "source_out_frame": 45}),
             tc("set_color_preset", {"clip_id": "agent_clip", "preset_id": "monochrome"})],
            [tc("finish", {"summary": "保留第 15 到 44 帧并转为黑白"})],
        ])
    client = TestClient(create_app(tmp_path / "agent-media.sqlite3", session_factory=factory))
    pid = client.post("/api/v1/projects", json={"name": "智能体真实导出", "composition": {"width": 320, "height": 180}}).json()["project_id"]
    prefix = f"/api/v1/projects/{pid}"
    assert client.post(prefix + "/assets/import", json={"path": "test-inputs/frame-red.mp4"}).status_code == 200
    run = client.post(prefix + "/commands", json={"request_id": "agent-media-1", "instruction": "保留素材第 15 到 44 帧并转为黑白", "expected_revision": 0}).json()
    assert run["status"] == "completed", run
    rendered = client.post(prefix + "/render", json={"expected_revision": run["result"]["revision"]})
    assert rendered.status_code == 200, rendered.text
    output = Path(rendered.json()["output"])
    verify_output(output, 30, 320, 180)
    # 解码真实视频帧，检查黑白预设对输出像素产生的效果。
    raw = run_process([FFMPEG, "-v", "error", "-i", output, "-vf", "select=eq(n\\,10)", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], binary=True)
    pixels = np.frombuffer(raw, dtype=np.uint8).reshape(180, 320, 3).astype(float)
    color_difference = float(np.abs(pixels[:, :, 0] - pixels[:, :, 2]).mean())
    assert color_difference < 4
    target = ARTIFACTS / "agent-acceptance"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(output, target / "agent-loop.mp4")
    (target / "report.json").write_text(json.dumps({"状态": "通过", "模型模式": "脚本模拟，未调用真实模型", "帧数": 30,
        "黑白通道平均差": color_difference, "运行记录": run}, ensure_ascii=False, indent=2), encoding="utf-8")
