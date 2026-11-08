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
import {ColorPanel,type ColorDraft} from './components/ColorPanel';
import { ExportDialog } from './components/ExportDialog';
import { ProjectDialog } from './components/ProjectDialog';
import { SavepointsDialog } from './components/SavepointsDialog';
const CoverPanel=lazy(()=>import('./components/CoverPanel').then(module=>({default:module.CoverPanel})));
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
  const [section, setSection] = useState<Section | "templates" | "color">("assets");
  const [motionDraft, setMotionDraft] = useState<MotionDraft>(null);
  const [colorDraft,setColorDraft]=useState<ColorDraft>(null);
  const [coverOpen,setCoverOpen]=useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [dialog, setDialog] = useState<"project" | "settings" | "savepoints" | null>(null);
  const previewing = !!(colorDraft || motionDraft);
  const [frame, setFrame] = useState(0);
  const player = useRef<PlayerRef>(null);
  const audioPlayer = useRef<HTMLAudioElement>(null);
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
    const nextFrame = Math.max(0, Math.min(Math.round(target), total - 1));
    setFrame(nextFrame);
    player.current?.seekTo(nextFrame);
  };

  useEffect(() => {
    const shortcuts = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        if (!event.repeat && !dialog && !target.closest("dialog") && !e.busy && !previewing && e.project)
          void e.savepoint();
        return;
      }
      if (
        target.closest(
          'input, textarea, select, audio, [data-audio-toggle], [contenteditable="true"], [role="separator"], [role="slider"], dialog',
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
        void e.deleteSelected();
      }
    };
    window.addEventListener("keydown", shortcuts);
    return () => window.removeEventListener("keydown", shortcuts);
  }, [e.project, e.busy, e.selected, dialog, previewing]);

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
              (previewing ? "正在预览调整" : e.project ? "所有更改已保存" : "本地视频工作台")}
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
          <Button icon="save" className="savepoint-save" aria-label="暂存" title={previewing ? "请先应用或退出预览" : "暂存当前已应用的剪辑（Ctrl+S）"}
            disabled={!e.project || !!e.busy || previewing} onClick={() => void e.savepoint()}>
            <span className="savepoint-button-label">暂存</span>
          </Button>
          <Button icon="clock" className="savepoint-history" aria-label="暂存历史" title="查看和恢复暂存"
            disabled={!e.project || !!e.busy} onClick={() => { player.current?.pause(); e.setError(null); setDialog("savepoints"); }} />
          <Button
            tone="primary"
            disabled={!e.exportState.active && (!!e.busy || !total)}
            onClick={() => { player.current?.pause(); setExportOpen(true); }}
          >
            {e.exportState.active ? `导出中 ${Math.round(e.exportState.job?.progress || 0)}%` : "导出成片"}
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
      {e.savepointNotice && <div className="savepoint-banner" role="status">
        <span>{e.savepointNotice}</span>
        <Button icon="close" tone="ghost" aria-label="关闭暂存提示" onClick={e.clearSavepointNotice} />
      </div>}
      {e.exportState.job?.status === "completed" && e.exportState.job.result && (
        <div className="export-banner" role="status">
          <span>
            ✓　版本 {e.exportState.job.revision} 已导出 ·{" "}
            {(e.exportState.job.result.frames / 30).toFixed(1)} 秒
            {e.exportState.saving ? " · 正在保存…" : e.exportState.saved ? " · 已保存" : " · 可保存成片"}
          </span>
          <Button tone="primary" onClick={() => setExportOpen(true)}>查看成片 <Icon name="download" /></Button>
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
          ) : section === 'color' ? (
            <ColorPanel editor={e} onDraft={setColorDraft}/>
          ) : (
            <AssetsPanel editor={e} section={section} audioPlayer={audioPlayer}
              onAudioPlay={() => player.current?.pause()}
              onTransitionPreview={target => { player.current?.pause(); seek(target); }} />
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
              onPlay={() => audioPlayer.current?.pause()}
              motionDraft={motionDraft}
              colorDraft={colorDraft}
              editor={e}
            />
          )}
        </Suspense>
        <Timeline
          editor={e}
          frame={Math.max(0, Math.min(frame, total - 1))}
          seek={seek}
          onScrubStart={() => { player.current?.pause(); audioPlayer.current?.pause(); }}
          changedIds={changedIds}
          onCover={() => { player.current?.pause(); setCoverOpen(true); }}
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
        <span role="status">{e.busy || "准备就绪"}</span>
        <span>
          {e.project
            ? `${e.project.snapshot.composition.width} × ${e.project.snapshot.composition.height}`
            : "1920 × 1080"}
          　·　30 fps
        </span>
      </footer>
      {coverOpen&&e.project&&<Suspense fallback={null}><CoverPanel editor={e} frame={frame} onClose={()=>setCoverOpen(false)}/></Suspense>}
      {exportOpen && e.project && <ExportDialog key={e.project.project_id} project={e.project} controller={e.exportState} onClose={() => setExportOpen(false)} />}
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
        <ProjectDialog editor={e} onClose={() => setDialog(null)} onDeletedCurrent={() => {
          player.current?.pause();
          setFrame(0);
          setColorDraft(null);
          setMotionDraft(null);
          setCoverOpen(false);
          setExportOpen(false);
        }} />
      )}
      {dialog === "savepoints" && e.project && <SavepointsDialog editor={e} previewing={previewing}
        onClose={() => setDialog(null)} onRestored={() => {
          player.current?.pause();
          player.current?.seekTo(0);
          setFrame(0);
          setColorDraft(null);
          setMotionDraft(null);
        }} />}
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
