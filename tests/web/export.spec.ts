// 验证导出参数、后台进度恢复和用户选择的本机保存位置。
import { test, expect, type Page } from "@playwright/test";
import type { ExportJob, ExportOptions } from "../../apps/web/src/contracts";

async function exportProject(page: Page) {
  const created = await (await page.request.post("/api/v1/projects", { data: { name: "导出设置验收", composition: { width: 640, height: 360 } } })).json();
  const pid = created.project_id;
  const root = `/api/v1/projects/${pid}`;
  const imported = await page.request.post(root + "/assets/import", { data: { path: "test-inputs/grid.png" } });
  expect(imported.ok()).toBe(true);
  const asset = await imported.json();
  const draft = await (await page.request.post(root + "/drafts", { data: {} })).json();
  expect((await page.request.post(`${root}/drafts/${draft.id}/tools`, { data: {
    tool: "assemble_sequence", arguments: { clips: [{ clip_id: "export-clip", asset_id: asset.asset_id, kind: "image", still_duration_frames: 90 }] },
    op_id: "export-setup", expected_seq: 0,
  } })).ok()).toBe(true);
  expect((await page.request.post(`${root}/drafts/${draft.id}/commit`, { data: {} })).ok()).toBe(true);
  await page.addInitScript(value => localStorage.setItem("shiguang-project", value), pid);
  return { root, pid };
}

function running(pid: string, options: ExportOptions): ExportJob {
  return { export_id: "export-test", project_id: pid, revision: 1, status: "running", stage: "rendering", progress: 40,
    rendered_frames: 36, total_frames: 90, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), options, result: null, error: null };
}
function completed(job: ExportJob): ExportJob {
  return { ...job, status: "completed", stage: "completed", progress: 100, rendered_frames: 90,
    result: { revision: 1, frames: 90, filename: job.options.filename, width: 1280, height: 720, fps: 30, bytes: 1024 * 1024,
      warnings: [], download_url: `/api/v1/projects/${job.project_id}/exports/jobs/${job.export_id}/download` } };
}

test("导出前可选参数，后台进度关闭和刷新后可恢复，完成后才提供下载", async ({ page }) => {
  const { root, pid } = await exportProject(page);
  await page.addInitScript(() => Object.defineProperty(window, "showSaveFilePicker", { configurable: true, value: undefined }));
  let job: ExportJob | null = null;
  const submitted: Record<string, unknown>[] = [];
  await page.route(`**${root}/exports/jobs/latest`, route => route.fulfill({ json: job }));
  await page.route(`**${root}/exports/jobs/export-test`, route => route.fulfill({ json: job }));
  await page.route(`**${root}/exports`, route => {
    const body = route.request().postDataJSON(); submitted.push(body);
    job = running(pid, body);
    return route.fulfill({ status: 202, json: job });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "导出成片", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "导出成片" });
  await expect(dialog).toContainText("浏览器下载目录");
  expect(submitted).toHaveLength(0);
  await dialog.getByLabel("导出分辨率").selectOption("720p");
  await dialog.getByLabel("导出画质").selectOption("compact");
  await dialog.getByLabel("导出文件名").fill("酒吧之夜-分享版");
  await expect(dialog).toContainText("1280 × 720");
  await page.screenshot({ path: "artifacts/export-ui/settings.png", fullPage: true });
  await dialog.getByRole("button", { name: "开始导出", exact: true }).click();
  await expect(dialog.getByRole("progressbar")).toHaveAttribute("value", "40");
  expect(submitted[0]).toEqual({ expected_revision: 1, resolution: "720p", quality: "compact", filename: "酒吧之夜-分享版.mp4" });
  await expect(dialog).toContainText("36 / 90 帧");
  await expect(dialog.getByRole("link", { name: "下载 MP4" })).toHaveCount(0);
  await dialog.getByRole("button", { name: "后台继续导出" }).click();
  await expect(page.getByRole("button", { name: "撤销", exact: true })).toBeEnabled();
  await page.reload();
  await page.getByRole("button", { name: "导出中 40%", exact: true }).click();
  await expect(dialog).toContainText("渲染画面");
  job = completed(job!);
  await expect(dialog).toContainText("成片已就绪");
  await expect(dialog.getByRole("link", { name: "下载 MP4" })).toHaveAttribute("download", "酒吧之夜-分享版.mp4");
  await expect(dialog).toContainText("1.0 MB");
  expect(submitted).toHaveLength(1);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "artifacts/export-ui/narrow-complete.png", fullPage: true });
});

test("取消保存位置不开始导出，保存失败可单独重试且不重新渲染", async ({ page }) => {
  const { root, pid } = await exportProject(page);
  await page.addInitScript(() => {
    const state = { picks: 0, writes: 0, bytes: 0 };
    (window as unknown as { exportTest: typeof state }).exportTest = state;
    Object.defineProperty(window, "showSaveFilePicker", { configurable: true, value: async () => {
      state.picks++;
      if (state.picks === 1) throw new DOMException("User cancelled", "AbortError");
      return { name: "选定位置.mp4", createWritable: async () => {
        state.writes++;
        if (state.writes === 1) throw new DOMException("文件被其他程序占用", "NotAllowedError");
        return new WritableStream<Uint8Array>({ write(chunk) { state.bytes += chunk.byteLength; } });
      } };
    } });
  });
  let job: ExportJob | null = null;
  let submits = 0;
  await page.route(`**${root}/exports/jobs/latest`, route => route.fulfill({ json: job }));
  await page.route(`**${root}/exports`, route => {
    submits++; job = completed(running(pid, route.request().postDataJSON()));
    return route.fulfill({ status: 202, json: job });
  });
  await page.route(`**${root}/exports/jobs/export-test/download`, route => route.fulfill({ contentType: "video/mp4", body: Buffer.from([1, 2, 3, 4]) }));
  await page.goto("/");
  await page.getByRole("button", { name: "导出成片", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "导出成片" });
  await dialog.getByRole("button", { name: "选择保存位置" }).click();
  await expect(dialog.getByRole("alert")).toHaveCount(0);
  expect(submits).toBe(0);
  await dialog.getByRole("button", { name: "选择保存位置" }).click();
  await expect(dialog).toContainText("已选择：选定位置.mp4");
  await expect(dialog.getByLabel("导出文件名")).toHaveValue("选定位置.mp4");
  await dialog.getByRole("button", { name: "开始导出", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("成片已导出，保存未完成");
  await expect(dialog.getByRole("link", { name: "下载 MP4" })).toBeVisible();
  await dialog.getByRole("button", { name: "保存到…", exact: true }).click();
  await expect(dialog).toContainText("已保存到所选位置");
  expect(await page.evaluate(() => (window as unknown as { exportTest: { bytes: number } }).exportTest.bytes)).toBe(4);
  expect(submits).toBe(1);
});

test("导出失败显示原因并允许重新导出", async ({ page }) => {
  const { root, pid } = await exportProject(page);
  let job: ExportJob = { ...running(pid, { resolution: "source", quality: "standard", filename: "失败后重试.mp4" }),
    status: "failed", stage: "failed", error: { code: "RENDER_FAILED", message: "视频渲染失败，请重试。" } };
  await page.route(`**${root}/exports/jobs/latest`, route => route.fulfill({ json: job }));
  let submits = 0;
  await page.route(`**${root}/exports`, route => {
    submits++; job = completed(running(pid, route.request().postDataJSON()));
    return route.fulfill({ json: job });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "导出成片", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "导出成片" });
  await expect(dialog.getByRole("alert")).toContainText("视频渲染失败");
  await dialog.getByRole("button", { name: "重新导出", exact: true }).click();
  await expect(dialog).toContainText("成片已就绪");
  expect(submits).toBe(1);
});
