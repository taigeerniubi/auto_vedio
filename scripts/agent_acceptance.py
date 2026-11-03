"""使用已配置的真实模型执行隔离剪辑任务并验收导出视频。"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

from shiguang.agent.contracts import Command
from shiguang.agent.loop import Agent
from shiguang.agent.providers import ModelConfig, RemoteSession
from shiguang.media import ARTIFACTS, DATABASE, FFMPEG, import_media, render_snapshot, run, verify_output
from shiguang.models import Clip, Composition, Snapshot
from shiguang.store import Store
from shiguang.tools import ToolError, uid


def main():
    """按明确的命令行选项调用真实模型并写入本次验收报告。

    Returns:
        验收成功时返回零，否则返回非零退出码。
    """
    parser = argparse.ArgumentParser(description="真实模型剪辑验收；会使用模型 API 配额")
    parser.add_argument("--live", action="store_true", help="明确启用真实模型调用")
    args = parser.parse_args()
    if not args.live:
        parser.error("需要显式传入 --live；模拟验收请运行 pytest")
    folder = ARTIFACTS / "agent-live" / uid("acceptance")
    folder.mkdir(parents=True, exist_ok=True)
    report = {"状态": "运行中", "模型模式": "真实模型"}
    report_path = folder / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        config = ModelConfig.from_env()
        report.update({"服务": config.provider, "模型": config.model})
        if not (DATABASE / "test-inputs/frame-red.mp4").is_file():
            raise ToolError("FIXTURE_NOT_FOUND", "请先运行 scripts/media_acceptance.py 生成测试素材")
        store = Store(folder / "state.sqlite3")
        pid = store.create_project("真实模型隔离验收", Composition(width=320, height=180))["project_id"]
        asset = import_media(store, pid, "test-inputs/frame-red.mp4")
        instruction = (f"使用素材 {asset.asset_id} 创建唯一一个视频片段，片段编号为 live_clip。"
                       "原素材入点为第 15 帧、出点为第 45 帧（左闭右开），速度保持 1 倍，不倒放。"
                       "给该片段应用 monochrome 黑白预设，不添加其他效果，完成后调用 finish。")
        result = Agent(store, lambda ctx: RemoteSession(config, ctx)).execute(pid, Command(
            request_id="live-acceptance", instruction=instruction, expected_revision=0))
        report["运行记录"] = result
        if result["status"] != "completed":
            raise ToolError("AGENT_ACCEPTANCE_FAILED", "真实模型未完成指定剪辑任务，请查看运行记录")
        snapshot = Snapshot.model_validate(store.project(pid)["snapshot"])
        if len(snapshot.clips) != 1:
            raise ToolError("SEMANTIC_CHECK_FAILED", "实际片段数量与任务要求不符")
        clip = snapshot.clips[0]
        expected = Clip(clip_id="live_clip", asset_id=asset.asset_id, kind="video", source_in_frame=15,
                        source_out_frame=45, color_preset="monochrome")
        if clip != expected:
            raise ToolError("SEMANTIC_CHECK_FAILED", "实际剪辑参数与任务要求不符")
        if snapshot.text_items or snapshot.audio_items or snapshot.transitions or clip.motion:
            raise ToolError("SEMANTIC_CHECK_FAILED", "模型添加了任务未要求的效果")
        output = folder / "live-loop.mp4"
        render_snapshot(snapshot, store.assets(pid), output)
        verify_output(output, 30, 320, 180)
        import numpy as np
        raw = run([FFMPEG, "-v", "error", "-i", output, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], binary=True)
        pixels = np.frombuffer(raw, dtype=np.uint8).reshape(180, 320, 3).astype(float)
        difference = float(np.abs(pixels[:, :, 0] - pixels[:, :, 2]).mean())
        if difference >= 4:
            raise ToolError("PIXEL_CHECK_FAILED", "实际输出未达到黑白效果要求")
        report.update({"状态": "通过", "帧数": 30, "黑白通道平均差": difference, "输出文件": str(output)})
        return 0
    except Exception as exc:
        report.update({"状态": "失败", "错误代码": getattr(exc, "code", "ACCEPTANCE_ERROR"),
                       "说明": str(exc) if isinstance(exc, ToolError) else "验收程序异常，请检查媒体环境"})
        return 1
    finally:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"事件": "真实模型验收结束", "状态": report["状态"], "报告": str(report_path)}, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
