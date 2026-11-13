// 按连接状态和高级配置分层展示模型设置。
import type { Editor } from "../hooks/useEditor";
import { Modal } from "./Ui";

export function SettingsDialog({
  editor: e,
  onClose,
}: {
  editor: Editor;
  onClose: () => void;
}) {
  return (
    <Modal title="模型连接" onClose={onClose}>
      <div className="settings-content">
        <p className="muted">
          连接模型后，就可以用自然语言剪辑。手动编辑与导出随时可用。
        </p>
        <small>密钥只在后端保存，无需输入到网页。</small>
        <div className="connection-list">
          <div>
            <span>
              <strong>剪辑助手</strong>
              <small>理解要求，编排镜头与效果</small>
            </span>
            <span
              className={e.model?.configured ? "connection-ready" : "muted"}
            >
              <i className={`dot ${e.model?.configured ? "" : "amber"}`} />
              {e.model?.configured ? e.model.model : "未连接"}
            </span>
          </div>
          {(["vision", "speech", "audio"] as const).map((kind) => (
            <div key={kind}>
              <span>
                <strong>
                  {
                    {
                      vision: "画面理解",
                      speech: "语音识别",
                      audio: "音频问答",
                    }[kind]
                  }
                </strong>
                <small>
                  {
                    {
                      vision: "识别镜头内容与场景",
                      speech: "识别对白，生成字幕",
                      audio: "分析声音与音乐",
                    }[kind]
                  }
                </small>
              </span>
              <span
                className={
                  e.perception?.[kind]?.configured
                    ? "connection-ready"
                    : "muted"
                }
              >
                <i
                  className={`dot ${e.perception?.[kind]?.configured ? "" : "amber"}`}
                />
                {e.perception?.[kind]?.configured
                  ? e.perception[kind].model
                  : "未连接"}
              </span>
            </div>
          ))}
        </div>
        <details className="disclosure settings-advanced">
          <summary>
            配置连接 <span>环境变量与接口设置</span>
          </summary>
          <p>
            在项目根目录 .env
            填写以下配置，重启后端并刷新页面。密钥只保存在后端。
          </p>
          <pre>{`SHIGUANG_MODEL_PROVIDER=openai 或 deepseek
SHIGUANG_MODEL_NAME=模型名称
SHIGUANG_MODEL_API_KEY=你的 API Key

SHIGUANG_VISION_BASE_URL=兼容接口地址
SHIGUANG_VISION_MODEL=视觉模型
SHIGUANG_VISION_API_KEY=密钥

SHIGUANG_SPEECH_BASE_URL=兼容接口地址
SHIGUANG_SPEECH_MODEL=语音模型
SHIGUANG_SPEECH_API_KEY=密钥
SHIGUANG_SPEECH_RESPONSE_FORMAT=verbose_json

SHIGUANG_AUDIO_API_KEY=百炼密钥`}</pre>
          <small>
            自动字幕需要句级时间戳。仅返回文本的语音接口仍可用于录音输入。
          </small>
        </details>
      </div>
    </Modal>
  );
}
