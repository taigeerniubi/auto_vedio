// 使用当前草稿的真实素材、转场和文字进行逐帧监看。
import { Thumbnail } from "@remotion/player";
import { useMemo } from "react";
import { VlogComposition } from "../../../../packages/composition";
import { projectPath } from "../api";
import type { Asset, Project } from "../contracts";
import { layout, timecode } from "../timeline";

export function DraftMonitor({
  project,
  assets,
  frame,
  seek,
  seq,
}: {
  project: Project;
  assets: Asset[];
  frame: number;
  seek: (value: number) => void;
  seq: number;
}) {
  const s = project.snapshot;
  const plan = useMemo(() => {
    const { rows, total } = layout(s);
    return {
      composition: s.composition,
      total_frames: total,
      mix_file: "",
      transitions: s.transitions,
      text_items: s.text_items,
      clips: rows.map(({ clip, start, end }) => {
        const asset = assets.find((item) => item.asset_id === clip.asset_id);
        return {
          ...clip,
          file: `${projectPath(project.project_id)}/assets/${encodeURIComponent(clip.asset_id)}/file`,
          width: asset?.width || s.composition.width,
          height: asset?.height || s.composition.height,
          start_frame: start,
          end_frame: end,
          duration_frames: end - start,
          sourcePlayback: {
            inFrame: clip.source_in_frame,
            outFrame: clip.source_out_frame || 0,
            numerator: clip.rate_num,
            denominator: clip.rate_den,
            reverse: clip.reverse,
          },
        };
      }),
    };
  }, [s, assets, project.project_id]);
  const current = Math.max(0, Math.min(frame, plan.total_frames - 1));
  return (
    <section
      id="workspace-monitor"
      className="preview-panel panel draft-monitor"
      aria-label="AI 草稿监看"
    >
      <div className="panel-heading">
        <h2>
          <i className="live-dot" /> 草稿监看
        </h2>
        <span className="chip">修改 {seq}</span>
      </div>
      <div
        className={`preview-stage ${s.composition.width < s.composition.height ? "portrait" : ""}`}
      >
        {plan.total_frames > 0 ? (
          <Thumbnail
            component={VlogComposition}
            inputProps={{ plan }}
            frameToDisplay={current}
            durationInFrames={plan.total_frames}
            fps={30}
            compositionWidth={s.composition.width}
            compositionHeight={s.composition.height}
            style={{
              width: "100%",
              maxHeight: "100%",
              aspectRatio: `${s.composition.width}/${s.composition.height}`,
            }}
          />
        ) : (
          <p className="muted">草稿时间线为空</p>
        )}
      </div>
      <div className="playback-bar">
        <strong className="draft-time">{timecode(current)}</strong>
        <input
          type="range"
          aria-label="查看草稿帧"
          min="0"
          max={Math.max(0, plan.total_frames - 1)}
          disabled={!plan.total_frames}
          value={current}
          onChange={(event) => seek(Number(event.target.value))}
        />
        <small>静音逐帧预览</small>
      </div>
    </section>
  );
}
