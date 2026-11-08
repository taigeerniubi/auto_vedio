// 配置本机前端开发服务、后端代理和生产构建目录。
import { defineConfig } from "vite";
import path from "node:path";
import { fileURLToPath } from "node:url";

export default defineConfig({
  root: path.dirname(fileURLToPath(import.meta.url)),
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: process.env.SHIGUANG_API_URL || "http://127.0.0.1:8000",
        // 长任务由后端单次请求超时和用户取消控制，代理不截断整个剪辑任务。
        timeout: 0,
      },
      "/health": process.env.SHIGUANG_API_URL || "http://127.0.0.1:8000",
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
