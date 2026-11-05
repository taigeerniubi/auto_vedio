// 展示按工程持久化的历史对话、当前运行及可展开的编辑结果。
import { useEffect, useRef, useState } from "react";
import type { Editor } from "../hooks/useEditor";
import type { Run, ConversationEntry } from "../contracts";
import { Button, Icon } from "./Ui";
import { progressLabel } from "../runProgress";

const states = {
  running: "正在剪辑",
  completed: "编辑完成",
  failed: "本次未完成",
  cancelled: "已取消",
  needs_input: "需要你的补充",
  unsupported: "暂不支持",
};

/** 展示历史对话，并在新指令到来时滚动到最新消息。
 * Args:
 *   editor: 当前工程的编辑器状态。
 * Returns:
 *   可分页的用户指令与执行结果列表。
 */
export function Conversation({ editor: e, onSuggest }: { editor: Editor; onSuggest: (text: string) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const previousLast = useRef<string | undefined>(undefined);
  const active = e.busy === "AI 正在编辑";
  const entries: ConversationEntry[] = e.conversation.entries.filter(
    (entry) => entry.run_id !== e.run?.run_id,
  );
  if (e.run)
    entries.push({ ...e.run, instruction: e.instruction, created_at: null });
  const pending = !!e.instruction && !e.run;
  const lastId = pending ? `pending:${e.instruction}` : entries.at(-1)?.run_id;
  const last = entries.at(-1);
  useEffect(() => {
    const element = ref.current;
    if (
      element &&
      (lastId !== previousLast.current ||
        element.scrollHeight - element.scrollTop - element.clientHeight < 100)
    ) {
      element.scrollTop = element.scrollHeight;
    }
    previousLast.current = lastId;
  }, [lastId, last?.status, last?.events.length, entries.length]);
  return (
    <div
      className="conversation"
      aria-live="polite"
      aria-label="历史对话"
      ref={ref}
    >
      {e.conversation.hasMore && (
        <Button
          disabled={e.conversation.loading}
          onClick={() => void e.conversation.loadOlder()}
        >
          加载更早对话
        </Button>
      )}
      {e.conversation.loading && <p className="muted">正在加载历史对话…</p>}
      {!entries.length && !pending && !e.conversation.loading && (
        <div className="agent-welcome">
          <h3>想怎么剪这段素材？</h3>
          <div className="starter-actions">
            {[
              ["layers", "给选中镜头加缓推", "给选中片段添加缓推效果"],
              ["text", "给开头添加标题", "在开头添加标题「慢慢生活」"],
              ["sliders", "把选中镜头调成黑白", "将选中片段变为黑白"],
            ].map(([icon, label, value]) => (
              <button
                key={value}
                disabled={!!e.busy || !e.project}
                onClick={() => onSuggest(value)}
              >
                <Icon name={icon} />
                <span>{label}</span>
                <Icon name="right" />
              </button>
            ))}
          </div>
        </div>
      )}
      {entries.map((entry, index) => (
        <div
          className="conversation-turn"
          key={entry.run_id}
          data-run-id={entry.run_id}
        >
          <div className="user-message">
            <small>你</small>
            <p>
              {entry.instruction ??
                "旧版本未保存指令原文，以下是当时的运行结果。"}
            </p>
          </div>
          <RunMessage
            run={entry}
            editor={e}
            active={entry.status === "running"}
            latest={!pending && index === entries.length - 1}
          />
        </div>
      ))}
      {pending && (
        <div className="conversation-turn">
          <div className="user-message">
            <small>你</small>
            <p>{e.instruction}</p>
          </div>
          <RunMessage run={null} editor={e} active={active} latest />
        </div>
      )}
    </div>
  );
}

/** 展示一次运行的进度、原因和可查看的编辑差异。
 * Args:
 *   run: 已建立的运行，提交期间可为空。
 *   editor: 编辑器操作接口。
 *   active: 是否仍在执行。
 *   latest: 是否为最新一条对话。
 * Returns:
 *   对应运行的助手消息。
 */
function RunMessage({
  run,
  editor: e,
  active,
  latest,
}: {
  run: Run | null;
  editor: Editor;
  active: boolean;
  latest: boolean;
}) {
  const [showDiff, setShowDiff] = useState(false);
  const result = run?.result;
  return (
    <div className="agent-message">
      <div className="row">
        <span className="agent-avatar">
          <Icon name="spark" />
        </span>
        <strong>拾光</strong>
        <small title={run ? `运行编号：${run.run_id}` : undefined}>
          {run ? states[run.status] : active ? "开始处理…" : "本次未完成"}
        </small>
      </div>
      {run?.events.length ? (
        <div className="execution-list">
          {run.events.filter((event, i) => event.事件 === "工具执行" || event.事件 === "请求模型" ||
            (event.事件 === "开始工具" && active && i === run.events.length - 1)).slice(-7).map((event, index) => {
            const working = active && event === run.events.at(-1) && event.事件 !== "工具执行";
            return <div key={index} className={working ? "working-step" : ""}>
              <span className={event.状态 === "失败" ? "failed-dot" : "step-indicator"}>
                {working ? "●" : event.状态 === "失败" ? "!" : event.事件 === "工具执行" ? "✓" : "·"}
              </span>
              <span>{progressLabel(event)}{event.状态 === "失败" && ` · ${event.原因 || "失败"}`}</span>
              {working && <small>进行中</small>}
            </div>;
          })}
        </div>
      ) : (
        active && <p className="muted">正在读取工程与素材…</p>
      )}
      {!run && !active && e.error && (
        <p className="failure-text">{e.error.message}</p>
      )}
      {result && (
        <p className={run?.status === "failed" ? "failure-text" : ""}>
          {result.question || result.reason || result.summary}
        </p>
      )}
      {!!result?.error_code && (
        <small className="error-code">{result.error_code}</small>
      )}
      {run?.status === "failed" && <small className="run-id">运行编号：{run.run_id}</small>}
      {run?.status === "needs_input" && (
        <small>结合上面的要求补充说明，再次发送完整指令。</small>
      )}
      {run?.status === "completed" && (
        <div className="row result-actions">
          <Button
            disabled={
              !!e.busy || !latest || run.result.changed === false || run.result.revision !== e.project?.revision
            }
            onClick={() => void e.history("undo")}
          >
            撤销更改
          </Button>
          <Button tone="ghost" onClick={() => setShowDiff(!showDiff)}>
            查看修改
          </Button>
        </div>
      )}
      {showDiff && result?.diff && (
        <div className="diff-summary">
          {Object.keys(result.diff).length
            ? Object.keys(result.diff).map((key) => (
                <div key={key}>
                  ✓　
                  {{
                    clips: "画面片段",
                    text_items: "标题文字",
                    audio_items: "配乐",
                    transitions: "转场",
                    composition: "画布设置",
                  }[key] || key}
                  已更新
                </div>
              ))
            : "本次没有修改工程。"}
        </div>
      )}
    </div>
  );
}
