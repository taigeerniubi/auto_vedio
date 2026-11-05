// 预设在正式提交前可调参预览，应用后形成一个可撤销版本。
import { useEffect, useState } from "react";
import type { Motion } from "../../../../packages/composition/math";
import type { Editor } from "../hooks/useEditor";
import { api, thumbnailUrl } from "../api";
import { duration, layout } from "../timeline";
import { Button } from "./Ui";

export type MotionDraft = { clip_id: string; motion: Motion } | null;
const labels: Record<Motion["preset_id"], string> = {
  slow_push_in: "缓缓推进",
  slow_pull_out: "缓缓拉远",
  pan_left: "向左平移",
  pan_right: "向右平移",
  pan_up: "向上平移",
  pan_down: "向下平移",
};
type Presets = {
  motion: { ids: Motion["preset_id"][] };
  styles: { id: string; name: string; description: string }[];
};

export function TemplatesPanel({
  editor: e,
  draft,
  onDraft,
  onPreview,
}: {
  editor: Editor;
  draft: MotionDraft;
  onDraft: (draft: MotionDraft) => void;
  onPreview: (frame: number) => void;
}) {
  const [catalog, setCatalog] = useState<Presets | null>(null);
  const [tab, setTab] = useState("motion");
  const [style, setStyle] = useState("calm_travel");
  const clip = e.project?.snapshot.clips.find((c) => c.clip_id === e.selected);
  const asset = e.assets.find((a) => a.asset_id === clip?.asset_id);
  const motion = draft?.clip_id === clip?.clip_id ? draft?.motion : null;
  useEffect(() => {
    api<Presets>("/api/v1/presets").then(setCatalog).catch(e.report);
  }, []);
  const choose = (preset_id: Motion["preset_id"]) => {
    if (!clip || !e.project) return;
    onDraft({
      clip_id: clip.clip_id,
      motion: {
        preset_id,
        local_start_frame: 0,
        local_end_frame: duration(clip),
        intensity: 0.12,
        anchor_x: 0.5,
        anchor_y: 0.5,
        easing: "ease_in_out",
      },
    });
    const row = layout(e.project.snapshot).rows.find(
      (r) => r.clip.clip_id === clip.clip_id,
    );
    onPreview(row?.start ?? 0);
  };
  const change = (value: Partial<Motion>) => {
    if (clip && motion)
      onDraft({ clip_id: clip.clip_id, motion: { ...motion, ...value } });
  };
  return (
    <section className="asset-panel" aria-label="剪辑模板库">
      <div className="panel-heading">
        <h2>剪辑模板</h2>
      </div>
      <div className="filter-tabs">
        <button
          className={tab === "motion" ? "active" : ""}
          onClick={() => setTab("motion")}
        >
          单镜头运镜
        </button>
        <button
          className={tab === "style" ? "active" : ""}
          onClick={() => {
            setTab("style");
            onDraft(null);
          }}
        >
          成片风格
        </button>
      </div>
      <div className="effect-content template-content">
        {tab === "motion" ? (
          <>
            <p className="muted">
              {clip
                ? "选一个模板，在中间播放器查看效果。"
                : "先在时间线选择一个片段。"}
            </p>
            <div className="motion-grid">
              {catalog?.motion.ids.map((id) => (
                <button
                  key={id}
                  aria-pressed={motion?.preset_id === id}
                  disabled={!clip || !!e.busy}
                  className="motion-card"
                  onClick={() => choose(id)}
                >
                  <div className={`motion-sample ${id}`}>
                    <div
                      className="sample-image"
                      style={
                        asset && e.project
                          ? {
                              backgroundImage: `url(${thumbnailUrl(e.project.project_id, asset.asset_id)})`,
                            }
                          : {}
                      }
                    />
                  </div>
                  <strong>{labels[id]}</strong>
                </button>
              ))}
            </div>
            {motion && clip && (
              <div className="motion-controls">
                <p className="preview-notice">正在预览 · 点击应用后保存</p>
                <label>
                  运动强度 {Math.round(motion.intensity * 100)}%
                  <input
                    aria-label="运动强度"
                    type="range"
                    min=".01"
                    max=".5"
                    step=".01"
                    value={motion.intensity}
                    onChange={(v) =>
                      change({ intensity: Number(v.target.value) })
                    }
                  />
                </label>
                <div className="form-grid">
                  <label>
                    开始 / 秒
                    <input
                      aria-label="运镜开始"
                      type="number"
                      min="0"
                      max={(motion.local_end_frame - 1) / 30}
                      step=".1"
                      value={motion.local_start_frame / 30}
                      onChange={(v) =>
                        change({
                          local_start_frame: Math.max(
                            0,
                            Math.min(
                              motion.local_end_frame - 1,
                              Math.round(Number(v.target.value) * 30),
                            ),
                          ),
                        })
                      }
                    />
                  </label>
                  <label>
                    结束 / 秒
                    <input
                      aria-label="运镜结束"
                      type="number"
                      min={(motion.local_start_frame + 1) / 30}
                      max={duration(clip) / 30}
                      step=".1"
                      value={motion.local_end_frame / 30}
                      onChange={(v) =>
                        change({
                          local_end_frame: Math.min(
                            duration(clip),
                            Math.max(
                              motion.local_start_frame + 1,
                              Math.round(Number(v.target.value) * 30),
                            ),
                          ),
                        })
                      }
                    />
                  </label>
                </div>
                <label>
                  运动节奏
                  <select
                    aria-label="运动节奏"
                    value={motion.easing}
                    onChange={(v) =>
                      change({ easing: v.target.value as Motion["easing"] })
                    }
                  >
                    <option value="ease_in_out">轻起轻落</option>
                    <option value="linear">匀速</option>
                  </select>
                </label>
                <p>点击画面选择运动中心</p>
                <button
                  className="motion-anchor"
                  aria-label="运镜中心"
                  onClick={(event) => {
                    const box = event.currentTarget.getBoundingClientRect();
                    change({
                      anchor_x: Math.max(
                        0,
                        Math.min(1, (event.clientX - box.left) / box.width),
                      ),
                      anchor_y: Math.max(
                        0,
                        Math.min(1, (event.clientY - box.top) / box.height),
                      ),
                    });
                  }}
                >
                  {asset && e.project && (
                    <img
                      alt="选择运镜中心"
                      src={thumbnailUrl(e.project.project_id, asset.asset_id) + "?pad=false"}
                    />
                  )}
                  <i
                    style={{
                      left: `${motion.anchor_x * 100}%`,
                      top: `${motion.anchor_y * 100}%`,
                    }}
                  />
                </button>
                <div className="form-grid">
                  {(["anchor_x", "anchor_y"] as const).map((key, i) => (
                    <label key={key}>
                      {i ? "垂直中心" : "水平中心"}
                      <input
                        aria-label={i ? "垂直中心" : "水平中心"}
                        type="range"
                        min="0"
                        max="1"
                        step=".01"
                        value={motion[key]}
                        onChange={(v) =>
                          change({ [key]: Number(v.target.value) })
                        }
                      />
                    </label>
                  ))}
                </div>
                <div className="row">
                  <Button
                    tone="primary"
                    disabled={!!e.busy || clip.transform.fit !== "cover"}
                    onClick={() =>
                      void e.edit("apply_motion_preset", {
                        clip_id: clip.clip_id,
                        motion,
                      })
                    }
                  >
                    应用运镜
                  </Button>
                  <Button onClick={() => onDraft(null)}>取消预览</Button>
                </div>
                {clip.transform.fit !== "cover" && (
                  <p className="muted">
                    请先在片段属性中将构图改为“填满画布”。
                  </p>
                )}
              </div>
            )}
          </>
        ) : (
          <>
            <p className="muted">让镜头节奏、色彩和声音保持一致。</p>
            {catalog?.styles.map((item) => (
              <button
                className={`style-card ${style === item.id ? "active" : ""}`}
                key={item.id}
                aria-pressed={style === item.id}
                onClick={() => setStyle(item.id)}
              >
                <strong>{item.name}</strong>
                <span>{item.description}</span>
              </button>
            ))}
            <Button
              disabled={!e.project?.snapshot.clips.length || !!e.busy}
              onClick={() =>
                void e.edit("apply_style_preset", { preset_id: style })
              }
            >
              应用到当前成片
            </Button>
            <form
              className="roughcut-form"
              onSubmit={(event) => {
                event.preventDefault();
                const values = new FormData(event.currentTarget);
                void e.command(
                  `请自动初剪，替换当前时间线。主题：${values.get("theme")}。目标时长 ${values.get("seconds")} 秒，画幅 ${values.get("ratio")}。风格预设 ${style}。必留和排除要求：${values.get("constraints") || "无"}。先根据素材分析选片，不确定则询问；应用成片风格，使用用户已上传的配乐（若有），最后检查实际时长。`,
                );
              }}
            >
              <h3>AI 自动初剪</h3>
              <label>
                故事主题
                <input
                  name="theme"
                  aria-label="初剪主题"
                  required
                  maxLength={300}
                  placeholder="周末去海边，轻松一点"
                />
              </label>
              <div className="form-grid">
                <label>
                  时长 / 秒
                  <input
                    name="seconds"
                    type="number"
                    defaultValue="60"
                    min="15"
                    max="180"
                    required
                  />
                </label>
                <label>
                  画幅
                  <select name="ratio">
                    <option>16:9 横屏</option>
                    <option>9:16 竖屏</option>
                  </select>
                </label>
              </div>
              <label>
                必留 / 排除
                <textarea
                  name="constraints"
                  rows={2}
                  maxLength={1000}
                  placeholder="保留日落，不要坐车的片段"
                />
              </label>
              <small className="muted">
                将重新组织时间线，可整次撤销。未分析的素材会先按已配置模型分析。
              </small>
              <Button
                type="submit"
                tone="primary"
                disabled={
                  !!e.busy ||
                  !e.project ||
                  !e.assets.length ||
                  !e.model?.configured
                }
              >
                生成初剪
              </Button>
            </form>
          </>
        )}
      </div>
    </section>
  );
}
