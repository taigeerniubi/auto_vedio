// 真实预览上验收框选、会话共享、输入恢复、主题与语音；模型响应使用隔离模拟。
import { test, expect, type Page } from "@playwright/test";

test.use({
  permissions: ["microphone"],
  launchOptions: {
    executablePath: process.env.CHROME_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe",
    args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"],
  },
});

async function workspace(page: Page, portrait = false) {
  await page.route("**/api/v1/model-status", route => route.fulfill({ json: { configured: true, model: "annotation-test" } }));
  const project = await (await page.request.post("/api/v1/projects", { data: {
    name: "画面批注验收", composition: portrait ? { width: 360, height: 640 } : { width: 640, height: 360 },
  } })).json();
  const pid = project.project_id as string;
  const root = `/api/v1/projects/${pid}`;
  const asset = await (await page.request.post(`${root}/assets/import`, { data: { path: "test-inputs/grid.png" } })).json();
  const draft = await (await page.request.post(`${root}/drafts`, { data: {} })).json();
  expect((await page.request.post(`${root}/drafts/${draft.id}/tools`, { data: {
    tool: "insert_clip", op_id: "annotation-fixture", expected_seq: 0,
    arguments: { clip: { clip_id: "annotation-clip", asset_id: asset.asset_id, kind: "image", still_duration_frames: 150 } },
  } })).ok()).toBe(true);
  await page.request.post(`${root}/drafts/${draft.id}/commit`, { data: {} });
  const history: Record<string, any[]> = {};
  const submitted: any[] = [];
  await page.route(`**${root}/sessions`, route => route.fulfill({ json: Object.entries(history).map(([sid, entries]) => ({
    session_id: sid, project_id: pid, title: entries[0].instruction, has_messages: true, running: false,
    created_at: entries[0].created_at, updated_at: entries.at(-1).created_at,
  })) }));
  await page.route(`**${root}/runs?*`, route => route.fulfill({ json: {
    items: history[new URL(route.request().url()).searchParams.get("session_id")!] || [], next_cursor: null,
  } }));
  await page.route(`**${root}/commands`, route => {
    const command = route.request().postDataJSON();
    submitted.push(command);
    const sid = command.new_session ? `session-${Object.keys(history).length}` : command.session_id;
    const entries = history[sid] ||= [];
    const run = { run_id: `annotation-run-${submitted.length}`, project_id: pid, session_id: sid,
      status: "completed", events: [], result: { summary: `收到：${command.instruction}`, changed: false, revision: 1 },
      instruction: command.instruction, created_at: new Date().toISOString(),
      annotation: command.annotation ? { ...command.annotation, revision: 1, composition: project.snapshot.composition,
        clips: [{ clip_id: "annotation-clip", asset_id: asset.asset_id, local_frame: command.annotation.frame, source_frame: null }] } : null,
    };
    entries.push(run);
    return route.fulfill({ json: run });
  });
  await page.addInitScript(id => localStorage.setItem("shiguang-project", id), pid);
  await page.goto("/");
  await expect(page.getByRole("button", { name: "播放", exact: true })).toBeEnabled({ timeout: 40000 });
  await expect(page.getByRole("button", { name: "新会话", exact: true })).toBeEnabled();
  return { root, pid, submitted, history };
}

async function selectRegion(page: Page, start = [0.2, 0.2], end = [0.7, 0.75]) {
  const button = page.getByRole("button", { name: "批注", exact: true });
  if (await button.getAttribute("aria-pressed") !== "true") await button.click();
  const box = (await page.locator(".annotation-surface").boundingBox())!;
  await page.mouse.move(box.x + box.width * start[0], box.y + box.height * start[1]);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * end[0], box.y + box.height * end[1], { steps: 6 });
  await page.mouse.up();
  await expect(page.getByRole("form", { name: "添加画面批注" })).toBeVisible();
  return box;
}

async function sendSidebar(page: Page, text: string) {
  await page.getByLabel("剪辑指令", { exact: true }).fill(text);
  await page.getByRole("button", { name: "发送剪辑指令" }).click();
  await expect(page.locator(".agent-message").last()).toContainText(`收到：${text}`);
  await expect(page.getByRole("button", { name: "新会话", exact: true })).toBeEnabled();
}

test("首条批注创建一个会话，保留右侧草稿，后续普通输入和刷新共用历史", async ({ page }) => {
  const { submitted, history } = await workspace(page);
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.getByLabel("剪辑指令", { exact: true }).fill("右侧还没发送的想法");
  await page.getByRole("tab", { name: "片段属性", exact: true }).click();
  await selectRegion(page);
  await page.getByLabel("批注内容", { exact: true }).fill("给这里加一句字幕");
  await page.getByRole("button", { name: "发送批注" }).click();
  await expect(page.locator(".annotation-composer")).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "AI 助手", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".annotation-badge")).toContainText("00:00.00");
  await expect(page.getByLabel("剪辑指令", { exact: true })).toHaveValue("右侧还没发送的想法");
  expect(submitted).toHaveLength(1);
  expect(submitted[0].new_session).toBe(true);
  expect(submitted[0].selected_clip_ids).toEqual([]);
  expect(submitted[0].annotation.region.x).toBeCloseTo(0.2, 2);
  expect(submitted[0].annotation.region.height).toBeCloseTo(0.55, 2);
  await sendSidebar(page, "沿用刚才批注的字幕风格");
  expect(submitted[1].session_id).toBe("session-0");
  expect(submitted[1].annotation).toBeUndefined();
  expect(Object.keys(history)).toHaveLength(1);
  await page.reload();
  await page.getByRole("button", { name: "切换会话：给这里加一句字幕", exact: true }).click();
  await expect(page.locator(".conversation-turn")).toHaveCount(2);
  await expect(page.locator(".annotation-badge")).toHaveCount(1);
  expect(errors).toEqual([]);
});

test("普通讨论后批注沿用会话；换帧和新会话清除区域，文字不会误发到旧帧", async ({ page }) => {
  const { submitted } = await workspace(page);
  await sendSidebar(page, "短片用温暖的色调");
  await selectRegion(page);
  await page.getByLabel("批注内容", { exact: true }).fill("这段也保持温暖");
  // 换帧使框选失效，重新框选保留未发送的文字。
  await page.getByRole("button", { name: "下一帧", exact: true }).click();
  await expect(page.locator(".annotation-composer")).toHaveCount(0);
  await selectRegion(page, [0.8, 0.8], [0.1, 0.25]);
  await expect(page.getByLabel("批注内容", { exact: true })).toHaveValue("这段也保持温暖");
  await page.getByRole("button", { name: "发送批注" }).click();
  await expect(page.locator(".agent-message").last()).toContainText("收到：这段也保持温暖");
  expect(submitted[1].session_id).toBe("session-0");
  expect(submitted[1].playhead_frame).toBe(1);
  expect(submitted[1].annotation.frame).toBe(1);
  expect(submitted[1].annotation.region).toMatchObject({ x: expect.any(Number), y: expect.any(Number) });
  expect(submitted[1].annotation.region.width).toBeCloseTo(0.7, 2);
  await selectRegion(page);
  await page.getByRole("button", { name: "新会话", exact: true }).click();
  await expect(page.locator(".annotation-composer, .annotation-surface")).toHaveCount(0);
  await expect(page.locator(".conversation-turn")).toHaveCount(0);
});

test("拒绝提交保留输入，中文输入法不会误发，Esc 恢复焦点，键盘可批注整帧", async ({ page }) => {
  const { root, submitted } = await workspace(page);
  await page.route(`**${root}/commands`, route => route.fulfill({ status: 409,
    json: { error_code: "REVISION_CONFLICT", summary: "项目已变更，请重新选择画面" } }));
  await page.getByRole("button", { name: "批注", exact: true }).focus();
  await page.keyboard.press("Space");
  await expect(page.getByRole("button", { name: "批注", exact: true })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "批注整个画面" }).focus();
  await page.keyboard.press("Enter");
  const field = page.getByLabel("批注内容", { exact: true });
  await field.fill("不要丢掉这句批注");
  await field.dispatchEvent("keydown", { key: "Enter", code: "Enter", isComposing: true, keyCode: 229 });
  expect(submitted).toHaveLength(0);
  await field.press("Shift+Enter");
  await expect(field).toHaveValue("不要丢掉这句批注\n");
  await field.press("Enter");
  await expect(page.getByRole("alert")).toContainText("项目已变更");
  await expect(field).toHaveValue("不要丢掉这句批注\n");
  await expect(page.getByRole("button", { name: "发送批注" })).toBeEnabled();
  await page.keyboard.press("Escape");
  await expect(page.locator(".annotation-surface")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "批注", exact: true })).toBeFocused();
});

test("竖屏框选按画布计算，边缘裁切与浅深主题和窄屏输入框不溢出", async ({ page }) => {
  const { submitted } = await workspace(page, true);
  await page.setViewportSize({ width: 1440, height: 960 });
  await selectRegion(page, [0.25, 0.25], [1.2, 1.2]);
  await page.getByLabel("批注内容", { exact: true }).fill("给这段画面加一个标题");
  for (const mode of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: mode });
    await expect(page.locator("html")).toHaveAttribute("data-theme", mode);
    await page.screenshot({ path: `artifacts/annotations/${mode}-1440.png`, fullPage: true, animations: "disabled" });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  const form = page.getByRole("form", { name: "添加画面批注" });
  await expect(form).toBeVisible();
  await expect.poll(async () => {
    const bounds = (await form.boundingBox())!;
    return bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= 390 && bounds.y + bounds.height <= 844;
  }).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "artifacts/annotations/dark-390.png", fullPage: true, animations: "disabled" });
  await page.getByRole("button", { name: "发送批注" }).click();
  await expect(page.locator(".annotation-composer")).toHaveCount(0);
  expect(submitted[0].annotation.region.x).toBeCloseTo(0.25, 2);
  expect(submitted[0].annotation.region.width).toBeCloseTo(0.75, 2);
  expect(submitted[0].annotation.region.height).toBeCloseTo(0.75, 2);
});

test("批注录音只转写到悬浮输入，发送期间禁用重复操作，取消会释放麦克风", async ({ page }) => {
  await page.route("**/api/v1/perception-status", route => route.fulfill({ json: {
    vision: { configured: false }, speech: { configured: true, model: "fake-asr" }, audio: { configured: false },
  } }));
  let transcriptions = 0;
  await page.route("**/voice-inputs", route => { transcriptions++; return route.fulfill({ json: { text: "让这段画面亮一些" } }); });
  const { submitted } = await workspace(page);
  await selectRegion(page);
  const form = page.getByRole("form", { name: "添加画面批注" });
  await form.getByRole("button", { name: "录音输入" }).click();
  await expect(form.getByRole("button", { name: "结束并转写" })).toBeVisible();
  await expect(form.getByRole("button", { name: "发送批注" })).toBeDisabled();
  await form.getByRole("button", { name: "结束并转写" }).click();
  await expect(page.getByLabel("批注内容", { exact: true })).toHaveValue("让这段画面亮一些");
  expect(transcriptions).toBe(1);
  expect(submitted).toHaveLength(0);
  await form.getByRole("button", { name: "录音输入" }).click();
  await expect(form.getByRole("button", { name: "取消录音" })).toBeVisible();
  await form.getByRole("button", { name: "取消录音" }).click();
  await expect(form.getByRole("button", { name: "录音输入" })).toBeVisible();
  expect(transcriptions).toBe(1);
});
