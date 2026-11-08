"""验证音频观察持久化、精确缓存隔离和并发去重，避免重复付费分析。"""
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event
from types import SimpleNamespace

import pytest

from shiguang import audio_query
from shiguang.analysis import AnalysisService
from shiguang.audio_query import AudioQuestion, audio_query_records
from shiguang.store import Store
from shiguang.tools import ToolError


@pytest.fixture
def cached_service(context, monkeypatch):
    store, pid, _ = context
    config = SimpleNamespace(model="audio-test", base_url="https://test.invalid/v1", api_key="private-audio-key")
    provider = SimpleNamespace(config=config)
    requests = []

    def query(asset, question, factory):
        assert factory("audio") is provider
        requests.append((asset.asset_id, question.model_dump()))
        return {"asset_id": asset.asset_id, "timebase": "source_ms", "range": {"start_ms": 0, "end_ms": 20000},
                "timing": "model_estimate", "status": "matched", "answer": "在 5 秒进入主段。",
                "matches": [{"label": "主段", "start_ms": 5000, "end_ms": 18000, "anchor_ms": 5000,
                             "anchor_role": "start", "certainty": "medium", "evidence": "鼓组变密"}]}

    monkeypatch.setattr(audio_query, "query_audio", query)
    service = AnalysisService(store, provider_factory=lambda _: provider)
    return store, pid, service, config, requests


def test_repeated_question_reuses_complete_source_timing_after_restart(cached_service):
    store, pid, service, _, requests = cached_service
    question = AudioQuestion(query="主段从哪里开始？")
    first = service.query_audio(pid, "music", question)
    reopened = AnalysisService(Store(store.db), provider_factory=service.provider_factory)
    second = reopened.query_audio(pid, "music", question.model_copy(update={"end_ms": 20000}))
    assert first["cache_hit"] is False and second == {**first, "cache_hit": True}
    assert len(requests) == 1
    observations = audio_query_records(reopened.store, pid)
    assert len(observations) == 1
    assert observations[0]["query"] == question.query
    assert observations[0]["matches"][0]["anchor_ms"] == 5000
    with store.connect() as con:
        stored = con.execute("SELECT data FROM audio_query_cache").fetchone()[0]
    assert "private-audio-key" not in stored and "path" not in json.loads(stored)["provenance"]


def test_questions_ranges_timing_models_and_fingerprints_do_not_share_cache(cached_service):
    store, pid, service, config, requests = cached_service
    question = AudioQuestion(query="主段从哪里开始？")
    service.query_audio(pid, "music", question)
    for changes in [{"query": "风格是否适合酒吧？"}, {"start_ms": 1000}, {"end_ms": 12000}, {"include_timestamps": False}]:
        assert service.query_audio(pid, "music", question.model_copy(update=changes))["cache_hit"] is False
    config.model = "another-model"
    assert service.query_audio(pid, "music", question)["cache_hit"] is False
    asset = store.assets(pid)["music"].model_copy(update={"sha256": "new-source-fingerprint"})
    with store.connect() as con:
        con.execute("UPDATE assets SET data=? WHERE id=?", (asset.model_dump_json(), asset.asset_id))
    assert service.query_audio(pid, "music", question)["cache_hit"] is False
    assert len(requests) == 7
    assert len(audio_query_records(store, pid)) == 1


def test_failed_queries_are_not_cached(cached_service, monkeypatch):
    store, pid, service, _, requests = cached_service
    original = audio_query.query_audio
    fail = [True]

    def attempt(*args):
        if fail[0]:
            fail[0] = False
            raise ToolError("MODEL_TIMEOUT", "transient")
        return original(*args)

    monkeypatch.setattr(audio_query, "query_audio", attempt)
    question = AudioQuestion(query="主段从哪里开始？")
    with pytest.raises(ToolError, match="transient"):
        service.query_audio(pid, "music", question)
    assert audio_query_records(store, pid) == []
    assert service.query_audio(pid, "music", question)["cache_hit"] is False
    assert service.query_audio(pid, "music", question)["cache_hit"] is True
    assert len(requests) == 1


def test_project_deleted_during_audio_query_does_not_leave_orphan_cache(cached_service, monkeypatch):
    store, pid, service, _, _ = cached_service
    original = audio_query.query_audio

    def finish_after_delete(*args):
        result = original(*args)
        store.delete_project(pid, 0)
        return result

    monkeypatch.setattr(audio_query, "query_audio", finish_after_delete)
    with pytest.raises(ToolError) as failure:
        service.query_audio(pid, "music", AudioQuestion(query="主段在哪里？"))
    assert failure.value.code == "PROJECT_NOT_FOUND"
    with store.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM audio_query_cache").fetchone()[0] == 0


def test_concurrent_identical_queries_issue_one_model_request(cached_service, monkeypatch):
    store, pid, service, _, requests = cached_service
    entered, release = Event(), Event()
    original = audio_query.query_audio

    def delayed(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(audio_query, "query_audio", delayed)
    another = AnalysisService(store, provider_factory=service.provider_factory)
    question = AudioQuestion(query="主段从哪里开始？")
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(service.query_audio, pid, "music", question)
        assert entered.wait(5)
        second = executor.submit(another.query_audio, pid, "music", question)
        release.set()
        results = [first.result(timeout=5), second.result(timeout=5)]
    assert sorted(result["cache_hit"] for result in results) == [False, True]
    assert len(requests) == 1


def test_cache_does_not_cross_asset_or_project(cached_service):
    store, pid, service, _, requests = cached_service
    question = AudioQuestion(query="主段从哪里开始？")
    service.query_audio(pid, "music", question)
    service.query_audio(pid, "a", question)
    other = store.create_project("other")["project_id"]
    with pytest.raises(ToolError):
        service.query_audio(other, "music", question)
    assert len(requests) == 2 and audio_query_records(store, other) == []
    assert [record["asset_id"] for record in audio_query_records(store, pid, asset_id="music")] == ["music"]


def test_explicit_refresh_replaces_same_cache_entry(cached_service):
    store, pid, service, _, requests = cached_service
    question = AudioQuestion(query="主段从哪里开始？")
    assert service.query_audio(pid, "music", question)["cache_hit"] is False
    assert service.query_audio(pid, "music", question.model_copy(update={"force_refresh": True}))["cache_hit"] is False
    assert service.query_audio(pid, "music", question)["cache_hit"] is True
    assert len(requests) == 2 and len(audio_query_records(store, pid)) == 1
