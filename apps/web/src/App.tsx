// 组装拾光剪辑工作台并连接工程、素材、预览和智能体面板。
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import type { PlayerRef } from "@remotion/player";
import { useEditor } from "./hooks/useEditor";
import { AssetsPanel, type Section } from "./components/AssetsPanel";
import { AgentPanel } from "./components/AgentPanel";
import { Inspector } from "./components/Inspector";
import { Timeline } from "./components/Timeline";
import { Button, Icon, Modal } from "./components/Ui";
import { layout } from "./timeline";
const PreviewPanel = lazy(() =>
  import("./components/PreviewPanel").then((module) => ({
    default: module.PreviewPanel,
  })),
);

/** 装配可运行的剪辑工作台。
 * Returns:
 *   完整的应用界面。
 */
export function App() {
  const e = useEditor();
  const [section, setSection] = useState<Section>("assets");
  const [rightTab, setRightTab] = useState("agent");
  const [dialog, setDialog] = useState<"project" | "settings" | null>(null);
  const [frame, setFrame] = useState(0);
  const player = useRef<PlayerRef>(null);
  const total = e.project ? layout(e.project.snapshot).total : 0;
  const seek = (target: number) => {
    setFrame(target);
    player.current?.seekTo(target);
  };

  useEffect(() => {
    const shortcuts = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (
        target.closest(
          'input, textarea, select, [contenteditable="true"], dialog',
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
            {e.busy || (e.project ? "所有更改已保存" : "本地视频工作台")}
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
      <main className="workspace">
        <nav className="tool-rail" aria-label="编辑工具">
          {(
            [
              ["assets", "folder", "素材"],
              ["audio", "music", "音频"],
              ["text", "text", "文字"],
              ["transitions", "layers", "转场"],
              ["color", "sliders", "调色"],
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
          <button
            className="rail-settings"
            aria-label="模型设置"
            onClick={() => setDialog("settings")}
          >
            <Icon name="settings" />
          </button>
        </nav>
        <div className="editing-area">
          <div className="media-area">
            <AssetsPanel editor={e} section={section} />
            <Suspense
              fallback={
                <section className="preview-panel panel">
                  <div className="empty-preview">正在加载播放器…</div>
                </section>
              }
            >
              <PreviewPanel
                project={e.project}
                player={player}
                frame={frame}
                onFrame={setFrame}
                onError={e.report}
              />
            </Suspense>
          </div>
          <Timeline editor={e} frame={frame} seek={seek} />
        </div>
        <aside className="right-panel panel">
          <div className="right-tabs" role="tablist" aria-label="右侧工具">
            <button
              role="tab"
              aria-selected={rightTab === "agent"}
              className={rightTab === "agent" ? "active" : ""}
              onClick={() => setRightTab("agent")}
            >
              AI 助手
            </button>
            <button
              role="tab"
              aria-selected={rightTab === "inspector"}
              className={rightTab === "inspector" ? "active" : ""}
              onClick={() => setRightTab("inspector")}
            >
              片段属性
            </button>
          </div>
          {rightTab === "agent" ? (
            <AgentPanel editor={e} onSettings={() => setDialog("settings")} />
          ) : (
            <Inspector editor={e} />
          )}
        </aside>
      </main>
      <footer className="statusbar">
        <span>
          <span className="dot" />
          本地项目 {e.project ? `· 版本 ${e.project.revision}` : ""}
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
          </div>
        </Modal>
      )}
    </div>
  );
}
