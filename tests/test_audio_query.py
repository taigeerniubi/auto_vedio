"""音频问答的真实媒体解码、模型协议、时间映射和只读约束。"""
import base64
import io
import json
import wave

import httpx
import pytest
from fastapi.testclient import TestClient

from shiguang import audio_query
from shiguang.api import create_app
from shiguang.media import ffmpeg
from shiguang.models import Asset
from shiguang.perception_provider import PerceptionConfig, PerceptionProvider
from shiguang.tools import ToolError


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_query, "DATABASE", tmp_path)
    replies = []
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.path == "/v1/chat/completions"
        assert body["response_format"] == {"type": "json_object"} and body["reasoning_effort"] == "none"
        clip, question = body["messages"][1]["content"]
        assert question["text"] in {"第二次笑声在哪里结束？", "这首音乐是什么风格？"}
        payload = clip["input_audio"]["data"].split(",")[1]
        with wave.open(io.BytesIO(base64.b64decode(payload)), "rb") as wav:
            assert wav.getnframes() == 24000 and wav.getframerate() == 24000
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(replies.pop(0))}}]})

    def provider(kind):
        assert kind == "audio"
        return PerceptionProvider(PerceptionConfig(kind, "qwen3.8-omni-flash", "https://test.invalid/v1", "mock"),
                                  httpx.MockTransport(handle))

    app = create_app(tmp_path / "test.db", perception_factory=provider, log_dir=tmp_path / "logs")
    client = TestClient(app)
    store = app.state.store
    pid = store.create_project("audio")["project_id"]

    def add(aid, path, kind, has_audio):
        store.add_asset(Asset(asset_id=aid, project_id=pid, kind=kind, name=aid, path=str(path),
                              total_frames=60, has_audio=has_audio, sha256="test"))

    wav = tmp_path / "tone.wav"
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=2", wav)
    mp4 = tmp_path / "video.mp4"
    ffmpeg("-f", "lavfi", "-i", "color=s=32x32:r=30:d=2", "-i", wav, "-c:v", "libx264", "-c:a", "aac", mp4)
    add("music", wav, "audio", True)
    add("clip", mp4, "video", True)
    add("silent", mp4, "video", False)
    return client, store, pid, replies, requests


QUESTION = {"query": "第二次笑声在哪里结束？", "start_ms": 500, "end_ms": 1500}
MARK = {"label": "笑声", "start_ms": 100, "end_ms": 400, "anchor_ms": 400, "anchor_role": "end",
        "certainty": "medium", "evidence": "笑声在停顿处结束"}


def test_audio_and_video_queries_use_source_time_and_preserve_draft(workspace):
    client, store, pid, replies, requests = workspace
    reply = {"status": "matched", "answer": "在停顿处结束。", "matches": [MARK]}
    replies.extend([reply, reply])
    response = client.post(f"/api/v1/projects/{pid}/assets/music/audio-query", json=QUESTION)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["range"] == {"start_ms": 500, "end_ms": 1500}
    assert data["matches"] == [{**MARK, "start_ms": 600, "end_ms": 900, "anchor_ms": 900}]
    draft = store.create_draft(pid)
    result = store.call(draft["id"], "query_audio", {"asset_id": "clip", **QUESTION})
    assert result["ok"] and result["data"]["matches"] == data["matches"]
    assert store.draft(draft["id"]) == draft and len(requests) == 2


def test_audio_query_rejects_invalid_ranges_and_foreign_assets(workspace):
    client, store, pid, _, requests = workspace
    def ask(project, aid, **overrides):
        return client.post(f"/api/v1/projects/{project}/assets/{aid}/audio-query", json={**QUESTION, **overrides})
    assert ask(pid, "silent").json()["error_code"] == "NO_AUDIO"
    assert ask(pid, "music", end_ms=2200).status_code == 400
    assert ask(pid, "music", query="   ").status_code == 422
    assert ask(store.create_project("other")["project_id"], "music").status_code == 404
    assert not requests


def test_audio_query_validates_status_and_timestamps(workspace):
    client, store, pid, replies, _ = workspace
    for status, matches, expected in [
        ("matched", [], 200), ("not_found", [], 200), ("uncertain", [MARK], 200),
        ("not_found", [MARK], 502), ("matched", [{**MARK, "anchor_ms": 700}], 502),
        ("matched", [{**MARK, "end_ms": 1001, "anchor_ms": 1001}], 502),
    ]:
        # 每次明确检查一份新的模型回答，缓存行为由独立测试覆盖。
        with store.connect() as con:
            con.execute("DELETE FROM audio_query_cache")
        replies.append({"status": status, "answer": "模型回答", "matches": matches})
        response = client.post(f"/api/v1/projects/{pid}/assets/music/audio-query", json=QUESTION)
        assert response.status_code == expected, response.text


@pytest.mark.parametrize("status", ["matched", "not_found", "uncertain"])
def test_overall_style_uses_untimed_schema_and_stays_readonly(workspace, status):
    client, store, pid, replies, requests = workspace
    replies.extend([{"status": status, "answer": "电子舞曲，合成器与鼓机，节奏轻快。"}] * 2)
    question = {**QUESTION, "query": "这首音乐是什么风格？", "include_timestamps": False}
    before = store.project(pid)
    response = client.post(f"/api/v1/projects/{pid}/assets/music/audio-query", json=question)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == status and data["matches"] == [] and data["timing"] == "not_requested"
    assert data["range"] == {"start_ms": 500, "end_ms": 1500}
    draft = store.create_draft(pid)
    result = store.call(draft["id"], "query_audio", {"asset_id": "music", **question})
    assert result["ok"] and result["data"] == {**data, "cache_hit": True}
    assert store.draft(draft["id"]) == draft and store.project(pid) == before
    assert len(requests) == 1
    for body in requests:
        prompt = body["messages"][0]["content"]
        schema = json.loads(prompt[prompt.index('{"'):])
        assert set(schema["properties"]) == {"status", "answer"} and "$defs" not in schema
        assert "不要返回 matches" in prompt


def test_invalid_model_answer_reports_rule_without_logging_raw_content(workspace, caplog):
    client, store, pid, replies, requests = workspace
    secret = "private-model-answer-do-not-log"
    replies.append({"status": "matched", "answer": secret, "matches": [{**MARK, "anchor_ms": 700}]})
    draft = store.create_draft(pid)
    result = store.call(draft["id"], "query_audio", {"asset_id": "music", **QUESTION})
    assert not result["ok"] and result["error_code"] == "MODEL_ANALYSIS_INVALID"
    assert "锚点超出声音区间" in result["summary"]
    assert result["details"]["validation_errors"][0]["field"] == ["matches", 0]
    assert secret not in json.dumps(result) and secret not in caplog.text
    assert "value_error" in caplog.text
    assert store.draft(draft["id"]) == draft and len(requests) == 1
    # 格式错误仍应拒绝，整体分析不能通过省略正文或填入无关标记绕过校验。
    replies.append({"status": "matched", "matches": []})
    response = client.post(f"/api/v1/projects/{pid}/assets/music/audio-query",
                           json={**QUESTION, "include_timestamps": False})
    assert response.status_code == 502
    assert response.json()["details"]["validation_errors"][0]["field"] == ["answer"]


def test_only_audio_key_is_required(monkeypatch):
    for key in ("API_KEY", "MODEL", "BASE_URL"):
        monkeypatch.delenv("SHIGUANG_AUDIO_" + key, raising=False)
    monkeypatch.setenv("SHIGUANG_SPEECH_API_KEY", "asr-only")
    with pytest.raises(ToolError, match="SHIGUANG_AUDIO_API_KEY"):
        PerceptionConfig.from_env("audio")
    monkeypatch.setenv("SHIGUANG_AUDIO_API_KEY", "audio-only")
    config = PerceptionConfig.from_env("audio")
    assert config.model == "qwen3.8-omni-flash"
    assert config.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_long_source_sent_whole_as_bounded_mp3(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_query, "DATABASE", tmp_path)
    path = tmp_path / "long.wav"
    ffmpeg("-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", 160, path)
    asset = Asset(asset_id="long", project_id="p", kind="audio", has_audio=True, path=str(path),
                  sha256="test", name="long", total_frames=160*30)

    def handle(request):
        block = json.loads(request.content)["messages"][1]["content"][0]["input_audio"]
        assert block["format"] == "mp3" and len(block["data"]) < 10_000_000
        answer = {"status": "not_found", "answer": "没有听到鼓声", "matches": []}
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}]})

    provider = PerceptionProvider(PerceptionConfig("audio", "mock", "https://test.invalid/v1", "test"), httpx.MockTransport(handle))
    data = audio_query.query_audio(asset, audio_query.AudioQuestion(query="鼓点在哪？"), lambda _: provider)
    assert data["range"]["end_ms"] == 160000 and data["status"] == "not_found"
