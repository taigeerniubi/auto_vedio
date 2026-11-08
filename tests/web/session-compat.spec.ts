// 验证前端更新而后端仍运行旧版本时，会话标题和日期仍可从历史正确恢复。
import { test, expect } from "@playwright/test";

test("旧会话接口从分页首条消息恢复标题且缺失日期不会显示 Invalid Date", async ({ page }) => {
  const pid = "session-compat-project";
  const project = { project_id: pid, name: "会话兼容验收", revision: 0, snapshot: {
    schema_version: 1, composition: { width: 1920, height: 1080, fps: 30 },
    clips: [], transitions: [], text_items: [], audio_items: [],
  } };
  const message = (instruction: string, index: number) => ({
    run_id: `legacy-${index}`, project_id: pid, session_id: "old-session", status: "completed",
    instruction, created_at: "2026-11-08T08:47:58Z", events: [], result: { summary: "已收到", changed: false },
  });
  let olderPageRead = false;
  await page.route("**/api/v1/**", route => {
    const url = new URL(route.request().url());
    let json: unknown = {};
    if (url.pathname === "/api/v1/projects") json = [project];
    else if (url.pathname.endsWith("/model-status")) json = { configured: false, provider: "deepseek", model: "" };
    else if (url.pathname.endsWith("/perception-status")) json = {
      speech: { configured: false }, vision: { configured: false }, audio: { configured: false },
    };
    else if (url.pathname === `/api/v1/projects/${pid}`) json = project;
    else if (url.pathname.endsWith("/assets")) json = [];
    else if (url.pathname.endsWith("/sessions")) {
      json = route.request().method() === "POST"
        ? { session_id: "new-session", project_id: pid, title: "会话 7", created_at: "bad-date", running: false }
        : [{ session_id: "old-session", project_id: pid, title: "会话 6", created_at: "2026-11-08T08:44:29Z", running: false }];
    } else if (url.pathname.endsWith("/runs")) {
      if (url.searchParams.get("session_id") === "old-session") {
        const older = url.searchParams.has("before");
        olderPageRead ||= older;
        json = { items: [message(older ? "帮我剪辑一下，主题是酒吧之夜，按照风格来选" : "继续", older ? 1 : 101)], next_cursor: older ? null : 101 };
      } else json = { items: [], next_cursor: null };
    }
    return route.fulfill({ json });
  });
  await page.addInitScript(id => localStorage.setItem("shiguang-project", id), pid);
  await page.goto("/");
  await expect(page.getByRole("button", { name: "新会话", exact: true })).toBeEnabled();
  await expect(page.locator(".recent-sessions .chat-session-title")).toHaveText([
    "帮我剪辑一下，主题是酒吧之夜，按照风格来选",
  ]);
  expect(olderPageRead).toBe(true);
  await expect(page.locator(".chat-sessions")).not.toContainText("Invalid Date");
  await expect(page.locator(".chat-sessions")).not.toContainText("会话 6");
  await expect(page.locator(".chat-sessions")).not.toContainText("会话 7");
  await expect(page.locator('[data-session-id="old-session"] time')).toHaveAttribute("datetime", "2026-11-08T08:47:58Z");
  await page.locator('[data-session-id="old-session"]').click();
  await expect(page.locator(".chat-sessions")).toHaveAttribute("data-active-session", "old-session");
});
