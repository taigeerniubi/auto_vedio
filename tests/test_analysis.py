"""验证模型协议、分析隔离、证据约束、字幕时序与可撤销风格。"""
from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from shiguang.analysis import AnalysisService, search_segments, validate_visual
from shiguang.api import create_app
from shiguang.models import Snapshot, timeline
from shiguang.perception_provider import PerceptionConfig, PerceptionProvider
from shiguang.tools import ToolError
from conftest import call, clip


def visual(ids=("frame_0", "frame_30")):
    return {"description": "海边的人物", "tags": ["海边", "人物"], "subjects": [{"label": "人物", "frame_id": ids[0], "bbox": [.1, .2, .2, .5]}],
            "actions": [{"description": "人物走动", "evidence_frame_ids": list(ids)}], "shot_scale": "wide",
            "apparent_motion": "still", "issues": [], "needs_review": False, "review_reasons": []}


@pytest.mark.parametrize("mutation", ["unknown_frame", "bad_box", "single_frame_action", "review"])
def test_evidence_must_be_real(mutation):
    result = visual()
    if mutation == "unknown_frame": result["subjects"][0]["frame_id"] = "invented"
    if mutation == "bad_box": result["subjects"][0]["bbox"] = [.9, .2, .3, .3]
    if mutation == "single_frame_action": result["actions"][0]["evidence_frame_ids"] = ["frame_0", "frame_0"]
    if mutation == "review": result["needs_review"] = True
    with pytest.raises(ToolError, match="校验"):
        validate_visual(result, ["frame_0", "frame_30"])


def test_compatible_vision_and_speech_protocol(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path.endswith("chat/completions"):
            body = json.loads(request.content)
            assert body["model"] == "visual-model"
            assert body["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(visual())}}]})
        assert b'name="file"' in request.content
        assert b'verbose_json' in request.content
        return httpx.Response(200, json={"text": "你好", "segments": [{"start": 0, "end": 1, "text": "你好"}]})
    transport = httpx.MockTransport(handler)
    vision = PerceptionProvider(PerceptionConfig("vision", "visual-model", "https://example.test/v1", "vision-secret"), transport)
    assert vision.describe([("frame_0", "data:image/jpeg;base64,AA==")], {})["tags"] == ["海边", "人物"]
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"audio")
    speech = PerceptionProvider(PerceptionConfig("speech", "speech-model", "https://speech.test/v1", "speech-secret"), transport)
    assert speech.transcribe(audio)["text"] == "你好"
    assert calls[0].headers["authorization"] == "Bearer vision-secret"
    assert calls[1].headers["authorization"] == "Bearer speech-secret"


def test_text_only_asr_refuses_fake_subtitle_timing(tmp_path):
    provider = PerceptionProvider(PerceptionConfig("speech", "text-only", "https://example.test", "secret"),
        httpx.MockTransport(lambda r: httpx.Response(200, json={"text": "没有时间戳"})))
    file = tmp_path / "audio.wav"
    file.write_bytes(b"audio")
    assert provider.transcribe(file, timed=False)["text"] == "没有时间戳"
    with pytest.raises(ToolError) as error: provider.transcribe(file)
    assert error.value.code == "MODEL_TIMESTAMPS_REQUIRED"


def test_config_does_not_reuse_agent_key(monkeypatch):
    monkeypatch.setenv("SHIGUANG_MODEL_API_KEY", "agent-only")
    monkeypatch.delenv("SHIGUANG_VISION_API_KEY", raising=False)
    with pytest.raises(ToolError): PerceptionConfig.from_env("vision")
    monkeypatch.setenv("SHIGUANG_VISION_MODEL", "vision")
    monkeypatch.setenv("SHIGUANG_VISION_API_KEY", "vision-only")
    monkeypatch.setenv("SHIGUANG_VISION_BASE_URL", "https://user:secret@example.test")
    with pytest.raises(ToolError): PerceptionConfig.from_env("vision")


def seed_analysis(store, pid, aid="a"):
    service = AnalysisService(store)
    record = service.blank(store.assets(pid)[aid])
    record["stages"] = {kind: {"status": "succeeded", "reason_code": None} for kind in ("visual", "speech")}
    record["segments"] = [{"segment_id": "segment_0", "start_frame": 0, "end_frame": 600, "status": "succeeded", "frame_ids": ["frame_0", "frame_30"], "visual": visual()}]
    record["speech"] = {"presence": "detected", "language": "zh", "utterances": [
        {"utterance_id": "u1", "start_ms": 500, "end_ms": 2000, "text": "第一句"},
        {"utterance_id": "u2", "start_ms": 2000, "end_ms": 4000, "text": "第二句"}]}
    service.save(pid, aid, record, publish=True)
    return service, record


def test_subtitles_map_trim_speed_and_preserve_titles(context):
    store, pid, did = context
    seed_analysis(store, pid)
    value = {**clip(end=120), "source_in_frame": 30, "rate_num": 2}
    assert call(store, did, "assemble_sequence", {"clips": [value]})["ok"]
    call(store, did, "add_text", {"text": "标题", "anchor": {"end_frame": 30}})
    assert call(store, did, "generate_subtitles", {})["ok"]
    snapshot = store.draft(did)["snapshot"]
    lines = snapshot["text_items"]
    assert [t["text"] for t in lines] == ["标题", "第一句", "第二句"]
    assert [(t["anchor"]["start_frame"], t["anchor"]["end_frame"]) for t in lines[1:]] == [(0, 15), (15, 45)]
    assert call(store, did, "generate_subtitles", {})["ok"]
    assert store.draft(did)["snapshot"] == snapshot
    committed = store.commit(did)
    assert not store.history(pid, "undo", committed["revision"])["snapshot"]["clips"]


def test_subtitles_failure_is_atomic(context):
    store, pid, did = context
    seed_analysis(store, pid)
    call(store, did, "assemble_sequence", {"clips": [clip(), clip("c2", "b")]})
    before = store.draft(did)
    assert not call(store, did, "generate_subtitles", {})["ok"]
    assert store.draft(did) == before


def test_style_handles_short_clips_audio_and_undo(context):
    store, pid, did = context
    call(store, did, "assemble_sequence", {"clips": [clip(end=12), clip("c2", "b", 12)]})
    call(store, did, "add_audio", {"asset_id": "music", "anchor": {"end_frame": 24}})
    assert call(store, did, "apply_style_preset", {"preset_id": "calm_travel"})["ok"]
    snapshot = Snapshot.model_validate(store.draft(did)["snapshot"])
    assert timeline(snapshot)[1] == 21
    assert snapshot.audio_items[0].anchor.end_frame == 21
    assert call(store, did, "validate_draft", {})["data"]["valid"]


def test_search_and_analysis_are_project_scoped(context):
    store, pid, did = context
    seed_analysis(store, pid)
    assert search_segments(store, pid, "海边")[0]["asset_id"] == "a"
    assert search_segments(store, pid, "第二句")[0]["start_frame"] == 60
    other = store.create_project("other")["project_id"]
    assert not search_segments(store, other, "海边")
    with pytest.raises(ToolError): AnalysisService(store).get(other, "a")
    assert call(store, did, "get_asset_analysis", {"asset_id": "missing"})["error_code"] == "ASSET_NOT_FOUND"


def test_analysis_cached_failure_recovery_and_immutable_versions(context, monkeypatch):
    store, pid, _ = context
    counter = []
    service = AnalysisService(store, lambda kind: SimpleNamespace(config=SimpleNamespace(model="fake")))
    def describe(asset, start, end, provider, samples, token):
        counter.append(start)
        if len(counter) == 2: raise ToolError("MODEL_TIMEOUT", "测试超时")
        ids = [f"frame_{start}", f"frame_{start + 1}"]
        return [{"frame_id": i, "frame_index": start + n, "image_key": "test"} for n, i in enumerate(ids)], visual(ids)
    monkeypatch.setattr(service, "describe_range", describe)
    first = service.analyze(pid, "a", speech=False)
    assert first["stages"]["visual"]["status"] == "partial"
    second = service.analyze(pid, "a", speech=False)
    assert counter == [0, 300, 300]
    assert second["stages"]["visual"]["status"] == "succeeded"
    service.analyze(pid, "a", speech=False)
    assert counter == [0, 300, 300]
    with store.connect() as con:
        old = json.loads(con.execute("SELECT data FROM analysis_versions WHERE id=?", (first["analysis_id"],)).fetchone()[0])
    assert old["stages"]["visual"]["status"] == "partial"
    record = deepcopy(second)
    record["stages"]["speech"]["status"] = "running"
    service.save(pid, "a", record)
    with pytest.raises(ToolError): service.analyze(pid, "a")
    service.recover_interrupted()
    assert service.get(pid, "a")["stages"]["speech"]["reason_code"] == "ANALYSIS_INTERRUPTED"


def test_analysis_api_reports_missing_configuration_and_hides_keys(tmp_path, monkeypatch):
    for kind in ("VISION", "SPEECH"):
        monkeypatch.delenv(f"SHIGUANG_{kind}_API_KEY", raising=False)
    app = create_app(tmp_path / "api.sqlite3", log_dir=tmp_path / "logs")
    client = TestClient(app)
    pid = client.post("/api/v1/projects", json={"name": "test"}).json()["project_id"]
    assert not client.get("/api/v1/perception-status").json()["vision"]["configured"]
    assert client.get(f"/api/v1/projects/{pid}/analyses").json() == {}
    assert len(client.get("/api/v1/presets").json()["styles"]) == 3
    assert client.post(f"/api/v1/projects/{pid}/voice-inputs", content=b"audio").status_code == 503


def test_agent_uses_analysis_to_select_and_edit_atomically(context):
    from shiguang.agent.loop import Agent
    from shiguang.agent.contracts import Command
    from fakes import ScriptSession, tc
    store, pid, _ = context
    seed_analysis(store, pid)
    def select(results):
        candidate = results[0][1]["data"][0]
        assert candidate["asset_id"] == "a"
        return [tc("assemble_sequence", {"clips": [clip(end=120)]})]
    session = ScriptSession([[tc("search_segments", {"query": "海边"})], select,
        [tc("generate_subtitles", {})], [tc("apply_style_preset", {"preset_id": "calm_travel"})],
        [tc("finish", {"summary": "已选择海边片段，应用字幕和舒缓风格"})]])
    captured = []
    def factory(value):
        captured.append(value)
        return session
    result = Agent(store, factory).execute(pid, Command(request_id="roughcut", instruction="用海边素材初剪", expected_revision=0, playhead_frame=30))
    assert result["status"] == "completed", result
    assert captured[0]["播放帧"] == 30 and captured[0]["素材分析"][0]["segments"][0]["description"] == "海边的人物"
    snapshot = store.project(pid)["snapshot"]
    assert len(snapshot["text_items"]) == 2 and snapshot["clips"][0]["motion"]
    assert store.history(pid, "undo", 1)["snapshot"]["clips"] == []


def test_recovery_only_runs_when_application_starts(context, tmp_path):
    store, pid, _ = context
    service, record = seed_analysis(store, pid)
    record["status"] = "running"
    record["stages"]["speech"]["status"] = "running"
    service.save(pid, "a", record)
    app = create_app(store.db, log_dir=tmp_path / "logs")
    assert service.get(pid, "a")["status"] == "running"
    with TestClient(app) as client:
        state = client.get(f"/api/v1/projects/{pid}/assets/a/analysis").json()
        assert state["status"] == "failed"
        assert state["stages"]["speech"]["reason_code"] == "ANALYSIS_INTERRUPTED"
