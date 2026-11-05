// 验证浏览器中的素材导入、时间线编辑、真实预览和成片下载。
import { test, expect, type Page } from "@playwright/test";

/** 创建用于浏览器验收的独立工程。
 * Args:
 *   page: 浏览器页面。
 * Returns:
 *   新建工程编号。
 */
async function createProject(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "打开工程", exact: true }).click();
  await page
    .getByLabel("项目名称", { exact: true })
    .fill("前端验收 · 日常片段");
  const created = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/v1/projects") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "创建项目", exact: true }).click();
  const project = await (await created).json();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  return project.project_id as string;
}

/** 等待工程达到期望版本后再执行下一次操作。
 * Args:
 *   page: 浏览器页面。
 *   revision: 期望版本号。
 */
async function saved(page: Page, revision: number) {
  await expect(page.locator(".statusbar")).toContainText(`版本 ${revision}`);
  await expect(page.locator(".save-status")).toContainText("所有更改已保存");
  await expect(page.getByRole("alert")).toHaveCount(0);
}

test("真实素材到可下载成片的完整工作台链路", async ({ page }) => {
  test.setTimeout(180000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const pid = await createProject(page);
  await page.getByRole("button", { name: "导入素材", exact: true }).click();
  await page
    .locator(".library-list label")
    .filter({ hasText: "frame-red.mp4" })
    .getByRole("checkbox")
    .check();
  await page
    .locator(".library-list label")
    .filter({ hasText: "frame-blue-24fps.mp4" })
    .getByRole("checkbox")
    .check();
  await page.getByRole("button", { name: "导入所选 2" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 30000 });
  await expect(page.locator(".asset-card")).toHaveCount(2);
  await page
    .getByRole("button", { name: "添加素材 frame-red.mp4", exact: true })
    .click();
  await saved(page, 1);
  await page
    .getByRole("button", { name: "添加素材 frame-blue-24fps.mp4", exact: true })
    .click();
  await saved(page, 2);
  await expect(
    page.getByRole("button", { name: "播放", exact: true }),
  ).toBeEnabled({ timeout: 40000 });
  await page
    .getByRole("button", { name: "片段 1 frame-red.mp4", exact: true })
    .click();
  await page.getByRole("tab", { name: "片段属性" }).click();
  await page.getByLabel("裁切出点", { exact: true }).fill("1");
  await page.getByRole("button", { name: "应用裁切", exact: true }).click();
  await saved(page, 3);
  await page.getByLabel("原声音量").fill("-6");
  await page.getByRole("button", { name: "应用音量" }).click();
  await saved(page, 4);
  await page.getByRole("button", { name: "向后移动" }).click();
  await saved(page, 5);
  await expect(
    page.getByRole("button", { name: "片段 2 frame-red.mp4", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await saved(page, 6);
  await expect(
    page.getByRole("button", { name: "片段 1 frame-red.mp4", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "调色", exact: true }).click();
  await page.getByRole("button", { name: "黑白", exact: true }).click();
  await saved(page, 7);
  await page.getByRole("button", { name: "文字", exact: true }).click();
  await page.getByLabel("标题内容").fill("拾光 · 慢慢生活");
  await page.getByRole("button", { name: "添加标题", exact: true }).click();
  await saved(page, 8);
  await expect(
    page.getByRole("button", { name: "播放", exact: true }),
  ).toBeEnabled({ timeout: 40000 });
  await page.getByRole("button", { name: "播放", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "暂停", exact: true }),
  ).toBeVisible();
  await expect
    .poll(() => page.locator(".time-display strong").textContent())
    .not.toBe("00:00.00");
  await page.getByRole("button", { name: "暂停", exact: true }).click();
  await page.getByRole("tab", { name: "AI 助手" }).click();
  await page.getByRole("button", { name: "素材", exact: true }).click();
  await page.screenshot({
    path: "artifacts/frontend-acceptance/editor-desktop.png",
    fullPage: true,
  });
  const snapshot = await (
    await page.request.get(`/api/v1/projects/${pid}`)
  ).json();
  expect(snapshot.snapshot.clips[0].source_out_frame).toBe(30);
  expect(snapshot.snapshot.clips[0].original_audio_gain_db).toBe(-6);
  expect(snapshot.snapshot.clips[0].color_preset).toBe("monochrome");
  expect(snapshot.snapshot.text_items[0].text).toBe("拾光 · 慢慢生活");
  await page.getByRole("button", { name: "导出成片", exact: true }).click();
  await expect(page.getByRole("link", { name: "下载 MP4" })).toBeVisible({
    timeout: 60000,
  });
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("link", { name: "下载 MP4" }).click();
  const download = await downloadEvent;
  await download.saveAs("artifacts/frontend-acceptance/browser-export.mp4");
  expect(await download.failure()).toBeNull();
  await page.reload();
  await expect(page.locator(".project-name")).toContainText("前端验收");
  await expect(page.locator(".clip-block")).toHaveCount(2);
  expect(errors).toEqual([]);
});

test("窄屏界面没有横向溢出且模型未配置提示可操作", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "新建或打开项目" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "关闭弹窗" }).click();
  await page.getByRole("button", { name: "模型设置", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("密钥只在后端保存");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "artifacts/frontend-acceptance/editor-mobile.png",
    fullPage: true,
  });
});

test("接口失败在前端显示可追踪的请求编号", async ({ page }) => {
  await createProject(page);
  await page.route("**/api/v1/library", (route) =>
    route.fulfill({
      status: 500,
      headers: {
        "Content-Type": "application/json",
        "X-Request-ID": "frontend-error-test",
      },
      body: JSON.stringify({
        ok: false,
        summary: "素材目录读取失败",
        error_code: "INTERNAL_ERROR",
      }),
    }),
  );
  await page.getByRole("button", { name: "导入素材", exact: true }).click();
  await page.getByRole("button", { name: "关闭弹窗" }).click();
  await expect(page.getByRole("alert")).toContainText("素材目录读取失败");
  await expect(page.getByRole("alert")).toContainText("frontend-error-test");
});

test("模拟模型的进度、取消和澄清反馈在真实工作台中可用", async ({ page }) => {
  await page.route("**/api/v1/model-status", (route) =>
    route.fulfill({
      json: { configured: true, provider: "deepseek", model: "模拟协议测试" },
    }),
  );
  await createProject(page);
  let release: () => void = () => {};
  let state = "running";
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  const result = () => ({
    run_id: "mock-ui-run",
    status: state,
    events: [{ 事件: "请求模型", 轮次: 1 }],
    result: { summary: state === "cancelled" ? "用户取消了运行" : "" },
  });
  await page.route("**/commands/*", (route) =>
    route.fulfill({ json: result() }),
  );
  await page.route("**/runs/mock-ui-run/cancel", async (route) => {
    state = "cancelled";
    await route.fulfill({ json: result() });
    release();
  });
  await page.route("**/commands", async (route) => {
    const body = route.request().postDataJSON();
    expect(body.instruction).toBe("测试取消剪辑");
    expect(body.selected_clip_ids).toEqual([]);
    await pending;
    await route.fulfill({ json: result() });
  });
  await page.getByLabel("剪辑指令", { exact: true }).fill("测试取消剪辑");
  await page.getByRole("button", { name: "发送剪辑指令" }).click();
  await expect(page.locator(".execution-list")).toContainText("第 1 轮");
  await page.getByRole("button", { name: "取消AI运行" }).click();
  await expect(page.locator(".agent-message")).toContainText("用户取消了运行");
  await expect(page.locator(".save-status")).toContainText("所有更改已保存");
  await page.unroute("**/commands");
  await page.route("**/commands", (route) =>
    route.fulfill({
      json: {
        run_id: "mock-clarify",
        status: "needs_input",
        events: [],
        result: { question: "要使用哪一个素材？" },
      },
    }),
  );
  await page.getByLabel("剪辑指令", { exact: true }).fill("帮我选择一个素材");
  await page.getByRole("button", { name: "发送剪辑指令" }).click();
  await expect(page.locator(".agent-message").last()).toContainText(
    "要使用哪一个素材？",
  );
  await page.screenshot({
    path: "artifacts/frontend-acceptance/agent-feedback.png",
    fullPage: true,
  });
});

test("历史对话持续追加，刷新恢复且按项目隔离", async ({ page }) => {
  await page.route("**/api/v1/model-status", (route) =>
    route.fulfill({
      json: { configured: true, provider: "deepseek", model: "history-test" },
    }),
  );
  const pid = await createProject(page);
  const history: Record<string, unknown>[] = [];
  await page.route(`**/projects/${pid}/runs`, (route) =>
    route.fulfill({
      json: { items: history, next_cursor: null },
    }),
  );
  await page.route(`**/projects/${pid}/commands`, (route) => {
    const command = route.request().postDataJSON();
    const run = {
      run_id: `history-ui-${history.length}`,
      project_id: pid,
      status: "needs_input",
      events: [],
      result: { question: `回复：${command.instruction}` },
      instruction: command.instruction,
      created_at: new Date().toISOString(),
    };
    history.push(run);
    return route.fulfill({ json: run });
  });
  for (const text of ["第一条历史指令", "第二条历史指令"]) {
    await page.getByLabel("剪辑指令", { exact: true }).fill(text);
    await page.getByLabel("剪辑指令", { exact: true }).press("Enter");
    await expect(page.locator(".agent-message").last()).toContainText(
      `回复：${text}`,
    );
    await expect(page.getByLabel("剪辑指令", { exact: true })).toBeEnabled();
  }
  await expect(page.locator(".conversation-turn")).toHaveCount(2);
  await page.reload();
  await expect(page.locator(".conversation-turn")).toHaveCount(2);
  await expect(page.locator(".user-message").first()).toContainText(
    "第一条历史指令",
  );
  await expect(page.locator(".agent-message").last()).toContainText(
    "回复：第二条历史指令",
  );
  await page.screenshot({
    path: "artifacts/frontend-acceptance/conversation-history.png",
    fullPage: true,
  });
  await createProject(page);
  await expect(page.locator(".conversation-turn")).toHaveCount(0);
  await expect(page.locator(".conversation")).not.toContainText(
    "第一条历史指令",
  );
});

test("浏览器上传图片后可分割片段并保持草稿版本一致", async ({ page }) => {
  const pid = await createProject(page);
  await page
    .locator("input[type=file]")
    .setInputFiles("database/test-inputs/grid.png");
  await expect(page.locator(".asset-card")).toHaveCount(1, { timeout: 30000 });
  await page.getByRole("button", { name: "添加素材 grid.png" }).click();
  await saved(page, 1);
  await expect(
    page.getByRole("button", { name: "播放", exact: true }),
  ).toBeEnabled({ timeout: 30000 });
  await page.getByRole("button", { name: "片段 1 grid.png" }).click();
  await page.getByRole("button", { name: "下一帧", exact: true }).click();
  await page.getByRole("button", { name: "在播放头处分割" }).click();
  await saved(page, 2);
  await expect(page.locator(".clip-block")).toHaveCount(2);
  const snapshot = (
    await (await page.request.get(`/api/v1/projects/${pid}`)).json()
  ).snapshot;
  expect(
    snapshot.clips[0].still_duration_frames +
      snapshot.clips[1].still_duration_frames,
  ).toBe(90);
});

test("模型调用返回502时展示额度原因并保留失败运行", async ({ page }) => {
  await page.route("**/api/v1/model-status", (route) =>
    route.fulfill({
      json: { configured: true, provider: "openai", model: "test-model" },
    }),
  );
  await createProject(page);
  const summary = "openai: API credits or quota exhausted. (upstream HTTP 429)";
  await page.route("**/commands", (route) =>
    route.fulfill({
      status: 502,
      headers: {
        "Content-Type": "application/json",
        "X-Request-ID": "quota-error-test",
      },
      body: JSON.stringify({
        ok: false,
        error_code: "MODEL_HTTP_ERROR",
        summary,
        run_id: "quota-test-run",
        status: "failed",
        events: [],
        result: { summary, upstream_status: 429 },
      }),
    }),
  );
  await page.getByLabel("剪辑指令", { exact: true }).fill("测试模型额度错误");
  await page.getByRole("button", { name: "发送剪辑指令" }).click();
  await expect(page.getByRole("alert")).toContainText(
    "API credits or quota exhausted",
  );
  await expect(page.getByRole("alert")).toContainText("HTTP 502");
  await expect(page.getByRole("alert")).toContainText("quota-error-test");
  await expect(page.locator(".agent-message")).toContainText(summary);
  await expect(page.locator(".save-status")).toContainText("所有更改已保存");
});

test("回车发送并清空输入，Shift换行和中文组词回车不发送", async ({ page }) => {
  await page.route("**/api/v1/model-status", (route) =>
    route.fulfill({
      json: { configured: true, provider: "deepseek", model: "test-model" },
    }),
  );
  await createProject(page);
  const sent: string[] = [];
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/commands", async (route) => {
    sent.push(route.request().postDataJSON().instruction);
    await gate;
    await route.fulfill({
      json: {
        run_id: "keyboard-run",
        status: "needs_input",
        events: [],
        result: { question: "要使用哪个素材？" },
      },
    });
  });
  const input = page.getByLabel("剪辑指令", { exact: true });
  await input.fill("第一行");
  await input.press("Shift+Enter");
  await expect(input).toHaveValue("第一行\n");
  await input.press("ControlOrMeta+A");
  await input.fill("中文组词确认");
  await input.dispatchEvent("keydown", { key: "Enter", isComposing: true });
  expect(sent).toHaveLength(0);
  await expect(input).toHaveValue("中文组词确认");
  await input.press("Enter");
  await expect.poll(() => sent.length).toBe(1);
  await expect(input).toHaveValue("");
  await expect(input).toBeDisabled();
  release();
  await expect(input).toBeEnabled();
  await expect(page.locator(".user-message")).toContainText("中文组词确认");
  await input.fill("按钮发送也清空");
  await page.getByRole("button", { name: "发送剪辑指令" }).click();
  await expect.poll(() => sent.length).toBe(2);
  await expect(input).toHaveValue("");
});

test("素材弹窗内上传完成自动关闭，失败保留错误提示", async ({ page }) => {
  await createProject(page);
  await page.getByRole("button", { name: "导入素材", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "导入素材" });
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/assets/upload?*", async (route) => {
    await gate;
    await route.continue();
  });
  const chooser = page.waitForEvent("filechooser");
  await dialog.getByRole("button", { name: "从电脑上传" }).click();
  await (await chooser).setFiles("database/test-inputs/grid.png");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("status")).toContainText("导入素材 1/1");
  release();
  await expect(dialog).toHaveCount(0);
  await expect(page.locator(".asset-card")).toHaveCount(1);
  await expect(page.locator(".save-status")).toContainText("所有更改已保存");
  await page.unroute("**/assets/upload?*");
  await page.route("**/assets/upload?*", (route) =>
    route.fulfill({
      status: 400,
      json: {
        ok: false,
        summary: "测试素材无法导入",
        error_code: "INVALID_MEDIA_FILE",
      },
    }),
  );
  await page.getByRole("button", { name: "导入素材", exact: true }).click();
  const failedChooser = page.waitForEvent("filechooser");
  await dialog.getByRole("button", { name: "从电脑上传" }).click();
  await (await failedChooser).setFiles("database/test-inputs/grid.png");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("alert")).toContainText("测试素材无法导入");
  await expect(page.locator(".asset-card")).toHaveCount(1);
});
