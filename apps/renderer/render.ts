// 通过共享 Remotion 组件执行单个或批量视频渲染任务。
import {bundle} from '@remotion/bundler';
import {openBrowser, renderMedia, selectComposition} from '@remotion/renderer';
import {readFileSync, existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import type {Plan} from '../../packages/composition/math';

const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const [first, second] = process.argv.slice(2);
if (!first || !second) throw new Error('Usage: render.ts manifest.json output.mp4 | --batch jobs.json');
type Job = {manifest: string; output: string};
const jobs = first === '--batch' ? JSON.parse(readFileSync(second, 'utf8')) as Job[] : [{manifest: first, output: second}];
if (!jobs.length) throw new Error('No render jobs');
const manifestPath = jobs[0].manifest;
const candidates = [process.env.CHROME_PATH, 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', '/usr/bin/chromium', '/usr/bin/google-chrome'];
const browserExecutable = candidates.find((p): p is string => Boolean(p && existsSync(p)));
const serveUrl = await bundle({entryPoint: path.join(project, 'apps/renderer/entry.ts'),
  publicDir: path.join(path.dirname(manifestPath), 'public'), outDir: path.join(path.dirname(manifestPath), 'bundle'),
  enableCaching: true});
const browser = await openBrowser('chrome', {browserExecutable});
try {
  for (const job of jobs) {
    const plan = JSON.parse(readFileSync(job.manifest, 'utf8')) as Plan;
    const inputProps = {plan};
    const composition = await selectComposition({serveUrl, id: 'Vlog', inputProps, puppeteerInstance: browser});
    await renderMedia({serveUrl, composition, inputProps, outputLocation: path.resolve(job.output), codec: 'h264',
      crf: 16, pixelFormat: 'yuv420p', muted: true, concurrency: 2, puppeteerInstance: browser,
      offthreadVideoThreads: 1, logLevel: 'error', overwrite: true});
    process.stdout.write(JSON.stringify({状态: '成功', 输出文件: job.output, 帧数: plan.total_frames}) + '\n');
  }
} finally {
  await browser.close({silent: true});
}
