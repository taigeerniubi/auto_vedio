// 提供导出参数、保存位置与后台渲染进度。
import { useEffect, useState } from "react";
import type { ExportOptions, Project } from "../contracts";
import type { ExportController } from "../hooks/useExport";
import { Button, Modal } from "./Ui";
import { layout } from "../timeline";

const stages: Record<string, string> = {
  queued: "等待导出", preparing: "准备素材与音频", bundling: "准备渲染器",
  rendering: "渲染画面", muxing: "合成音画", verifying: "检查成片", completed: "导出完成", failed: "导出未完成",
};
const qualityLabels = { standard: "标准", high: "高画质", compact: "较小文件" };
function defaultName(name: string) {
  const stem = name.replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").replace(/[. ]+$/, "").slice(0, 90) || "拾光成片";
  return `${/^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i.test(stem) ? `成片-${stem}` : stem}.mp4`;
}
function elapsed(start: string, now: number) {
  const seconds = Math.max(0, Math.floor((now - Date.parse(start)) / 1000)) || 0;
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

/** 导出设置独立于工程画布，导出期间可以关闭窗口继续编辑。 */
export function ExportDialog({ project, controller: c, onClose }: {
  project: Project; controller: ExportController; onClose: () => void;
}) {
  const { job } = c;
  const [options, setOptions] = useState<ExportOptions>(() => ({
    resolution: job?.options.resolution || "source", quality: job?.options.quality || "standard",
    filename: c.destination || job?.options.filename || defaultName(project.name),
  }));
  const [configuring, setConfiguring] = useState(job?.status !== "completed");
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!c.active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [c.active]);
  useEffect(() => {
    if (job?.status === "completed") setConfiguring(false);
  }, [job?.export_id, job?.status]);
  const { width, height } = project.snapshot.composition;
  const bounds = { "720p": [720, 1280], "1080p": [1080, 1920], "2160p": [2160, 3840] } as const;
  const scale = options.resolution === "source" ? 1 : Math.min(
    bounds[options.resolution][0] / Math.min(width, height),
    bounds[options.resolution][1] / Math.max(width, height),
  );
  // 与渲染器一致：保留比例，并将缩放后的编码尺寸调整为偶数。
  const scaledEven = (value: number) => {
    while (Math.round(value * scale) % 2) value -= 1;
    return Math.round(value * scale);
  };
  const dimensions = `${scaledEven(width)} × ${scaledEven(height)}`;
  const filenameValid = !!options.filename.trim() && !/[<>:"/\\|?*\x00-\x1f]/.test(options.filename) && !/[. ]$/.test(options.filename);
  const duration = (layout(project.snapshot).total / 30).toFixed(1);
  const progress = Math.min(100, Math.max(0, job?.progress || 0));
  const result = job?.status === "completed" ? job.result : null;
  return <Modal title="导出成片" onClose={onClose}>
    <div className="export-dialog">
      {c.active && job ? <section className="export-progress" aria-label="导出进度">
        <div className="export-progress-heading"><strong>{stages[job.stage] || "正在导出"}</strong><b>{Math.round(progress)}%</b></div>
        <progress aria-label="导出完成比例" max={100} value={progress} />
        <div className="export-progress-meta"><span>{job.rendered_frames} / {job.total_frames} 帧</span><span>已用 {elapsed(job.created_at, now)}</span></div>
        <p>{job.options.filename} · {job.options.resolution === "source" ? "工程分辨率" : job.options.resolution} · {qualityLabels[job.options.quality]}</p>
        <p className="muted">正在导出版本 {job.revision}。可以关闭此窗口继续编辑，后续修改不会影响本次成片。</p>
        <Button onClick={onClose}>后台继续导出</Button>
      </section> : result && !configuring ? <section className="export-complete" aria-label="导出结果">
        <span className="export-complete-mark">✓</span>
        <h3>{c.saving ? "正在保存到所选位置…" : c.saved ? "已保存到所选位置" : "成片已就绪"}</h3>
        <p className="export-filename">{result.filename}</p>
        <p className="muted">{result.width} × {result.height} · {result.fps} fps · {(result.frames / 30).toFixed(1)} 秒 · {(result.bytes / 1024 / 1024).toFixed(1)} MB</p>
        {c.saved && <p className="muted">保存文件：{c.destination}</p>}
        {!c.saved && !c.saving && <p className="muted">选择保存位置，或下载到浏览器设置的目录。</p>}
        {!!result.warnings.length && <p className="muted">{result.warnings.join("；")}</p>}
        <div className="export-actions">
          {c.canChooseLocation && <Button tone="primary" disabled={c.saving} onClick={() => void c.saveAs()}>{c.saved ? "另存为…" : "保存到…"}</Button>}
          <a className={`button ${c.canChooseLocation ? "default" : "primary"}`} href={result.download_url} download={result.filename}>下载 MP4</a>
        </div>
        <Button tone="ghost" disabled={c.saving} onClick={() => setConfiguring(true)}>调整设置重新导出</Button>
      </section> : <form onSubmit={event => {
        event.preventDefault();
        if (filenameValid) void c.start({ ...options, filename: options.filename.trim().replace(/\.mp4$/i, "") + ".mp4" });
      }}>
        <p className="export-project-summary">{project.name} <span>{duration} 秒 · 版本 {project.revision}</span></p>
        {job?.status === "failed" && <p className="export-error" role="alert">{job.error?.message || "导出未完成，请重试。"}</p>}
        <label>文件名<input aria-label="导出文件名" value={options.filename} maxLength={120} disabled={c.starting || !!c.destination}
          onChange={event => setOptions({ ...options, filename: event.target.value })} required /></label>
        {!filenameValid && <small className="export-error">文件名不能为空，也不能包含斜杠、冒号等特殊字符。</small>}
        <div className="export-settings-grid">
          <label>分辨率<select aria-label="导出分辨率" value={options.resolution} disabled={c.starting}
            onChange={event => setOptions({ ...options, resolution: event.target.value as ExportOptions["resolution"] })}>
            <option value="source">与工程一致 · {width} × {height}</option>
            <option value="720p">720p · 高清</option><option value="1080p">1080p · 全高清</option><option value="2160p">4K · 2160p</option>
          </select></label>
          <label>画质<select aria-label="导出画质" value={options.quality} disabled={c.starting}
            onChange={event => setOptions({ ...options, quality: event.target.value as ExportOptions["quality"] })}>
            <option value="standard">标准 · 画质与文件大小均衡</option><option value="high">高画质 · 文件较大</option><option value="compact">较小文件 · 便于分享</option>
          </select></label>
        </div>
        <div className="export-format"><span>{dimensions}</span><span>30 fps</span><span>MP4 · H.264 / AAC</span></div>
        <p className="muted">保持原画幅比例。分辨率越高，渲染通常越久；放大导出不会增加原素材细节。</p>
        <div className="export-destination">
          <div><strong>保存位置</strong><p>{c.destination ? `已选择：${c.destination}` : "浏览器下载目录"}</p>
            <small>{c.destination ? "导出完成后会自动保存到刚才选择的文件位置。" : c.canChooseLocation ? "可先选择文件夹和文件名；未选择时，完成后手动下载。" : "当前浏览器不支持页面选择文件夹，下载位置由浏览器设置决定。"}</small></div>
          {c.canChooseLocation && <Button disabled={c.starting || !filenameValid} icon="folder" onClick={async () => {
            const name = await c.chooseDestination(options.filename);
            if (name) setOptions(old => ({ ...old, filename: name }));
          }}>选择保存位置</Button>}
          {c.destination && <Button tone="ghost" disabled={c.starting} onClick={c.clearDestination}>改用下载</Button>}
        </div>
        <div className="export-actions"><Button onClick={onClose}>取消</Button><Button type="submit" tone="primary"
          disabled={c.starting || c.loading || !filenameValid || !project.snapshot.clips.length}>
          {c.starting ? "正在开始…" : c.loading ? "读取导出状态…" : job?.status === "failed" ? "重新导出" : "开始导出"}
        </Button></div>
      </form>}
      {c.error && <p className="export-error" role="alert">{c.error}</p>}
    </div>
  </Modal>;
}
