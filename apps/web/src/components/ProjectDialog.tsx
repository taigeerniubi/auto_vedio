// 管理项目创建、打开及带明确确认的删除操作。
import { useEffect, useState } from "react";
import type { ProjectInfo } from "../contracts";
import type { Editor } from "../hooks/useEditor";
import { Button, Icon, Modal } from "./Ui";
import { savepointLabel } from "../projectTime";

/** 在同一弹窗内完成项目选择和删除确认，避免嵌套模态窗口。 */
export function ProjectDialog({ editor: e, onClose, onDeletedCurrent }: {
  editor: Editor; onClose: () => void; onDeletedCurrent: () => void;
}) {
  const [removing, setRemoving] = useState<ProjectInfo | null>(null);
  useEffect(() => { void e.refreshProjects(); }, []);
  return <Modal title={removing ? "删除项目" : "你的剪辑项目"} onClose={() => { if (!e.busy) onClose(); }}>
    {removing ? <div className="project-delete-confirm">
      <p>确定删除项目「<strong>{removing.name}</strong>」？</p>
      <p className="muted">该项目的时间线、会话、素材和暂存记录会被删除，无法撤销。电脑上的素材文件、已保存的成片和其他项目会保留。</p>
      <footer>
        <Button autoFocus disabled={!!e.busy} onClick={() => {
          setRemoving(null);
          void e.refreshProjects();
        }}>取消</Button>
        <Button className="danger" icon="trash" disabled={!!e.busy} onClick={async () => {
          const wasCurrent = e.project?.project_id === removing.project_id;
          if (await e.deleteProject(removing.project_id, removing.revision)) {
            setRemoving(null);
            if (wasCurrent) { onDeletedCurrent(); onClose(); }
          }
        }}>{e.busy === "正在删除项目" ? "正在删除…" : "确认删除项目"}</Button>
      </footer>
    </div> : <>
      <form onSubmit={async event => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        const name = String(form.get("name")).trim();
        if (name && await e.create(name, form.get("ratio") === "vertical")) onClose();
      }}>
        <label>项目名称<input name="name" aria-label="项目名称" placeholder="给这段故事起个名字"
          required maxLength={100} defaultValue="我的日常片段" /></label>
        <label>画布比例<select name="ratio">
          <option value="horizontal">横屏 16:9 · 1920 × 1080</option>
          <option value="vertical">竖屏 9:16 · 1080 × 1920</option>
        </select></label>
        <Button type="submit" tone="primary" disabled={!!e.busy}>创建项目</Button>
      </form>
      {!!e.projects.length && <>
        <h3 className="recent-title">已有项目</h3>
        <div className="project-list">
          {e.projects.map(p => <div className="project-row" data-project-id={p.project_id} key={p.project_id}>
            <button className="project-open" disabled={!!e.busy}
              aria-current={e.project?.project_id === p.project_id ? "page" : undefined}
              onClick={async () => { if (await e.open(p.project_id)) onClose(); }}>
              <Icon name="folder" />
              <span>{p.name}<small>{e.project?.project_id === p.project_id ? "当前项目 · " : ""}{savepointLabel(p.last_savepoint)}</small></span>
              <Icon name="right" />
            </button>
            <Button tone="ghost" className="project-delete" icon="trash" disabled={!!e.busy}
              title={`删除项目 ${p.name}`} aria-label={`删除项目 ${p.name}`}
              onClick={() => { e.setError(null); setRemoving(p); }} />
          </div>)}
        </div>
      </>}
    </>}
    {e.error && <p className="project-dialog-error" role="alert">{e.error.message}</p>}
  </Modal>;
}
