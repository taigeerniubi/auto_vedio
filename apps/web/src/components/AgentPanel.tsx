// 展示智能体指令、真实执行进度、结果与可恢复的失败信息。
import { useRef, useState } from "react";
import type { Editor } from "../hooks/useEditor";
import { Button, Icon } from "./Ui";
import { Conversation } from "./Conversation";
import { VoiceInput } from "./VoiceInput";
import { Inspector } from "./Inspector";

/** 渲染自然语言编辑入口及运行反馈。
 * Args:
 *   editor: 工程状态和模型运行操作。
 *   onSettings: 打开模型配置说明。
 * Returns:
 *   智能体对话面板。
 */
export function AgentPanel({
  editor: e,
  onSettings,
  onRecordStart,
}: {
  editor: Editor;
  onSettings: () => void;
  onRecordStart: () => void;
}) {
  const [text, setText] = useState("");
  const [tab, setTab] = useState<"agent" | "inspector">("agent");
  const input = useRef<HTMLTextAreaElement>(null);
  const selected = e.project?.snapshot.clips.find(
    (c) => c.clip_id === e.selected,
  );
  const name = e.assets.find((a) => a.asset_id === selected?.asset_id)?.name;
  const active = !!e.busy && e.busy === "AI 正在编辑";
  const submit = () => {
    if (!text.trim() || e.busy || !e.project || !e.model?.configured) return;
    void e.command(text.trim(), () => setText(""));
  };
  return (
    <aside id="workspace-dock" className="right-panel panel" aria-label="剪辑助手与属性">
      <header className="dock-header">
        <div className="right-tabs" role="tablist" aria-label="右侧工具">
          {(["agent", "inspector"] as const).map((id) => (
            <button
              key={id}
              id={`tab-${id}`}
              role="tab"
              aria-selected={tab === id}
              aria-controls="dock-content"
              tabIndex={tab === id ? 0 : -1}
              onClick={() => setTab(id)}
              onKeyDown={(event) => {
                if (
                  !["ArrowLeft", "ArrowRight", "Home", "End"].includes(
                    event.key,
                  )
                )
                  return;
                event.preventDefault();
                const next =
                  event.key === "Home"
                    ? "agent"
                    : event.key === "End"
                      ? "inspector"
                      : id === "agent"
                        ? "inspector"
                        : "agent";
                setTab(next);
                document.getElementById(`tab-${next}`)?.focus();
              }}
            >
              <Icon name={id === "agent" ? "spark" : "sliders"} />
              {id === "agent" ? "AI 助手" : "片段属性"}
            </button>
          ))}
        </div>
        <Button
          tone="ghost"
          icon="settings"
          aria-label="模型设置"
          title="模型设置"
          onClick={onSettings}
        />
      </header>
      <div
        id="dock-content"
        role="tabpanel"
        aria-labelledby={`tab-${tab}`}
        className="dock-content"
      >
        {tab === "inspector" ? (
          <Inspector editor={e} />
        ) : (
          <div className="agent-panel">
            <Conversation
              editor={e}
              onSuggest={(value) => {
                setText(value);
                input.current?.focus();
              }}
            />
            <div className="composer-dock">
              <form
                className="composer"
                onSubmit={(event) => {
                  event.preventDefault();
                  submit();
                }}
              >
                {selected && (
                  <div className="selection-chip" title={name}>
                    已选 · {name}
                  </div>
                )}
                <textarea
                  ref={input}
                  aria-label="剪辑指令"
                  value={text}
                  onChange={(event) => setText(event.target.value)}
                  placeholder="告诉我，你想怎么剪…"
                  rows={2}
                  maxLength={8000}
                  disabled={!!e.busy || !e.project}
                  onKeyDown={(event) => {
                    if (
                      event.nativeEvent.isComposing ||
                      event.nativeEvent.keyCode === 229
                    )
                      return;
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      submit();
                    }
                  }}
                />
                <div className="composer-bottom">
                  <VoiceInput
                    editor={e}
                    onStart={onRecordStart}
                    onText={(value) =>
                      setText((old) => (old ? `${old}\n${value}` : value))
                    }
                  />
                  {active ? (
                    <Button
                      aria-label="取消AI运行"
                      disabled={!e.activeRun}
                      onClick={() => void e.cancel()}
                    >
                      取消
                    </Button>
                  ) : (
                    <Button
                      type="submit"
                      tone="primary"
                      icon="arrow"
                      aria-label="发送剪辑指令"
                      disabled={
                        !text.trim() ||
                        !!e.busy ||
                        !e.project ||
                        !e.model?.configured
                      }
                    />
                  )}
                </div>
              </form>
              <div className="composer-meta">
                <button
                  onClick={onSettings}
                  title={e.model?.configured ? e.model.model : "配置剪辑模型"}
                >
                  <span
                    className={`dot ${e.model?.configured ? "" : "amber"}`}
                  />
                  {e.model?.configured ? e.model.model : "连接剪辑模型"}
                </button>
                <small>Enter 发送 · Shift+Enter 换行</small>
              </div>
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}
