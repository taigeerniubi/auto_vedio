// 在合成坐标中处理画面移动、等比缩放和独立宽高拉伸，每次手势只保存一个版本。
import { useRef, useState } from "react";
import {
  geometry,
  type PreparedClip,
  type Transform,
} from "../../../../packages/composition/math";
import type { Editor } from "../hooks/useEditor";
import { Button } from "./Ui";

export type TransformDraft = { clip_id: string; transform: Transform } | null;
export function CanvasTransform({
  clip,
  frame,
  width,
  height,
  editor: e,
  onDraft,
  onPause,
}: {
  clip: PreparedClip;
  frame: number;
  width: number;
  height: number;
  editor: Editor;
  onDraft: (draft: TransformDraft) => void;
  onPause: () => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const [locked, setLocked] = useState(true);
  const gesture = useRef<{
    x: number;
    y: number;
    clip: PreparedClip;
    mode: string;
    locked: boolean;
    opposite: { x: number; y: number };
    diagonal: { x: number; y: number };
    value: Transform;
  } | null>(null);
  const disabled = !!e.busy,
    g = geometry(clip, frame - clip.start_frame, width, height),
    tr = clip.transform;
  const rotate = (x: number, y: number, angle: number) => ({
    x: x * Math.cos(angle) - y * Math.sin(angle),
    y: x * Math.sin(angle) + y * Math.cos(angle),
  });
  function start(event: React.PointerEvent<HTMLElement>, mode: string) {
    if (disabled) return;
    event.preventDefault();
    event.stopPropagation();
    onPause();
    event.currentTarget.setPointerCapture(event.pointerId);
    const sx = mode.includes("w") ? -1 : mode.includes("e") ? 1 : 0,
      sy = mode.includes("n") ? -1 : mode.includes("s") ? 1 : 0,
      angle = (tr.rotation_deg * Math.PI) / 180;
    const opposite = rotate(
      g.left + (sx < 0 ? g.width : sx === 0 ? g.width / 2 : 0),
      g.top + (sy < 0 ? g.height : sy === 0 ? g.height / 2 : 0),
      angle,
    );
    gesture.current = {
      x: event.clientX,
      y: event.clientY,
      clip,
      mode,
      locked,
      opposite: {
        x: width / 2 + (tr.position_x || 0) * width + opposite.x,
        y: height / 2 + (tr.position_y || 0) * height + opposite.y,
      },
      diagonal: rotate(sx * g.width, sy * g.height, angle),
      value: tr,
    };
  }
  function move(event: React.PointerEvent<HTMLElement>) {
    const state = gesture.current,
      rect = root.current?.getBoundingClientRect();
    if (!state || !rect) return;
    const old = state.clip.transform;
    let next = { ...old };
    if (state.mode === "move") {
      next.position_x = Math.max(
        -2,
        Math.min(
          2,
          (old.position_x || 0) + (event.clientX - state.x) / rect.width,
        ),
      );
      next.position_y = Math.max(
        -2,
        Math.min(
          2,
          (old.position_y || 0) + (event.clientY - state.y) / rect.height,
        ),
      );
    } else {
      const d = state.diagonal;
      const vector = {
        x: d.x + ((event.clientX - state.x) / rect.width) * width,
        y: d.y + ((event.clientY - state.y) / rect.height) * height,
      };
      const angle = (old.rotation_deg * Math.PI) / 180;
      const sx = state.mode.includes("w")
        ? -1
        : state.mode.includes("e")
          ? 1
          : 0;
      const sy = state.mode.includes("n")
        ? -1
        : state.mode.includes("s")
          ? 1
          : 0;
      if (state.locked) {
        const factor =
          (vector.x * d.x + vector.y * d.y) / (d.x * d.x + d.y * d.y);
        next.scale = Math.max(0.1, Math.min(4, old.scale * factor));
      } else {
        // 在素材局部坐标中分别投影两个轴，旋转后仍沿边拉伸。
        const local = rotate(vector.x, vector.y, -angle);
        const diagonal = rotate(d.x, d.y, -angle);
        if (sx)
          next.scale_x = Math.max(
            0.1,
            Math.min(4, ((old.scale_x ?? 1) * local.x) / diagonal.x),
          );
        if (sy)
          next.scale_y = Math.max(
            0.1,
            Math.min(4, ((old.scale_y ?? 1) * local.y) / diagonal.y),
          );
      }
      const resized = geometry(
        { ...state.clip, transform: next },
        frame - clip.start_frame,
        width,
        height,
      );
      const offset = rotate(
        resized.left +
          (sx < 0 ? resized.width : sx === 0 ? resized.width / 2 : 0),
        resized.top +
          (sy < 0 ? resized.height : sy === 0 ? resized.height / 2 : 0),
        angle,
      );
      next.position_x = Math.max(
        -2,
        Math.min(2, (state.opposite.x - width / 2 - offset.x) / width),
      );
      next.position_y = Math.max(
        -2,
        Math.min(2, (state.opposite.y - height / 2 - offset.y) / height),
      );
    }
    state.value = next;
    onDraft({ clip_id: clip.clip_id, transform: next });
  }
  async function save(value: Transform) {
    onDraft({ clip_id: clip.clip_id, transform: value });
    await e.edit("set_transform", { clip_id: clip.clip_id, transform: value });
    onDraft(null);
  }
  function end(event: React.PointerEvent<HTMLElement>) {
    const state = gesture.current;
    if (!state) return;
    gesture.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
    if (JSON.stringify(state.value) !== JSON.stringify(state.clip.transform))
      void save(state.value);
    else onDraft(null);
  }
  const events = {
    onPointerMove: move,
    onPointerUp: end,
    onPointerCancel: () => {
      gesture.current = null;
      onDraft(null);
    },
  };
  return (
    <div className="canvas-transform" ref={root}>
      <svg
        className="transform-outline"
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <g
          transform={`translate(${width / 2 + (tr.position_x || 0) * width} ${height / 2 + (tr.position_y || 0) * height}) rotate(${g.rotation})`}
        >
          <rect
            x={g.left}
            y={g.top}
            width={g.width}
            height={g.height}
            fill="none"
            stroke="#b8e695"
            strokeWidth="1.5"
            vectorEffect="non-scaling-stroke"
          />
        </g>
      </svg>
      <div
        className="transform-drag-area"
        role="button"
        tabIndex={disabled ? -1 : 0}
        aria-label="拖动画面移动位置"
        aria-disabled={disabled}
        onPointerDown={(event) => start(event, "move")}
        {...events}
        onKeyDown={(event) => {
          if (disabled || !event.key.startsWith("Arrow")) return;
          event.preventDefault();
          const next = {
            ...tr,
            position_x: Math.max(
              -2,
              Math.min(
                2,
                (tr.position_x || 0) +
                  (event.key === "ArrowRight"
                    ? 0.01
                    : event.key === "ArrowLeft"
                      ? -0.01
                      : 0),
              ),
            ),
            position_y: Math.max(
              -2,
              Math.min(
                2,
                (tr.position_y || 0) +
                  (event.key === "ArrowDown"
                    ? 0.01
                    : event.key === "ArrowUp"
                      ? -0.01
                      : 0),
              ),
            ),
          };
          void save(next);
        }}
      />
      {(locked
        ? ["nw", "ne", "sw", "se"]
        : ["nw", "ne", "sw", "se", "w", "e", "n", "s"]
      ).map((mode, i) => {
        const point = rotate(
          g.left +
            (mode.includes("e")
              ? g.width
              : mode.includes("w")
                ? 0
                : g.width / 2),
          g.top +
            (mode.includes("s")
              ? g.height
              : mode.includes("n")
                ? 0
                : g.height / 2),
          (tr.rotation_deg * Math.PI) / 180,
        );
        const x = 50 + (tr.position_x || 0) * 100 + (point.x / width) * 100,
          y = 50 + (tr.position_y || 0) * 100 + (point.y / height) * 100;
        return (
          <button
            key={mode}
            className={`transform-handle ${mode}`}
            style={{
              left:
                x >= 0 && x <= 100
                  ? `clamp(7px, ${x}%, calc(100% - 7px))`
                  : `${x}%`,
              top:
                y >= 0 && y <= 100
                  ? `clamp(7px, ${y}%, calc(100% - 7px))`
                  : `${y}%`,
            }}
            disabled={disabled}
            aria-label={
              i < 4
                ? `拖动${["左上", "右上", "左下", "右下"][i]}角缩放画面`
                : `拖动${["左", "右", "上", "下"][i - 4]}边调整画面${i < 6 ? "宽度" : "高度"}`
            }
            onPointerDown={(event) => start(event, mode)}
            {...events}
            onKeyDown={(event) => {
              if (event.key.startsWith("Arrow")) {
                event.preventDefault();
                const delta =
                  event.key === "ArrowUp" || event.key === "ArrowRight"
                    ? 0.05
                    : -0.05;
                const next = { ...tr };
                if (locked)
                  next.scale = Math.max(0.1, Math.min(4, tr.scale + delta));
                else {
                  if (mode.includes("w") || mode.includes("e"))
                    next.scale_x = Math.max(
                      0.1,
                      Math.min(4, (tr.scale_x ?? 1) + delta),
                    );
                  if (mode.includes("n") || mode.includes("s"))
                    next.scale_y = Math.max(
                      0.1,
                      Math.min(4, (tr.scale_y ?? 1) + delta),
                    );
                }
                void save(next);
              }
            }}
          />
        );
      })}
      <div
        className="transform-toolbar"
        onPointerDown={(event) => event.stopPropagation()}
      >
        <Button
          tone="ghost"
          aria-label="锁定画面比例"
          aria-pressed={locked}
          disabled={disabled}
          title={locked ? "点击解锁，可独立调整宽高" : "点击锁定当前宽高比例"}
          onClick={() => setLocked(!locked)}
        >
          {locked ? "锁定比例" : "自由拉伸"}
        </Button>
        {(locked
          ? (["scale"] as const)
          : (["scale_x", "scale_y"] as const)
        ).map((key) => (
          <label className="transform-scale-control" key={key}>
            <span>
              {key === "scale" ? "缩放" : key === "scale_x" ? "宽" : "高"}
            </span>
            <input
              type="range"
              min="0.1"
              max="4"
              step="0.01"
              aria-label={
                key === "scale"
                  ? "预览画面缩放"
                  : key === "scale_x"
                    ? "预览画面宽度拉伸"
                    : "预览画面高度拉伸"
              }
              value={tr[key] ?? 1}
              disabled={disabled}
              onPointerDown={() => onPause()}
              onChange={(event) =>
                onDraft({
                  clip_id: clip.clip_id,
                  transform: { ...tr, [key]: Number(event.target.value) },
                })
              }
              onPointerUp={(event) =>
                void save({ ...tr, [key]: Number(event.currentTarget.value) })
              }
              onKeyUp={(event) => {
                if (
                  event.key.startsWith("Arrow") ||
                  event.key === "Home" ||
                  event.key === "End"
                )
                  void save({
                    ...tr,
                    [key]: Number(event.currentTarget.value),
                  });
              }}
              onPointerCancel={() => onDraft(null)}
            />
            <output>{Math.round((tr[key] ?? 1) * 100)}%</output>
          </label>
        ))}
        {!locked && (
          <Button
            tone="ghost"
            disabled={disabled}
            onClick={() => void save({ ...tr, scale_x: 1, scale_y: 1 })}
          >
            还原比例
          </Button>
        )}
        <Button
          tone="ghost"
          disabled={disabled}
          onClick={() => void save({ ...tr, position_x: 0, position_y: 0 })}
        >
          居中
        </Button>
      </div>
    </div>
  );
}
