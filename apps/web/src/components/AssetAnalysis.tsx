// 展示素材分析阶段、候选片段与对白，支持定位播放和重试。
import { useRef, useState } from "react";
import type { Editor } from "../hooks/useEditor";
import { post, projectPath } from "../api";
import type { AudioQueryResult } from "../contracts";
import { Button, Modal } from "./Ui";

const states: Record<string, string> = {
  pending: "尚未分析",
  running: "分析中",
  succeeded: "已完成",
  failed: "失败，可重试",
  partial: "部分完成，可重试",
  skipped: "无需分析",
};
export function AssetAnalysis({
  editor: e,
  aid,
  onClose,
}: {
  editor: Editor;
  aid: string;
  onClose: () => void;
}) {
  const asset = e.assets.find((a) => a.asset_id === aid)!;
  const record = e.analyses[aid];
  const video = useRef<HTMLVideoElement>(null);
  const audio = useRef<HTMLAudioElement>(null);
  const [asking, setAsking] = useState(false);
  const [answer, setAnswer] = useState<AudioQueryResult | null>(null);
  const [queryError, setQueryError] = useState("");
  const root = projectPath(e.project!.project_id);
  return (
    <Modal title={`素材理解 · ${asset.name}`} onClose={onClose}>
      <p className="muted">
        抽样画面和对白会发送到你配置的模型服务。分析结果保存在本机，可重复使用。
      </p>
      <div className="row">
        <Button
          disabled={
            !!e.busy ||
            (!e.perception?.vision.configured &&
              !e.perception?.speech.configured)
          }
          onClick={() => void e.analyze(aid)}
        >
          分析 / 继续未完成阶段
        </Button>
        <Button
          disabled={
            !!e.busy ||
            !record ||
            (!e.perception?.vision.configured &&
              !e.perception?.speech.configured)
          }
          onClick={() => void e.analyze(aid, true)}
        >
          重新分析
        </Button>
        <Button
          disabled={
            !!e.busy || !asset.has_audio || !e.perception?.speech.configured
          }
          onClick={() => void e.analyze(aid, false, true)}
        >
          仅识别对白
        </Button>
      </div>
      <div className="analysis-stages">
        {(["visual", "speech"] as const).map((kind) => (
          <div key={kind} role="status">
            <strong>{kind === "visual" ? "画面理解" : "语音识别"}</strong>
            <span>{states[record?.stages[kind].status || "pending"]}</span>
            {record?.stages[kind].message && (
              <p>{record.stages[kind].message}</p>
            )}
          </div>
        ))}
      </div>
      {!e.perception?.vision.configured && (
        <p className="muted">视觉接口尚未配置，请在“模型设置”查看配置项。</p>
      )}
      {asset.kind === "video" && (
        <video
          ref={video}
          className="analysis-video"
          controls
          preload="metadata"
          src={`${root}/assets/${aid}/file`}
        />
      )}
      {asset.kind === "audio" && (
        <audio ref={audio} controls preload="metadata" src={`${root}/assets/${aid}/file`} />
      )}
      {asset.has_audio && (
        <section>
          <h3>音频问答</h3>
          {e.perception?.audio?.configured ? (
            <form onSubmit={async (event) => {
              event.preventDefault();
              if (asking) return;
              const form = new FormData(event.currentTarget);
              const body = {
                query: String(form.get("question")).trim(),
                start_ms: Math.round(Number(form.get("start")) * 1000),
                end_ms: form.get("end") ? Math.round(Number(form.get("end")) * 1000) : null,
              };
              setAsking(true);
              setAnswer(null);
              setQueryError("");
              try {
                setAnswer(await post<AudioQueryResult>(`${root}/assets/${aid}/audio-query`, body));
              } catch (error) {
                setQueryError(error instanceof Error ? error.message : "音频查询失败");
              } finally {
                setAsking(false);
              }
            }}>
              <label>关于这段声音，你想知道什么？
                <input name="question" required maxLength={2000} pattern=".*\S.*" placeholder="例如：第二次笑声在哪里结束？" disabled={asking} />
              </label>
              <div className="form-grid">
                <label>从第几秒开始<input name="start" type="number" step="0.001" min="0" placeholder="0" disabled={asking} /></label>
                <label>到第几秒结束<input name="end" type="number" step="0.001" min="0" placeholder="素材结尾" disabled={asking} /></label>
              </div>
              <Button type="submit" disabled={asking}>{asking ? "正在听取音频…" : "查询音频"}</Button>
            </form>
          ) : <p className="muted">在项目 .env 填写 SHIGUANG_AUDIO_API_KEY 并重启，即可提问。</p>}
          {queryError && <p role="alert">{queryError}</p>}
          {answer && <div role="status">
            <p><strong>{{matched: "查询结果", not_found: "未找到", uncertain: "待确认"}[answer.status]}</strong> · {answer.answer}</p>
            {answer.matches.map((mark, i) => (
              <div key={i}>
                <Button onClick={() => {
                  const player = video.current || audio.current;
                  if (player) {
                    player.currentTime = Math.max(0, mark.anchor_ms / 1000 - 0.5);
                    void player.play().catch(() => setQueryError("请使用播放器手动试听"));
                  }
                }}>试听 {mark.label} · 约 {(mark.anchor_ms / 1000).toFixed(2)} 秒</Button>
                <p>{(mark.start_ms / 1000).toFixed(2)}–{(mark.end_ms / 1000).toFixed(2)} 秒 · 置信度{{high: "高", medium: "中", low: "低"}[mark.certainty]} · {mark.evidence}</p>
              </div>
            ))}
          </div>}
        </section>
      )}
      <div className="analysis-segments">
        {record?.segments.map((segment) => (
          <article key={segment.segment_id}>
            <div className="row">
              <strong>
                {(segment.start_frame / 30).toFixed(1)}–
                {(segment.end_frame / 30).toFixed(1)} 秒
              </strong>
              <Button
                onClick={() => {
                  if (video.current) {
                    video.current.currentTime = segment.start_frame / 30;
                    void video.current.play();
                  }
                }}
                disabled={asset.kind !== "video"}
              >
                定位播放
              </Button>
            </div>
            <p>{segment.visual?.description || states[segment.status]}</p>
            <div className="analysis-tags">
              {segment.visual?.tags.map((tag) => (
                <span className="chip" key={tag}>
                  {tag}
                </span>
              ))}
            </div>
            {segment.visual?.needs_review && (
              <p className="muted">
                需复核：{segment.visual.review_reasons.join("；")}
              </p>
            )}
            <Button
              disabled={!!e.busy || !segment.visual}
              onClick={() =>
                void e.edit("insert_clip", {
                  clip: {
                    clip_id: `clip_${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`,
                    asset_id: aid,
                    kind: asset.kind,
                    ...(asset.kind === "image"
                      ? { still_duration_frames: 90 }
                      : {
                          source_in_frame: segment.start_frame,
                          source_out_frame: segment.end_frame,
                        }),
                  },
                })
              }
            >
              添加该段到时间线
            </Button>
          </article>
        ))}
      </div>
      {!!record?.speech.utterances.length && (
        <section>
          <h3>对白 · 句级时间戳</h3>
          {record.speech.utterances.map((line, i) => (
            <p key={i}>
              <span className="muted">
                {(line.start_ms / 1000).toFixed(1)}–
                {(line.end_ms / 1000).toFixed(1)}s　
              </span>
              {line.text}
            </p>
          ))}
        </section>
      )}
    </Modal>
  );
}
