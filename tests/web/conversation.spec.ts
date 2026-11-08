// 验证助手回复的 Markdown 排版、旧记录换行、窄栏布局和不可信内容隔离。
import { test, expect, type Page } from "@playwright/test";

async function history(page: Page, messages: string[]) {
  const created = await page.request.post("/api/v1/projects", { data: { name: "对话排版验收" } });
  const { project_id: pid } = await created.json();
  await page.addInitScript((id) => localStorage.setItem("shiguang-project", id), pid);
  await page.route(`**/projects/${pid}/sessions`, route => route.fulfill({ json: [{
    session_id: "reply-session", project_id: pid, title: "看看这次分析", has_messages: true, running: false,
    created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
  }] }));
  await page.route(`**/projects/${pid}/runs?*`, (route) => route.fulfill({ json: {
    items: messages.map((summary, i) => ({
      run_id: `reply-${i}`, project_id: pid, session_id: "reply-session", status: "completed",
      instruction: i ? "我这几个音频都是什么风格？" : "看看这次分析",
      created_at: new Date().toISOString(), result: { summary, changed: false, revision: 0 },
      events: [{ 事件: "工具执行", 工具: "query_audio", asset_name: "配乐.mp3", 状态: "成功" }],
    })), next_cursor: null,
  } }));
  await page.goto("/");
  await page.getByRole("button", { name: "切换会话：看看这次分析", exact: true }).click();
  await expect(page.locator(".reply-content")).toHaveCount(messages.length);
}

const musicReply = `这几首以电子乐为主，主要区别在**氛围和节奏**。

1. **bgm-tech-house.mp3**
   **Tech House** · 冷峻、律动强
   适合城市夜景、时尚片段。

2. **house-vibez.mp3**
   **House / EDM** · 明亮、轻快
   适合街拍、日常 Vlog。

3. **cat-walk.mp3**
   **Synthwave** · 复古、电子感
   适合夜驾、科技数码片段。

### 搭配建议

当前的冷调画面可以继续用 **tech-house**；想更复古，可以试试 **cat-walk**。

> 以上是模型听感分析，本次没有修改时间线。`;

test("新旧回复按段落列表显示，处理过程可展开且刷新后保留排版", async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1100 });
  const oldReply = "已听过这些音频。\n\n1. bgm-tech-house.mp3 — Tech House，节奏有力。\n2. cat-walk.mp3 — Synthwave，复古氛围。\n\n这是推荐理由。\n本次没有修改时间线。";
  await history(page, [oldReply, musicReply]);
  const old = page.locator(".reply-content").first();
  const current = page.locator(".reply-content").last();
  await expect(old.locator("ol > li")).toHaveCount(2);
  await expect(old.locator("p").last().locator("br")).toHaveCount(1);
  await expect(current.locator("ol > li")).toHaveCount(3);
  await expect(current.locator("li").first().locator("strong").first()).toHaveText("bgm-tech-house.mp3");
  await expect(current.getByRole("heading", { name: "搭配建议" })).toBeVisible();
  await expect(current.locator("blockquote")).toContainText("没有修改时间线");
  await expect(page.locator(".execution-list").last()).toBeHidden();
  await page.locator(".execution-details > summary").last().click();
  await expect(page.locator(".execution-list").last()).toContainText("配乐.mp3");
  await page.locator(".execution-details > summary").last().click();
  await page.reload();
  await page.getByRole("button", { name: "切换会话：看看这次分析", exact: true }).click();
  await expect(page.locator(".reply-content").last().locator("ol > li")).toHaveCount(3);
  await page.locator(".conversation-turn").last().scrollIntoViewIfNeeded();
  await page.locator("#workspace-dock").screenshot({ path: "artifacts/conversation-acceptance/reply-layout.png" });
});

test("表格长行在窄栏内滚动，HTML和危险链接不执行或加载", async ({ page }) => {
  const remoteRequests: string[] = [];
  await page.route("https://reply-test.invalid/**", route => { remoteRequests.push(route.request().url()); return route.abort(); });
  const longCell = "长文件名".repeat(35);
  await history(page, [`## 排版检查

| 文件 | 风格 | 建议场景 |
| --- | --- | --- |
| ${longCell} | 电子乐 | 城市夜景 |

\`\`\`text
${"long-code-line".repeat(35)}
\`\`\`

[正常链接](https://example.com) [危险链接](javascript:alert(1))

![仅显示图片说明](https://reply-test.invalid/tracker.png)

<img src="https://reply-test.invalid/raw.png" onerror="window.replyInjected=true">
<script>window.replyInjected=true</script>`]);
  const reply = page.locator(".reply-content");
  await expect(reply.getByRole("table")).toHaveCount(1);
  await expect(reply.getByRole("link", { name: "正常链接" })).toHaveAttribute("rel", "noopener noreferrer");
  await expect(reply.getByRole("link", { name: "危险链接" })).toHaveCount(0);
  await expect(reply.locator("img, script")).toHaveCount(0);
  await expect(reply.locator(".reply-image-label")).toHaveText("仅显示图片说明");
  for (const width of [1600, 390]) {
    await page.setViewportSize({ width, height: 1100 });
    expect(await reply.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const table = reply.getByRole("region", { name: "回复中的表格" });
    expect(await table.evaluate(el => el.scrollWidth >= el.clientWidth)).toBe(true);
  }
  expect(await page.evaluate(() => "replyInjected" in window)).toBe(false);
  expect(remoteRequests).toEqual([]);
});
