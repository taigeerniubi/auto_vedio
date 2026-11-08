// 跟踪后台导出任务，并在用户选择的位置流式保存成片。
import { useEffect, useRef, useState } from "react";
import { api, ApiError, post, projectPath } from "../api";
import type { ExportJob, ExportOptions, Project } from "../contracts";

type SaveHandle = { name: string; createWritable(): Promise<WritableStream<Uint8Array>> };
type SavePicker = (options: { suggestedName: string; types: { description: string; accept: Record<string, string[]> }[] }) => Promise<SaveHandle>;
const picker = () => (window as Window & { showSaveFilePicker?: SavePicker }).showSaveFilePicker;
const message = (error: unknown) => error instanceof Error ? error.message : "导出操作未完成";

/** 按工程恢复和轮询导出状态；保存位置只在当前页面内保留。 */
export function useExport(project: Project | null) {
  const [job, setJob] = useState<ExportJob | null>(null);
  const [starting, setStarting] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [destination, setDestination] = useState("");
  const [saving, setSaving] = useState(false);
  const [savedId, setSavedId] = useState("");
  const target = useRef<SaveHandle | null>(null);
  const currentProject = useRef(project?.project_id);
  currentProject.current = project?.project_id;
  const version = useRef(0);
  const startLock = useRef(false);
  const saveLock = useRef(false);
  const attemptedSave = useRef("");
  const visibleJob = job?.project_id === project?.project_id ? job : null;
  const active = visibleJob?.status === "queued" || visibleJob?.status === "running";

  useEffect(() => {
    const generation = ++version.current;
    const pid = project?.project_id;
    setJob(null); setError(""); setDestination(""); setSavedId("");
    setStarting(false); setSaving(false);
    attemptedSave.current = "";
    target.current = null;
    if (!pid) { setLoading(false); return; }
    setLoading(true);
    void api<ExportJob | null>(`${projectPath(pid)}/exports/jobs/latest`).then(value => {
      if (version.current === generation) setJob(value);
    }).catch(err => {
      if (version.current === generation && !(err instanceof ApiError && err.status === 404))
        setError(message(err));
    }).finally(() => {
      if (version.current === generation) setLoading(false);
    });
    return () => { ++version.current; };
  }, [project?.project_id]);

  useEffect(() => {
    if (!active || !visibleJob) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const { project_id: pid, export_id: id } = visibleJob;
    const poll = async () => {
      try {
        const next = await api<ExportJob>(`${projectPath(pid)}/exports/jobs/${encodeURIComponent(id)}`);
        if (alive) { setJob(next); setError(""); }
      } catch (err) {
        if (alive) setError(`进度连接暂时中断，正在重连。${message(err)}`);
      }
      if (alive) timer = setTimeout(poll, 1000);
    };
    timer = setTimeout(poll, 300);
    return () => { alive = false; clearTimeout(timer); };
  }, [visibleJob?.export_id, active]);

  /** 原生选择器必须直接从用户点击进入，取消不会创建导出任务。 */
  async function chooseDestination(filename: string) {
    const open = picker();
    if (!open) return;
    const pid = project?.project_id;
    try {
      const handle = await open.call(window, {
        suggestedName: filename.endsWith(".mp4") ? filename : `${filename}.mp4`,
        types: [{ description: "MP4 视频", accept: { "video/mp4": [".mp4"] } }],
      });
      if (currentProject.current !== pid) return;
      target.current = handle;
      setDestination(handle.name);
      setError("");
      return handle.name;
    } catch (err) {
      if (!(err instanceof DOMException && err.name === "AbortError")) setError(message(err));
    }
  }

  /** 冻结当前正式版本启动导出，后续编辑不影响后台任务。 */
  async function start(options: ExportOptions) {
    if (!project || startLock.current || active) return false;
    startLock.current = true;
    setStarting(true); setError(""); setSavedId("");
    const pid = project.project_id;
    const generation = ++version.current;
    attemptedSave.current = "";
    try {
      const next = await post<ExportJob>(`${projectPath(pid)}/exports`, {
        expected_revision: project.revision, ...options,
      });
      if (version.current === generation && currentProject.current === pid) setJob(next);
      return true;
    } catch (err) {
      if (currentProject.current === pid) setError(message(err));
      return false;
    } finally {
      startLock.current = false;
      if (currentProject.current === pid) { setStarting(false); setLoading(false); }
    }
  }

  async function writeResult(value: ExportJob, handle: SaveHandle) {
    if (!value.result || saveLock.current) return;
    saveLock.current = true;
    setSaving(true); setError("");
    try {
      const response = await fetch(value.result.download_url);
      if (!response.ok || !response.body) throw new Error("成片下载失败，请重试保存。");
      const writable = await handle.createWritable();
      await response.body.pipeTo(writable);
      if (currentProject.current === value.project_id) setSavedId(value.export_id);
    } catch (err) {
      if (currentProject.current === value.project_id) setError(`成片已导出，保存未完成：${message(err)}`);
    } finally {
      saveLock.current = false;
      if (currentProject.current === value.project_id) setSaving(false);
    }
  }

  // 关闭设置窗口仍可完成保存；刷新后没有文件句柄时只提供重新保存入口。
  useEffect(() => {
    if (visibleJob?.status !== "completed" || !target.current || attemptedSave.current === visibleJob.export_id) return;
    attemptedSave.current = visibleJob.export_id;
    void writeResult(visibleJob, target.current);
  }, [visibleJob?.export_id, visibleJob?.status]);

  async function saveAs() {
    if (!visibleJob?.result || saving) return;
    const name = await chooseDestination(visibleJob.result.filename);
    if (name && target.current) await writeResult(visibleJob, target.current);
  }

  function clearDestination() { target.current = null; setDestination(""); }
  return { job: visibleJob, active, loading, starting, error, destination, saving,
    saved: !!visibleJob && savedId === visibleJob.export_id, canChooseLocation: !!picker(),
    start, chooseDestination, clearDestination, saveAs };
}

export type ExportController = ReturnType<typeof useExport>;
