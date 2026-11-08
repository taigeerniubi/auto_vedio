// 用真实像素、工程存储和图片导出验证调色、画布缩放及封面工作流。
import { test, expect, type Page } from "@playwright/test";

async function setup(page: Page) {
  await page.goto("/");
  const png = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 320;
    canvas.height = 180;
    const ctx = canvas.getContext("2d")!;
    for (const [i, color] of [
      "rgb(150,55,40)",
      "rgb(40,140,60)",
      "rgb(35,70,150)",
    ].entries()) {
      ctx.fillStyle = color;
      ctx.fillRect(i * 80, 0, 80, 180);
    }
    for (let y = 0; y < 180; y++) {
      ctx.fillStyle = `rgb(${40 + y},${40 + y},${40 + y})`;
      ctx.fillRect(240, y, 80, 1);
    }
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(5, 5, 10, 10);
    return canvas.toDataURL("image/png").split(",")[1];
  });
  const p = await (
    await page.request.post("/api/v1/projects", {
      data: {
        name: "调色与封面验收",
        composition: { width: 320, height: 180 },
      },
    })
  ).json();
  const root = `/api/v1/projects/${p.project_id}`;
  const a = await (
    await page.request.post(root + "/assets/upload?filename=color-chart.png", {
      data: Buffer.from(png, "base64"),
      headers: { "Content-Type": "application/octet-stream" },
    })
  ).json();
  expect(a.asset_id).toBeTruthy();
  const d = await (
    await page.request.post(root + "/drafts", { data: {} })
  ).json();
  expect(
    (
      await page.request.post(`${root}/drafts/${d.id}/tools`, {
        data: {
          tool: "assemble_sequence",
          arguments: {
            clips: [
              {
                clip_id: "chart",
                asset_id: a.asset_id,
                kind: "image",
                still_duration_frames: 90,
              },
            ],
          },
          op_id: "setup",
          expected_seq: 0,
        },
      })
    ).ok(),
  ).toBe(true);
  await page.request.post(`${root}/drafts/${d.id}/commit`, { data: {} });
  await page.evaluate(
    (pid) => localStorage.setItem("shiguang-project", pid),
    p.project_id,
  );
  await page.reload();
  await expect(
    page.getByRole("button", { name: "播放", exact: true }),
  ).toBeEnabled();
  await page
    .getByRole("button", { name: "片段 1 color-chart.png", exact: true })
    .click();
  return { root, png };
}
async function pixel(page: Page, x = 280, y = 90) {
  const c = page.locator('.preview-stage canvas[data-color-clip="chart"]');
  await expect(c).toHaveAttribute("data-frame", /\d+/);
  return c.evaluate(
    (node, { x, y }) =>
      Array.from(
        (node as HTMLCanvasElement).getContext("2d")!.getImageData(x, y, 1, 1)
          .data,
      ).slice(0, 3),
    { x, y },
  );
}

test("暂停时调色即时改变真实像素，保存、曲线、批量工具和撤销保持一致", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const { root } = await setup(page);
  const before = await (
    await page.request.post(root + "/preview", {
      data: { expected_revision: 1 },
    })
  ).json();
  await page.getByRole("button", { name: "调色", exact: true }).click();
  await page.getByLabel("曝光 / EV数值", { exact: true }).fill("1");
  await expect.poll(async () => (await pixel(page))[0]).toBeGreaterThan(170);
  expect((await (await page.request.get(root)).json()).revision).toBe(1);
  await page.getByRole("button", { name: "应用调色", exact: true }).click();
  await expect(page.locator(".statusbar")).toContainText("版本 2");
  expect(
    (await (await page.request.get(root)).json()).snapshot.clips[0].color_grade
      .exposure,
  ).toBe(1);
  const after = await (
    await page.request.post(root + "/preview", {
      data: { expected_revision: 2 },
    })
  ).json();
  expect(after.plan.mix_file).toBe(before.plan.mix_file);
  await page.getByRole("button", { name: "色轮", exact: true }).click();
  await page.getByLabel("暗部色相数值", { exact: true }).fill("180");
  await page.getByLabel("暗部强度数值", { exact: true }).fill("0.6");
  await page.getByRole("button", { name: "HSL", exact: true }).click();
  await page.getByLabel("分色饱和度数值", { exact: true }).fill("-0.8");
  const red = await pixel(page, 40, 90);
  expect(Math.abs(red[0] - red[1])).toBeLessThan(65);
  await page.getByRole("button", { name: "曲线", exact: true }).click();
  await page.getByLabel("曲线通道", { exact: true }).selectOption("red");
  const box = (await page
    .locator('[data-curve-editor="红色曲线"]')
    .boundingBox())!;
  await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.3);
  await page.getByRole("button", { name: "应用调色", exact: true }).click();
  await expect(page.locator(".statusbar")).toContainText("版本 3");
  const saved = await (await page.request.get(root)).json();
  expect(saved.snapshot.clips[0].color_grade.curves.red).toHaveLength(3);
  expect(saved.snapshot.clips[0].color_grade.exposure).toBe(1);
  await page.screenshot({ path: "artifacts/grading-acceptance/workbench.png" });
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await expect(page.locator(".statusbar")).toContainText("版本 4");
  const undone = await (await page.request.get(root)).json();
  expect(undone.snapshot.clips[0].color_grade.hsl.red.saturation).toBe(0);
  await expect.poll(async () => (await pixel(page))[0]).toBeGreaterThan(170);
  await page.getByRole("button", { name: "预设", exact: true }).click();
  await page.getByRole("button", { name: "奶油柔和", exact: true }).click();
  await page.getByLabel("滤镜强度 / %数值", { exact: true }).fill("40");
  await page.getByRole("button", { name: "应用调色", exact: true }).click();
  await expect(page.locator(".statusbar")).toContainText("版本 5");
  const filtered = (await (await page.request.get(root)).json()).snapshot
    .clips[0];
  expect(filtered.color_preset).toBe("cream");
  expect(filtered.color_preset_intensity).toBe(0.4);
  expect(filtered.color_grade.exposure).toBe(1);
  expect(errors).toEqual([]);
});

test("直接拖拽缩小及移动画面，每次手势只写一个版本", async ({ page }) => {
  const { root } = await setup(page);
  const handle = page.getByRole("button", { name: "拖动右下角缩放画面" });
  const viewport = (await page.locator(".preview-viewport").boundingBox())!,
    h = (await handle.boundingBox())!;
  await page.mouse.move(h.x + h.width / 2 - 2, h.y + h.height / 2 - 2);
  await page.mouse.down();
  await page.mouse.move(
    viewport.x + viewport.width * 0.7,
    viewport.y + viewport.height * 0.7,
    { steps: 8 },
  );
  expect((await (await page.request.get(root)).json()).revision).toBe(1);
  await page.mouse.up();
  await expect(page.locator(".statusbar")).toContainText("版本 2");
  const scale = (await (await page.request.get(root)).json()).snapshot.clips[0]
    .transform.scale;
  expect(scale).toBeLessThan(0.8);
  expect(scale).toBeGreaterThan(0.5);
  const area = page.getByRole("button", { name: "拖动画面移动位置" });
  const b = (await area.boundingBox())!;
  await page.mouse.move(b.x + b.width * 0.4, b.y + b.height * 0.4);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width * 0.5, b.y + b.height * 0.5, {
    steps: 5,
  });
  await page.mouse.up();
  await expect(page.locator(".statusbar")).toContainText("版本 3");
  expect(
    (await (await page.request.get(root)).json()).snapshot.clips[0].transform
      .position_x,
  ).toBeGreaterThan(-0.1);
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await expect(page.locator(".statusbar")).toContainText("版本 4");
  expect(
    (await (await page.request.get(root)).json()).snapshot.clips[0].transform
      .scale,
  ).toBeCloseTo(scale, 4);
});

test("解锁后边点独立调整宽高、角点自由拉伸，重新锁定保持当前比例", async ({
  page,
}) => {
  const { root } = await setup(page);
  const lock = page.getByRole("button", { name: "锁定画面比例", exact: true });
  await expect(lock).toHaveAttribute("aria-pressed", "true");
  await lock.click();
  const picture = page.locator(".preview-viewport img");
  const initial = (await picture.boundingBox())!;
  async function drag(name: string, dx: number, dy: number, revision: number) {
    const b = (await page
      .getByRole("button", { name, exact: true })
      .boundingBox())!;
    const x = b.x + b.width / 2,
      y = b.y + b.height / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + dx, y + dy, { steps: 8 });
    expect((await (await page.request.get(root)).json()).revision).toBe(
      revision - 1,
    );
    await page.mouse.up();
    await expect(page.locator(".statusbar")).toContainText(`版本 ${revision}`);
    return (await picture.boundingBox())!;
  }
  const narrow = await drag(
    "拖动右边调整画面宽度",
    -initial.width * 0.25,
    0,
    2,
  );
  expect(narrow.width / initial.width).toBeCloseTo(0.75, 2);
  expect(Math.abs(narrow.height - initial.height)).toBeLessThan(1);
  expect(Math.abs(narrow.x - initial.x)).toBeLessThan(1);
  const short = await drag(
    "拖动上边调整画面高度",
    0,
    initial.height * 0.2,
    3,
  );
  expect(short.height / initial.height).toBeCloseTo(0.8, 2);
  expect(Math.abs(short.width - narrow.width)).toBeLessThan(1);
  const free = await drag(
    "拖动右下角缩放画面",
    -short.width * 0.1,
    -short.height * 0.2,
    4,
  );
  expect(free.width / short.width).toBeCloseTo(0.9, 2);
  expect(free.height / short.height).toBeCloseTo(0.8, 2);
  await lock.click();
  await expect(
    page.getByRole("button", { name: "拖动右边调整画面宽度", exact: true }),
  ).toHaveCount(0);
  const uniform = await drag(
    "拖动右下角缩放画面",
    -free.width * 0.1,
    -free.height * 0.1,
    5,
  );
  expect(uniform.width / uniform.height).toBeCloseTo(
    free.width / free.height,
    2,
  );
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await expect(page.locator(".statusbar")).toContainText("版本 6");
  const restored = (await picture.boundingBox())!;
  expect(restored.width).toBeCloseTo(free.width, 1);
  expect(restored.height).toBeCloseTo(free.height, 1);
  await page.screenshot({
    path: "artifacts/grading-acceptance/free-resize.png",
  });
});

test("封面选帧和上传图片可以保存、恢复并真实导出 PNG", async ({ page }) => {
  test.setTimeout(180000);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const { root, png } = await setup(page);
  await page.getByRole("button", { name: "调色", exact: true }).click();
  await page.getByLabel("曝光 / EV数值", { exact: true }).fill("1");
  await page.getByRole("button", { name: "应用调色", exact: true }).click();
  await expect(page.locator(".statusbar")).toContainText("版本 2");
  await expect(
    page
      .locator(".header-actions")
      .getByRole("button", { name: "封面", exact: true }),
  ).toHaveCount(0);
  await page
    .locator(".timeline-toolbar")
    .getByRole("button", { name: "封面", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "设置封面" });
  await expect(
    dialog.locator('canvas[data-color-clip="chart"]'),
  ).toHaveAttribute("data-frame", "0");
  await dialog.getByLabel("封面标题", { exact: true }).fill("我的城市日常");
  await dialog.getByLabel("标题字号数值").fill("32");
  await dialog.getByLabel("背景压暗数值").fill("0");
  await dialog.getByRole("button", { name: "保存封面", exact: true }).click();
  await expect(page.locator(".statusbar")).toContainText("版本 3");
  const saved = await (await page.request.get(root)).json();
  expect(saved.snapshot.cover.title).toBe("我的城市日常");
  expect(saved.snapshot.clips[0].still_duration_frames).toBe(90);
  await dialog
    .getByRole("button", { name: "导出封面 PNG", exact: true })
    .click();
  const link = dialog.getByRole("link", { name: /下载封面/ });
  await expect(link).toBeVisible({ timeout: 90000 });
  const href = (await link.getAttribute("href"))!;
  const image = await page.request.get(href);
  expect(image.ok()).toBe(true);
  expect(image.headers()["content-type"]).toContain("image/png");
  const sample = await page.evaluate(async (url) => {
    const image = new Image();
    image.src = url;
    await image.decode();
    const c = document.createElement("canvas");
    c.width = image.width;
    c.height = image.height;
    const ctx = c.getContext("2d")!;
    ctx.drawImage(image, 0, 0);
    return {
      w: image.width,
      h: image.height,
      pixel: Array.from(ctx.getImageData(280, 90, 1, 1).data),
    };
  }, href);
  expect([sample.w, sample.h]).toEqual([320, 180]);
  expect(sample.pixel[0]).toBeGreaterThan(170);
  await page.screenshot({ path: "artifacts/grading-acceptance/cover.png" });
  await dialog.getByRole("button", { name: "使用图片", exact: true }).click();
  await dialog.getByLabel("上传封面图片文件").setInputFiles({
    name: "uploaded-cover.png",
    mimeType: "image/png",
    buffer: Buffer.from(png, "base64"),
  });
  await expect(dialog.getByLabel("选择项目封面图片")).toContainText(
    "uploaded-cover.png",
  );
  await dialog.getByRole("button", { name: "保存封面", exact: true }).click();
  await expect(page.locator(".statusbar")).toContainText("版本 4");
  expect(
    (await (await page.request.get(root)).json()).snapshot.cover.source,
  ).toBe("image");
  await dialog
    .getByRole("button", { name: "导出封面 PNG", exact: true })
    .click();
  await expect(link).toBeVisible({ timeout: 90000 });
  const uploaded = await page.evaluate(
    async (url) => {
      const img = new Image();
      img.src = url;
      await img.decode();
      const c = document.createElement("canvas");
      c.width = 320;
      c.height = 180;
      const ctx = c.getContext("2d")!;
      ctx.drawImage(img, 0, 0);
      return Array.from(ctx.getImageData(280, 90, 1, 1).data);
    },
    (await link.getAttribute("href"))!,
  );
  expect(uploaded[0]).toBeCloseTo(130, 0);
  expect(errors).toEqual([]);
});
