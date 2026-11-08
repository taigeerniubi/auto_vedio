"""验证续跑不受浏览器旧选区影响，新任务仍严格校验片段归属。"""
import pytest

from shiguang.agent.contracts import Command, Limits
from shiguang.agent.loop import Agent
from shiguang.tools import ToolError
from conftest import call, clip
from fakes import ScriptSession, tc


def interrupted_edit(context, tool, arguments):
    """建立正式片段并在一次编辑后触发轮次上限，保留未提交断点。"""
    store, pid, did = context
    assert call(store, did, "assemble_sequence", {"clips": [clip("target"), clip("keep")]})["ok"]
    store.commit(did)
    command = Command(request_id="interrupted-selection", instruction="剪掉选中片段的中间一段",
                      expected_revision=1, selected_clip_ids=["target"])
    run = Agent(store, lambda _: ScriptSession([[tc(tool, arguments, "saved-edit")]]),
                limits=Limits(rounds=1)).execute(pid, command)
    assert run["status"] == "failed" and run["result"]["error_code"] == "ROUND_LIMIT"
    assert store.project(pid)["revision"] == 1
    return store, pid, run


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
def test_resume_after_deleting_selected_clip_ignores_stale_ui_selection(context, explicit, legacy):
    """正式选区已在断点删除时，按钮和自然语言继续都应恢复该草稿。"""
    store, pid, interrupted = interrupted_edit(context, "delete_clip", {"clip_id": "target"})
    if legacy:
        with store.connect() as con:
            con.execute("DELETE FROM agent_checkpoints WHERE run_id=?", (interrupted["run_id"],))
    captured = []

    def factory(ctx):
        captured.append(ctx)
        return ScriptSession([[tc("finish", {"summary": "继续完成删除"}, "resumed-finish")]])

    command = Command(request_id="resume-selection", instruction="继续上次任务", expected_revision=1,
                      selected_clip_ids=["target"],
                      resume_run_id=interrupted["run_id"] if explicit else None)
    agent = Agent(store, factory)
    completed = agent.execute(pid, command)
    assert completed["status"] == "completed", completed
    assert captured[0]["选中片段"] == []
    assert captured[0]["中断任务"]["原始要求"] == "剪掉选中片段的中间一段"
    assert [item["clip_id"] for item in captured[0]["工程"]["clips"]] == ["keep"]
    assert [item["clip_id"] for item in store.project(pid)["snapshot"]["clips"]] == ["keep"]
    assert store.project(pid)["revision"] == 2
    assert not any(event.get("工具") == "delete_clip" for event in completed["events"])
    assert agent.execute(pid, command) == completed
    assert len(captured) == 1


@pytest.mark.parametrize("selection", ["original", "split", "unrelated"])
def test_resume_after_split_does_not_replace_original_target_with_current_selection(context, selection):
    """分割生成的新片段或用户后来选中的其他片段都不能改变续跑目标。"""
    store, pid, interrupted = interrupted_edit(context, "split_clip", {"clip_id": "target", "local_frame": 30})
    saved = store.draft(interrupted["draft_id"])["snapshot"]
    ids = [item["clip_id"] for item in saved["clips"]]
    assert "target" not in ids
    selected = {"original": "target", "split": ids[0], "unrelated": "keep"}[selection]
    captured = []

    def factory(ctx):
        captured.append(ctx)
        return ScriptSession([[tc("finish", {"summary": "接着分割结果完成"}, "resumed-finish")]])

    completed = Agent(store, factory).execute(pid, Command(
        request_id="resume-split", instruction="重试", expected_revision=1,
        resume_run_id=interrupted["run_id"], selected_clip_ids=[selected]))
    assert completed["status"] == "completed", completed
    assert captured[0]["选中片段"] == []
    assert [item["clip_id"] for item in store.project(pid)["snapshot"]["clips"]] == ids


@pytest.mark.parametrize("instruction", ["删除选中片段", "继续上次任务"])
def test_missing_selection_without_resumable_task_still_rejected(context, instruction):
    """没有可恢复断点时，继续文字也不能绕过新任务选区校验。"""
    store, pid, _ = context
    agent = Agent(store, lambda _: pytest.fail("Invalid selection must precede model invocation"))
    with pytest.raises(ToolError) as error:
        agent.execute(pid, Command(request_id="invalid-selection", instruction=instruction,
                                  expected_revision=0, selected_clip_ids=["missing"]))
    assert error.value.code == "INVALID_SELECTION"
    assert agent.runs.conversation(pid)["items"] == []
