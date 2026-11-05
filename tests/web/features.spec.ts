// 验证模板预览、可撤销应用、素材分析与录音输入；外部模型响应按用例模拟。
import { test, expect, type Page } from "@playwright/test";
test.use({
  permissions: ["microphone"],
  launchOptions: {
    executablePath:
      process.env.CHROME_PATH ||
      "C:/Program Files/Google/Chrome/Application/chrome.exe",
    args: [
      "--use-fake-device-for-media-stream",
      "--use-fake-ui-for-media-stream",
    ],
  },
});

async function ready(page: Page) {
  const response = await page.request.post("/api/v1/projects", {
    data: {
      name: "新增功能浏览器验收",
      composition: { width: 320, height: 180 },
    },
  });
  const pid = (await response.json()).project_id;
  const root = `/api/v1/projects/${pid}`;
  const media = await page.request.post(root + "/assets/import", {
    data: { path: "test-inputs/frame-red.mp4" },
  });
  const asset = await media.json();
  const draft = await (
    await page.request.post(root + "/drafts", { data: {} })
  ).json();
  await page.request.post(`${root}/drafts/${draft.id}/tools`, {
    data: {
      tool: "insert_clip",
      op_id: "initial",
      expected_seq: 0,
      arguments: {
        clip: {
          clip_id: "c1",
          asset_id: asset.asset_id,
          kind: "video",
          source_out_frame: 60,
        },
      },
    },
  });
  await page.request.post(`${root}/drafts/${draft.id}/commit`, { data: {} });
  await page.addInitScript(
    (id) => localStorage.setItem("shiguang-project", id),
    pid,
  );
  await page.goto("/");
  await expect(page.locator(".statusbar")).toContainText("版本 1");
  return { root, asset, pid };
}

test("运镜先预览再保存，支持调参和撤销，成片风格入口可用", async ({ page }) => {
  const { root } = await ready(page);
  await page
    .getByRole("button", { name: "片段 1 frame-red.mp4", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "播放", exact: true }),
  ).toBeEnabled({ timeout: 40000 });
  await page.getByRole("button", { name: "模板", exact: true }).click();
  await page
    .getByRole("region", { name: "剪辑模板库" })
    .getByRole("button", { name: "缓缓推进", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "运镜预览 · 尚未应用" }),
  ).toBeVisible();
  await page.getByLabel("运动强度").fill("0.25");
  await page.getByLabel("水平中心", { exact: true }).fill("0.65");
  const before = await (await page.request.get(root)).json();
  expect(before.revision).toBe(1);
  expect(before.snapshot.clips[0].motion).toBeNull();
  await page.screenshot({
    path: "artifacts/feature-acceptance/motion-preview.png",
  });
  await page.getByRole("button", { name: "应用运镜", exact: true }).click();
  await expect(page.locator(".statusbar")).toContainText("版本 2");
  await expect(
    page.getByRole("heading", { name: "运镜预览 · 尚未应用" }),
  ).toHaveCount(0);
  const after = await (await page.request.get(root)).json();
  expect(after.snapshot.clips[0].motion.intensity).toBe(0.25);
  expect(after.snapshot.clips[0].motion.anchor_x).toBe(0.65);
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await expect(page.locator(".statusbar")).toContainText("版本 3");
  expect(
    (await (await page.request.get(root)).json()).snapshot.clips[0].motion,
  ).toBeNull();
  await page.getByRole("button", { name: "成片风格", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "AI 自动初剪" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "应用到当前成片", exact: true })
    .click();
  await expect(page.locator(".statusbar")).toContainText("版本 4");
  expect(
    (await (await page.request.get(root)).json()).snapshot.clips[0]
      .color_preset,
  ).toBe("warm");
  await page.screenshot({ path: "artifacts/feature-acceptance/styles.png" });
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("缺少模型时清楚展示配置状态，素材分析和字幕入口可见", async ({ page }) => {
  await ready(page);
  await page.getByRole("button", { name: "分析素材", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("视觉接口尚未配置");
  await expect(
    page.getByRole("button", { name: "分析 / 继续未完成阶段", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "关闭弹窗" }).click();
  await page.getByRole("button", { name: "文字", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "为所有视频生成字幕" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "模型设置" }).click();
  await expect(page.getByRole("dialog")).toContainText(
    "SHIGUANG_VISION_BASE_URL",
  );
  await expect(page.getByRole("dialog")).toContainText(
    "SHIGUANG_SPEECH_RESPONSE_FORMAT",
  );
});

test("后台分析显示进度、恢复结果，并按画面内容搜索素材", async ({ page }) => {
  await page.route("**/api/v1/perception-status", (route) => route.fulfill({ json: { vision: { configured: true, model: "mock-vision" }, speech: { configured: false, model: "" } } }));
  const { root, asset } = await ready(page);
  let reads = 0;
  let started = false;
  const record = (done: boolean) => ({
    status: done ? "succeeded" : "running", analysis_id: "test-analysis", asset_id: asset.asset_id,
    stages: { visual: { status: done ? "succeeded" : "running", reason_code: null }, speech: { status: "pending", reason_code: null } },
    segments: done ? [{ segment_id: "s1", start_frame: 0, end_frame: 60, frame_ids: [], status: "succeeded", visual: { description: "海边日落", tags: ["海边", "日落"], needs_review: false, review_reasons: [] } }] : [],
    speech: { presence: "unknown", language: null, utterances: [] },
  });
  await page.route(`**${root}/assets/${asset.asset_id}/analysis*`, (route) => {
    if (route.request().method() === "POST") { started = true; return route.fulfill({ json: record(false) }); }
    reads++;
    return route.fulfill({ json: record(reads >= 2) });
  });
  await page.route(`**${root}/analyses`, (route) => route.fulfill({ json: started ? { [asset.asset_id]: record(reads >= 2) } : {} }));
  await page.getByRole("button", { name: "分析素材", exact: true }).click();
  await page.getByRole("button", { name: "分析 / 继续未完成阶段", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("分析中");
  await expect(page.getByRole("dialog")).toContainText("海边日落", { timeout: 10000 });
  await page.getByRole("button", { name: "关闭弹窗" }).click();
  await page.getByLabel("搜索素材", { exact: true }).fill("日落");
  await expect(page.locator(".asset-card")).toHaveCount(1);
  await page.getByLabel("搜索素材", { exact: true }).fill("不存在的内容");
  await expect(page.locator(".asset-card")).toHaveCount(0);
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("素材音频问答单独配置，返回可试听的源时间且不改工程", async ({ page }) => {
  await page.route("**/api/v1/perception-status", route => route.fulfill({
    json: {
      vision: { configured: false }, speech: { configured: false },
      audio: { configured: true, model: "mock-omni" },
    },
  }));
  const { root, asset } = await ready(page);
  let calls = 0;
  await page.route(`**${root}/assets/${asset.asset_id}/audio-query`, route => {
    calls++;
    expect(route.request().postDataJSON()).toEqual({query: "哪里有鼓声？", start_ms: 500, end_ms: null});
    return route.fulfill({json: {status: "matched", answer: "听到一下鼓声", matches: [
      {label: "鼓声", start_ms: 1200, end_ms: 1250, anchor_ms: 1200, evidence: "短促敲击", certainty: "medium"},
    ]}});
  });
  await page.getByRole("button", { name: "分析素材", exact: true }).click();
  await page.getByLabel("关于这段声音，你想知道什么？").fill("哪里有鼓声？");
  await page.getByLabel("从第几秒开始").fill("0.5");
  await page.getByRole("button", { name: "查询音频" }).click();
  await expect(page.getByRole("dialog")).toContainText("听到一下鼓声");
  await page.getByRole("button", {name: "试听 鼓声 · 约 1.20 秒"}).click();
  await expect.poll(() => page.locator("dialog video").evaluate((video: HTMLVideoElement) => video.currentTime)).toBeGreaterThanOrEqual(0.69);
  expect(calls).toBe(1);
  expect((await (await page.request.get(root)).json()).revision).toBe(1);
});

test("两侧面板按屏宽排布，切换属性保留剪辑指令", async ({ page }) => {
  await page.route("**/api/v1/model-status", route => route.fulfill({
    json: { configured: true, provider: "deepseek", model: "layout-test" },
  }));
  await ready(page);
  for (const width of [1440, 1024, 820, 390]) {
    await page.setViewportSize({ width, height: 850 });
    const [left, preview, timeline, right] = await Promise.all(
      [".library-panel", ".preview-panel", ".timeline", ".right-panel"].map(selector =>
        page.locator(selector).boundingBox()),
    );
    expect(left && preview && timeline && right).toBeTruthy();
    if (width >= 981) {
      expect(left!.x + left!.width).toBeLessThanOrEqual(preview!.x);
      expect(preview!.x + preview!.width).toBeLessThanOrEqual(right!.x);
      expect(Math.abs(right!.y + right!.height - timeline!.y - timeline!.height)).toBeLessThan(2);
    } else if (width <= 680) {
      expect(preview!.y + preview!.height).toBeLessThanOrEqual(left!.y);
    } else {
      expect(right!.y).toBe(timeline!.y);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  await page.setViewportSize({ width: 1366, height: 768 });
  const instruction = page.getByLabel("剪辑指令", { exact: true });
  await instruction.fill("把两个镜头接起来");
  await page.getByRole("button", { name: "片段 1 frame-red.mp4" }).click();
  await page.getByRole("tab", { name: "片段属性" }).click();
  await expect(page.locator(".inspector-title")).toContainText("frame-red.mp4");
  await page.getByRole("tab", { name: "片段属性" }).press("ArrowLeft");
  await expect(instruction).toHaveValue("把两个镜头接起来");
});

test("网页分隔线可拖动、刷新保留尺寸、双击复位", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const { root } = await ready(page);
  const cases = [
    ["素材栏宽度", "#workspace-library", "width", 68, 0],
    ["助手与属性栏宽度", "#workspace-dock", "width", -52, 0],
    ["时间线高度", "#workspace-timeline", "height", 0, -76],
  ] as const;
  const original: number[] = [];
  for (const [name, selector, axis, dx, dy] of cases) {
    const divider = page.getByRole("separator", { name });
    const panel = page.locator(selector);
    const before = (await panel.boundingBox())![axis];
    original.push(before);
    const box = (await divider.boundingBox())!;
    const x = box.x + box.width / 2, y = box.y + box.height / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + dx, y + dy, { steps: 6 });
    await expect.poll(async () => (await panel.boundingBox())![axis]).toBe(before + Math.abs(dx || dy));
    await page.mouse.up();
  }
  await page.reload();
  for (const [i, [name, selector, axis, dx, dy]] of cases.entries()) {
    const divider = page.getByRole("separator", { name });
    await expect(divider).toHaveAttribute("aria-valuenow", String(original[i] + Math.abs(dx || dy)));
    await divider.dblclick();
    await expect.poll(async () => (await page.locator(selector).boundingBox())![axis]).toBe(original[i]);
  }
  const timeline = page.getByRole("separator", { name: "时间线高度" });
  await timeline.press("Home");
  const tracks = page.locator(".tracks-shell");
  await tracks.evaluate(el => el.scrollTop = el.scrollHeight);
  expect(await tracks.evaluate(el => el.scrollTop)).toBeGreaterThan(0);
  const [label, lane] = await Promise.all([page.locator(".track-labels > div").last().boundingBox(), page.locator(".music-track").boundingBox()]);
  expect(Math.abs(label!.y - lane!.y)).toBeLessThan(1);
  await timeline.press("Enter");
  expect((await (await page.request.get(root)).json()).revision).toBe(1);
});

test("平板与手机独立调整布局，支持触屏拖动", async ({ page }) => {
  await ready(page);
  await page.setViewportSize({ width: 820, height: 850 });
  const tablet = page.getByRole("separator", { name: "素材栏宽度" });
  await tablet.press("ArrowRight");
  const remembered = await tablet.getAttribute("aria-valuenow");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("separator")).toHaveCount(4);
  const preview = page.getByRole("separator", { name: "预览区高度" });
  const start = Number(await preview.getAttribute("aria-valuenow"));
  const box = (await preview.boundingBox())!;
  const x = box.x + box.width / 2, y = box.y + box.height / 2;
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: true });
  await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{x, y}] });
  await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{x, y: y + 58}] });
  await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await expect(preview).toHaveAttribute("aria-valuenow", String(start + 58));
  await page.getByRole("separator", { name: "素材栏高度" }).press("End");
  expect((await page.locator("#workspace-library").boundingBox())!.height).toBeGreaterThan(390);
  await page.setViewportSize({ width: 820, height: 850 });
  await expect(tablet).toHaveAttribute("aria-valuenow", remembered!);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(preview).toHaveAttribute("aria-valuenow", String(start + 58));
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("AI 工作中显示实时草稿、跟随文字修改，取消后恢复正式时间线", async ({page}) => {
  await page.route("**/api/v1/model-status", route => route.fulfill({json: {configured: true, model: "test", provider: "test"}}));
  const {root, pid} = await ready(page);
  const {snapshot} = await (await page.request.get(root)).json();
  const live = {run_id: "live-test", project_id: pid, status: "running", result: {}, events: [
    {事件: "工具执行", 工具: "add_text", 状态: "成功", draft_seq: 1, changed_ids: ["title1"]},
    {事件: "开始工具", 工具: "query_audio"},
  ], live_draft: {base: 1, seq: 1, snapshot: {...snapshot, text_items: [{
    text_id: "title1", text: "草稿标题", anchor: {kind: "timeline", start_frame: 15, end_frame: 45},
    style_preset: "title", x: 0.5, y: 0.5, font_size: 32, color: "#ffffff",
  }]}}};
  const terminal = {...live, status: "cancelled", live_draft: null, result: {summary: "已取消"}};
  let release!: () => void;
  const waiting = new Promise<void>(resolve => { release = resolve; });
  await page.route(`**${root}/commands`, async route => {await waiting; await route.fulfill({json: terminal});});
  await page.route(`**${root}/commands/*`, route => route.fulfill({json: live}));
  await page.route(`**${root}/runs/live-test/cancel`, route => route.fulfill({json: terminal}));
  await page.getByLabel("剪辑指令", {exact: true}).fill("添加标题，检查配乐");
  await page.getByRole("button", {name: "发送剪辑指令"}).click();
  const monitor = page.getByRole("region", {name: "AI 草稿监看"});
  await expect(monitor).toContainText("草稿标题");
  await expect(monitor.getByRole("slider")).toHaveValue("15");
  await expect(page.locator(".text-track [data-changed=true]")).toContainText("草稿标题");
  await expect(page.locator(".working-step")).toContainText("听取素材音频");
  await expect(page.getByRole("button", {name: "导出成片"})).toBeDisabled();
  await page.screenshot({path: ".cache/draft-monitor-workspace.png"});
  try {
    await page.getByRole("button", {name: "取消AI运行"}).click();
    await expect(monitor).toHaveCount(0);
    await expect(page.locator(".text-track")).not.toContainText("草稿标题");
    await expect(page.locator(".statusbar")).toContainText("版本 1");
    await expect(page.getByRole("button", {name: "导出成片"})).toBeEnabled();
  } finally {
    release();
  }
});

test.describe("录音", () => {
  test("转写先填入编辑框，取消录音不调用接口", async ({ page }) => {
    await page.route("**/api/v1/perception-status", (route) =>
      route.fulfill({
        json: {
          vision: { configured: false, model: "" },
          speech: { configured: true, model: "mock-asr" },
        },
      }),
    );
    let transcriptions = 0;
    let commands = 0;
    await page.route("**/voice-inputs", (route) => {
      transcriptions++;
      return route.fulfill({ json: { text: "把日落放在最后" } });
    });
    page.on("request", (request) => {
      if (request.url().endsWith("/commands") && request.method() === "POST")
        commands++;
    });
    await ready(page);
    await page.getByRole("button", { name: "录音输入" }).click();
    await expect(
      page.getByRole("button", { name: "结束并转写" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "结束并转写" }).click();
    await expect(page.getByLabel("剪辑指令", { exact: true })).toHaveValue(
      "把日落放在最后",
    );
    expect(transcriptions).toBe(1);
    expect(commands).toBe(0);
    await page.getByRole("button", { name: "录音输入" }).click();
    await expect(
      page.getByRole("button", { name: "结束并转写" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "取消录音" }).click();
    await expect(page.getByRole("button", { name: "录音输入" })).toBeVisible();
    expect(transcriptions).toBe(1);
  });
});
