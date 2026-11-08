// 验证用户可从失败消息明确继续任务，并保留原对话和恢复进度。
import { test, expect, type Page } from "@playwright/test";

async function createSelectionProject(page: Page) {
  const created = await page.request.post("/api/v1/projects", { data: { name: "中断恢复选区验收", composition: { width: 640, height: 360 } } });
  const { project_id: pid } = await created.json();
  const root = `/api/v1/projects/${pid}`;
  const imported = await page.request.post(root + "/assets/import", { data: { path: "test-inputs/grid.png" } });
  expect(imported.ok()).toBe(true);
  const asset = await imported.json();
  const draft = await (await page.request.post(root + "/drafts", { data: {} })).json();
  expect((await page.request.post(`${root}/drafts/${draft.id}/tools`, { data: {
    tool: "assemble_sequence", arguments: { clips: [{ clip_id: "original-selected", asset_id: asset.asset_id, kind: "image", still_duration_frames: 90 }] },
    op_id: "setup-selection", expected_seq: 0,
  } })).ok()).toBe(true);
  expect((await page.request.post(`${root}/drafts/${draft.id}/commit`, { data: {} })).ok()).toBe(true);
  const project = await (await page.request.get(root)).json();
  await page.addInitScript(id => localStorage.setItem("shiguang-project", id), pid);
  await page.route("**/model-status", route => route.fulfill({ json: { configured: true, model: "test", provider: "deepseek" } }));
  return { pid, root, project };
}

test("失败消息可继续原任务，提交明确来源并展示恢复进度", async ({ page }) => {
  const { pid } = await createSelectionProject(page);
  const submitted: Record<string, string>[] = [];
  const history: Record<string, unknown>[] = [];
  await page.route(`**/projects/${pid}/runs?*`, route => route.fulfill({ json: { items: history, next_cursor: null } }));
  await page.route(`**/projects/${pid}/commands`, route => {
    const body = route.request().postDataJSON();
    submitted.push(body);
    const resume = !!body.resume_run_id;
    const run = {
      run_id: resume ? "resumed-ui" : "failed-ui", project_id: pid, session_id: body.session_id || "resume-ui-session",
      instruction: body.instruction, created_at: new Date().toISOString(),
      status: resume ? "completed" : "failed",
      events: resume ? [{ 事件: "恢复任务" }] : [],
      result: resume ? { summary: "已接着原来的草稿完成剪辑", changed: false, revision: 1 }
        : { error_code: "MODEL_TIMEOUT", summary: "模型请求超时" },
      ...(resume ? {} : { ok: false, error_code: "MODEL_TIMEOUT", summary: "模型请求超时" }),
    };
    history.push(run);
    return route.fulfill({ status: resume ? 200 : 504, json: run });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "片段 1 grid.png", exact: true }).click();
  await page.getByLabel("剪辑指令", { exact: true }).fill("剪辑酒吧之夜的素材");
  await page.getByRole("button", { name: "发送剪辑指令" }).click();
  const retry = page.getByRole("button", { name: "继续任务", exact: true });
  await expect(retry).toBeEnabled();
  await expect(page.locator(".selection-chip")).toContainText("grid.png");
  await retry.click();
  await expect(page.locator(".agent-message").last()).toContainText("已接着原来的草稿完成剪辑");
  await expect(page.locator(".user-message")).toHaveCount(2);
  expect(submitted[1].resume_run_id).toBe("failed-ui");
  expect(submitted[0].new_session).toBe(true);
  expect(submitted[1].session_id).toBe("resume-ui-session");
  expect(submitted[0].selected_clip_ids).toEqual(["original-selected"]);
  expect(submitted[1].selected_clip_ids).toEqual([]);
  await page.locator(".execution-details").last().locator("summary").click();
  await expect(page.locator(".execution-list").last()).toContainText("已恢复保存的进度");
});

test("草稿切分清除失效选区，失败回滚后旧片段选区不会复活", async ({ page }) => {
  const { pid, root, project } = await createSelectionProject(page);
  const snapshot = project.snapshot;
  const live = {
    run_id: "selection-live", project_id: pid, session_id: "selection-session", status: "running", result: {},
    events: [{ 事件: "工具执行", 工具: "split_clip", 状态: "成功", draft_seq: 1, changed_ids: ["split-left", "split-right"] }],
    live_draft: { base: 1, seq: 1, snapshot: { ...snapshot, clips: [
      { ...snapshot.clips[0], clip_id: "split-left", still_duration_frames: 45 },
      { ...snapshot.clips[0], clip_id: "split-right", still_duration_frames: 45 },
    ] } },
  };
  const failed = { ...live, status: "failed", live_draft: null, ok: false, error_code: "ROUND_LIMIT",
    summary: "已达到模型循环轮次预算", result: { error_code: "ROUND_LIMIT", summary: "已达到模型循环轮次预算" } };
  let release!: () => void;
  const waiting = new Promise<void>(resolve => { release = resolve; });
  await page.route(`**${root}/runs?*`, route => route.fulfill({ json: { items: [], next_cursor: null } }));
  await page.route(`**${root}/commands/*`, route => route.fulfill({ json: live }));
  await page.route(`**${root}/commands`, async route => { await waiting; await route.fulfill({ status: 502, json: failed }); });
  await page.goto("/");
  await page.getByRole("button", { name: "片段 1 grid.png", exact: true }).click();
  await expect(page.locator(".selection-chip")).toHaveCount(1);
  await page.getByLabel("剪辑指令", { exact: true }).fill("删除中间一段");
  await page.getByRole("button", { name: "发送剪辑指令" }).click();
  try {
    await expect(page.locator(".clip-block")).toHaveCount(2);
    await expect(page.locator(".selection-chip")).toHaveCount(0);
    // 失败会恢复含原片段的正式版本，但已失效的旧选区不能随之复活。
    release();
    await expect(page.getByRole("button", { name: "继续任务", exact: true })).toBeEnabled();
    await expect(page.locator(".clip-block")).toHaveCount(1);
    await expect(page.locator(".selection-chip")).toHaveCount(0);
    await expect(page.locator(".clip-block.selected")).toHaveCount(0);
  } finally {
    release();
  }
});
