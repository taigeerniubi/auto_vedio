// 验证主题持久化、系统同步、本地字体及新版工作台的响应式与渐进披露。
import { test, expect, type Page } from "@playwright/test";

async function theme(page: Page, label: string) {
  const menu = page.locator(".theme-switcher");
  if (!(await menu.evaluate((node) => (node as HTMLDetailsElement).open)))
    await page.getByLabel("外观设置", { exact: true }).click();
  await page.getByRole("radio", { name: label, exact: true }).check();
  await page.keyboard.press("Escape");
}

/** 使用隔离服务的真实图片工程验证界面与编辑契约。 */
async function workspace(page: Page) {
  const project = await (
    await page.request.post("/api/v1/projects", {
      data: {
        name: "界面验收 · 日常片段",
        composition: { width: 640, height: 360 },
      },
    })
  ).json();
  const root = `/api/v1/projects/${project.project_id}`;
  const asset = await (
    await page.request.post(`${root}/assets/import`, {
      data: { path: "test-inputs/grid.png" },
    })
  ).json();
  const draft = await (
    await page.request.post(`${root}/drafts`, { data: {} })
  ).json();
  const inserted = await page.request.post(`${root}/drafts/${draft.id}/tools`, {
    data: {
      tool: "insert_clip",
      op_id: "appearance-setup",
      expected_seq: 0,
      arguments: {
        clip: {
          clip_id: "appearance-clip",
          asset_id: asset.asset_id,
          kind: "image",
          still_duration_frames: 150,
        },
      },
    },
  });
  expect(inserted.ok()).toBe(true);
  await page.request.post(`${root}/drafts/${draft.id}/commit`, { data: {} });
  await page.addInitScript(
    (pid) => localStorage.setItem("shiguang-project", pid),
    project.project_id,
  );
  await page.goto("/");
  await expect(page.locator(".asset-card")).toHaveCount(1);
  await expect(
    page.getByRole("button", { name: "播放", exact: true }),
  ).toBeEnabled({ timeout: 40000 });
  await page.evaluate(() => document.fonts.ready);
  return root;
}

test("三种主题可切换，刷新保留偏好，系统变化和其他标签页实时同步", async ({
  page,
  context,
}) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await theme(page, "浅色");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  const other = await context.newPage();
  await other.goto("/");
  await theme(page, "深色");
  await expect(other.locator("html")).toHaveAttribute("data-theme", "dark");
  await theme(page, "跟随系统");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await other.close();
});

test("浏览器禁用存储时仍可切换主题并加载本地思源黑体", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    Storage.prototype.getItem = () => {
      throw new Error("storage unavailable");
    };
    Storage.prototype.setItem = () => {
      throw new Error("storage unavailable");
    };
  });
  // 工程状态的存储错误不属于外观控件，因此本用例只模拟启动时没有工程。
  await page.route("**/api/v1/projects", (route) =>
    route.fulfill({ json: [] }),
  );
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/");
  await theme(page, "深色");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  const font = await page.evaluate(async () => {
    await document.fonts.ready;
    return [...document.fonts].some(
      (face) =>
        face.family.includes("Source Han Sans") && face.status === "loaded",
    );
  });
  expect(font).toBe(true);
  expect(
    (await page.request.get("/assets/fonts/SourceHanSansCN-VF.woff2")).status(),
  ).toBe(200);
  expect(errors).toEqual([]);
});

test("不同屏宽下工作台均可使用，浅色与深色无横向溢出", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await workspace(page);
  for (const mode of ["浅色", "深色"]) {
    await theme(page, mode);
    for (const [width, height] of [
      [1440, 900],
      [1024, 768],
      [820, 1180],
      [768, 1024],
      [390, 844],
    ]) {
      await page.setViewportSize({ width, height });
      await expect
        .poll(() =>
          page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
        )
        .toBe(true);
      for (const selector of [
        "#workspace-library",
        "#workspace-monitor",
        "#workspace-timeline",
        "#workspace-dock",
      ]) {
        const box = await page.locator(selector).boundingBox();
        expect(box!.width).toBeGreaterThan(200);
        expect(box!.x + box!.width).toBeLessThanOrEqual(width);
      }
      await page.screenshot({
        path: `artifacts/frontend-redesign/${mode === "浅色" ? "light" : "dark"}-${width}.png`,
        fullPage: true,
      });
    }
  }
  expect(errors).toEqual([]);
});

test("高级构图按需展开，隐藏后应用常用参数保留已有设置", async ({ page }) => {
  const root = await workspace(page);
  await page
    .getByRole("button", { name: "片段 1 grid.png", exact: true })
    .click();
  await page.getByRole("tab", { name: "片段属性" }).click();
  await expect(page.getByLabel("画面水平位置")).toBeHidden();
  await page.locator(".transform-advanced summary").click();
  await page.getByLabel("画面水平位置").fill("0.2");
  await page.getByLabel("画面宽度拉伸").fill("1.2");
  await page.getByRole("button", { name: "应用构图", exact: true }).click();
  await expect(page.locator(".statusbar")).toContainText("版本 2");
  await expect(page.getByLabel("画面水平位置")).toBeHidden();
  await page.getByLabel("画面缩放", { exact: true }).fill("1.1");
  await page.getByRole("button", { name: "应用构图", exact: true }).click();
  await expect(page.locator(".statusbar")).toContainText("版本 3");
  const saved = await (await page.request.get(root)).json();
  expect(saved.snapshot.clips[0].transform).toMatchObject({
    scale: 1.1,
    scale_x: 1.2,
    position_x: 0.2,
  });
  await page.getByRole("button", { name: "模型设置", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "模型连接" }).locator("pre"),
  ).toBeHidden();
  await page.locator(".settings-advanced summary").click();
  await expect(page.getByRole("dialog").locator("pre")).toBeVisible();
});

test("剪辑工具和属性在两种主题下保持可读与可达", async ({ page }) => {
  await workspace(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page
    .getByRole("button", { name: "片段 1 grid.png", exact: true })
    .click();
  for (const mode of ["浅色", "深色"]) {
    await theme(page, mode);
    for (const section of ["素材", "音频", "文字", "转场", "调色", "模板"]) {
      await page
        .getByRole("navigation", { name: "编辑工具" })
        .getByRole("button", { name: section, exact: true })
        .click();
      await expect(page.locator(".library-content")).toBeVisible();
      if (section === "转场")
        await expect
          .poll(() =>
            page
              .locator(".transition-demo > img")
              .evaluateAll(
                (images) =>
                  images.length > 0 &&
                  images.every(
                    (image) =>
                      (image as HTMLImageElement).complete &&
                      (image as HTMLImageElement).naturalWidth > 0,
                  ),
              ),
          )
          .toBe(true);
      await page
        .locator("#workspace-library")
        .screenshot({
          path: `artifacts/frontend-redesign/${mode}-${section}.png`,
        });
    }
    await page.getByRole("tab", { name: "片段属性" }).click();
    await expect(page.getByLabel("图片时长")).toBeVisible();
    await page
      .locator("#workspace-dock")
      .screenshot({ path: `artifacts/frontend-redesign/${mode}-属性.png` });
    await page.getByRole("button", { name: "模型设置", exact: true }).click();
    await page
      .getByRole("dialog")
      .screenshot({ path: `artifacts/frontend-redesign/${mode}-设置.png` });
    await page.getByRole("button", { name: "关闭弹窗", exact: true }).click();
  }
});
