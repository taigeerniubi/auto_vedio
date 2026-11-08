// 展示按时间命名的用户暂存，提供可撤销恢复和独立移除记录。
import { useEffect, useState } from "react";
import { api, projectPath } from "../api";
import type { Savepoint } from "../contracts";
import type { Editor } from "../hooks/useEditor";
import { formatSavepointTime } from "../projectTime";
import { Button, Modal } from "./Ui";

export function SavepointsDialog({ editor: e, previewing, onClose, onRestored }: {
  editor: Editor; previewing: boolean; onClose: () => void; onRestored: () => void;
}) {
  const pid = e.project?.project_id;
  const [items, setItems] = useState<Savepoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<{ item: Savepoint; action: "restore" | "remove" } | null>(null);
  useEffect(() => {
    let active = true;
    setLoading(true);
    if (pid) api<Savepoint[]>(`${projectPath(pid)}/savepoints`).then(value => {
      if (active) setItems(value);
    }).catch(error => { if (active) e.report(error); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [pid]);
  const busy = !!e.busy;
  return <Modal title={selected ? selected.action === "restore" ? "恢复暂存" : "删除暂存" : "暂存历史"}
    onClose={() => { if (!busy) onClose(); }}>
    {selected ? <div className="savepoint-confirm">
      <p>{selected.action === "restore" ? "恢复到" : "删除"} {formatSavepointTime(selected.item.created_at)} 的暂存？</p>
      <p className="muted">{selected.action === "restore"
        ? "时间线和效果将恢复到这一刻。恢复后可点击“撤销”找回之前的编辑，素材和会话会保留。"
        : "这条暂存记录将被移除，当前剪辑保持不变。删除记录后无法再从此处恢复。"}</p>
      <footer>
        <Button autoFocus disabled={busy} onClick={() => { setSelected(null); e.setError(null); }}>取消</Button>
        <Button disabled={busy || (selected.action === "restore" && previewing)}
          tone={selected.action === "restore" ? "primary" : "default"} className={selected.action === "remove" ? "danger" : ""}
          onClick={async () => {
            if (selected.action === "restore") {
              if (await e.restoreSavepoint(selected.item)) { onRestored(); onClose(); }
            } else if (await e.removeSavepoint(selected.item.savepoint_id)) {
              setItems(previous => previous.filter(item => item.savepoint_id !== selected.item.savepoint_id));
              setSelected(null);
            }
          }}>{busy ? e.busy : selected.action === "restore" ? "确认恢复" : "确认删除暂存"}</Button>
      </footer>
    </div> : <>
      <p className="muted">暂存保留已应用到工程的剪辑阶段。日常编辑仍会自动保存，你可以随时恢复到这些暂存点。</p>
      {previewing && <p className="savepoint-hint">请先应用或退出正在预览的调整，再暂存或恢复。</p>}
      {loading ? <p className="savepoint-empty" role="status">正在读取暂存…</p> : items.length ?
        <ul className="savepoint-list" aria-label="已保存的暂存">
          {items.map((item, index) => <li key={item.savepoint_id} data-savepoint-id={item.savepoint_id}>
            <div><time dateTime={item.created_at}>{formatSavepointTime(item.created_at)}</time><small>{index === 0 ? "最近暂存" : "历史暂存"}</small></div>
            <Button disabled={busy || previewing} onClick={() => { e.setError(null); setSelected({item, action: "restore"}); }}>恢复</Button>
            <Button tone="ghost" icon="trash" disabled={busy} aria-label={`删除暂存 ${formatSavepointTime(item.created_at)}`}
              onClick={() => { e.setError(null); setSelected({item, action: "remove"}); }} />
          </li>)}
        </ul> : <p className="savepoint-empty">还没有暂存。点击顶部“暂存”或按 Ctrl+S，保存一个可以返回的阶段。</p>}
      <footer>
        <Button disabled={busy} onClick={onClose}>关闭</Button>
        <Button tone="primary" icon="save" disabled={busy || loading || previewing} onClick={async () => {
          if (await e.savepoint()) onClose();
        }}>暂存当前剪辑</Button>
      </footer>
    </>}
    {e.error && <p className="project-dialog-error" role="alert">{e.error.message}</p>}
  </Modal>;
}
