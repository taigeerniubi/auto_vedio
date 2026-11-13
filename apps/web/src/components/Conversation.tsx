// 展示按工程持久化的历史对话、当前运行及编辑结果。
import { useEffect, useRef, useState } from "react";
import type { Editor } from "../hooks/useEditor";
import type { Run, ConversationEntry, FrameAnnotation } from "../contracts";
import { Button, Icon } from "./Ui";
import { progressLabel } from "../runProgress";
import { ReplyContent } from "./ReplyContent";
import { timecode } from "../timeline";

const states = {
  running: "正在处理",
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
          <span className="assistant-welcome-icon"><Icon name="spark" /></span>
          <h3>想怎么剪这段素材？</h3>
          <p>描述你的想法，或从一个小调整开始。</p>
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
      {entries.map((entry) => (
        <div
          className="conversation-turn"
          key={entry.run_id}
          data-run-id={entry.run_id}
        >
          <div className="user-message">
            <small>你</small>
            {entry.annotation && <AnnotationBadge annotation={entry.annotation} />}
            <p>
              {entry.instruction ??
                "旧版本未保存指令原文，以下是当时的运行结果。"}
            </p>
          </div>
          <RunMessage
            run={entry}
            editor={e}
            active={entry.status === "running"}
          />
        </div>
      ))}
      {pending && (
        <div className="conversation-turn">
          <div className="user-message">
            <small>你</small>
            {e.instructionAnnotation && <AnnotationBadge annotation={e.instructionAnnotation} />}
            <p>{e.instruction}</p>
          </div>
          <RunMessage run={null} editor={e} active={active} />
        </div>
      )}
    </div>
  );
}

function AnnotationBadge({ annotation }: { annotation: FrameAnnotation }) {
  const { region } = annotation;
  return <span className="annotation-badge" title={`位置 ${Math.round(region.x * 100)}%, ${Math.round(region.y * 100)}% · 大小 ${Math.round(region.width * 100)}% × ${Math.round(region.height * 100)}%`}>
    <span className="annotation-minimap" aria-hidden="true"><i style={{ left: `${region.x * 100}%`, top: `${region.y * 100}%`, width: `${region.width * 100}%`, height: `${region.height * 100}%` }} /></span>
    画面批注 · {timecode(annotation.frame)}
  </span>;
}

/** 展示一次运行的进度、原因和编辑结果。
 * Args:
 *   run: 已建立的运行，提交期间可为空。
 *   editor: 编辑器操作接口。
 *   active: 是否仍在执行。
 * Returns:
 *   对应运行的助手消息。
 */
function RunMessage({
  run,
  editor: e,
  active,
}: {
  run: Run | null;
  editor: Editor;
  active: boolean;
}) {
  const result = run?.result;
  const [expandedRun, setExpandedRun] = useState<string | null>(null);
  const showAll = !!run && expandedRun === run.run_id;
  const steps = run?.events.filter((event, i) => event.事件 === "工具执行" || event.事件 === "请求模型" ||
    event.事件 === "重试模型" || event.事件 === "恢复任务" ||
    (event.事件 === "开始工具" && active && i === run.events.length - 1)) || [];
  const hiddenCount = Math.max(0, steps.length - 7);
  const visibleSteps = showAll ? steps : steps.slice(-7);
  const resumed = run?.events.some(event => event.事件 === "恢复任务");
  return (
    <div className="agent-message">
      <div className="row">
        <span className="agent-avatar">
          <Icon name="spark" />
        </span>
        <strong>拾光</strong>
        <small title={run ? `运行编号：${run.run_id}` : undefined}>
          {run ? run.status === "completed" && result?.changed === false ? "回复完成" : states[run.status]
            : active ? "开始处理…" : "本次未完成"}
        </small>
      </div>
      {resumed && <p className="execution-resumed">从上次中断处继续</p>}
      {run?.events.length ? (
        <details className="execution-details" open={active || run.status === "failed"}>
          <summary>{active ? "正在处理" : "处理过程"}</summary>
          {hiddenCount > 0 && <div className="execution-history-control">
            <span>{showAll ? `共 ${steps.length} 条记录` : `仅显示最近 7 条，前 ${hiddenCount} 条已收起`}</span>
            <Button tone="ghost" aria-expanded={showAll} aria-controls={`execution-${run.run_id}`}
              onClick={() => setExpandedRun(showAll ? null : run.run_id)}>
              {showAll ? "收起早期步骤" : "展开完整过程"}
            </Button>
          </div>}
          <div className="execution-list" id={`execution-${run.run_id}`}>
          {visibleSteps.map((event, index) => {
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
        </details>
      ) : (
        active && <p className="muted">正在读取工程与素材…</p>
      )}
      {!run && !active && e.error && (
        <p className="failure-text">{e.error.message}</p>
      )}
      {result && <ReplyContent text={result.question || result.reason || result.summary || ""}
        failed={run?.status === "failed"} />}
      {!!result?.error_code && (
        <small className="error-code">{result.error_code}</small>
      )}
      {run?.status === "failed" && <small className="run-id">运行编号：{run.run_id}</small>}
      {run?.status === "failed" && <Button
        disabled={!!e.busy || !e.conversation.ready || !e.model?.configured}
        onClick={() => void e.command("继续上次任务", undefined, run.run_id)}
        title="恢复保存的草稿和任务上下文；工程版本改变时会提示冲突"
      >继续任务</Button>}
      {run?.status === "needs_input" && (
        <small>结合上面的要求补充说明，再次发送完整指令。</small>
      )}
    </div>
  );
}
