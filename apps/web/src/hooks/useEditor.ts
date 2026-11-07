// 管理工程加载、原子编辑、素材导入和智能体进度状态。
import { useEffect, useRef, useState } from "react";
import { api, ApiError, post, projectPath } from "../api";
import type {
  Asset,
  Project,
  ProjectInfo,
  ModelStatus,
  Run,
  ExportResult,
  Analysis,
  PerceptionStatus,
} from "../contracts";
import { layout } from "../timeline";
import { useConversation } from "./useConversation";

/** 维护工作台状态，并把每次手动编辑提交为可撤销版本。
 * Returns:
 *   工程数据、操作状态和各项编辑动作。
 */
export function useEditor() {
  const [project, setProject] = useState<Project | null>(null);
  const [projects, setProjects] = useState<ProjectInfo[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [model, setModel] = useState<ModelStatus | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState("");
  const [selected, setSelected] = useState("");
  const [run, setRun] = useState<Run | null>(null);
  const [instruction, setInstruction] = useState("");
  const [exported, setExported] = useState<ExportResult | null>(null);
  const [analyses, setAnalyses] = useState<Record<string, Analysis>>({});
  const [perception, setPerception] = useState<PerceptionStatus | null>(null);
  const playhead = useRef(0);
  const lock = useRef(false);
  const alive = useRef(true);
  const refreshId = useRef(0);
  const finishCommand = useRef<((result: Run) => void) | null>(null);
  const report = (err: unknown) =>
    setError(
      err instanceof ApiError
        ? err
        : new ApiError(err instanceof Error ? err.message : "操作未完成"),
    );
  const conversation = useConversation(project?.project_id, report);
  const restoredRun = [...conversation.entries].reverse().find(
    entry => entry.status === "running" && entry.run_id !== run?.run_id,
  );
  const activeRun = run?.status === "running" ? run : restoredRun;
  const liveDraft = activeRun?.live_draft?.base === project?.revision ? activeRun?.live_draft : null;
  const visibleProject = project && liveDraft ? { ...project, snapshot: liveDraft.snapshot } : project;
  const restoredRevision = conversation.entries.at(-1)?.result?.revision;
  useEffect(() => {
    if (project && !lock.current && restoredRevision !== undefined && restoredRevision > project.revision)
      void refresh(project.project_id).catch(report);
  }, [project?.project_id, project?.revision, restoredRevision, busy]);

  /** 重新读取工程和素材，保持浏览器与正式版本一致。
   * Args:
   *   pid: 工程编号。
   * Returns:
   *   当前工程。
   */
  async function refresh(pid: string) {
    const request = ++refreshId.current;
    const [next, media, analysis] = await Promise.all([
      api<Project>(projectPath(pid)),
      api<Asset[]>(`${projectPath(pid)}/assets`),
      api<Record<string, Analysis>>(`${projectPath(pid)}/analyses`),
    ]);
    if (alive.current && request === refreshId.current) {
      setProject(next);
      setAssets(media);
      setAnalyses(analysis);
      setSelected((old) =>
        next.snapshot.clips.some((c) => c.clip_id === old) ? old : "",
      );
    }
    return next;
  }

  /** 串行执行有写入副作用的界面操作。
   * Args:
   *   label: 当前操作提示。
   *   work: 实际任务。
   * Returns:
   *   是否成功完成；忙碌时或操作失败返回 false。
   */
  async function task(label: string, work: () => Promise<void>) {
    if (lock.current || restoredRun) return false;
    lock.current = true;
    setBusy(label);
    setError(null);
    try {
      await work();
      return true;
    } catch (err) {
      report(err);
      return false;
    } finally {
      lock.current = false;
      if (alive.current) setBusy("");
    }
  }

  useEffect(() => {
    alive.current = true;
    Promise.all([
      api<ProjectInfo[]>("/api/v1/projects"),
      api<ModelStatus>("/api/v1/model-status"),
      api<PerceptionStatus>("/api/v1/perception-status"),
    ])
      .then(([list, status, perceptionStatus]) => {
        if (!alive.current) return;
        setProjects(list);
        setModel(status);
        setPerception(perceptionStatus);
        const saved = localStorage.getItem("shiguang-project");
        if (saved && list.some((p) => p.project_id === saved))
          refresh(saved).catch(report);
      })
      .catch(report);
    return () => {
      alive.current = false;
    };
  }, []);

  /** 创建并打开新工程。
   * Args:
   *   name: 工程名称。
   *   vertical: 是否使用竖屏画布。
   */
  async function create(name: string, vertical: boolean) {
    await task("创建工程", async () => {
      refreshId.current++;
      const next = await post<Project>("/api/v1/projects", {
        name: name.trim(),
        composition: {
          width: vertical ? 1080 : 1920,
          height: vertical ? 1920 : 1080,
        },
      });
      next.name = name.trim();
      setProject(next);
      setAssets([]);
      setAnalyses({});
      setSelected("");
      setRun(null);
      setInstruction("");
      setExported(null);
      localStorage.setItem("shiguang-project", next.project_id);
      setProjects(await api<ProjectInfo[]>("/api/v1/projects"));
    });
  }

  /** 打开现有工程。
   * Args:
   *   pid: 工程编号。
   */
  const open = (pid: string) =>
    task("读取工程", async () => {
      await refresh(pid);
      localStorage.setItem("shiguang-project", pid);
      setRun(null);
      setInstruction("");
      setExported(null);
    });

  /** 通过隔离草稿执行一次编辑并原子提交。
   * Args:
   *   tool: 后端工具名称。
   *   args: 工具参数。
   */
  async function edit(tool: string, args: unknown) {
    if (!project) return false;
    return task("保存更改", async () => {
      const prefix = projectPath(project.project_id);
      const draft = await post<{ id: string; seq: number; base: number }>(
        `${prefix}/drafts`,
      );
      if (draft.base !== project.revision) {
        await refresh(project.project_id);
        throw new ApiError("工程已有新版本，已刷新，请重新操作。");
      }
      await post(`${prefix}/drafts/${draft.id}/tools`, {
        tool,
        arguments: args,
        op_id: crypto.randomUUID(),
        expected_seq: draft.seq,
      });
      await post(`${prefix}/drafts/${draft.id}/commit`);
      await refresh(project.project_id);
      setExported(null);
    });
  }

  /** 导入目录素材或浏览器选择的文件。
   * Args:
   *   files: 相对路径或本地文件列表。
   *   onImported: 所有文件完成导入后触发，用于关闭素材选择弹窗。
   * Returns:
   *   导入及刷新是否完成。
   */
  async function importFiles(
    files: (string | File)[],
    onImported?: () => void,
  ) {
    if (!project || !files.length) return false;
    return task("导入素材", async () => {
      const imported: Asset[] = [];
      for (const [index, file] of files.entries()) {
        setBusy(
          `导入素材 ${index + 1}/${files.length}：${typeof file === "string" ? file.split("/").at(-1) : file.name}`,
        );
        const asset =
          typeof file === "string"
            ? await post<Asset>(
                `${projectPath(project.project_id)}/assets/import`,
                { path: file },
              )
            : await api<Asset>(
                `${projectPath(project.project_id)}/assets/upload?filename=${encodeURIComponent(file.name)}`,
                {
                  method: "POST",
                  body: file,
                  headers: { "Content-Type": "application/octet-stream" },
                },
              );
        // 每个文件返回后立即显示，避免一批大素材全部结束后才更新界面。
        setAssets((old) =>
          old.some((item) => item.asset_id === asset.asset_id)
            ? old
            : [...old, asset],
        );
        imported.push(asset);
      }
      onImported?.();
      await refresh(project.project_id);
      if (perception?.vision.configured || perception?.speech.configured) {
        for (const asset of imported) {
          setBusy(`分析素材：${asset.name}`);
          try {
            await analyzeOne(asset.asset_id);
          } catch (err) {
            report(err);
          }
        }
      }
    });
  }

  /** 从素材创建视频片段或配乐。
   * Args:
   *   asset: 已导入素材。
   *   beforeClipId: 可选的插入位置，空值表示末尾。
   */
  async function addAsset(asset: Asset, beforeClipId: string | null = null) {
    if (!project) return;
    if (asset.kind === "audio") {
      const total = layout(project.snapshot).total;
      if (!total) {
        report(new ApiError("请先添加画面，再添加配乐。"));
        return;
      }
      await edit("add_audio", {
        asset_id: asset.asset_id,
        anchor: { end_frame: Math.min(total, asset.total_frames) },
      });
    } else {
      await edit("insert_clip", {
        clip: {
          clip_id:
            "clip_" + crypto.randomUUID().replaceAll("-", "").slice(0, 16),
          asset_id: asset.asset_id,
          kind: asset.kind,
          ...(asset.kind === "image"
            ? { still_duration_frames: 90 }
            : { source_out_frame: Math.min(asset.total_frames, 300) }),
        },
        before_clip_id: beforeClipId,
      });
    }
  }

  /** 切换到撤销或重做后的正式版本。
   * Args:
   *   action: 历史操作类型。
   */
  const history = (action: "undo" | "redo") =>
    project &&
    task(action === "undo" ? "撤销更改" : "重做更改", async () => {
      await post(`${projectPath(project.project_id)}/history/${action}`, {
        expected_revision: project.revision,
      });
      await refresh(project.project_id);
      setExported(null);
    });

  /** 提交自然语言指令并轮询真实执行进度。
   * Args:
   *   text: 完整编辑要求。
   *   onAccepted: 操作取得执行权后触发，用于清空已发送的输入。
   */
  async function command(text: string, onAccepted?: () => void) {
    if (!project || !text.trim() || !model?.configured) return false;
    return task("AI 正在编辑", async () => {
      const id = crypto.randomUUID();
      const prefix = projectPath(project.project_id);
      setInstruction(text);
      setRun(null);
      onAccepted?.();
      let polling = false,
        active = true;
      let lastUpdate: Run | null = null;
      const timer = window.setInterval(async () => {
        if (polling) return;
        polling = true;
        try {
          const update = await api<Run>(`${prefix}/commands/${id}`);
          if (alive.current && active) {
            lastUpdate = update;
            if (update.status === "running") setRun(update);
          }
        } catch {
          /* 首次轮询可能早于运行记录建立，最终请求会返回错误信息。 */
        } finally {
          polling = false;
        }
      }, 800);
      try {
        const submitted = post<Run>(`${prefix}/commands`, {
          request_id: id,
          instruction: text,
          expected_revision: project.revision,
          selected_clip_ids: selected ? [selected] : [],
          playhead_frame: playhead.current,
        });
        // 取消接口可先返回终态，无需等待上游模型连接退出才能继续操作。
        const cancelled = new Promise<Run>(resolve => { finishCommand.current = resolve; });
        const result = await Promise.race([submitted, cancelled]);
        active = false;
        await refresh(project.project_id);
        setRun(result);
        conversation.remember(result, text);
        setExported(null);
      } catch (err) {
        // 失败运行现在返回 4xx/5xx，同时保留运行记录供面板展示原因和进度。
        if (
          err instanceof ApiError &&
          err.payload &&
          typeof err.payload === "object" &&
          "run_id" in err.payload
        ) {
          setRun(err.payload as Run);
          conversation.remember(err.payload as Run, text);
        } else {
          // 命令连接中断后用同一请求编号找回运行，交给历史轮询继续跟踪。
          const existing = await api<Run>(`${prefix}/commands/${id}`).catch(() => lastUpdate);
          if (existing) {
            conversation.remember(existing, text);
            setRun(existing.status === "running" ? null : existing);
            if (existing.status === "running") setInstruction("");
          }
        }
        throw err;
      } finally {
        active = false;
        finishCommand.current = null;
        window.clearInterval(timer);
      }
    });
  }

  /** 取消当前模型运行，保持已提交工程不变。 */
  async function cancel() {
    if (!project || !activeRun) return;
    const finish = activeRun.run_id === run?.run_id ? finishCommand.current : null;
    try {
      const cancelled = await post<Run>(`${projectPath(project.project_id)}/runs/${activeRun.run_id}/cancel`);
      if (finish) finish(cancelled);
      else conversation.remember(cancelled, restoredRun?.instruction || "");
    } catch (err) {
      report(err);
    }
  }

  /** 渲染并发布当前工程的 MP4 成片。 */
  const exportVideo = () =>
    project &&
    task("正在导出成片", async () => {
      setExported(
        await post<ExportResult>(`${projectPath(project.project_id)}/render`, {
          expected_revision: project.revision,
        }),
      );
    });

  async function analyzeOne(aid: string, force = false, speechOnly = false) {
    if (!project) return;
    try {
      const url = `${projectPath(project.project_id)}/assets/${aid}/analysis`;
      let record = await post<Analysis>(url + "?background=true", {
        visual: !speechOnly && !!perception?.vision.configured,
        speech: !!perception?.speech.configured,
        force,
      });
      setAnalyses((old) => ({ ...old, [aid]: record }));
      while (record.status === "running") {
        await new Promise((resolve) => window.setTimeout(resolve, 1200));
        if (!alive.current) return;
        record = await api<Analysis>(url);
        setAnalyses((old) => ({ ...old, [aid]: record }));
      }
      const failed = Object.values(record.stages).find(
        (stage) => stage.status === "failed" || stage.status === "partial",
      );
      if (failed)
        throw new ApiError(
          failed.message || "部分素材分析失败，可继续重试未完成阶段。",
          failed.reason_code || "",
        );
    } finally {
      setAnalyses(
        await api<Record<string, Analysis>>(
          `${projectPath(project.project_id)}/analyses`,
        ),
      );
    }
  }

  const analyze = (aid: string, force = false, speechOnly = false) =>
    task("正在分析素材", () => analyzeOne(aid, force, speechOnly));

  const hasAnalysisRunning = Object.values(analyses).some(
    (record) => record.status === "running",
  );
  useEffect(() => {
    if (!project || (!busy && !hasAnalysisRunning)) return;
    const pid = project.project_id;
    let pending = false;
    let active = true;
    const timer = window.setInterval(async () => {
      if (pending) return;
      pending = true;
      try {
        const value = await api<Record<string, Analysis>>(
          `${projectPath(pid)}/analyses`,
        );
        if (active) setAnalyses(value);
      } catch {
        /* 最终请求显示错误。 */
      } finally {
        pending = false;
      }
    }, 1500);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [project?.project_id, busy, hasAnalysisRunning]);

  return {
    project: visibleProject,
    liveDraft,
    activeRun,
    projects,
    assets,
    model,
    error,
    busy: busy || (activeRun ? "AI 正在编辑" : ""),
    selected,
    run,
    instruction,
    exported,
    conversation,
    setSelected,
    setError,
    report,
    refresh,
    create,
    open,
    edit,
    importFiles,
    addAsset,
    history,
    command,
    cancel,
    exportVideo,
    analyses,
    perception,
    analyze,
    task,
    playhead,
  };
}

export type Editor = ReturnType<typeof useEditor>;
