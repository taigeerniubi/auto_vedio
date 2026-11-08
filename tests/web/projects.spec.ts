// 验证项目删除确认、刷新后的持久化状态及当前工程安全退出。
import { test, expect, type Page } from "@playwright/test";

async function create(page: Page, name: string) {
  const response = await page.request.post("/api/v1/projects", { data: { name } });
  expect(response.ok()).toBe(true);
  return (await response.json()).project_id as string;
}
async function open(page: Page, pid: string) {
  await page.addInitScript(value => localStorage.setItem("shiguang-project", value), pid);
  await page.goto("/");
  await expect(page.getByRole("button", { name: "新会话", exact: true })).toBeEnabled();
}
async function projects(page: Page) {
  await page.locator(".project-name").click();
  const dialog = page.getByRole("dialog", { name: "你的剪辑项目", exact: true });
  await expect(dialog.getByRole("button", { name: "创建项目", exact: true })).toBeEnabled();
  return dialog;
}
const row = (page: Page, pid: string) => page.locator(`.project-row[data-project-id="${pid}"]`);

test("旧项目删除先确认，取消不修改，确认后刷新也不会恢复且保留当前工程", async ({ page }) => {
  const older = await create(page, "以前的剪辑项目");
  const current = await create(page, "正在使用的项目");
  await open(page, current);
  await projects(page);
  await row(page, older).getByRole("button", { name: "删除项目 以前的剪辑项目", exact: true }).click();
  const confirmation = page.getByRole("dialog", { name: "删除项目", exact: true });
  await expect(confirmation).toContainText("以前的剪辑项目");
  await expect(confirmation).toContainText("无法撤销");
  await confirmation.getByRole("button", { name: "取消", exact: true }).click();
  await expect(row(page, older)).toBeVisible();
  expect((await page.request.get(`/api/v1/projects/${older}`)).ok()).toBe(true);
  await row(page, older).getByRole("button", { name: /^删除项目/ }).click();
  await confirmation.getByRole("button", { name: "确认删除项目", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "你的剪辑项目", exact: true })).toBeVisible();
  await expect(row(page, older)).toHaveCount(0);
  await expect(row(page, current)).toContainText("当前项目");
  expect((await page.request.get(`/api/v1/projects/${older}`)).status()).toBe(404);
  expect(await page.evaluate(() => localStorage.getItem("shiguang-project"))).toBe(current);
  await page.screenshot({ path: "artifacts/project-deletion/project-list.png", fullPage: true });
  await page.reload();
  await projects(page);
  await expect(row(page, older)).toHaveCount(0);
  await expect(row(page, current)).toBeVisible();
});

test("删除当前项目清空时间线和恢复入口，窄屏确认正常且可以再建项目", async ({ page }) => {
  const pid = await create(page, "准备删除的当前项目");
  const root = `/api/v1/projects/${pid}`;
  const asset = await (await page.request.post(root + "/assets/import", { data: { path: "test-inputs/grid.png" } })).json();
  const draft = await (await page.request.post(root + "/drafts", { data: {} })).json();
  expect((await page.request.post(`${root}/drafts/${draft.id}/tools`, { data: {
    tool: "insert_clip", op_id: "image", expected_seq: 0,
    arguments: { clip: { clip_id: "image", asset_id: asset.asset_id, kind: "image", still_duration_frames: 30 } },
  } })).ok()).toBe(true);
  await page.request.post(`${root}/drafts/${draft.id}/commit`, { data: {} });
  await open(page, pid);
  await expect(page.locator(".clip-block")).toHaveCount(1);
  await projects(page);
  await expect(row(page, pid)).toContainText("尚未暂存");
  await row(page, pid).getByRole("button", { name: /^删除项目/ }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  const confirmation = page.getByRole("dialog", { name: "删除项目", exact: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "artifacts/project-deletion/confirmation-narrow.png", fullPage: true });
  await confirmation.getByRole("button", { name: "确认删除项目", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "新建或打开项目", exact: true })).toBeVisible();
  await expect(page.locator(".clip-block, .asset-card")).toHaveCount(0);
  expect(await page.evaluate(() => localStorage.getItem("shiguang-project"))).toBeNull();
  await page.reload();
  await expect(page.getByRole("button", { name: "新建或打开项目", exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => localStorage.getItem("shiguang-project"))).toBeNull();
  await projects(page);
  await expect(row(page, pid)).toHaveCount(0);
  await page.getByLabel("项目名称", { exact: true }).fill("删除后的新项目");
  await page.getByRole("button", { name: "创建项目", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator(".project-name")).toContainText("删除后的新项目");
});

test("确认期间被其他页面修改的项目不会删除，刷新列表后可重新确认", async ({ page }) => {
  const pid = await create(page, "并发编辑的项目");
  const root = `/api/v1/projects/${pid}`;
  await open(page, pid);
  await projects(page);
  await row(page, pid).getByRole("button", { name: /^删除项目/ }).click();
  const draft = await (await page.request.post(root + "/drafts", { data: {} })).json();
  expect((await page.request.post(`${root}/drafts/${draft.id}/tools`, { data: {
    tool: "set_composition", arguments: { width: 1080, height: 1920 }, op_id: "external-edit", expected_seq: 0,
  } })).ok()).toBe(true);
  expect((await page.request.post(`${root}/drafts/${draft.id}/commit`, { data: {} })).ok()).toBe(true);
  const confirmation = page.getByRole("dialog", { name: "删除项目", exact: true });
  await confirmation.getByRole("button", { name: "确认删除项目", exact: true }).click();
  await expect(confirmation.getByRole("alert")).toContainText("项目已更新");
  expect((await page.request.get(root)).status()).toBe(200);
  await confirmation.getByRole("button", { name: "取消", exact: true }).click();
  await expect(row(page, pid).getByRole("button", {name: /^删除项目/})).toBeEnabled();
  await row(page, pid).getByRole("button", { name: /^删除项目/ }).click();
  await confirmation.getByRole("button", { name: "确认删除项目", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect((await page.request.get(root)).status()).toBe(404);
});
