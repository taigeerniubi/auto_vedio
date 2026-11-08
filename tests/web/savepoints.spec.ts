// 验证手动暂存时间、快捷键、刷新恢复、撤销及未应用预览的边界。
import { test, expect, type Page } from "@playwright/test";

async function project(page: Page, name: string) {
  const result = await page.request.post("/api/v1/projects", {data:{name}});
  expect(result.ok()).toBe(true);
  return (await result.json()).project_id as string;
}
async function setup(page: Page) {
  const pid = await project(page, "暂存验收项目");
  const root = `/api/v1/projects/${pid}`;
  const imported = await page.request.post(root + "/assets/import", {data:{path:"test-inputs/grid.png"}});
  const asset = await imported.json();
  const draft = await (await page.request.post(root + "/drafts",{data:{}})).json();
  expect((await page.request.post(`${root}/drafts/${draft.id}/tools`, {data:{
    tool:"insert_clip", op_id:"initial", expected_seq:0,
    arguments:{clip:{clip_id:"initial",asset_id:asset.asset_id,kind:"image",still_duration_frames:90}},
  }})).ok()).toBe(true);
  expect((await page.request.post(`${root}/drafts/${draft.id}/commit`,{data:{}})).ok()).toBe(true);
  await page.addInitScript(id => localStorage.setItem("shiguang-project",id),pid);
  await page.goto("/");
  await expect(page.locator(".clip-block")).toHaveCount(1);
  await expect(page.getByRole("button",{name:"暂存",exact:true})).toBeEnabled();
  return {pid,root};
}
async function save(page: Page, root: string, shortcut=false) {
  const saved = page.waitForResponse(response => response.url().endsWith(root+"/savepoints") && response.request().method()==="POST");
  if (shortcut) await page.keyboard.press("Control+s");
  else await page.getByRole("button",{name:"暂存",exact:true}).click();
  const response = await saved;
  expect(response.ok()).toBe(true);
  await expect(page.locator(".savepoint-banner")).toContainText("已暂存于");
  return await response.json() as {savepoint_id:string;created_at:string;revision:number};
}
async function history(page: Page) {
  await page.getByRole("button",{name:"暂存历史",exact:true}).click();
  const dialog = page.getByRole("dialog",{name:"暂存历史",exact:true});
  await expect(dialog.getByRole("button",{name:"暂存当前剪辑",exact:true})).toBeEnabled();
  return dialog;
}

test("暂存时间替代项目版本，自动编辑不改时间，刷新后可恢复并撤销重做", async ({page}) => {
  const {pid,root} = await setup(page);
  const first = await save(page,root);
  expect((await (await page.request.get(root)).json()).revision).toBe(1);
  await page.getByRole("button",{name:"添加素材 grid.png",exact:true}).click();
  await expect(page.locator(".clip-block")).toHaveCount(2);
  await expect(page.getByRole("button",{name:"暂存",exact:true})).toBeEnabled();
  expect((await (await page.request.get(root)).json()).last_savepoint).toEqual(first);
  await page.locator(".project-name").click();
  const projects = page.getByRole("dialog",{name:"你的剪辑项目",exact:true});
  const row = projects.locator(`[data-project-id="${pid}"]`);
  await expect(row).toContainText("暂存于");
  await expect(row).not.toContainText("版本");
  await expect(row).not.toContainText("Invalid Date");
  await page.screenshot({path:"artifacts/savepoints/project-time.png",fullPage:true});
  await projects.getByRole("button",{name:"关闭弹窗",exact:true}).click();
  const second = await save(page,root,true);
  expect(second.savepoint_id).not.toBe(first.savepoint_id);
  await page.reload();
  await expect(page.getByRole("button",{name:"暂存历史",exact:true})).toBeEnabled();
  let dialog = await history(page);
  await expect(dialog.locator("[data-savepoint-id]")).toHaveCount(2);
  await expect(dialog.locator(`[data-savepoint-id="${first.savepoint_id}"] time`)).toHaveAttribute("datetime",first.created_at);
  await page.screenshot({path:"artifacts/savepoints/history.png",fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(() => document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:"artifacts/savepoints/history-narrow.png",fullPage:true});
  await page.setViewportSize({width:1600,height:1000});
  await dialog.locator(`[data-savepoint-id="${first.savepoint_id}"]`).getByRole("button",{name:"恢复",exact:true}).click();
  let confirmation = page.getByRole("dialog",{name:"恢复暂存",exact:true});
  await confirmation.getByRole("button",{name:"取消",exact:true}).click();
  await expect(page.locator(".clip-block")).toHaveCount(2);
  dialog = page.getByRole("dialog",{name:"暂存历史",exact:true});
  await dialog.locator(`[data-savepoint-id="${first.savepoint_id}"]`).getByRole("button",{name:"恢复",exact:true}).click();
  confirmation = page.getByRole("dialog",{name:"恢复暂存",exact:true});
  await confirmation.getByRole("button",{name:"确认恢复",exact:true}).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator(".clip-block")).toHaveCount(1);
  await expect(page.locator(".savepoint-banner")).toContainText("可撤销");
  await page.getByRole("button",{name:"撤销",exact:true}).click();
  await expect(page.locator(".clip-block")).toHaveCount(2);
  await page.getByRole("button",{name:"重做",exact:true}).click();
  await expect(page.locator(".clip-block")).toHaveCount(1);
  const current = await (await page.request.get(root)).json();
  expect(current.revision).toBe(5);
  expect(current.last_savepoint).toEqual(second);
  expect(await (await page.request.get(root+"/savepoints")).json()).toEqual([second,first]);
});

test("删除暂存记录保持当前剪辑，其他项目有独立的暂存历史", async ({page}) => {
  const {root} = await setup(page);
  const first = await save(page,root);
  const second = await save(page,root);
  const before = await (await page.request.get(root)).json();
  const other = await project(page,"没有暂存的另一个项目");
  await history(page);
  await page.locator(`[data-savepoint-id="${second.savepoint_id}"]`).getByRole("button",{name:/删除暂存/}).click();
  await page.getByRole("dialog",{name:"删除暂存",exact:true}).getByRole("button",{name:"确认删除暂存",exact:true}).click();
  await expect(page.getByRole("dialog",{name:"暂存历史",exact:true}).locator("[data-savepoint-id]")).toHaveCount(1);
  const after = await (await page.request.get(root)).json();
  expect(after.snapshot).toEqual(before.snapshot);
  expect(after.revision).toBe(before.revision);
  expect(after.last_savepoint).toEqual(first);
  await page.getByRole("button",{name:"关闭",exact:true}).click();
  await page.locator(".project-name").click();
  await page.locator(`[data-project-id="${other}"] .project-open`).click();
  const dialog = await history(page);
  await expect(dialog).toContainText("还没有暂存");
  await expect(dialog.locator("[data-savepoint-id]")).toHaveCount(0);
  expect(await (await page.request.get(root+"/savepoints")).json()).toEqual([first]);
});

test("预览中的调整须先应用，暂存不会误报保存未应用的画面", async ({page}) => {
  const {root} = await setup(page);
  await page.getByRole("button",{name:"片段 1 grid.png",exact:true}).click();
  await page.getByRole("button",{name:"模板",exact:true}).click();
  await page.getByRole("button",{name:"缓缓推进",exact:true}).click();
  await expect(page.getByRole("button",{name:"暂存",exact:true})).toBeDisabled();
  await expect(page.locator(".save-status")).toContainText("正在预览调整");
  await page.keyboard.press("Control+s");
  expect(await (await page.request.get(root+"/savepoints")).json()).toEqual([]);
  await page.getByRole("button",{name:"素材",exact:true}).click();
  await expect(page.getByRole("button",{name:"暂存",exact:true})).toBeEnabled();
  await page.setViewportSize({width:390,height:844});
  await expect(page.getByRole("button",{name:"暂存",exact:true})).toBeInViewport();
  await expect(page.getByRole("button",{name:"暂存历史",exact:true})).toBeInViewport();
  await save(page,root);
  await page.screenshot({path:"artifacts/savepoints/toolbar-narrow.png",fullPage:true});
  expect((await (await page.request.get(root)).json()).snapshot.clips[0].motion).toBeNull();
});
