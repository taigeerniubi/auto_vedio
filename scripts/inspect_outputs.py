"""复核成片元数据并生成用于人工检查的画面总览图。"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from PIL import Image, ImageDraw
from shiguang.media import ARTIFACTS, FFMPEG, run, verify_output

folder = ARTIFACTS / "tool-acceptance"
report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
for case in report["cases"]:
    plan = json.loads((ROOT / ".cache/media-acceptance" / (case["name"] + ".json")).read_text(encoding="utf-8"))
    verify_output(case["output"], plan["total_frames"], plan["composition"]["width"], plan["composition"]["height"])
integration = folder / "integration-1080p.mp4"
if integration.exists():
    verify_output(integration, 78, 1920, 1080)
    report["http_1080p_integration"] = {"status": "passed", "output": str(integration), "frames": 78,
                                       "width": 1920, "height": 1080, "fps": 30, "audio_sample_rate": 48000}
    (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    html = (folder / "index.html").read_text(encoding="utf-8")
    if 'src="integration-1080p.mp4"' not in html:
        html = html.replace("</main>", '<article><h2>HTTP → 1080p 完整导出</h2><video controls preload="metadata" src="integration-1080p.mp4"></video><p>三段视频 · 中文标题 · 运镜 · 叠化 · 配乐与淡变 · 78 frames / 30 fps</p></article></main>')
        (folder / "index.html").write_text(html, encoding="utf-8")
names = ["assemble_sequence", "set_transform", "motion_slow_push_in", "motion_pan_right", "set_transition", "color_monochrome", "add_text", "update_text", "portrait_output", "integration-1080p"]
sheet = Image.new("RGB", (1000, len(names) * 160), "#101214")
draw = ImageDraw.Draw(sheet)
for row, name in enumerate(names):
    file = folder / (name + ".mp4")
    if not file.exists():
        continue
    draw.text((10, row * 160 + 4), name, fill="#b8f292", font_size=16)
    for i, time in enumerate([0, .83, 1.6]):
        frame = run([FFMPEG, "-v", "error", "-ss", str(time), "-i", file, "-frames:v", "1", "-vf", "scale=300:120:force_original_aspect_ratio=decrease,pad=300:120:(ow-iw)/2:(oh-ih)/2", "-f", "image2pipe", "-vcodec", "png", "pipe:1"], binary=True)
        import io
        sheet.paste(Image.open(io.BytesIO(frame)), (10 + 330 * i, row * 160 + 30))
sheet.save(folder / "contact-sheet.png")
print(json.dumps({"事件": "成片复核完成", "视频数量": len(report['cases']), "总览图": str(folder / 'contact-sheet.png')}, ensure_ascii=False))
