// 复用成片合成器生成选帧封面，也支持独立上传的封面图片。
import { AbsoluteFill, Composition } from "remotion";
import { VlogComposition } from "./index";
import type { Plan } from "./math";

export type CoverSettings = {
  source: "timeline" | "image";
  frame: number;
  asset_id: string | null;
  title: string;
  x: number;
  y: number;
  font_size: number;
  color: string;
  shade: number;
  fit: "cover" | "contain";
};
export type CoverImage = { file: string; width: number; height: number };
export function CoverComposition({
  plan,
  cover,
  image,
}: {
  plan: Plan;
  cover: CoverSettings;
  image?: CoverImage;
}) {
  const picture: Plan =
    cover.source === "image" && image
      ? {
          ...plan,
          total_frames: Math.max(1, plan.total_frames),
          transitions: [],
          text_items: [],
          mix_file: "",
          clips: [
            {
              clip_id: "cover_image",
              asset_id: cover.asset_id || "cover",
              kind: "image",
              file: image.file,
              width: image.width,
              height: image.height,
              start_frame: 0,
              end_frame: Math.max(1, plan.total_frames),
              duration_frames: Math.max(1, plan.total_frames),
              transform: {
                fit: cover.fit || "cover",
                scale: 1,
                anchor_x: 0.5,
                anchor_y: 0.5,
                rotation_deg: 0,
              },
              motion: null,
              color_preset: null,
            },
          ],
        }
      : { ...plan, mix_file: "" };
  return (
    <AbsoluteFill>
      <VlogComposition plan={picture} />
      {cover.shade > 0 && (
        <AbsoluteFill
          style={{
            background: `linear-gradient(transparent,rgba(0,0,0,${cover.shade}))`,
            pointerEvents: "none",
          }}
        />
      )}
      {cover.title && (
        <div
          style={{
            position: "absolute",
            left: `${cover.x * 100}%`,
            top: `${cover.y * 100}%`,
            transform: "translate(-50%,-50%)",
            fontFamily: '"Microsoft YaHei", "Noto Sans CJK SC", sans-serif',
            fontWeight: 700,
            fontSize: cover.font_size,
            color: cover.color,
            whiteSpace: "pre-wrap",
            overflowWrap: "anywhere",
            maxWidth: "90%",
            width: "max-content",
            textAlign: "center",
            lineHeight: 1.25,
            textShadow: "0 2px 8px #0008",
          }}
        >
          {cover.title}
        </div>
      )}
    </AbsoluteFill>
  );
}

export function CoverRoot() {
  const plan: Plan = {
    composition: { width: 1920, height: 1080, fps: 30 },
    clips: [],
    text_items: [],
    transitions: [],
    mix_file: "",
    total_frames: 1,
  };
  const cover: CoverSettings = {
    source: "timeline",
    frame: 0,
    asset_id: null,
    title: "",
    x: 0.5,
    y: 0.75,
    font_size: 72,
    color: "#ffffff",
    shade: 0.25,
    fit: "cover",
  };
  return (
    <Composition
      id="Cover"
      component={CoverComposition}
      width={1920}
      height={1080}
      fps={30}
      durationInFrames={1}
      defaultProps={{ plan, cover }}
      calculateMetadata={({ props }) => ({
        width: props.plan.composition.width,
        height: props.plan.composition.height,
        durationInFrames: Math.max(1, props.plan.total_frames),
      })}
    />
  );
}
