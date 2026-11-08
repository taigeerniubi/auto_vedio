// 验证拖动播放头时的时间读数、实际视频画面和缩放滚动后的定位。
import { test, expect, type Page } from "@playwright/test";

async function openScrubProject(page: Page) {
  const created = await page.request.post("/api/v1/projects", {
    data: { name: "时间线拖动验收", composition: { width: 640, height: 360 } },
  });
  expect(created.ok()).toBe(true);
  const project = await created.json();
  const root = `/api/v1/projects/${project.project_id}`;
  const clips = [];
  for (const [index, name] of ["frame-red.mp4", "frame-blue-24fps.mp4"].entries()) {
    const imported = await page.request.post(root + "/assets/import", {
      data: { path: "test-inputs/" + name },
    });
    expect(imported.ok()).toBe(true);
    const asset = await imported.json();
    clips.push({ clip_id: `scrub-${index}`, asset_id: asset.asset_id, kind: "video", source_out_frame: 60 });
  }
  const draft = await (await page.request.post(root + "/drafts", { data: {} })).json();
  expect((await page.request.post(`${root}/drafts/${draft.id}/tools`, {
    data: { tool: "assemble_sequence", arguments: { clips }, op_id: "scrub-setup", expected_seq: 0 },
  })).ok()).toBe(true);
  expect((await page.request.post(`${root}/drafts/${draft.id}/commit`, { data: {} })).ok()).toBe(true);
  await page.addInitScript(id => localStorage.setItem("shiguang-project", id), project.project_id);
  await page.goto("/");
  await expect(page.getByRole("button", { name: "播放", exact: true })).toBeEnabled();
  await expect(page.getByRole("slider", { name: "播放头", exact: true })).toBeVisible();
  return root;
}

async function previewColor(page: Page): Promise<string> {
  return page.locator(".preview-stage video").evaluateAll(nodes => {
    const video = nodes.find(node => {
      const media = node as HTMLVideoElement;
      const box = media.getBoundingClientRect();
      return box.width > 0 && box.height > 0 && getComputedStyle(media).display !== "none" && media.readyState >= 2 && !media.seeking;
    }) as HTMLVideoElement | undefined;
    if (!video) return "pending";
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const ctx = canvas.getContext("2d")!;
    // 测试视频上方是不同底色，下方网格和移动方块不可用于整帧平均。
    ctx.drawImage(video, 10, 10, 1, 1, 0, 0, 1, 1);
    const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
    if (r > 20 && r > b * 1.3 && r > g * 1.3) return "red";
    if (b > 20 && b > r * 1.4 && b > g * 1.3) return "blue";
    return `${r},${g},${b}`;
  });
}

async function pointAtFrame(page: Page, frame: number) {
  const ruler = await page.getByLabel("时间标尺").boundingBox();
  const clip = await page.locator(".clip-block").first().boundingBox();
  expect(ruler).not.toBeNull();
  expect(clip).not.toBeNull();
  return { x: ruler!.x + frame * (clip!.width + 2) / 60, y: ruler!.y + 12 };
}

test("按住标尺拖动时持续显示秒数并切换真实预览画面，越界拖动可钳制", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const root = await openScrubProject(page);
  const slider = page.getByRole("slider", { name: "播放头", exact: true });
  await expect.poll(() => previewColor(page)).toBe("red");
  await expect(page.locator(".timeline-toolbar")).toContainText("00:04.00");
  // 播放中开始拖动应暂停，确保后续位置不会被播放时钟覆盖。
  await page.getByRole("button", { name: "播放", exact: true }).click();
  await expect(page.getByRole("button", { name: "暂停", exact: true })).toBeVisible();
  let point = await pointAtFrame(page, 15);
  await page.mouse.move(point.x, point.y);
  await page.mouse.down();
  await expect(page.getByRole("button", { name: "播放", exact: true })).toBeVisible();
  await expect(slider).toHaveAttribute("aria-valuenow", "15");
  await expect(page.locator(".playhead")).toContainText("0.50 秒");
  await expect(page.locator(".time-display strong")).toHaveText("00:00.15");
  // 所有断言均在 mouse.up 之前执行，不能只在释放后才更新画面。
  point = await pointAtFrame(page, 90);
  await page.mouse.move(point.x, point.y, { steps: 8 });
  await expect(slider).toHaveAttribute("aria-valuenow", "90");
  await expect(page.locator(".playhead")).toContainText("3.00 秒");
  await expect(page.locator(".time-display strong")).toHaveText("00:03.00");
  await expect.poll(() => previewColor(page)).toBe("blue");
  point = await pointAtFrame(page, 30);
  await page.mouse.move(point.x, point.y, { steps: 8 });
  await expect(slider).toHaveAttribute("aria-valuenow", "30");
  await expect.poll(() => previewColor(page)).toBe("red");
  const ruler = (await page.getByLabel("时间标尺").boundingBox())!;
  await page.mouse.move(ruler.x - 35, ruler.y + 70);
  await expect(slider).toHaveAttribute("aria-valuenow", "0");
  await page.mouse.move(ruler.x + ruler.width + 25, ruler.y + 70);
  await expect(slider).toHaveAttribute("aria-valuenow", "119");
  await expect(page.locator(".playhead")).toContainText("3.97 秒");
  await expect.poll(() => previewColor(page)).toBe("blue");
  await page.mouse.up();
  await page.screenshot({ path: "artifacts/timeline-scrub/editor.png", fullPage: true });
  expect((await (await page.request.get(root)).json()).revision).toBe(1);
  expect(errors).toEqual([]);
});

test("放大并横向滚动后拖动播放头仍定位准确，方向键和首尾键可逐帧定位", async ({ page }) => {
  await openScrubProject(page);
  const zoom = page.getByRole("slider", { name: "时间线缩放", exact: true });
  await zoom.fill("3");
  const scroll = page.locator(".track-scroll");
  await scroll.evaluate(node => { node.scrollLeft = (node.scrollWidth - node.clientWidth) / 2; });
  const slider = page.getByRole("slider", { name: "播放头", exact: true });
  let point = await pointAtFrame(page, 60);
  await page.mouse.click(point.x, point.y);
  await expect(slider).toHaveAttribute("aria-valuenow", "60");
  await expect.poll(() => previewColor(page)).toBe("blue");
  const handle = (await slider.boundingBox())!;
  await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2);
  await page.mouse.down();
  point = await pointAtFrame(page, 75);
  await page.mouse.move(point.x, point.y, { steps: 6 });
  await expect(slider).toHaveAttribute("aria-valuenow", "75");
  await expect(page.locator(".playhead")).toContainText("2.50 秒");
  await page.mouse.up();
  await slider.focus();
  await page.keyboard.press("ArrowLeft");
  await expect(slider).toHaveAttribute("aria-valuenow", "74");
  await page.keyboard.press("ArrowRight");
  await expect(slider).toHaveAttribute("aria-valuenow", "75");
  await page.keyboard.press("Home");
  await expect(slider).toHaveAttribute("aria-valuenow", "0");
  await expect.poll(() => previewColor(page)).toBe("red");
  await page.keyboard.press("End");
  await expect(slider).toHaveAttribute("aria-valuenow", "119");
  await expect.poll(() => previewColor(page)).toBe("blue");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".timeline-time")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "artifacts/timeline-scrub/narrow.png", fullPage: true });
});
