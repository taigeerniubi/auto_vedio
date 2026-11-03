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
} from "../contracts";
import { layout } from "../timeline";

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
  const lock = useRef(false);
  const alive = useRef(true);
  const report = (err: unknown) =>
    setError(
      err instanceof ApiError
        ? err
        : new ApiError(err instanceof Error ? err.message : "操作未完成"),
    );

  /** 重新读取工程和素材，保持浏览器与正式版本一致。
   * Args:
   *   pid: 工程编号。
   * Returns:
   *   当前工程。
   */
  async function refresh(pid: string) {
    const [next, media] = await Promise.all([
      api<Project>(projectPath(pid)),
      api<Asset[]>(`${projectPath(pid)}/assets`),
    ]);
    if (alive.current) {
      setProject(next);
      setAssets(media);
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
   */
  async function task(label: string, work: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true;
    setBusy(label);
    setError(null);
    try {
      await work();
    } catch (err) {
      report(err);
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
    ])
      .then(([list, status]) => {
        if (!alive.current) return;
        setProjects(list);
        setModel(status);
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
      setSelected("");
      setRun(null);
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
      setExported(null);
    });

  /** 通过隔离草稿执行一次编辑并原子提交。
   * Args:
   *   tool: 后端工具名称。
   *   args: 工具参数。
   */
  async function edit(tool: string, args: unknown) {
    if (!project) return;
    await task("保存更改", async () => {
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
   */
  async function importFiles(files: (string | File)[]) {
    if (!project) return;
    await task("导入素材", async () => {
      try {
        for (const file of files) {
          if (typeof file === "string")
            await post(`${projectPath(project.project_id)}/assets/import`, {
              path: file,
            });
          else
            await api(
              `${projectPath(project.project_id)}/assets/upload?filename=${encodeURIComponent(file.name)}`,
              {
                method: "POST",
                body: file,
                headers: { "Content-Type": "application/octet-stream" },
              },
            );
        }
      } finally {
        await refresh(project.project_id);
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
   */
  async function command(text: string) {
    if (!project || !text.trim()) return;
    await task("AI 正在编辑", async () => {
      const id = crypto.randomUUID();
      const prefix = projectPath(project.project_id);
      setInstruction(text);
      setRun(null);
      let polling = false,
        active = true;
      const timer = window.setInterval(async () => {
        if (polling) return;
        polling = true;
        try {
          const update = await api<Run>(`${prefix}/commands/${id}`);
          if (alive.current && active)
            setRun((old) => (old?.status !== "running" && old ? old : update));
        } catch {
          /* 首次轮询可能早于运行记录建立，最终请求会返回错误信息。 */
        } finally {
          polling = false;
        }
      }, 800);
      try {
        const result = await post<Run>(`${prefix}/commands`, {
          request_id: id,
          instruction: text,
          expected_revision: project.revision,
          selected_clip_ids: selected ? [selected] : [],
        });
        setRun(result);
        await refresh(project.project_id);
        setExported(null);
      } finally {
        active = false;
        window.clearInterval(timer);
      }
    });
  }

  /** 取消当前模型运行，保持已提交工程不变。 */
  async function cancel() {
    if (!project || !run || run.status !== "running") return;
    try {
      setRun(
        await post<Run>(
          `${projectPath(project.project_id)}/runs/${run.run_id}/cancel`,
        ),
      );
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

  return {
    project,
    projects,
    assets,
    model,
    error,
    busy,
    selected,
    run,
    instruction,
    exported,
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
  };
}

export type Editor = ReturnType<typeof useEditor>;
