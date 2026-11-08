// 验证执行记录不再静默截断，完整步骤与断点恢复提示按任务独立展示。
import { test, expect, type Page } from "@playwright/test";

function planningSteps(rounds: number) {
  return Array.from({ length: rounds }, (_, index) => [
    { 事件: "请求模型", 轮次: index + 1 },
    { 事件: "工具执行", 工具: "validate_draft", 状态: "成功" },
  ]).flat();
}

async function openHistory(page: Page, resumed = false) {
  const created = await page.request.post("/api/v1/projects", { data: { name: "完整处理过程验收" } });
  const { project_id: pid } = await created.json();
  const createdAt = new Date().toISOString();
  await page.addInitScript(id => localStorage.setItem("shiguang-project", id), pid);
  await page.route(`**/projects/${pid}/sessions`, route => route.fulfill({ json: [{
    session_id: "history-session", project_id: pid, title: "检查完整处理过程", has_messages: true,
    running: false, created_at: createdAt, updated_at: createdAt,
  }] }));
  await page.route(`**/projects/${pid}/runs?*`, route => route.fulfill({ json: {
    items: [
      { run_id: "history-first", project_id: pid, session_id: "history-session", status: "failed",
        instruction: "剪掉中间一段", created_at: createdAt,
        result: { error_code: "ROUND_LIMIT", summary: "已达到模型循环轮次预算" }, events: planningSteps(8) },
      { run_id: "history-second", project_id: pid, session_id: "history-session", status: "completed",
        instruction: resumed ? "继续上次任务" : "调整另一个镜头", created_at: createdAt,
        result: { summary: "处理完成", changed: false, revision: 0 },
        events: [...(resumed ? [{ 事件: "恢复任务", source_run_id: "history-first" }] : []), ...planningSteps(8)] },
    ], next_cursor: null,
  } }));
  await page.goto("/");
  await page.getByRole("button", { name: "切换会话：检查完整处理过程", exact: true }).click();
  await expect(page.locator(".conversation-turn")).toHaveCount(2);
}

test("早期步骤明确标为收起，可展开从第一轮查看且不影响其他任务", async ({ page }) => {
  await openHistory(page);
  const first = page.locator('[data-run-id="history-first"]');
  const second = page.locator('[data-run-id="history-second"]');
  await expect(first).toContainText("仅显示最近 7 条，前 9 条已收起");
  await expect(first.locator(".execution-list > div")).toHaveCount(7);
  await expect(first.getByText("第 1 轮规划", { exact: true })).toHaveCount(0);
  const expand = first.getByRole("button", { name: "展开完整过程", exact: true });
  await expect(expand).toHaveAttribute("aria-expanded", "false");
  await expand.click();
  await expect(first.getByText("第 1 轮规划", { exact: true })).toBeVisible();
  await expect(first.locator(".execution-list > div")).toHaveCount(16);
  await expect(first.getByText("第 8 轮规划", { exact: true })).toBeVisible();
  await expect(first.getByRole("button", { name: "收起早期步骤", exact: true })).toHaveAttribute("aria-expanded", "true");
  // 后一条任务保持自己的折叠状态和从第一轮开始的编号。
  await second.locator(".execution-details > summary").click();
  await expect(second.locator(".execution-list > div")).toHaveCount(7);
  await expect(second.getByText("第 1 轮规划", { exact: true })).toHaveCount(0);
  await expect(page.locator(".execution-resumed")).toHaveCount(0);
  await first.getByRole("button", { name: "收起早期步骤", exact: true }).click();
  await expect(first.locator(".execution-list > div")).toHaveCount(7);
  await expect(first).toContainText("前 9 条已收起");
});

test("恢复提示不会被最近七条裁掉，折叠和窄栏中也能辨认续跑任务", async ({ page }) => {
  await openHistory(page, true);
  const first = page.locator('[data-run-id="history-first"]');
  const resumed = page.locator('[data-run-id="history-second"]');
  await expect(resumed.locator(".execution-list")).toBeHidden();
  await expect(resumed.getByText("从上次中断处继续", { exact: true })).toBeVisible();
  await expect(first.locator(".execution-resumed")).toHaveCount(0);
  await resumed.locator(".execution-details > summary").click();
  await expect(resumed).toContainText("前 10 条已收起");
  await expect(resumed.locator(".execution-list")).not.toContainText("已恢复保存的进度");
  await resumed.getByRole("button", { name: "展开完整过程", exact: true }).click();
  await expect(resumed.locator(".execution-list")).toContainText("已恢复保存的进度");
  await expect(resumed.getByText("第 1 轮规划", { exact: true })).toBeVisible();
  await expect(resumed.locator(".execution-list > div")).toHaveCount(17);
  await resumed.getByRole("button", { name: "收起早期步骤", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 1100 });
  await expect(resumed.getByText("从上次中断处继续", { exact: true })).toBeVisible();
  expect(await resumed.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
  await resumed.locator(".execution-history-control").scrollIntoViewIfNeeded();
  await page.locator("#workspace-dock").screenshot({ path: "artifacts/execution-history/narrow.png" });
});
