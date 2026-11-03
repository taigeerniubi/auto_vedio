// 展示智能体指令、真实执行进度、结果与可恢复的失败信息。
import { useState } from "react";
import type { Editor } from "../hooks/useEditor";
import { Button, Icon } from "./Ui";

const states = {
  running: "正在剪辑",
  completed: "编辑完成",
  failed: "本次未完成",
  cancelled: "已取消",
  needs_input: "需要你的补充",
  unsupported: "暂不支持",
};

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
}: {
  editor: Editor;
  onSettings: () => void;
}) {
  const [text, setText] = useState("");
  const [showDiff, setShowDiff] = useState(false);
  const selected = e.project?.snapshot.clips.find(
    (c) => c.clip_id === e.selected,
  );
  const name = e.assets.find((a) => a.asset_id === selected?.asset_id)?.name;
  const active = !!e.busy && e.busy === "AI 正在编辑";
  const submit = () => {
    if (!text.trim() || e.busy || !e.project) return;
    void e.command(text);
  };
  const result = e.run?.result;
  return (
    <div className="agent-panel">
      <div className="agent-intro">
        <h2>
          <Icon name="spark" />
          让想法，直接成片
        </h2>
        <p>说说你想怎么剪，剩下的交给我。</p>
      </div>
      <div className="conversation" aria-live="polite">
        {!e.instruction && (
          <div className="agent-welcome">
            <div className="agent-avatar">
              <Icon name="spark" />
            </div>
            <h3>你好，我是拾光。</h3>
            <p>
              从一个简单的想法开始。
              <br />
              裁短镜头、添加标题，或给画面一点运动。
            </p>
            <div className="capability-note">
              先导入素材，再告诉我使用哪个文件。涉及画面内容时，请描述清楚。
            </div>
          </div>
        )}
        {e.instruction && (
          <div className="user-message">
            <small>你</small>
            <p>{e.instruction}</p>
          </div>
        )}
        {(active || e.run) && (
          <div className="agent-message">
            <div className="row">
              <span className="agent-avatar">
                <Icon name="spark" />
              </span>
              <strong>拾光</strong>
              <small>{e.run ? states[e.run.status] : "开始处理…"}</small>
            </div>
            {e.run?.events.length ? (
              <div className="execution-list">
                {e.run.events
                  .filter((x) => x.事件 === "工具执行" || x.事件 === "请求模型")
                  .slice(-7)
                  .map((event, index) => (
                    <div key={index}>
                      <span
                        className={
                          event.状态 === "失败" ? "failed-dot" : "success-dot"
                        }
                      >
                        {event.状态 === "失败"
                          ? "!"
                          : event.事件 === "请求模型"
                            ? "·"
                            : "✓"}
                      </span>
                      <span>
                        {event.事件 === "请求模型"
                          ? `正在规划第 ${event.轮次} 轮操作`
                          : event.状态 === "失败"
                            ? event.原因 || "工具执行未通过"
                            : "已执行 " + event.工具}
                      </span>
                    </div>
                  ))}
              </div>
            ) : (
              active && <p className="muted">正在读取工程与素材…</p>
            )}
            {result && (
              <p className={e.run?.status === "failed" ? "failure-text" : ""}>
                {result.question || result.reason || result.summary}
              </p>
            )}
            {!!result?.error_code && (
              <small className="error-code">{result.error_code}</small>
            )}
            {e.run && (
              <small className="run-id">运行编号：{e.run.run_id}</small>
            )}
            {e.run?.status === "needs_input" && (
              <small>结合上面的要求补充说明，再次发送完整指令。</small>
            )}
            {e.run?.status === "completed" && (
              <div className="row result-actions">
                <Button
                  disabled={!!e.busy}
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
        )}
      </div>
      <div className="suggestions">
        <small>还可以试试</small>
        <div className="row">
          {[
            "给选中片段添加缓推效果",
            "在开头添加标题「慢慢生活」",
            "将选中片段变为黑白",
          ].map((value, index) => (
            <button
              key={value}
              disabled={!!e.busy}
              onClick={() => setText(value)}
            >
              {["缓缓推进", "加个标题", "黑白质感"][index]}
            </button>
          ))}
        </div>
      </div>
      {!e.model?.configured && (
        <button className="model-notice" onClick={onSettings}>
          <span className="dot amber" />
          连接模型后可使用 AI 编辑 <span>设置 ↗</span>
        </button>
      )}
      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <textarea
          aria-label="剪辑指令"
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="告诉我，你想怎么剪…"
          rows={3}
          maxLength={8000}
          disabled={!!e.busy || !e.project}
          onKeyDown={(event) => {
            if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
              event.preventDefault();
              submit();
            }
          }}
        />
        {selected && (
          <div className="selection-chip" title={name}>
            已选 · {name}
          </div>
        )}
        <div className="composer-bottom">
          <small>
            {e.model?.configured
              ? `${e.model.provider === "openai" ? "OpenAI" : "DeepSeek"} · ${e.model.model}`
              : "Ctrl + Enter 发送"}
          </small>
          {active ? (
            <Button
              aria-label="取消AI运行"
              disabled={!e.run || e.run.status !== "running"}
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
                !text.trim() || !!e.busy || !e.project || !e.model?.configured
              }
            />
          )}
        </div>
      </form>
    </div>
  );
}
