// 验证浏览器中的素材导入、时间线编辑、真实预览和成片下载。
import { test, expect, type Page } from "@playwright/test";

// 导入卡片来自真实导入记录，先在独立工程登记验收素材。
test.beforeAll(async ({ request }) => {
  const project = await (await request.post("/api/v1/projects", {data:{name:"工作台验收素材历史"}})).json();
  for (const name of ["frame-red.mp4", "frame-blue-24fps.mp4"]) {
    const response = await request.post(`/api/v1/projects/${project.project_id}/assets/import`, {data:{path:`test-inputs/${name}`}});
    expect(response.ok()).toBe(true);
  }
});

/** 通过会话列表验证选择状态及完整历史入口。 */
async function currentSession(page: Page) {
  return (await page.locator(".chat-sessions").getAttribute("data-active-session"))!;
}
async function chooseSession(page: Page, id: string) {
  await page.getByRole("button", { name: /^查看全部/ }).click();
  await page.getByRole("dialog", { name: "全部会话" }).locator(`[data-session-id="${id}"]`).click();
  await expect(page.getByRole("dialog", { name: "全部会话" })).toHaveCount(0);
}
async function allSessionIds(page: Page) {
  await page.getByRole("button", { name: /^查看全部/ }).click();
  const dialog = page.getByRole("dialog", { name: "全部会话" });
  const ids = await dialog.locator("[data-session-id]").evaluateAll(rows => rows.map(row => row.getAttribute("data-session-id")));
  await dialog.getByRole("button", { name: "关闭弹窗" }).click();
  return ids;
}

/** 模拟首条指令与会话原子保存，后续读取仍保留消息。 */
async function mockSessionMessages(page: Page, pid: string) {
  const history: Record<string, Record<string, unknown>[]> = {};
  const submitted: Record<string, unknown>[] = [];
  await page.route(`**/projects/${pid}/sessions`, route => {
    expect(route.request().method()).toBe("GET");
    return route.fulfill({ json: Object.entries(history).reverse().map(([sid, entries]) => ({
      session_id: sid, project_id: pid, title: entries[0].instruction,
      created_at: entries[0].created_at, updated_at: entries.at(-1)!.created_at,
      has_messages: true, running: false,
    })) });
  });
  await page.route(`**/projects/${pid}/runs?*`, route => {
    const sid = new URL(route.request().url()).searchParams.get("session_id")!;
    return route.fulfill({ json: { items: history[sid] || [], next_cursor: null } });
  });
  await page.route(`**/projects/${pid}/commands`, route => {
    const command = route.request().postDataJSON();
    submitted.push(command);
    const sid = command.new_session ? `${pid}-chat-${Object.keys(history).length}` : command.session_id;
    expect(sid).toBeTruthy();
    const entries = history[sid] ||= [];
    const run = {
      run_id: `${sid}-${entries.length}`, project_id: pid, session_id: sid,
      status: "needs_input", events: [], result: { question: `回复：${command.instruction}` },
      instruction: command.instruction, created_at: new Date().toISOString(),
    };
    entries.push(run);
    return route.fulfill({ json: run });
  });
  return { history, submitted };
}

async function sendMessage(page: Page, text: string) {
  await page.getByLabel("剪辑指令", { exact: true }).fill(text);
  await page.getByRole("button", { name: "发送剪辑指令" }).click();
  await expect(page.locator(".agent-message").last()).toContainText(`回复：${text}`);
  await expect(page.getByRole("button", { name: "新会话", exact: true })).toBeEnabled();
}

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
  await expect(page.getByRole("button", { name: "新会话", exact: true })).toBeEnabled();
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
  await page.getByRole("button", { name: "预设", exact: true }).click();
  await page.getByRole("button", { name: "经典黑白", exact: true }).click();
  await page.getByRole("button", { name: "应用调色", exact: true }).click();
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
  await page.getByRole("button", { name: "开始导出", exact: true }).click();
  await expect(page.getByRole("dialog", {name:"导出成片"}).getByRole("link", { name: "下载 MP4" })).toBeVisible({
    timeout: 60000,
  });
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("dialog", {name:"导出成片"}).getByRole("link", { name: "下载 MP4" }).click();
  const download = await downloadEvent;
  await download.saveAs("artifacts/frontend-acceptance/browser-export.mp4");
  expect(await download.failure()).toBeNull();
  await page.reload();
  await expect(page.locator(".project-name")).toContainText("前端验收");
  await expect(page.locator(".clip-block")).toHaveCount(2);
  expect(errors).toEqual([]);
});

test("修改画面复用播放器且素材删除可以完整撤销", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const pid = await createProject(page);
  await page.getByRole("button", { name: "导入素材", exact: true }).click();
  await page.locator(".library-list label").filter({ hasText: "frame-red.mp4" }).getByRole("checkbox").check();
  await page.getByRole("button", { name: "导入所选 1" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 30000 });
  await page.getByRole("button", { name: "添加素材 frame-red.mp4", exact: true }).click();
  await saved(page, 1);
  await expect(page.getByRole("button", { name: "播放", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "片段 1 frame-red.mp4", exact: true }).click();
  await page.getByRole("button", { name: "播放", exact: true }).click();
  await expect.poll(() => page.locator(".time-display strong").textContent()).not.toBe("00:00.00");
  await page.getByRole("button", { name: "暂停", exact: true }).click();
  const position = await page.locator(".time-display strong").textContent();
  const video = page.locator(".preview-stage video").first();
  await expect(video).toBeVisible();
  await video.evaluate(element => element.setAttribute("data-test-retained", "yes"));
  const source = await video.getAttribute("src");
  expect(source).toContain("/assets/");
  await page.getByRole("button", { name: "调色", exact: true }).click();
  const previewResponse = page.waitForResponse(r => r.url().endsWith("/preview") && r.request().method() === "POST");
  await page.getByRole("button", { name: "预设", exact: true }).click();
  await page.getByRole("button", { name: "经典黑白", exact: true }).click();
  await page.getByRole("button", { name: "应用调色", exact: true }).click();
  await saved(page, 2);
  expect((await previewResponse).ok()).toBeTruthy();
  await expect(page.getByRole("button", { name: "播放", exact: true })).toBeEnabled();
  await expect(video).toHaveAttribute("data-test-retained", "yes");
  await expect(video).toHaveAttribute("src", source!);
  await expect(page.locator(".time-display strong")).toHaveText(position!);
  const graded = page.locator('.preview-stage canvas[data-color-clip]');
  await expect(graded).toHaveAttribute('data-frame', /\d+/);
  await expect.poll(() => graded.evaluate(node => {
    const canvas = node as HTMLCanvasElement;
    const pixel = canvas.getContext('2d')!.getImageData(canvas.width / 2, canvas.height / 2, 1, 1).data;
    return pixel[0] > 10 && Math.max(pixel[0], pixel[1], pixel[2]) - Math.min(pixel[0], pixel[1], pixel[2]) <= 2;
  })).toBe(true);
  await page.getByRole("button", { name: "素材", exact: true }).click();
  await page.getByRole("button", { name: "删除素材 frame-red.mp4", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("磁盘原文件会保留");
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.locator(".asset-card")).toHaveCount(1);
  await page.getByRole("button", { name: "删除素材 frame-red.mp4", exact: true }).click();
  await page.getByRole("button", { name: "确认删除", exact: true }).click();
  await saved(page, 3);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator(".asset-card")).toHaveCount(0);
  await expect(page.locator(".clip-block")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "播放", exact: true })).toBeDisabled();
  await page.reload();
  await expect(page.locator(".statusbar")).toContainText("版本 3");
  await expect(page.locator(".asset-card")).toHaveCount(0);
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await saved(page, 4);
  await expect(page.locator(".asset-card")).toHaveCount(1);
  await expect(page.locator(".clip-block")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "播放", exact: true })).toBeEnabled();
  const project = await (await page.request.get(`/api/v1/projects/${pid}`)).json();
  expect(project.snapshot.clips[0].color_preset).toBe("monochrome");
  await page.screenshot({ path: "artifacts/frontend-acceptance/preview-assets-fixed.png", fullPage: true });
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

test("历史对话持续追加，刷新后可从历史选择且按项目隔离", async ({ page }) => {
  await page.route("**/api/v1/model-status", (route) =>
    route.fulfill({
      json: { configured: true, provider: "deepseek", model: "history-test" },
    }),
  );
  const pid = await createProject(page);
  await mockSessionMessages(page, pid);
  for (const text of ["第一条历史指令", "第二条历史指令"]) {
    await page.getByLabel("剪辑指令", { exact: true }).fill(text);
    await page.getByLabel("剪辑指令", { exact: true }).press("Enter");
    await expect(page.locator(".agent-message").last()).toContainText(
      `回复：${text}`,
    );
    await expect(page.getByLabel("剪辑指令", { exact: true })).toBeEnabled();
  }
  await expect(page.locator(".conversation-turn")).toHaveCount(2);
  const sessionId = await currentSession(page);
  await page.reload();
  await expect(page.getByRole("button", { name: "新会话", exact: true })).toBeEnabled();
  await expect(page.locator(".chat-sessions")).not.toHaveAttribute("data-active-session", sessionId);
  await expect(page.locator(".conversation-turn")).toHaveCount(0);
  await chooseSession(page, sessionId);
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

test("回车发送在服务端接受后清空输入，Shift换行和中文组词回车不发送", async ({ page }) => {
  await page.route("**/api/v1/model-status", (route) =>
    route.fulfill({
      json: { configured: true, provider: "deepseek", model: "test-model" },
    }),
  );
  await createProject(page);
  const sent: string[] = [];
  let accepted = false;
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/commands/*", route => accepted
    ? route.fulfill({ json: { run_id: "keyboard-run", status: "running", events: [], result: {} } })
    : route.fulfill({ status: 404, json: { error_code: "RUN_NOT_FOUND" } }));
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
  await expect(input).toHaveValue("中文组词确认");
  accepted = true;
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

test("上传开始立即收起素材弹窗，处理进度和失败提示留在工作台", async ({ page }) => {
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
  await dialog.getByRole("button", { name: "从文件导入", exact: false }).click();
  await (await chooser).setFiles("database/test-inputs/grid.png");
  await expect(dialog).toHaveCount(0);
  await expect(page.locator(".statusbar").getByRole("status")).toContainText("导入素材 1/1");
  await expect(page.locator(".asset-card")).toHaveCount(0);
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
  await dialog.getByRole("button", { name: "从文件导入", exact: false }).click();
  await (await failedChooser).setFiles("database/test-inputs/grid.png");
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("alert")).toContainText("测试素材无法导入");
  await expect(page.locator(".statusbar").getByRole("status")).toContainText("准备就绪");
  await expect(page.locator(".asset-card")).toHaveCount(1);
});

test("历史批量导入开始即可收起弹窗，完成一个就显示一个素材", async ({ page }) => {
  await createProject(page);
  await page.getByRole("button", { name: "导入素材", exact: true }).click();
  for (const name of ["frame-red.mp4", "frame-blue-24fps.mp4"]) {
    await page.locator(".library-list label").filter({ hasText: name }).getByRole("checkbox").check();
  }
  const releases: (() => void)[] = [];
  await page.route("**/assets/reuse", async (route) => {
    await new Promise<void>((resolve) => releases.push(resolve));
    await route.continue();
  });
  await page.getByRole("button", { name: "导入所选 2" }).click();
  await expect(page.getByRole("dialog", { name: "导入素材" })).toHaveCount(0);
  await expect(page.locator(".statusbar")).toContainText("导入素材 1/2");
  await expect.poll(() => releases.length).toBe(1);
  releases[0]();
  await expect(page.locator(".asset-card")).toHaveCount(1);
  await expect(page.locator(".statusbar")).toContainText("导入素材 2/2");
  await expect.poll(() => releases.length).toBe(2);
  releases[1]();
  await expect(page.locator(".asset-card")).toHaveCount(2);
  await expect(page.locator(".statusbar")).toContainText("准备就绪");
});

test("刷新和新会话按钮不保存空记录，第一条消息才创建且后续消息沿用", async ({ page }) => {
  await page.route("**/api/v1/model-status", route => route.fulfill({ json: { configured: true, model: "session-test" } }));
  const pid = await createProject(page);
  const chats = await mockSessionMessages(page, pid);
  for (let i = 0; i < 3; i++) {
    await page.reload();
    await expect(page.getByRole("button", { name: "新会话", exact: true })).toBeEnabled();
    await page.getByLabel("剪辑指令", { exact: true }).fill("还未发送的草稿");
    await page.getByRole("button", { name: "新会话", exact: true }).click();
    await expect(page.getByLabel("剪辑指令", { exact: true })).toHaveValue("");
    await expect(page.locator(".recent-sessions .chat-session-row")).toHaveCount(0);
    expect(await currentSession(page)).toBeNull();
  }
  expect(chats.submitted).toHaveLength(0);
  await sendMessage(page, "第一条正式消息");
  const sid = await currentSession(page);
  expect(sid).toBeTruthy();
  expect(chats.submitted[0].new_session).toBe(true);
  await sendMessage(page, "第二条消息");
  expect(chats.submitted[1].session_id).toBe(sid);
  expect(chats.submitted[1].new_session).toBeUndefined();
  expect(Object.keys(chats.history)).toHaveLength(1);
  await page.reload();
  await expect(page.getByRole("button", { name: "新会话", exact: true })).toBeEnabled();
  expect(await currentSession(page)).toBeNull();
  await expect(page.locator(".recent-sessions .chat-session-title")).toHaveText(["第一条正式消息"]);
  expect(Object.keys(chats.history)).toHaveLength(1);
});

test("切换工程只列出本工程会话，返回原工程不保存空会话", async ({ page }) => {
  await page.route("**/api/v1/model-status", route => route.fulfill({ json: { configured: true, model: "session-test" } }));
  const name = `会话隔离-${Date.now()}`;
  const a = await (await page.request.post("/api/v1/projects", { data: { name: `${name}-A` } })).json();
  const b = await (await page.request.post("/api/v1/projects", { data: { name: `${name}-B` } })).json();
  const aChats = await mockSessionMessages(page, a.project_id);
  const bChats = await mockSessionMessages(page, b.project_id);
  await page.addInitScript(pid => localStorage.setItem("shiguang-project", pid), a.project_id);
  await page.goto("/");
  await sendMessage(page, "A 的第一条指令");
  const first = await currentSession(page);
  await page.getByRole("button", { name: "新会话", exact: true }).click();
  await sendMessage(page, "A 的第二个会话");
  async function openProject(suffix: string) {
    await page.getByRole("button", { name: "打开工程", exact: true }).click();
    await page.locator(".project-list button").filter({ hasText: `${name}-${suffix}` }).click();
    await expect(page.getByRole("button", { name: "新会话", exact: true })).toBeEnabled();
  }
  await page.getByLabel("剪辑指令", { exact: true }).fill("A 的未发送内容");
  await openProject("B");
  await expect(page.locator(".recent-sessions .chat-session-row")).toHaveCount(0);
  await expect(page.getByLabel("剪辑指令", { exact: true })).toHaveValue("");
  expect(Object.keys(bChats.history)).toHaveLength(0);
  await sendMessage(page, "B 独立的会话");
  await expect(page.locator(".recent-sessions .chat-session-title")).toHaveText(["B 独立的会话"]);
  await openProject("A");
  expect(await currentSession(page)).toBeNull();
  expect(Object.keys(aChats.history)).toHaveLength(2);
  expect(await allSessionIds(page)).toHaveLength(2);
  await chooseSession(page, first);
  await expect(page.locator(".user-message")).toContainText("A 的第一条指令");
});

test("刷新时恢复仍在运行的会话，保留取消入口且不额外创建会话", async ({ page }) => {
  const pid = await createProject(page);
  const root = `/api/v1/projects/${pid}`;
  const sid = "active-session";
  const sessions = [{ session_id: sid, project_id: pid, title: "正在执行的编辑", has_messages: true,
    created_at: new Date().toISOString(), updated_at: new Date().toISOString() }];
  const run = {
    run_id: "restored-active-run", project_id: pid, session_id: sid,
    status: "running", instruction: "正在执行的编辑", events: [], result: {},
    created_at: new Date().toISOString(),
  };
  await page.route(`**${root}/sessions`, route => route.fulfill({
    json: sessions.map((s: { session_id: string }) => ({ ...s, running: s.session_id === sid })),
  }));
  await page.route(`**${root}/runs?*`, route => route.fulfill({ json: { items: [run], next_cursor: null } }));
  await page.route(`**${root}/runs/${run.run_id}`, route => route.fulfill({ json: run }));
  let creations = 0;
  page.on("request", request => {
    if (request.url().endsWith(`${root}/sessions`) && request.method() === "POST") creations++;
  });
  await page.reload();
  await expect(page.locator(".chat-sessions")).toHaveAttribute("data-active-session", sid);
  await expect(page.getByRole("button", { name: "取消AI运行" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "新会话", exact: true })).toBeDisabled();
  await expect(page.locator(".user-message")).toContainText("正在执行的编辑");
  expect(creations).toBe(0);
});

test("同一工程发送后才保存会话，刷新和重开不新增且历史可切回", async ({ page }) => {
  await page.route("**/api/v1/model-status", (route) => route.fulfill({
    json: { configured: true, provider: "deepseek", model: "session-test" },
  }));
  const pid = await createProject(page);
  const root = `/api/v1/projects/${pid}`;
  await page.locator("input[type=file]").setInputFiles("database/test-inputs/grid.png");
  await expect(page.locator(".asset-card")).toHaveCount(1);
  await page.getByRole("button", { name: "添加素材 grid.png", exact: true }).click();
  await saved(page, 1);
  const before = await (await page.request.get(root)).json();
  const chats = await mockSessionMessages(page, pid);
  const send = (text: string) => sendMessage(page, text);
  await send("旧会话的编辑想法");
  const firstId = await currentSession(page);
  await expect(page.locator(`.recent-sessions [data-session-id="${firstId}"] .chat-session-title`)).toHaveText("旧会话的编辑想法");
  await send("追加要求不应该替换标题");
  await expect(page.locator(`.recent-sessions [data-session-id="${firstId}"] .chat-session-title`)).toHaveText("旧会话的编辑想法");
  await page.getByLabel("剪辑指令", { exact: true }).fill("旧会话未发送的内容");
  await page.getByRole("button", { name: "新会话", exact: true }).click();
  await expect(page.locator(".chat-sessions")).not.toHaveAttribute("data-active-session", firstId);
  await expect(page.locator(".conversation-turn")).toHaveCount(0);
  await expect(page.getByLabel("剪辑指令", { exact: true })).toHaveValue("");
  await expect(page.locator(".asset-card")).toHaveCount(1);
  expect(await (await page.request.get(root)).json()).toEqual(before);
  await send("新会话的编辑想法");
  const secondId = await currentSession(page);
  await page.reload();
  await expect(page.getByRole("button", { name: "新会话", exact: true })).toBeEnabled();
  expect(await currentSession(page)).toBeNull();
  expect(await allSessionIds(page)).toHaveLength(2);
  await expect(page.locator(".conversation-turn")).toHaveCount(0);
  await chooseSession(page, firstId);
  await expect(page.locator(".user-message").first()).toContainText("旧会话的编辑想法");
  await expect(page.locator(".conversation")).not.toContainText("新会话的编辑想法");
  await chooseSession(page, secondId);
  await expect(page.locator(".user-message")).toContainText("新会话的编辑想法");
  // 从项目列表重开当前工程只回到空白状态，不保存新的会话。
  await page.getByRole("button", { name: "打开工程", exact: true }).click();
  await page.locator(".project-list button").filter({ hasText: "前端验收 · 日常片段" }).first().click();
  await expect(page.getByRole("button", { name: "新会话", exact: true })).toBeEnabled();
  expect(await currentSession(page)).toBeNull();
  expect(await allSessionIds(page)).toHaveLength(2);
  expect(Object.keys(chats.history)).toHaveLength(2);
  await expect(page.locator(".conversation-turn")).toHaveCount(0);
  expect(await (await page.request.get(root)).json()).toEqual(before);
  await page.screenshot({ path: "artifacts/frontend-acceptance/chat-sessions.png", fullPage: true });
});

test("最近三行使用首条消息标题，全部会话可选择且长标题不撑宽面板", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/api/v1/model-status", route => route.fulfill({ json: {
    configured: true, provider: "openai", model: "session-layout-test",
  } }));
  const pid = await createProject(page);
  const root = `/api/v1/projects/${pid}`;
  const titles = [
    "给旅行素材加上柔和的配乐，让整体节奏更舒缓，同时保留海边的环境音和人物对白",
    "把开头的三个镜头剪短一点",
    "给夜景画面做电影感调色",
    "在结尾添加旅行日期",
  ];
  const sessions = titles.map((title, i) => ({
    session_id: `layout-session-${i}`, project_id: pid, title, has_messages: true, running: false,
    created_at: new Date(Date.now() - (i + 1) * 86_400_000).toISOString(),
    updated_at: new Date(Date.now() - (i + 1) * 3_600_000).toISOString(),
  }));
  await page.route(`**${root}/sessions`, async route => {
    if (route.request().method() !== "GET") return route.continue();
    return route.fulfill({ json: sessions });
  });
  await page.route(`**${root}/runs?*`, route => {
    const sid = new URL(route.request().url()).searchParams.get("session_id");
    const session = sessions.find(s => s.session_id === sid);
    return route.fulfill({ json: { items: session ? [{
      run_id: `${sid}-run`, project_id: pid, session_id: sid, instruction: session.title,
      status: "completed", result: { summary: "已完成这次编辑。", changed: false }, events: [],
      created_at: session.created_at,
    }] : [], next_cursor: null } });
  });
  await page.route(`**${root}/commands`, route => {
    const command = route.request().postDataJSON();
    return route.fulfill({ json: {
      run_id: "layout-new-run", project_id: pid, session_id: command.session_id || "layout-new-session",
      status: "completed", result: { summary: "可以，先看看素材。", changed: false }, events: [],
    } });
  });
  await page.reload();
  await expect(page.getByLabel("剪辑指令", { exact: true })).toBeEnabled();
  await page.getByLabel("剪辑指令", { exact: true }).fill("帮我把旅行素材剪成一分钟的 Vlog");
  await page.getByRole("button", { name: "发送剪辑指令" }).click();
  await expect(page.getByRole("button", { name: "新会话", exact: true })).toBeEnabled();
  await expect(page.locator(".recent-sessions .chat-session-row")).toHaveCount(3);
  await expect(page.locator(".recent-sessions .chat-session-title")).toHaveText([
    "帮我把旅行素材剪成一分钟的 Vlog", titles[0], titles[1],
  ]);
  await expect(page.locator(".chat-sessions select")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "查看全部（5 个）", exact: true })).toBeVisible();
  await chooseSession(page, sessions[3].session_id);
  await expect(page.locator(".user-message")).toHaveText(`你${titles[3]}`);
  await page.locator(`.recent-sessions [data-session-id="${sessions[0].session_id}"]`).click();
  await expect(page.locator(`.recent-sessions [data-session-id="${sessions[0].session_id}"]`)).toHaveAttribute("aria-current", "true");
  await page.locator("#workspace-dock").screenshot({ path: "artifacts/frontend-acceptance/recent-sessions.png" });
  await page.setViewportSize({ width: 1100, height: 850 });
  const title = page.locator(".recent-sessions .chat-session-title").nth(1);
  expect(await title.evaluate(element => element.scrollWidth > element.clientWidth)).toBe(true);
  expect(await page.locator(".chat-sessions").evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  expect(errors).toEqual([]);
});
