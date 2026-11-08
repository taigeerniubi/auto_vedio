// 编辑选帧或上传图片封面，预览和导出使用同一个合成组件。
import { Thumbnail } from "@remotion/player";
import { useEffect, useMemo, useRef, useState } from "react";
import { CoverComposition } from "../../../../packages/composition/Cover";
import type { Plan } from "../../../../packages/composition/math";
import type { Asset, Cover, Preview } from "../contracts";
import type { Editor } from "../hooks/useEditor";
import { api, post, projectPath } from "../api";
import { layout, timecode } from "../timeline";
import { Button, Modal } from "./Ui";
import { ColorSlider } from "./ColorControls";

export function CoverPanel({
  editor: e,
  frame,
  onClose,
}: {
  editor: Editor;
  frame: number;
  onClose: () => void;
}) {
  const p = e.project!,
    total = layout(p.snapshot).total;
  const initial = (): Cover =>
    p.snapshot.cover || {
      source: "timeline",
      frame: Math.min(frame, Math.max(0, total - 1)),
      asset_id: null,
      title: "",
      x: 0.5,
      y: 0.75,
      font_size: 72,
      color: "#ffffff",
      shade: 0.25,
      fit: "cover",
    };
  const [cover, setCover] = useState<Cover>(initial),
    [preview, setPreview] = useState<Preview | null>(null),
    [dirty, setDirty] = useState(!p.snapshot.cover);
  const [exported, setExported] = useState<{
    download_url: string;
    width: number;
    height: number;
    revision: number;
  } | null>(null);
  const upload = useRef<HTMLInputElement>(null);
  useEffect(() => {
    setCover(initial());
    setDirty(!p.snapshot.cover);
    setExported(null);
  }, [p.project_id, p.revision]);
  useEffect(() => {
    let active = true;
    setPreview(null);
    if (total)
      post<Preview>(`${projectPath(p.project_id)}/preview`, {
        expected_revision: p.revision,
      })
        .then((value) => {
          if (active) setPreview(value);
        })
        .catch((error) => {
          if (active) e.report(error);
        });
    return () => {
      active = false;
    };
  }, [p.project_id, p.revision, total]);
  const set = (patch: Partial<Cover>) => {
    setCover((value) => ({ ...value, ...patch }));
    setDirty(true);
    setExported(null);
  };
  const imageAsset = e.assets.find(
    (a) => a.asset_id === cover.asset_id && a.kind === "image",
  );
  const plan = useMemo<Plan>(
    () =>
      preview?.plan || {
        composition: p.snapshot.composition,
        total_frames: Math.max(1, total),
        clips: [],
        text_items: [],
        transitions: [],
        mix_file: "",
      },
    [preview, p.snapshot.composition, total],
  );
  const inputProps = useMemo(
    () => ({
      plan,
      cover,
      image: imageAsset
        ? {
            file: `${projectPath(p.project_id)}/assets/${imageAsset.asset_id}/file`,
            width: imageAsset.width,
            height: imageAsset.height,
          }
        : undefined,
    }),
    [plan, cover, imageAsset, p.project_id],
  );
  const ready = cover.source === "image" ? !!imageAsset : !!preview;
  async function importImage(file?: File) {
    if (!file) return;
    await e.task("导入封面图片", async () => {
      const asset = await api<Asset>(
        `${projectPath(p.project_id)}/assets/upload?filename=${encodeURIComponent(file.name)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/octet-stream" },
          body: file,
        },
      );
      await e.refresh(p.project_id);
      set({ source: "image", asset_id: asset.asset_id });
    });
  }
  return (
    <Modal title="设置封面" onClose={onClose}>
      <div className="cover-editor">
        <div className="color-subtabs" role="group" aria-label="封面来源">
          <button
            aria-pressed={cover.source === "timeline"}
            disabled={!total || !!e.busy}
            onClick={() =>
              set({
                source: "timeline",
                asset_id: null,
                frame: Math.min(cover.frame, total - 1),
              })
            }
          >
            视频选帧
          </button>
          <button
            aria-pressed={cover.source === "image"}
            disabled={!!e.busy}
            onClick={() =>
              set({
                source: "image",
                asset_id:
                  e.assets.find((a) => a.kind === "image")?.asset_id || null,
              })
            }
          >
            使用图片
          </button>
        </div>
        <div
          className="cover-preview"
          style={{
            width: Math.min(
              720,
              (320 * plan.composition.width) / plan.composition.height,
            ),
            aspectRatio: `${plan.composition.width}/${plan.composition.height}`,
          }}
        >
          {ready ? (
            <Thumbnail
              component={CoverComposition}
              inputProps={inputProps}
              frameToDisplay={cover.source === "timeline" ? cover.frame : 0}
              durationInFrames={Math.max(1, plan.total_frames)}
              compositionWidth={plan.composition.width}
              compositionHeight={plan.composition.height}
              fps={30}
              style={{ width: "100%", height: "100%" }}
            />
          ) : (
            <span>
              {cover.source === "timeline"
                ? "正在准备封面画面…"
                : "上传图片，或选择项目中的图片"}
            </span>
          )}
        </div>
        {cover.source === "timeline" ? (
          <div className="cover-frame-controls">
            <label>
              封面帧 · {timecode(cover.frame)}
              <input
                type="range"
                aria-label="封面选帧"
                min={0}
                max={Math.max(0, total - 1)}
                step={1}
                value={cover.frame}
                disabled={!!e.busy || !total}
                onChange={(event) => set({ frame: Number(event.target.value) })}
              />
            </label>
            <div className="row">
              <Button
                tone="ghost"
                disabled={!total || !!e.busy}
                onClick={() => set({ frame: Math.max(0, cover.frame - 1) })}
              >
                前一帧
              </Button>
              <Button
                tone="ghost"
                disabled={!total || !!e.busy}
                onClick={() => set({ frame: Math.min(total - 1, frame) })}
              >
                使用播放头位置
              </Button>
              <Button
                tone="ghost"
                disabled={!total || !!e.busy}
                onClick={() =>
                  set({ frame: Math.min(total - 1, cover.frame + 1) })
                }
              >
                后一帧
              </Button>
            </div>
          </div>
        ) : (
          <div className="cover-upload-controls">
            <input
              ref={upload}
              type="file"
              accept=".png,.jpg,.jpeg"
              aria-label="上传封面图片文件"
              hidden
              onChange={(event) => {
                void importImage(event.target.files?.[0]);
                event.target.value = "";
              }}
            />
            <Button
              icon="plus"
              disabled={!!e.busy}
              onClick={() => upload.current?.click()}
            >
              上传封面图片
            </Button>
            <select
              aria-label="选择项目封面图片"
              value={cover.asset_id || ""}
              disabled={!!e.busy}
              onChange={(event) =>
                set({ asset_id: event.target.value || null })
              }
            >
              <option value="">选择项目图片</option>
              {e.assets
                .filter((a) => a.kind === "image")
                .map((a) => (
                  <option key={a.asset_id} value={a.asset_id}>
                    {a.name}
                  </option>
                ))}
            </select>
            <select
              aria-label="封面图片适配"
              value={cover.fit}
              disabled={!!e.busy}
              onChange={(event) =>
                set({ fit: event.target.value as Cover["fit"] })
              }
            >
              <option value="cover">填满封面</option>
              <option value="contain">完整显示图片</option>
            </select>
          </div>
        )}
        <label>
          封面标题（可选）
          <textarea
            aria-label="封面标题"
            maxLength={120}
            rows={2}
            value={cover.title}
            disabled={!!e.busy}
            onChange={(event) => set({ title: event.target.value })}
          />
        </label>
        <div className="cover-adjustments">
          <ColorSlider
            label="标题横向位置"
            value={cover.x}
            min={0}
            max={1}
            disabled={!!e.busy}
            onChange={(x) => set({ x })}
          />
          <ColorSlider
            label="标题纵向位置"
            value={cover.y}
            min={0}
            max={1}
            disabled={!!e.busy}
            onChange={(y) => set({ y })}
          />
          <ColorSlider
            label="标题字号"
            value={cover.font_size}
            min={12}
            max={240}
            step={1}
            disabled={!!e.busy}
            onChange={(font_size) => set({ font_size })}
          />
          <ColorSlider
            label="背景压暗"
            value={cover.shade}
            min={0}
            max={1}
            disabled={!!e.busy}
            onChange={(shade) => set({ shade })}
          />
          <label>
            标题颜色
            <input
              aria-label="封面标题颜色"
              type="color"
              value={cover.color}
              disabled={!!e.busy}
              onChange={(event) => set({ color: event.target.value })}
            />
          </label>
        </div>
        <div className="cover-actions">
          <Button
            tone="ghost"
            disabled={!!e.busy || !p.snapshot.cover}
            onClick={async () => {
              if (await e.edit("set_cover", { cover: null })) onClose();
            }}
          >
            移除封面
          </Button>
          <Button
            tone="primary"
            disabled={!!e.busy || !ready || !dirty}
            onClick={async () => {
              if (await e.edit("set_cover", { cover })) {
                setDirty(false);
              }
            }}
          >
            保存封面
          </Button>
          <Button
            disabled={!!e.busy || dirty || !p.snapshot.cover}
            onClick={() =>
              void e.task("正在导出封面", async () => {
                setExported(
                  await post(`${projectPath(p.project_id)}/cover/export`, {
                    expected_revision: p.revision,
                  }),
                );
              })
            }
          >
            {e.busy === "正在导出封面" ? "导出中…" : "导出封面 PNG"}
          </Button>
        </div>
        {exported && (
          <a className="button primary" href={exported.download_url} download>
            下载封面 · {exported.width} × {exported.height}
          </a>
        )}
        <small className="muted">
          {dirty ? "预览尚未保存。" : "封面设置已保存。"}
          封面单独导出，不改变视频时长。
        </small>
      </div>
    </Modal>
  );
}
