"""验证调色增量更新、几何缩放、封面引用和旧快照的事务兼容性。"""
import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from shiguang.color_models import COLOR_PRESETS, ColorGrade
from shiguang.models import Snapshot, Transform
from shiguang.tools import definitions, presets
from conftest import call, clip


def test_partial_grade_preserves_other_controls_and_undo(context):
    store, pid, did = context
    assert call(store, did, "assemble_sequence", {"clips": [clip(), clip("c2", "b")]})["ok"]
    assert call(store, did, "set_color_grade", {"clip_ids": ["c1"], "adjustments": {"exposure": .5, "wheels": {"shadows": {"hue": 190, "saturation": .3}}}})["ok"]
    before = store.commit(did)
    did = store.create_draft(pid)["id"]
    assert call(store, did, "set_color_grade", {"clip_ids": ["c1"], "adjustments": {"wheels": {"shadows": {"luminance": .1}}, "hsl": {"red": {"hue": 20}}}, "preset_id": "teal_orange", "intensity": .45})["ok"]
    c = store.draft(did)["snapshot"]["clips"][0]
    assert c["color_grade"]["exposure"] == .5
    assert c["color_grade"]["wheels"]["shadows"] == {"hue": 190., "saturation": .3, "luminance": .1}
    assert call(store, did, "copy_color_grade", {"source_clip_id": "c1", "target_clip_ids": ["c2"]})["ok"]
    after = store.commit(did)
    assert after["snapshot"]["clips"][1]["color_grade"] == c["color_grade"]
    assert after["snapshot"]["clips"][1]["color_preset_intensity"] == .45
    assert store.history(pid, "undo", after["revision"])["snapshot"] == before["snapshot"]


@pytest.mark.parametrize("adjustments", [
    {"exposure": 6}, {"temperature": float("nan")}, {"wheels": {"offset": {"hue": 400}}},
    {"curves": {"red": [{"x": 0, "y": 0}, {"x": .4, "y": .2}, {"x": .3, "y": .5}, {"x": 1, "y": 1}]}},
    {"curves": {"hue_sat": [{"x": 0, "y": .5}, {"x": 1, "y": .8}]}},
])
def test_invalid_grade_is_rejected_atomically(context, adjustments):
    store, _, did = context
    call(store, did, "assemble_sequence", {"clips": [clip()]})
    before = store.draft(did)
    result = call(store, did, "set_color_grade", {"clip_ids": ["c1"], "adjustments": adjustments})
    assert not result["ok"]
    assert store.draft(did) == before


def test_catalog_and_reset_and_scale(context):
    ids = [p["id"] for p in COLOR_PRESETS]
    assert len(ids) == len(set(ids)) == 18
    assert ids == presets()["color"]
    for preset in COLOR_PRESETS:
        ColorGrade.model_validate(preset["grade"])
    store, _, did = context
    call(store, did, "assemble_sequence", {"clips": [clip()]})
    assert call(store, did, "set_transform", {"clip_id": "c1", "transform": {"scale": .45, "scale_x": .7, "scale_y": 1.2, "position_x": .1, "position_y": -.2}})["ok"]
    transform = store.draft(did)["snapshot"]["clips"][0]["transform"]
    assert transform["scale_x"] == .7 and transform["scale_y"] == 1.2
    before = store.draft(did)
    for invalid in [{"scale_x": .09}, {"scale_y": 4.1}, {"scale_y": float("nan")}]:
        assert not call(store, did, "set_transform", {"clip_id": "c1", "transform": invalid})["ok"]
        assert store.draft(did) == before
    assert call(store, did, "set_color_grade", {"clip_ids": ["c1"], "adjustments": {"exposure": 1}})["ok"]
    assert call(store, did, "set_color_grade", {"clip_ids": ["c1"], "adjustments": {}, "replace": True})["ok"]
    assert store.draft(did)["snapshot"]["clips"][0]["color_grade"] == ColorGrade().model_dump()
    with pytest.raises(ValidationError):
        Transform(scale=.09)


def test_cover_frame_image_validation_and_shortening(context):
    store, pid, did = context
    call(store, did, "assemble_sequence", {"clips": [clip()]})
    for cover in [{"frame": 60}, {"source": "image", "asset_id": "a"}, {"source": "image", "asset_id": "other-project-image"}]:
        before = store.draft(did)
        assert not call(store, did, "set_cover", {"cover": cover})["ok"]
        assert store.draft(did) == before
    assert call(store, did, "set_cover", {"cover": {"frame": 58, "title": "A cover"}})["ok"]
    assert call(store, did, "trim_clip", {"clip_id": "c1", "source_out_frame": 30})["ok"]
    assert store.draft(did)["snapshot"]["cover"]["frame"] == 29
    assert call(store, did, "set_cover", {"cover": {"source": "image", "asset_id": "pic"}})["ok"]
    assert call(store, did, "remove_asset", {"asset_id": "pic"})["ok"]
    assert store.draft(did)["snapshot"]["cover"] is None


def test_old_project_defaults_and_diff_are_readable(context):
    store, pid, did = context
    call(store, did, "assemble_sequence", {"clips": [clip()]})
    p = store.commit(did)
    legacy = deepcopy(p["snapshot"])
    legacy.pop("cover")
    for c in legacy["clips"]:
        for name in ["color_grade", "color_preset_intensity"]:
            c.pop(name)
        c["transform"].pop("position_x")
        c["transform"].pop("position_y")
        c["transform"].pop("scale_x")
        c["transform"].pop("scale_y")
    with store.connect() as con:
        con.execute("UPDATE revisions SET snapshot=? WHERE project_id=? AND revision=?", (json.dumps(legacy), pid, p["revision"]))
    assert store.project(pid)["snapshot"]["clips"][0]["color_grade"] == ColorGrade().model_dump()
    restored = store.project(pid)["snapshot"]["clips"][0]["transform"]
    assert restored["scale_x"] == restored["scale_y"] == 1
    did = store.create_draft(pid)["id"]
    assert call(store, did, "get_draft_diff", {})["data"] == {}


def test_agent_uses_same_grade_transform_and_cover_tools(context):
    from shiguang.agent.contracts import Command
    from shiguang.agent.loop import Agent
    from fakes import ScriptSession, tc
    store, pid, _ = context
    session = ScriptSession([
        [tc("assemble_sequence", {"clips": [clip()]})],
        [tc("set_color_grade", {"clip_ids": ["c1"], "adjustments": {"exposure": .3, "hsl": {"orange": {"saturation": -.1}}}, "preset_id": "cream", "intensity": .4})],
        [tc("set_transform", {"clip_id": "c1", "transform": {"scale": .7, "position_x": .1}})],
        [tc("set_cover", {"cover": {"source": "timeline", "frame": 15, "title": "City diary"}})],
        [tc("finish", {"summary": "Applied grade, resized the picture and set a cover."})],
    ])
    result = Agent(store, lambda _: session).execute(pid, Command(request_id="grade-cover-agent", instruction="调暖、缩小画面并设置封面", expected_revision=0))
    assert result["status"] == "completed", result
    snapshot = store.project(pid)["snapshot"]
    assert snapshot["clips"][0]["color_grade"]["exposure"] == .3
    assert snapshot["clips"][0]["transform"]["scale"] == .7
    assert snapshot["cover"]["frame"] == 15
    assert session.closed
