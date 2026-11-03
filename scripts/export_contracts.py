"""将后端工程模型和工具定义导出为前后端共用的 JSON 契约。"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from shiguang.models import Snapshot
from shiguang.tools import definitions

folder = ROOT / "packages/contracts"
folder.mkdir(parents=True, exist_ok=True)
for filename, value in [("project-v1.schema.json", Snapshot.model_json_schema()),
                        ("tools-v1.json", {"version": 1, "tools": definitions()})]:
    (folder / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"事件": "导出契约", "文件": ["project-v1.schema.json", "tools-v1.json"]}, ensure_ascii=False))
