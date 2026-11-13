// 展示项目入口与真实的近期项目，让创建和继续剪辑一目了然。
import type { Editor } from "../hooks/useEditor";
import { savepointLabel } from "../projectTime";
import { Button, Icon } from "./Ui";

export function StartScreen({
  editor: e,
  onProjects,
}: {
  editor: Editor;
  onProjects: () => void;
}) {
  return (
    <main className="start-screen">
      <section className="start-intro">
        <div className="start-kicker">
          <span className="dot" /> 拾光 · AI 视频工作台
        </div>
        <h1>
          把生活的片段，
          <br />
          <span>剪成你的故事。</span>
        </h1>
        <p>
          从一段视频开始。整理镜头、调整画面，
          <br className="desktop-break" />
          也可以把剪辑想法交给 AI 助手。
        </p>
        <Button
          tone="primary"
          icon="plus"
          onClick={onProjects}
          disabled={!!e.busy}
        >
          新建或打开项目 <Icon name="right" />
        </Button>
        <div className="start-note">
          <Icon name="folder" /> 素材与项目保存在这台电脑
        </div>
      </section>
      <section
        className="start-projects"
        aria-labelledby="recent-projects-heading"
      >
        <header>
          <div>
            <span className="section-kicker">你的工作区</span>
            <h2 id="recent-projects-heading">继续创作</h2>
          </div>
          {!!e.projects.length && (
            <Button tone="ghost" onClick={onProjects}>
              全部项目 <Icon name="right" />
            </Button>
          )}
        </header>
        <div className="start-project-grid">
          <button
            className="new-project-tile"
            onClick={onProjects}
            disabled={!!e.busy}
          >
            <span className="new-project-icon">
              <Icon name="plus" />
            </span>
            <strong>开始新的剪辑</strong>
            <span>横屏或竖屏，由你决定</span>
          </button>
          {e.projects.slice(0, 5).map((project) => (
            <button
              className="recent-project-tile"
              key={project.project_id}
              disabled={!!e.busy}
              onClick={() => void e.open(project.project_id)}
            >
              <div className="project-tile-top">
                <span className="project-tile-icon">
                  <Icon name="video" />
                </span>
                <Icon name="right" />
              </div>
              <strong title={project.name}>{project.name}</strong>
              <span>{savepointLabel(project.last_savepoint)}</span>
            </button>
          ))}
        </div>
      </section>
      <div className="start-workflow" aria-label="剪辑流程">
        {[
          ["01", "导入素材", "视频、照片与声音"],
          ["02", "编排故事", "手动剪辑或与 AI 协作"],
          ["03", "导出成片", "留住值得分享的时刻"],
        ].map(([step, title, hint]) => (
          <div key={step}>
            <span>{step}</span>
            <div>
              <strong>{title}</strong>
              <p>{hint}</p>
            </div>
          </div>
        ))}
      </div>
    </main>
  );
}
