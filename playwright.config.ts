// 配置真实浏览器验收及失败截图输出。
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/web",
  timeout: 90000,
  workers: 1,
  retries: 0,
  use: {
    baseURL: process.env.SHIGUANG_WEB_TEST_URL || "http://127.0.0.1:5173",
    viewport: { width: 1600, height: 1000 },
    launchOptions: {
      executablePath:
        process.env.CHROME_PATH ||
        "C:/Program Files/Google/Chrome/Application/chrome.exe",
    },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  reporter: [
    ["list"],
    ["json", { outputFile: "artifacts/frontend-acceptance/results.json" }],
  ],
});
