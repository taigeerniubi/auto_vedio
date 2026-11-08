"""验证旧断点的听音证据可跨会话读取，不依赖再次调用音频模型。"""
import json

from shiguang.agent.contracts import Command
from shiguang.agent.prompts import context as build_context
from shiguang.agent.repository import RunRepository
from shiguang.audio_observations import audio_observations
from conftest import call


def saved_audio(store, pid, aid="music", *, ok=True):
    repo = RunRepository(store)
    run, _ = repo.begin(pid, Command(request_id=f"observation-{aid}", instruction="听听节奏", expected_revision=0))
    marks = [{"label": "主节奏", "start_ms": 15000, "end_ms": 16000, "anchor_ms": 15200,
              "anchor_role": "point", "certainty": "medium", "evidence": "鼓点加入"}] * 15
    repo.save_checkpoint(run["run_id"], {"trace": [{"tool": "query_audio", "arguments": {"query": "何时变响"},
        "result": {"ok": ok, "data": {"asset_id": aid, "answer": "约十五秒进入主节奏", "matches": marks,
            "timing": "model_estimate", "timebase": "source_ms", "range": {"start_ms": 0, "end_ms": 20000}}}}]})
    repo.stop(pid, run["run_id"], "failed", {"summary": "随后规划超时"})


def test_historical_audio_is_available_to_new_session_with_times(context):
    store, pid, did = context
    saved_audio(store, pid)
    session = RunRepository(store).create_session(pid)
    ctx = build_context(store, store.draft(did), Command(request_id="new", instruction="之前何时变响？",
        expected_revision=0, session_id=session["session_id"]))
    observations = ctx["已有音频观察"]
    assert len(observations) == 1
    assert observations[0]["matches"][0]["anchor_ms"] == 15200
    assert observations[0]["timing"] == "model_estimate"
    assert observations[0]["matches_total"] == 15
    assert observations[0]["details_truncated"] is True
    assert len(observations[0]["matches"]) == 12
    # 读取完整资料走本地工具，保留所有标记，不创建音频模型。
    result = call(store, did, "get_asset_analysis", {"asset_id": "music"})
    assert result["ok"]
    assert len(result["data"]["audio_queries"][0]["matches"]) == 15


def test_audio_observations_exclude_failure_removed_assets_and_other_projects(context):
    store, pid, did = context
    saved_audio(store, pid, "a", ok=False)
    saved_audio(store, pid)
    assert len(audio_observations(store, pid, store.assets(pid))) == 1
    call(store, did, "remove_asset", {"asset_id": "music"})
    ctx = build_context(store, store.draft(did), Command(request_id="filtered", instruction="配乐", expected_revision=0))
    assert ctx["已有音频观察"] == []
    other = store.create_project("Other")["project_id"]
    assert audio_observations(store, other, store.assets(pid)) == []
