// 通过共享 Remotion 组件执行单个或批量视频渲染任务。
import {bundle} from '@remotion/bundler';
import {openBrowser, renderMedia, selectComposition} from '@remotion/renderer';
import {readFileSync, existsSync} from 'node:fs';
import path from 'node:path';
import {availableParallelism} from 'node:os';
import {fileURLToPath} from 'node:url';
import type {Plan} from '../../packages/composition/math';

const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const [first, second] = process.argv.slice(2);
if (!first || !second) throw new Error('Usage: render.ts manifest.json output.mp4 | --batch jobs.json');
type Job = {manifest: string; output: string};
type RenderPlan = Plan & {export_settings?: {scale: number; quality: 'high' | 'standard' | 'compact'}};
const jobs = first === '--batch' ? JSON.parse(readFileSync(second, 'utf8')) as Job[] : [{manifest: first, output: second}];
if (!jobs.length) throw new Error('No render jobs');
const manifestPath = jobs[0].manifest;
const firstPlan = JSON.parse(readFileSync(manifestPath, 'utf8')) as RenderPlan;
let lastProgressTime = 0;
function progress(stage: string, value: number, renderedFrames = 0, totalFrames = firstPlan.total_frames, force = false) {
  const now = Date.now();
  if (!force && now - lastProgressTime < 400) return;
  lastProgressTime = now;
  process.stdout.write(JSON.stringify({event: 'render_progress', stage, progress: Math.min(100, value),
    rendered_frames: renderedFrames, total_frames: totalFrames}) + '\n');
}
const candidates = [process.env.CHROME_PATH, 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', '/usr/bin/chromium', '/usr/bin/google-chrome'];
const browserExecutable = candidates.find((p): p is string => Boolean(p && existsSync(p)));
const serveUrl = await bundle({entryPoint: path.join(project, 'apps/renderer/entry.ts'),
  publicDir: path.join(path.dirname(manifestPath), 'public'), outDir: path.join(path.dirname(manifestPath), 'bundle'),
  enableCaching: true, onProgress: (value) => progress('bundling', 12 + value * .08)});
const browser = await openBrowser('chrome', {browserExecutable});
try {
  for (const job of jobs) {
    const plan = JSON.parse(readFileSync(job.manifest, 'utf8')) as RenderPlan;
    const settings = plan.export_settings || {scale: 1, quality: 'high'};
    const inputProps = {plan};
    const composition = await selectComposition({serveUrl, id: 'Vlog', inputProps, puppeteerInstance: browser});
    progress('rendering', 20, 0, plan.total_frames, true);
    await renderMedia({serveUrl, composition, inputProps, outputLocation: path.resolve(job.output), codec: 'h264',
      crf: {high: 16, standard: 20, compact: 24}[settings.quality], x264Preset: 'fast',
      scale: settings.scale, pixelFormat: 'yuv420p', muted: true,
      concurrency: Math.max(1, Math.min(4, availableParallelism() - 1)), puppeteerInstance: browser,
      offthreadVideoThreads: 2, logLevel: 'error', overwrite: true,
      onProgress: (value) => progress('rendering', 20 + value.progress * 70, value.renderedFrames, plan.total_frames)});
    progress('rendering', 90, plan.total_frames, plan.total_frames, true);
    process.stdout.write(JSON.stringify({状态: '成功', 输出文件: job.output, 帧数: plan.total_frames}) + '\n');
  }
} finally {
  await browser.close({silent: true});
}
