// 组装拾光剪辑工作台并连接工程、素材、预览和智能体面板。
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import type { PlayerRef } from "@remotion/player";
import { useEditor } from "./hooks/useEditor";
import { AssetsPanel, type Section } from "./components/AssetsPanel";
import { AgentPanel } from "./components/AgentPanel";
import { Timeline } from "./components/Timeline";
import { ResizableWorkspace } from "./components/ResizableWorkspace";
import { Button, Icon, Modal } from "./components/Ui";
import { anchorRange, layout } from "./timeline";
import { progressLabel } from "./runProgress";
import { TemplatesPanel, type MotionDraft } from "./components/TemplatesPanel";
const PreviewPanel = lazy(() =>
  import("./components/PreviewPanel").then((module) => ({
    default: module.PreviewPanel,
  })),
);
const DraftMonitor = lazy(() => import("./components/DraftMonitor").then(module => ({default: module.DraftMonitor})));

/** 装配可运行的剪辑工作台。
 * Returns:
 *   完整的应用界面。
 */
export function App() {
  const e = useEditor();
  const [section, setSection] = useState<Section | "templates">("assets");
  const [motionDraft, setMotionDraft] = useState<MotionDraft>(null);
  const [dialog, setDialog] = useState<"project" | "settings" | null>(null);
  const [frame, setFrame] = useState(0);
  const player = useRef<PlayerRef>(null);
  const total = e.project ? layout(e.project.snapshot).total : 0;
  const step = e.activeRun?.events.at(-1);
  const activeStep = step && (step.事件 === "开始工具" || step.事件 === "请求模型")
    ? progressLabel(step) : "";
  const change = e.activeRun?.events.filter(event =>
    event.事件 === "工具执行" && event.状态 === "成功" && event.changed_ids?.length &&
    event.draft_seq === e.liveDraft?.seq).at(-1);
  const changedIds = change?.changed_ids || [];
  const focusKey = change && e.liveDraft ? `${e.activeRun?.run_id}-${e.liveDraft.seq}` : "";
  useEffect(() => {
    if (!e.liveDraft || !focusKey) return;
    const snapshot = e.liveDraft.snapshot;
    const effect = [...snapshot.text_items, ...snapshot.audio_items].find(item =>
      changedIds.includes("text_id" in item ? item.text_id : item.audio_id));
    const rows = layout(snapshot).rows;
    const clips = rows.filter(row => changedIds.includes(row.clip.clip_id));
    const row = change?.工具 === "set_transition" ? clips.at(-1) : clips[0];
    let target = effect ? anchorRange(effect.anchor, snapshot)[0] : row?.start;
    if (row && change?.工具 === "set_transition") {
      const transition = snapshot.transitions.find(t => t.right_clip_id === row.clip.clip_id);
      if (transition) target = row.start + Math.floor(transition.duration_frames / 2);
    }
    if (row?.clip.motion && change?.工具 === "apply_motion_preset") {
      const motion = row.clip.motion;
      target = row.start + Math.floor((motion.local_start_frame + motion.local_end_frame - 1) / 2);
    }
    if (target !== undefined) setFrame(target);
  }, [focusKey]);
  e.playhead.current = frame;
  useEffect(
    () => setMotionDraft(null),
    [e.selected, e.project?.revision, e.project?.project_id, section],
  );
  const seek = (target: number) => {
    setFrame(target);
    player.current?.seekTo(target);
  };

  useEffect(() => {
    const shortcuts = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (
        target.closest(
          'input, textarea, select, [contenteditable="true"], [role="separator"], dialog',
        ) ||
        dialog
      )
        return;
      if (event.code === "Space") {
        event.preventDefault();
        player.current?.toggle();
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
        event.preventDefault();
        if (!e.busy) void e.history(event.shiftKey ? "redo" : "undo");
      }
      if (event.key === "Delete" && e.selected && !e.busy) {
        event.preventDefault();
        void e.edit("delete_clip", { clip_id: e.selected });
      }
    };
    window.addEventListener("keydown", shortcuts);
    return () => window.removeEventListener("keydown", shortcuts);
  }, [e.project, e.busy, e.selected, dialog]);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">
            <Icon name="spark" />
          </div>
          <strong>拾光</strong>
          <span>SHIGUANG</span>
        </div>
        <div className="project-info">
          <Button
            tone="ghost"
            icon="back"
            aria-label="打开工程"
            disabled={!!e.busy}
            onClick={() => setDialog("project")}
          />
          <button
            className="project-name"
            disabled={!!e.busy}
            onClick={() => setDialog("project")}
          >
            {e.project?.name || "开始一段新故事"}
            <Icon name="down" />
          </button>
          <span className="save-status">
            <i className={`dot ${e.busy ? "amber" : ""}`} />
            {activeStep ||
              e.busy ||
              (e.project ? "所有更改已保存" : "本地视频工作台")}
          </span>
        </div>
        <div className="header-actions">
          <Button
            tone="ghost"
            icon="undo"
            disabled={!!e.busy || !e.project || e.project.revision === 0}
            onClick={() => void e.history("undo")}
          >
            撤销
          </Button>
          <Button
            tone="ghost"
            icon="redo"
            disabled={!!e.busy || !e.project}
            onClick={() => void e.history("redo")}
          >
            重做
          </Button>
          <Button
            tone="primary"
            disabled={!!e.busy || !total}
            onClick={() => void e.exportVideo()}
          >
            {e.busy === "正在导出成片" ? "导出中…" : "导出成片"}
          </Button>
        </div>
      </header>
      {e.error && (
        <div className="error-banner" role="alert">
          <div>
            <strong>{e.error.message}</strong>
            {e.error.status > 0 && (
              <small>
                HTTP {e.error.status}
                {e.error.code ? ` · ${e.error.code}` : ""}
              </small>
            )}
            {e.error.requestId && (
              <small>
                请求编号：{e.error.requestId} · 可在 logs/app.log 中定位
              </small>
            )}
          </div>
          <Button
            icon="close"
            tone="ghost"
            aria-label="关闭错误提示"
            onClick={() => e.setError(null)}
          />
        </div>
      )}
      {e.exported && (
        <div className="export-banner" role="status">
          <span>
            ✓　版本 {e.exported.revision} 已导出 ·{" "}
            {(e.exported.frames / 30).toFixed(1)} 秒
          </span>
          <a className="button primary" href={e.exported.download_url} download>
            下载 MP4 <Icon name="download" />
          </a>
        </div>
      )}
      <ResizableWorkspace>
        <aside id="workspace-library" className="library-panel panel" aria-label="剪辑工具与素材">
          <nav className="library-tools" aria-label="编辑工具">
            {(
              [
                ["assets", "folder", "素材"],
                ["audio", "music", "音频"],
                ["text", "text", "文字"],
                ["transitions", "layers", "转场"],
                ["color", "sliders", "调色"],
                ["templates", "spark", "模板"],
              ] as const
            ).map(([id, icon, label]) => (
              <button
                key={id}
                className={section === id ? "active" : ""}
                onClick={() => setSection(id)}
                aria-current={section === id ? "page" : undefined}
              >
                <Icon name={icon} />
                <span>{label}</span>
              </button>
            ))}
          </nav>
          {section === "templates" ? (
            <TemplatesPanel
              editor={e}
              draft={motionDraft}
              onDraft={setMotionDraft}
              onPreview={(target) => {
                seek(target);
                player.current?.play();
              }}
            />
          ) : (
            <AssetsPanel editor={e} section={section} />
          )}
        </aside>
        <Suspense
          fallback={
            <section id="workspace-monitor" className="preview-panel panel">
              <div className="empty-preview">正在加载播放器…</div>
            </section>
          }
        >
          {e.liveDraft && e.project ? (
            <DraftMonitor
              project={e.project}
              seq={e.liveDraft.seq}
              assets={e.assets}
              frame={frame}
              seek={setFrame}
            />
          ) : (
            <PreviewPanel
              project={e.project}
              player={player}
              frame={frame}
              onFrame={setFrame}
              onError={e.report}
              motionDraft={motionDraft}
            />
          )}
        </Suspense>
        <Timeline
          editor={e}
          frame={Math.max(0, Math.min(frame, total - 1))}
          seek={seek}
          changedIds={changedIds}
        />
        <AgentPanel
          key={e.project?.project_id}
          editor={e}
          onSettings={() => setDialog("settings")}
          onRecordStart={() => player.current?.pause()}
        />
      </ResizableWorkspace>
      <footer className="statusbar">
        <span>
          <span className="dot" />
          本地项目 {e.project ? `· 版本 ${e.project.revision}` : ""}
          {e.liveDraft ? " · 草稿未提交" : ""}
        </span>
        <span>{e.busy || "准备就绪"}</span>
        <span>
          {e.project
            ? `${e.project.snapshot.composition.width} × ${e.project.snapshot.composition.height}`
            : "1920 × 1080"}
          　·　30 fps
        </span>
      </footer>
      {!e.project && !dialog && (
        <div className="welcome-card">
          <span className="eyebrow">A LITTLE STORY, YOUR WAY</span>
          <h1>
            拾起日常，
            <br />
            剪成自己的故事。
          </h1>
          <p>导入素材，整理镜头，让想法慢慢成为画面。</p>
          <Button
            tone="primary"
            icon="plus"
            onClick={() => setDialog("project")}
          >
            新建或打开项目
          </Button>
        </div>
      )}
      {dialog === "project" && (
        <Modal title="你的剪辑项目" onClose={() => setDialog(null)}>
          <form
            onSubmit={async (event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              const name = String(form.get("name")).trim();
              if (name) {
                await e.create(name, form.get("ratio") === "vertical");
                setDialog(null);
              }
            }}
          >
            <label>
              项目名称
              <input
                name="name"
                aria-label="项目名称"
                placeholder="给这段故事起个名字"
                required
                maxLength={100}
                defaultValue="我的日常片段"
              />
            </label>
            <label>
              画布比例
              <select name="ratio">
                <option value="horizontal">横屏 16:9 · 1920 × 1080</option>
                <option value="vertical">竖屏 9:16 · 1080 × 1920</option>
              </select>
            </label>
            <Button type="submit" tone="primary" disabled={!!e.busy}>
              创建项目
            </Button>
          </form>
          {!!e.projects.length && (
            <>
              <h3 className="recent-title">已有项目</h3>
              <div className="project-list">
                {e.projects.map((p) => (
                  <button
                    key={p.project_id}
                    disabled={!!e.busy}
                    onClick={async () => {
                      await e.open(p.project_id);
                      setDialog(null);
                    }}
                  >
                    <Icon name="folder" />
                    <span>
                      {p.name}
                      <small>版本 {p.revision}</small>
                    </span>
                    <Icon name="right" />
                  </button>
                ))}
              </div>
            </>
          )}
        </Modal>
      )}
      {dialog === "settings" && (
        <Modal title="模型连接" onClose={() => setDialog(null)}>
          <div className="settings-content">
            <p>支持 OpenAI 与 DeepSeek，当前使用后端配置的模型。</p>
            <div className="settings-status">
              <span className={`dot ${e.model?.configured ? "" : "amber"}`} />
              {e.model?.configured
                ? `已配置 ${e.model.provider} · ${e.model.model}`
                : "尚未配置模型"}
            </div>
            <p>在启动后端的终端配置以下环境变量，然后重启后端并刷新页面：</p>
            <pre>
              SHIGUANG_MODEL_PROVIDER=openai 或 deepseek{"\n"}
              SHIGUANG_MODEL_NAME=模型名称{"\n"}SHIGUANG_MODEL_API_KEY=你的 API
              Key
            </pre>
            <small>
              密钥只在后端保存，无需输入到网页。未连接模型时，手动剪辑、预览与导出仍可使用。
            </small>
            <h3>素材理解与语音输入</h3>
            {(["vision", "speech", "audio"] as const).map((kind) => (
              <p key={kind}>
                {
                  { vision: "视觉模型", speech: "语音模型", audio: "音频问答" }[
                    kind
                  ]
                }
                ：
                {e.perception?.[kind]?.configured
                  ? e.perception[kind].model
                  : "未配置"}
              </p>
            ))}
            <p>
              在根目录 .env
              独立填写配置并重启。画面和对白可在导入时分析，音频问答在提问时调用。
            </p>
            <pre>
              SHIGUANG_VISION_BASE_URL=兼容接口地址{"\n"}
              SHIGUANG_VISION_MODEL=视觉模型{"\n"}SHIGUANG_VISION_API_KEY=密钥
              {"\n\n"}SHIGUANG_SPEECH_BASE_URL=兼容接口地址{"\n"}
              SHIGUANG_SPEECH_MODEL=语音模型{"\n"}SHIGUANG_SPEECH_API_KEY=密钥
              {"\n"}SHIGUANG_SPEECH_RESPONSE_FORMAT=verbose_json
              {"\n\n"}SHIGUANG_AUDIO_API_KEY=百炼密钥
            </pre>
            <small>
              自动字幕需要语音模型返回句级时间戳。仅返回文本的接口仍可用于录音输入。
            </small>
          </div>
        </Modal>
      )}
    </div>
  );
}
