"""验证全部编辑工具的状态变更、约束和版本事务行为。"""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import pytest

from shiguang.models import Snapshot, timeline
from shiguang.store import Store
from shiguang.tools import TOOLS, ToolError
from conftest import clip, call


def initialize(context, clips=None):
    store, pid, did = context
    assert call(store, did, "assemble_sequence", {"clips": clips or [clip(), clip("c2", "b") ]})["ok"]
    return store, pid, did


def test_all_tools_have_typed_schema():
    assert len(TOOLS) == 33
    for tool in TOOLS.values():
        assert tool.arguments.model_json_schema()["additionalProperties"] is False


@pytest.mark.parametrize("name,args,check", [
    ("trim_clip", {"clip_id": "c1", "source_in_frame": 15, "source_out_frame": 45}, lambda s: s.clips[0].duration == 30),
    ("split_clip", {"clip_id": "c1", "local_frame": 30}, lambda s: len(s.clips) == 3 and s.clips[0].derived_from_clip_id == "c1" and timeline(s)[1] == 120),
    ("delete_clip", {"clip_id": "c1"}, lambda s: [c.clip_id for c in s.clips] == ["c2"]),
    ("move_clip", {"clip_id": "c2", "before_clip_id": "c1"}, lambda s: s.clips[0].clip_id == "c2"),
    ("insert_clip", {"clip": clip("c3", "a", 30), "before_clip_id": "c2"}, lambda s: [c.clip_id for c in s.clips] == ["c1", "c3", "c2"]),
    ("replace_clip", {"clip_id": "c1", "replacement": clip("ignored", "b", 30)}, lambda s: s.clips[0].clip_id == "c1" and s.clips[0].asset_id == "b"),
    ("set_playback", {"clip_id": "c1", "rate_num": 3, "rate_den": 2, "reverse": True}, lambda s: s.clips[0].duration == 40 and s.clips[0].reverse),
    ("set_transform", {"clip_id": "c1", "transform": {"scale": 1.5, "rotation_deg": 12}}, lambda s: s.clips[0].transform.scale == 1.5),
    ("apply_motion_preset", {"clip_id": "c1", "motion": {"preset_id": "slow_push_in", "local_end_frame": 60}}, lambda s: s.clips[0].motion.intensity == .12),
    ("set_transition", {"left_clip_id": "c1", "right_clip_id": "c2", "duration_frames": 15}, lambda s: timeline(s)[1] == 105),
    ("set_color_preset", {"clip_id": "c1", "preset_id": "warm"}, lambda s: s.clips[0].color_preset == "warm"),
    ("add_text", {"text": "海边的慢日子", "anchor": {"end_frame": 60}}, lambda s: s.text_items[0].text == "海边的慢日子"),
    ("add_audio", {"asset_id": "music", "anchor": {"end_frame": 120}}, lambda s: len(s.audio_items) == 1),
])
def test_each_independent_write(context, name, args, check):
    store, _, did = initialize(context)
    result = call(store, did, name, args)
    assert result["ok"], result
    assert check(Snapshot.model_validate(store.draft(did)["snapshot"]))


def test_update_remove_text(context):
    store, _, did = initialize(context)
    tid = call(store, did, "add_text", {"text": "old", "anchor": {"end_frame": 60}})["changed_ids"][0]
    assert call(store, did, "update_text", {"text_id": tid, "value": {"text": "new", "anchor": {"end_frame": 30}}})["ok"]
    assert store.draft(did)["snapshot"]["text_items"][0]["text"] == "new"
    assert call(store, did, "remove_text", {"text_id": tid})["ok"]
    assert not store.draft(did)["snapshot"]["text_items"]


def test_audio_tools_and_delete_cascade(context):
    store, _, did = initialize(context)
    aid = call(store, did, "add_audio", {"asset_id": "music", "anchor": {"end_frame": 60}})["changed_ids"][0]
    assert call(store, did, "set_audio_gain", {"target_id": aid, "gain_db": -18})["ok"]
    assert call(store, did, "set_audio_gain", {"target": "original", "target_id": "c1", "gain_db": -6})["ok"]
    assert call(store, did, "set_audio_fade", {"audio_id": aid, "fade_in_frames": 10, "fade_out_frames": 10})["ok"]
    assert call(store, did, "duck_music_for_clip", {"audio_id": aid, "clip_id": "c1", "gain_db": -12})["ok"]
    s = store.draft(did)["snapshot"]
    assert s["audio_items"][0]["gain_db"] == -18
    assert s["audio_items"][0]["fade_in_frames"] == 10
    assert s["audio_items"][0]["ducks"][0]["clip_id"] == "c1"
    call(store, did, "add_text", {"text": "bound", "anchor": {"kind": "clip", "clip_id": "c1", "end_frame": 60}})
    assert call(store, did, "delete_clip", {"clip_id": "c1"})["ok"]
    s = store.draft(did)["snapshot"]
    assert not s["text_items"] and not s["audio_items"][0]["ducks"]
    assert call(store, did, "remove_audio", {"audio_id": aid})["ok"]


@pytest.mark.parametrize("name,args", [
    ("trim_clip", {"clip_id": "c1", "source_out_frame": 9999}),
    ("trim_clip", {"clip_id": "c1", "source_out_frame": -1}),
    ("trim_clip", {"clip_id": "c1", "source_out_frame": 12.5}),
    ("trim_clip", {"clip_id": "c1", "source_out_frame": True}),
    ("trim_clip", {"clip_id": "c1", "source_in_frame": 50, "source_out_frame": 20}),
    ("split_clip", {"clip_id": "c1", "local_frame": 60}),
    ("move_clip", {"clip_id": "c1", "before_clip_id": "missing"}),
    ("set_playback", {"clip_id": "c1", "rate_num": 3}),
    ("insert_clip", {"clip": clip()}),
    ("insert_clip", {"clip": clip("../../outside")}),
    ("insert_clip", {"clip": clip("bad", "cross-project")}),
    ("set_transform", {"clip_id": "c1", "transform": {"scale": float("nan")}}),
    ("set_transform", {"clip_id": "c1", "transform": {"rotation_deg": 90}}),
    ("apply_motion_preset", {"clip_id": "c1", "motion": {"preset_id": "slow_push_in", "local_end_frame": 61}}),
    ("set_transition", {"left_clip_id": "c2", "right_clip_id": "c1", "duration_frames": 10}),
    ("set_transition", {"left_clip_id": "c1", "right_clip_id": "c2", "duration_frames": 60}),
    ("add_text", {"text": "x", "anchor": {"kind": "clip", "clip_id": "missing", "end_frame": 10}}),
    ("add_text", {"text": "x", "anchor": {"end_frame": 1000}}),
    ("add_audio", {"asset_id": "a", "anchor": {"end_frame": 60}}),
    ("set_color_preset", {"clip_id": "c1", "preset_id": "invented"}),
    ("delete_clip", {"clip_id": "missing"}),
    ("delete_clip", {"clip_id": "c1", "shell": "any code"}),
])
def test_invalid_tools_leave_no_partial_write(context, name, args):
    store, _, did = initialize(context)
    before = store.draft(did)
    assert not call(store, did, name, args)["ok"]
    assert store.draft(did) == before


def test_idempotency_conflicts_and_atomic_history(context):
    store, pid, did = initialize(context)
    args = {"clip": clip("c3", "a", 30)}
    result = store.call(did, "insert_clip", args, "fixed", 1)
    assert result["ok"]
    assert store.call(did, "insert_clip", args, "fixed", 1) == result
    assert store.call(did, "insert_clip", {"clip": clip("c4")}, "fixed", 2)["error_code"] == "OP_ID_CONFLICT"
    assert store.call(did, "delete_clip", {"clip_id": "c1"}, "next", 1)["error_code"] == "DRAFT_CONFLICT"
    p = store.commit(did)
    assert p["revision"] == 1
    restored = Store(store.db).project(pid)
    assert restored == p
    assert store.history(pid, "undo", 1)["snapshot"]["clips"] == []
    redone = store.history(pid, "redo", 2)
    assert redone["revision"] == 3 and redone["snapshot"] == p["snapshot"]
    with pytest.raises(ToolError, match="已提交"):
        store.commit(did)


def test_concurrent_commit_cannot_overwrite(context):
    store, pid, d1 = initialize(context)
    d2 = store.create_draft(pid)["id"]
    call(store, d2, "assemble_sequence", {"clips": [clip("different")]})
    def commit(did):
        try:
            return store.commit(did)["revision"]
        except ToolError as e:
            return e.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(commit, [d1, d2]))
    assert sorted(map(str, results)) == ["1", "REVISION_CONFLICT"]


def test_reads_and_unknown_tool(context):
    store, _, did = initialize(context)
    for name in ["get_project_state", "list_assets", "list_presets", "validate_draft", "get_draft_diff"]:
        r = store.call(did, name, {})
        assert r["ok"] and r["draft_seq"] == 1 and r["data"]
    assert store.call(did, "generate_captions", {})["error_code"] == "UNKNOWN_TOOL"


def test_image_split_and_transition_overlap(context):
    store, _, did = initialize(context, [{"clip_id": "p", "asset_id": "pic", "kind": "image", "still_duration_frames": 60}])
    assert call(store, did, "split_clip", {"clip_id": "p", "local_frame": 15})["ok"]
    assert [c["still_duration_frames"] for c in store.draft(did)["snapshot"]["clips"]] == [15, 45]


def test_bound_effects_follow_move_and_block_unsafe_split(context):
    store, _, did = initialize(context)
    call(store, did, "add_text", {"text": "bound", "anchor": {"kind": "clip", "clip_id": "c1", "end_frame": 30}})
    assert call(store, did, "split_clip", {"clip_id": "c1", "local_frame": 15})["error_code"] == "DEPENDENT_EFFECTS"
    assert call(store, did, "move_clip", {"clip_id": "c1"})["ok"]
    assert store.draft(did)["snapshot"]["text_items"][0]["anchor"]["clip_id"] == "c1"


def test_middle_clip_transition_overlap_sum(context):
    store, _, did = initialize(context, [clip("c1"), clip("c2", "b"), clip("c3")])
    assert call(store, did, "set_transition", {"left_clip_id": "c1", "right_clip_id": "c2", "duration_frames": 35})["ok"]
    before = store.draft(did)
    result = call(store, did, "set_transition", {"left_clip_id": "c2", "right_clip_id": "c3", "duration_frames": 30})
    assert not result["ok"] and "TRANSITION_TOO_LONG" in result["summary"]
    assert store.draft(did) == before


def test_seeded_edit_sequences_preserve_invariants(context):
    import random
    from shiguang.models import validate_snapshot
    rng = random.Random(20261103)
    store, pid, did = initialize(context)
    for step in range(100):
        s = Snapshot.model_validate(store.draft(did)["snapshot"])
        ids = [c.clip_id for c in s.clips]
        if len(ids) < 2 or rng.random() < .35:
            name, args = "insert_clip", {"clip": clip(f"random{step}", rng.choice(["a", "b"]), rng.randint(2, 120))}
        elif rng.random() < .5:
            name, args = "move_clip", {"clip_id": rng.choice(ids), "before_clip_id": rng.choice(ids + [None])}
        else:
            name, args = "delete_clip", {"clip_id": rng.choice(ids)}
        assert call(store, did, name, args)["ok"]
        after = Snapshot.model_validate(store.draft(did)["snapshot"])
        assert not validate_snapshot(after, store.assets(pid))
        assert timeline(after)[1] == sum(c.duration for c in after.clips)
