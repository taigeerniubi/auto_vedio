// 在助手顶部展示最近三条会话，并通过完整列表切换更早的聊天。
import { useEffect, useState } from "react";
import type { ChatSession } from "../contracts";
import type { Editor } from "../hooks/useEditor";
import { Button, Modal } from "./Ui";

/** 将最近活动时间显示为紧凑的相对时间。 */
function relativeTime(value: string, now: number) {
  if (!Number.isFinite(Date.parse(value))) return "";
  const elapsed = Math.max(0, now - Date.parse(value));
  if (elapsed < 60_000) return "刚刚";
  if (elapsed < 3_600_000) return `${Math.floor(elapsed / 60_000)} 分钟前`;
  if (elapsed < 86_400_000) return `${Math.floor(elapsed / 3_600_000)} 小时前`;
  if (elapsed < 604_800_000) return `${Math.floor(elapsed / 86_400_000)} 天前`;
  return new Date(value).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}

/** 展示当前工程的会话，首条消息作为标题，长标题保持单行省略。 */
export function ChatSessions({ editor: e }: { editor: Editor }) {
  const [expanded, setExpanded] = useState(false);
  const [now, setNow] = useState(Date.now);
  useEffect(() => setExpanded(false), [e.project?.project_id]);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);
  const { sessions, sessionId, loading } = e.chatSessions;
  const disabled = !!e.busy || loading || !e.conversation.ready;
  const choose = async (id: string) => {
    if (await e.switchSession(id)) setExpanded(false);
  };
  const row = (session: ChatSession) => (
    <button
      type="button"
      className="chat-session-row"
      key={session.session_id}
      data-session-id={session.session_id}
      aria-label={`切换会话：${session.title}`}
      aria-current={session.session_id === sessionId ? "true" : undefined}
      disabled={disabled}
      onClick={() => void choose(session.session_id)}
      title={session.title}
    >
      <span className="chat-session-title">{session.title}</span>
      <time dateTime={session.updated_at || undefined} title={Number.isFinite(Date.parse(session.updated_at)) ? new Date(session.updated_at).toLocaleString("zh-CN") : undefined}>
        {session.running ? "进行中" : relativeTime(session.updated_at, now)}
      </time>
    </button>
  );
  return (
    <section className="chat-sessions" aria-label="项目会话" data-active-session={sessionId}>
      <header className="chat-sessions-header">
        <span>聊天</span>
        <Button tone="ghost" icon="plus" aria-label="新会话" title="新会话"
          disabled={disabled || !e.project} onClick={() => void e.newSession()} />
      </header>
      <div className="recent-sessions" aria-label="最近会话">
        {sessions.slice(0, 3).map(row)}
        {!sessions.length && <p className="chat-sessions-empty">
          {e.project ? loading ? "正在加载会话…" : "暂无会话" : "打开项目后开始聊天"}
        </p>}
      </div>
      {!!sessions.length && <button type="button" className="all-sessions-button"
        onClick={() => setExpanded(true)}>
        查看全部（{sessions.length} 个）
      </button>}
      {expanded && <Modal title="全部会话" onClose={() => setExpanded(false)}>
        <div className="all-sessions-list">{sessions.map(row)}</div>
      </Modal>}
    </section>
  );
}
