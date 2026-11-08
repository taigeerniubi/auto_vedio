"""验证规划预算可配置，复杂任务不再在第八轮被提前截停。"""
import pytest

from shiguang.agent.contracts import Command, Limits
from shiguang.agent.loop import Agent
from fakes import ScriptSession, tc


def test_default_budget_allows_finish_after_eight_rounds(context, monkeypatch):
    monkeypatch.delenv("SHIGUANG_AGENT_MAX_ROUNDS", raising=False)
    monkeypatch.delenv("SHIGUANG_AGENT_TIMEOUT_SECONDS", raising=False)
    store, pid, _ = context
    steps = [[tc("get_project_state", {}, f"inspect-{i}")] for i in range(8)]
    steps.append([tc("finish", {"summary": "检查完成"}, "finish")])
    result = Agent(store, lambda _: ScriptSession(steps)).execute(pid, Command(
        request_id="nine-rounds", instruction="检查工程", expected_revision=0))
    assert Limits.from_env().rounds == 24
    assert result["status"] == "completed"
    assert [e["轮次"] for e in result["events"] if e["事件"] == "请求模型"] == list(range(1, 10))


def test_configured_round_limit_keeps_checkpoint(context, monkeypatch):
    monkeypatch.setenv("SHIGUANG_AGENT_MAX_ROUNDS", "3")
    store, pid, _ = context
    steps = [[tc("get_project_state", {}, f"inspect-{i}")] for i in range(4)]
    agent = Agent(store, lambda _: ScriptSession(steps))
    result = agent.execute(pid, Command(
        request_id="three-rounds", instruction="检查工程", expected_revision=0))
    assert result["result"]["error_code"] == "ROUND_LIMIT"
    assert "3 轮" in result["result"]["summary"]
    assert "继续任务" in result["result"]["summary"]
    assert len(agent.runs.checkpoint(result["run_id"])["trace"]) == 3


@pytest.mark.parametrize("value", ["0", "-1", "3.5", "nan", "inf", ""])
def test_invalid_round_budget_is_rejected(monkeypatch, value):
    monkeypatch.setenv("SHIGUANG_AGENT_MAX_ROUNDS", value)
    with pytest.raises(ValueError, match="SHIGUANG_AGENT_MAX_ROUNDS"):
        Limits.from_env()
