"""画面批注进入原会话，保持帧定位、幂等、恢复与历史隔离。"""
from copy import deepcopy
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient

from shiguang.api import create_app
from shiguang.agent.annotations import capture_annotation
from shiguang.agent.contracts import Command, FrameAnnotation, Limits
from shiguang.agent.loop import Agent
from shiguang.agent.repository import RunRepository
from shiguang.store import dumps
from conftest import call, clip
from fakes import ScriptSession, tc


REGION = {"x": 0.2, "y": 0.3, "width": 0.4, "height": 0.5}


def prepared(context):
    store, pid, did = context
    assert call(store, did, "assemble_sequence", {"clips": [clip(), clip("c2", "b")]})["ok"]
    store.commit(did)
    return store, pid


def test_annotation_and_sidebar_share_context_persist_and_deduplicate(context, tmp_path):
    store, pid = prepared(context)
    captured = []

    def factory(ctx):
        captured.append(ctx)
        return ScriptSession([[tc("finish", {"summary": "已理解这次要求"})]])

    client = TestClient(create_app(store.db, log_dir=tmp_path / "logs", session_factory=factory))
    base = f"/api/v1/projects/{pid}"
    first = client.post(base + "/commands", json={"request_id": "sidebar", "expected_revision": 1,
        "new_session": True, "instruction": "整支短片保持柔和风格"}).json()
    request = {"request_id": "annotation", "expected_revision": 1, "session_id": first["session_id"],
        "instruction": "这里放一句字幕", "selected_clip_ids": ["c2"], "playhead_frame": 90,
        "annotation": {"frame": 15, "region": REGION}}
    reply = client.post(base + "/commands", json=request)
    assert reply.status_code == 200, reply.text
    run = reply.json()
    saved = run["annotation"]
    assert run["session_id"] == first["session_id"]
    assert saved["revision"] == 1 and saved["frame"] == 15
    assert saved["clips"] == [{"clip_id": "c1", "asset_id": "a", "local_frame": 15, "source_frame": 15}]
    assert saved["composition"] == {"width": 320, "height": 180, "fps": 30}
    ctx = captured[-1]
    assert ctx["选中片段"] == ["c1"] and ctx["播放帧"] == 15
    assert ctx["画面批注"] == saved
    assert ctx["近期对话"][0]["指令"] == "整支短片保持柔和风格"
    assert client.post(base + "/commands", json=request).json() == run
    assert len(captured) == 2
    changed = deepcopy(request)
    changed["annotation"]["frame"] = 16
    assert client.post(base + "/commands", json=changed).status_code == 409
    client.post(base + "/commands", json={"request_id": "followup", "expected_revision": 1,
        "session_id": first["session_id"], "instruction": "刚才那块区域的字幕再短些"})
    assert captured[-1]["近期对话"][-1]["画面批注"] == saved
    assert "画面批注" not in captured[-1]
    client.post(base + "/commands", json={"request_id": "separate", "expected_revision": 1,
        "new_session": True, "instruction": "开始另一段讨论"})
    assert captured[-1]["近期对话"] == []
    reopened = TestClient(create_app(store.db, log_dir=tmp_path / "logs"))
    history = reopened.get(base + "/runs", params={"session_id": first["session_id"]}).json()["items"]
    assert history[1]["annotation"] == saved
    assert len(history) == 3
    assert "这里放一句字幕" not in (tmp_path / "logs" / "backend.log").read_text(encoding="utf-8")
    store.delete_project(pid, 1)
    with store.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM agent_run_annotations").fetchone()[0] == 0


def test_annotation_resolves_overlap_speed_reverse_and_image(context):
    store, pid, _ = context
    snapshot = store.project(pid)["snapshot"]
    snapshot["clips"] = [
        {**clip("fast", end=140), "source_in_frame": 20, "rate_num": 2},
        {**clip("reverse", "b", end=150), "source_in_frame": 30, "reverse": True},
        {"clip_id": "still", "asset_id": "pic", "kind": "image", "still_duration_frames": 30},
    ]
    snapshot["transitions"] = [{"left_clip_id": "fast", "right_clip_id": "reverse", "duration_frames": 15}]
    captured = capture_annotation(snapshot, 2, FrameAnnotation(frame=50, region=REGION))
    assert captured["clips"] == [
        {"clip_id": "fast", "asset_id": "a", "local_frame": 50, "source_frame": 120},
        {"clip_id": "reverse", "asset_id": "b", "local_frame": 5, "source_frame": 144},
    ]
    assert len(capture_annotation(snapshot, 2, FrameAnnotation(frame=60, region=REGION))["clips"]) == 1
    still = capture_annotation(snapshot, 2, FrameAnnotation(frame=165, region=REGION))
    assert still["clips"] == [{"clip_id": "still", "asset_id": "pic", "local_frame": 0, "source_frame": None}]


def test_annotation_edits_target_clip_and_uses_normal_undo(context):
    store, pid = prepared(context)
    before = store.project(pid)

    def factory(ctx):
        return ScriptSession([
            [tc("set_color_grade", {"clip_ids": ctx["选中片段"], "adjustments": {"exposure": 0.5}})],
            [tc("finish", {"summary": "已提亮当前片段"})],
        ])

    run = Agent(store, factory).execute(pid, Command(request_id="annotated-edit", instruction="把这段镜头提亮",
        expected_revision=1, selected_clip_ids=["c2"], annotation=FrameAnnotation(frame=15, region=REGION)))
    assert run["status"] == "completed"
    after = store.project(pid)
    assert after["snapshot"]["clips"][0]["color_grade"]["exposure"] == 0.5
    assert after["snapshot"]["clips"][1] == before["snapshot"]["clips"][1]
    assert store.history(pid, "undo", after["revision"])["snapshot"] == before["snapshot"]


@pytest.mark.parametrize("annotation,status", [
    ({"frame": 0, "region": {**REGION, "x": 0.8}}, 422),
    ({"frame": 0, "region": {**REGION, "width": 0}}, 422),
    ({"frame": 0, "region": {**REGION, "height": "0.5"}}, 422),
    ({"frame": -1, "region": REGION}, 422),
    ({"frame": True, "region": REGION}, 422),
    ({"frame": 120, "region": REGION}, 400),
])
def test_invalid_region_or_time_rejected_before_creating_session(context, tmp_path, annotation, status):
    store, pid = prepared(context)
    client = TestClient(create_app(store.db, log_dir=tmp_path / "logs",
        session_factory=lambda _: pytest.fail("Invalid annotation must not call model")))
    base = f"/api/v1/projects/{pid}"
    response = client.post(base + "/commands", json={"request_id": "invalid", "expected_revision": 1,
        "instruction": "这里加字幕", "new_session": True, "annotation": annotation})
    assert response.status_code == status, response.text
    assert client.get(base + "/sessions").json() == []
    assert client.get(base + "/runs").json()["items"] == []


def test_failed_annotation_resumes_with_original_anchor_after_draft_changes(context):
    store, pid = prepared(context)
    command = Command(request_id="annotated-failure", instruction="拆开这里的镜头", expected_revision=1,
        annotation=FrameAnnotation(frame=15, region=REGION))
    failed = Agent(store, lambda _: ScriptSession([[tc("split_clip", {"clip_id": "c1", "local_frame": 30})]]),
        limits=Limits(rounds=1)).execute(pid, command)
    assert failed["status"] == "failed"
    captured = []

    def factory(ctx):
        captured.append(ctx)
        return ScriptSession([[tc("finish", {"summary": "已继续完成"})]])

    completed = Agent(store, factory).execute(pid, Command(request_id="resume", instruction="继续上次任务",
        expected_revision=1, resume_run_id=failed["run_id"], selected_clip_ids=["c2"], playhead_frame=90))
    assert completed["status"] == "completed"
    assert completed["annotation"] == failed["annotation"]
    assert captured[0]["画面批注"] == failed["annotation"]
    assert captured[0]["选中片段"] == []
    assert captured[0]["播放帧"] == 15


def test_legacy_request_hash_and_new_annotation_continue_stay_compatible(context):
    store, pid = prepared(context)
    repo = RunRepository(store)
    plain = Command(request_id="old-format", instruction="原指令", expected_revision=1)
    old, _ = repo.begin(pid, plain)
    # 模拟升级前的幂等摘要，默认空批注不能改变它。
    digest = sha256(dumps(plain.model_dump(exclude={"annotation", "session_id", "new_session", "resume_run_id"})).encode()).hexdigest()
    with store.connect() as con:
        con.execute("UPDATE agent_runs SET hash=? WHERE id=?", (digest, old["run_id"]))
    assert repo.begin(pid, plain)[1] is False
    repo.stop(pid, old["run_id"], "failed", {"summary": "中断"})
    fresh, _ = repo.begin(pid, Command(request_id="fresh", instruction="继续", expected_revision=1,
        annotation=FrameAnnotation(frame=60, region=REGION)))
    assert repo.checkpoint(fresh["run_id"]) is None
    assert fresh["annotation"]["clips"][0]["clip_id"] == "c2"
