"""提供隔离的测试数据库、素材记录及工具调用辅助函数。"""
from pathlib import Path
import pytest

from shiguang.models import Asset, Composition
from shiguang.store import Store


@pytest.fixture
def context(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    p = store.create_project("test", Composition(width=320, height=180))
    pid = p["project_id"]
    for aid, kind in [("a", "video"), ("b", "video"), ("pic", "image"), ("music", "audio")]:
        store.add_asset(Asset(asset_id=aid, project_id=pid, kind=kind, name=aid, path=str(tmp_path / aid),
                              sha256="test", width=640, height=360, total_frames=600, has_audio=kind != "image"))
    draft = store.create_draft(pid)
    return store, pid, draft["id"]


def clip(cid="c1", aid="a", end=60):
    return {"clip_id": cid, "asset_id": aid, "kind": "video", "source_in_frame": 0, "source_out_frame": end}


def call(store, did, name, args, op=None):
    return store.call(did, name, args, op or f"op-{store.draft(did)['seq']}", store.draft(did)["seq"])
