"""回归验证预览缓存失效边界及素材删除、关联清理和完整撤销。"""
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from shiguang import media
from shiguang.api import create_app
from shiguang.models import Clip, Snapshot, TextItem, Anchor, validate_snapshot
from shiguang.preview import prepare_preview
from conftest import call, clip


def test_preview_reuses_media_for_visual_edits_and_invalidates_audio(context, tmp_path, monkeypatch):
    store, pid, _ = context
    monkeypatch.setattr(media, "DATABASE", tmp_path)
    monkeypatch.setattr(media, "ARTIFACTS", tmp_path / "artifacts")
    mixed, reversed_clips = [], []

    def mix(snapshot, assets, folder, tag, *, audio_only=False):
        assert audio_only
        mixed.append(snapshot.model_dump())
        (folder / "audio-mix.wav").write_bytes(b"mix")
        return {}, []

    def reverse(item, asset, output):
        reversed_clips.append(item.model_dump())
        output.write_bytes(b"reversed")

    monkeypatch.setattr(media, "prepare_render", mix)
    monkeypatch.setattr(media, "derive_video", reverse)
    s = Snapshot(clips=[Clip(**clip(end=120), rate_num=2)])
    s.clips[0].source_in_frame = 30
    assets = store.assets(pid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: prepare_preview(pid, s, assets)[0], range(2)))
    first = results[0]
    assert results[0] == results[1]
    assert len(mixed) == 1 and not reversed_clips
    assert first["clips"][0]["file"].endswith("/assets/a/file")
    assert first["clips"][0]["previewPlayback"] == {"trimBefore": 30, "playbackRate": 2}
    assert first["total_frames"] == 45
    s.clips[0].color_preset = "warm"
    s.clips[0].transform.scale = 1.2
    s.text_items = [TextItem(text_id="title", text="Updated title", anchor=Anchor(end_frame=30))]
    s.composition.width = 640
    updated, _ = prepare_preview(pid, s, assets)
    assert updated["mix_file"] == first["mix_file"] and len(mixed) == 1
    assert updated["clips"][0]["color_preset"] == "warm"
    s.clips[0].original_audio_gain_db = -6
    changed, _ = prepare_preview(pid, s, assets)
    assert changed["mix_file"] != first["mix_file"] and len(mixed) == 2
    s.clips[0].reverse = True
    backwards, _ = prepare_preview(pid, s, assets)
    assert len(reversed_clips) == 1
    s.clips[0].original_audio_gain_db = -12
    reused, _ = prepare_preview(pid, s, assets)
    assert reused["clips"][0]["file"] == backwards["clips"][0]["file"]
    assert len(reversed_clips) == 1
    assert reused["clips"][0]["previewPlayback"] == {"trimBefore": 0, "playbackRate": 1}
    s.clips[0].source_in_frame = 32
    prepare_preview(pid, s, assets)
    assert len(reversed_clips) == 2


def test_remove_asset_cascades_and_undo_restores_library_and_timeline(context, tmp_path):
    store, pid, did = context
    assert call(store, did, "assemble_sequence", {"clips": [clip(), clip("c2", "b"), clip("c3", "a")]})["ok"]
    assert call(store, did, "set_transition", {"left_clip_id": "c1", "right_clip_id": "c2", "duration_frames": 6})["ok"]
    for anchor in [{"kind": "clip", "clip_id": "c1", "end_frame": 30}, {"end_frame": 170}]:
        assert call(store, did, "add_text", {"text": "Title", "anchor": anchor})["ok"]
    music = call(store, did, "add_audio", {"asset_id": "music", "anchor": {"end_frame": 170}})["changed_ids"][0]
    assert call(store, did, "set_audio_fade", {"audio_id": music, "fade_in_frames": 50, "fade_out_frames": 50})["ok"]
    assert call(store, did, "duck_music_for_clip", {"audio_id": music, "clip_id": "c1"})["ok"]
    original = store.commit(did)["snapshot"]
    did = store.create_draft(pid)["id"]
    assert call(store, did, "remove_asset", {"asset_id": "a"})["ok"]
    removed = store.commit(did)
    snapshot = Snapshot.model_validate(removed["snapshot"])
    assert not validate_snapshot(snapshot, store.assets(pid))
    assert [c.clip_id for c in snapshot.clips] == ["c2"]
    assert not snapshot.transitions and len(snapshot.text_items) == 1
    assert snapshot.text_items[0].anchor.end_frame == 60
    assert snapshot.audio_items[0].anchor.end_frame == 60
    assert snapshot.audio_items[0].fade_out_frames == 10
    assert not snapshot.audio_items[0].ducks
    client = TestClient(create_app(store.db, log_dir=tmp_path / "logs"))
    base = f"/api/v1/projects/{pid}"
    assert "a" not in {a["asset_id"] for a in client.get(base + "/assets").json()}
    did = store.create_draft(pid)["id"]
    assert "a" not in {a["asset_id"] for a in call(store, did, "list_assets", {})["data"]}
    assert not call(store, did, "insert_clip", {"clip": clip("again")})["ok"]
    assert not call(store, did, "remove_asset", {"asset_id": "missing"})["ok"]
    restored = store.history(pid, "undo", removed["revision"])
    assert restored["snapshot"] == original
    assert "a" in {a["asset_id"] for a in client.get(base + "/assets").json()}
    redone = store.history(pid, "redo", restored["revision"])
    assert redone["snapshot"] == removed["snapshot"]
    assert "a" in store.assets(pid)  # 撤销保留母版记录，不触碰磁盘文件。


def test_remove_unused_audio_and_final_visual_asset(context):
    store, pid, did = context
    assert call(store, did, "remove_asset", {"asset_id": "pic"})["ok"]
    assert call(store, did, "assemble_sequence", {"clips": [clip()]})["ok"]
    assert call(store, did, "add_audio", {"asset_id": "music", "anchor": {"end_frame": 60}})["ok"]
    assert call(store, did, "remove_asset", {"asset_id": "music"})["ok"]
    assert call(store, did, "add_text", {"text": "Title", "anchor": {"end_frame": 60}})["ok"]
    assert call(store, did, "remove_asset", {"asset_id": "a"})["ok"]
    final = store.commit(did)["snapshot"]
    assert not final["clips"] and not final["audio_items"] and not final["text_items"]
