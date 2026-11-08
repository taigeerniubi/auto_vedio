// 验证素材入口只展示导入历史，支持文件选择和跨工程复用。
import { test, expect, type Page } from "@playwright/test";

async function project(page: Page, name: string) {
  const response = await page.request.post("/api/v1/projects", {data:{name}});
  expect(response.ok()).toBe(true);
  return (await response.json()).project_id as string;
}
async function open(page: Page, pid: string) {
  await page.addInitScript(id => localStorage.setItem("shiguang-project", id), pid);
  await page.goto("/");
  await expect(page.getByRole("button", {name:"导入素材",exact:true})).toBeEnabled();
}

test("没有导入历史时显示文件入口，文件夹快捷按钮和卡片均可选择文件", async ({page}) => {
  const pid = await project(page, "空历史文件导入");
  await page.route("**/api/v1/library", route => route.fulfill({json:[]}));
  await open(page,pid);
  const quick = page.getByRole("button", {name:"从文件导入",exact:true});
  await expect(quick.locator(".icon-folder")).toHaveCount(1);
  const quickPicker = page.waitForEvent("filechooser");
  await quick.click();
  expect((await quickPicker).isMultiple()).toBe(true);
  await page.getByRole("button", {name:"导入素材",exact:true}).click();
  const modal = page.getByRole("dialog",{name:"导入素材"});
  await expect(modal).toContainText("还没有导入记录");
  await expect(modal.getByRole("checkbox")).toHaveCount(0);
  await expect(modal).not.toContainText("database");
  await expect(modal.getByRole("button",{name:/导入所选/})).toBeDisabled();
  await page.screenshot({path:"artifacts/import-library/empty.png",fullPage:true});
  const chooser = page.waitForEvent("filechooser");
  await modal.getByRole("button",{name:/从文件导入/}).click();
  const upload = page.waitForRequest(request => request.url().includes("/assets/upload?"));
  await (await chooser).setFiles("database/test-inputs/grid.png");
  expect((await upload).method()).toBe("POST");
  await expect(modal).toHaveCount(0);
  await expect(page.locator(".asset-card")).toHaveCount(1);
});

test("历史可试听并复用到其他工程，重复选择不复制素材或改变时间线", async ({page}) => {
  const source = await project(page,"历史素材来源");
  const sourceAssets = [];
  for (const name of ["grid.png","music.mp3"]) {
    const imported = await page.request.post(`/api/v1/projects/${source}/assets/import`,{data:{path:`test-inputs/${name}`}});
    expect(imported.ok()).toBe(true);
    sourceAssets.push(await imported.json());
  }
  const records = (await (await page.request.get("/api/v1/library")).json()).filter((record:{project_id:string})=>record.project_id===source);
  expect(records).toHaveLength(2);
  expect(records.every((record:object)=>!("path" in record))).toBe(true);
  await page.route("**/api/v1/library",route=>route.fulfill({json:records}));
  const target = await project(page,"复用历史到新工程");
  await open(page,target);
  await page.getByRole("button",{name:"导入素材",exact:true}).click();
  const modal = page.getByRole("dialog",{name:"导入素材"});
  await expect(modal.getByRole("checkbox")).toHaveCount(2);
  await modal.getByRole("button",{name:"试听 music.mp3",exact:true}).click();
  const audio = page.locator("audio[data-audition]");
  await expect(audio).toHaveAttribute("src",`/api/v1/projects/${source}/assets/${sourceAssets[1].asset_id}/file`);
  await expect.poll(()=>audio.evaluate((node:HTMLAudioElement)=>node.currentTime)).toBeGreaterThan(.1);
  expect(await modal.getByRole("checkbox").evaluateAll(nodes=>nodes.some(node=>(node as HTMLInputElement).checked))).toBe(false);
  await page.screenshot({path:"artifacts/import-library/history.png",fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:"artifacts/import-library/history-narrow.png",fullPage:true});
  await modal.locator(".library-row").filter({hasText:"grid.png"}).getByRole("checkbox").check();
  const reuse = page.waitForRequest(request=>request.url().endsWith(`/projects/${target}/assets/reuse`));
  await modal.getByRole("button",{name:"导入所选 1",exact:true}).click();
  expect((await reuse).postDataJSON()).toEqual({source_project_id:source,source_asset_id:sourceAssets[0].asset_id});
  await expect(page.locator(".asset-card")).toHaveCount(1);
  await expect(page.locator(".statusbar")).toContainText("准备就绪");
  await page.getByRole("button",{name:"导入素材",exact:true}).click();
  await modal.locator(".library-row").filter({hasText:"grid.png"}).getByRole("checkbox").check();
  await modal.getByRole("button",{name:"导入所选 1",exact:true}).click();
  await expect(page.locator(".statusbar")).toContainText("准备就绪");
  await expect(page.locator(".asset-card")).toHaveCount(1);
  const result = await (await page.request.get(`/api/v1/projects/${target}`)).json();
  expect(result.revision).toBe(0);
  expect(result.snapshot.clips).toEqual([]);
});
