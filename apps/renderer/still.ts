// 从共享调色、构图和转场合成器导出完整分辨率的 PNG 封面。
import { bundle } from "@remotion/bundler";
import { renderStill, selectComposition } from "@remotion/renderer";
import { readFileSync, existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
const [manifest, output] = process.argv.slice(2);
if (!manifest || !output)
  throw new Error("Usage: still.ts manifest.json output.png");
const props = JSON.parse(readFileSync(manifest, "utf8"));
const browserExecutable = [
  process.env.CHROME_PATH,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "/usr/bin/chromium",
].find((p): p is string => !!p && existsSync(p));
const serveUrl = await bundle({
  entryPoint: path.join(root, "apps/renderer/cover-entry.ts"),
  publicDir: path.join(path.dirname(manifest), "public"),
  outDir: path.join(path.dirname(manifest), "bundle"),
  enableCaching: true,
});
const composition = await selectComposition({
  serveUrl,
  id: "Cover",
  inputProps: props,
  browserExecutable,
});
await renderStill({
  serveUrl,
  composition,
  inputProps: props,
  output: path.resolve(output),
  frame: props.cover.source === "timeline" ? props.cover.frame : 0,
  imageFormat: "png",
  browserExecutable,
  logLevel: "error",
  overwrite: true,
});
process.stdout.write(JSON.stringify({ status: "success", output }) + "\n");
