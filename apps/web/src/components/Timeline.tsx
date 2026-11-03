// 展示可选择、拖动排序和定位播放位置的多轨时间线。
import { useEffect, useRef, useState } from "react";
import type { Editor } from "../hooks/useEditor";
import { thumbnailUrl } from "../api";
import { anchorRange, layout, timecode } from "../timeline";
import { Button, Icon } from "./Ui";

/** 根据真实工程状态绘制时间线并提供编辑动作。
 * Args:
 *   editor: 当前工程及编辑操作。
 *   frame: 播放帧。
 *   seek: 定位预览播放位置的回调。
 * Returns:
 *   带画面、文字和音频轨的时间线。
 */
export function Timeline({
  editor: e,
  frame,
  seek,
}: {
  editor: Editor;
  frame: number;
  seek: (frame: number) => void;
}) {
  const [zoom, setZoom] = useState(1);
  const scroll = useRef<HTMLDivElement>(null);
  const [available, setAvailable] = useState(800);
  useEffect(() => {
    if (!scroll.current) return;
    const observer = new ResizeObserver((entries) =>
      setAvailable(entries[0].contentRect.width),
    );
    observer.observe(scroll.current);
    return () => observer.disconnect();
  }, []);
  const snapshot = e.project?.snapshot;
  const { rows, total } = snapshot ? layout(snapshot) : { rows: [], total: 0 };
  const px = (Math.max(300, available - 40) / Math.max(total, 150)) * zoom;
  const width = Math.max(available, total * px + 40);
  const disabled = !!e.busy || !e.project;
  const selected = rows.find((r) => r.clip.clip_id === e.selected);
  const split = () =>
    selected &&
    e.edit("split_clip", {
      clip_id: e.selected,
      local_frame: frame - selected.start,
    });

  /** 处理素材添加或片段重排的拖放。
   * Args:
   *   event: 浏览器拖放事件。
   *   before: 可选的插入目标片段。
   */
  function drop(event: React.DragEvent, before: string | null = null) {
    event.preventDefault();
    event.stopPropagation();
    if (disabled) return;
    const aid = event.dataTransfer.getData("application/shiguang-asset");
    const cid = event.dataTransfer.getData("application/shiguang-clip");
    if (aid) {
      const asset = e.assets.find((a) => a.asset_id === aid);
      if (asset) void e.addAsset(asset, before);
    } else if (cid && cid !== before)
      void e.edit("move_clip", { clip_id: cid, before_clip_id: before });
  }

  return (
    <section className="timeline panel" aria-label="时间线">
      <div className="timeline-toolbar">
        <div className="row">
          <span className="tool-pointer">↖</span>
          <Button
            icon="cut"
            tone="ghost"
            aria-label="在播放头处分割"
            disabled={
              disabled ||
              !selected ||
              frame <= selected.start ||
              frame >= selected.end
            }
            onClick={() => void split()}
          />
          <Button
            icon="trash"
            tone="ghost"
            aria-label="删除选中片段"
            disabled={disabled || !selected}
            onClick={() => void e.edit("delete_clip", { clip_id: e.selected })}
          />
          <span className="divider" />
          <h2>时间线</h2>
          <span className="chip">{timecode(total)}</span>
        </div>
        <label className="zoom-control">
          <span>−</span>
          <input
            type="range"
            aria-label="时间线缩放"
            min="0.4"
            max="3"
            step="0.1"
            value={zoom}
            onChange={(event) => setZoom(Number(event.target.value))}
          />
          <span>＋</span>
        </label>
      </div>
      <div className="tracks-shell">
        <div className="track-labels">
          <div className="ruler-space" />
          <div>
            <Icon name="text" />
            文字
          </div>
          <div className="video-label">
            <Icon name="video" />
            画面
          </div>
          <div>
            <Icon name="music" />
            原声
          </div>
          <div>
            <Icon name="music" />
            配乐
          </div>
        </div>
        <div
          className="track-scroll"
          ref={scroll}
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => drop(event)}
        >
          <div className="track-canvas" style={{ width }}>
            <div
              className="ruler"
              aria-label="时间标尺"
              onClick={(event) => {
                const pos =
                  event.clientX -
                  event.currentTarget.getBoundingClientRect().left;
                seek(Math.min(Math.max(0, total - 1), Math.round(pos / px)));
              }}
            >
              {Array.from(
                { length: Math.ceil(width / (150 * px)) + 1 },
                (_, i) => (
                  <span key={i} style={{ left: i * 150 * px }}>
                    {timecode(i * 150).slice(0, 5)}
                    <i />
                  </span>
                ),
              )}
            </div>
            <div className="track text-track">
              {snapshot?.text_items.map((item) => {
                const [start, end] = anchorRange(item.anchor, snapshot);
                return (
                  <div
                    key={item.text_id}
                    className="text-block"
                    title={item.text}
                    style={{ left: start * px, width: (end - start) * px }}
                  >
                    T　{item.text}
                  </div>
                );
              })}
            </div>
            <div className="track video-track">
              {rows.map(({ clip, start, end }, index) => {
                const asset = e.assets.find(
                  (a) => a.asset_id === clip.asset_id,
                );
                return (
                  <button
                    key={clip.clip_id}
                    className={`clip-block ${e.selected === clip.clip_id ? "selected" : ""}`}
                    style={{
                      left: start * px,
                      width: Math.max(10, (end - start) * px - 2),
                    }}
                    title={`${asset?.name || "画面"} · 拖动排序`}
                    aria-label={`片段 ${index + 1} ${asset?.name || ""}`}
                    aria-pressed={e.selected === clip.clip_id}
                    draggable={!disabled}
                    onDragStart={(event) =>
                      event.dataTransfer.setData(
                        "application/shiguang-clip",
                        clip.clip_id,
                      )
                    }
                    onDragOver={(event) => event.preventDefault()}
                    onDrop={(event) => drop(event, clip.clip_id)}
                    onClick={() => {
                      e.setSelected(clip.clip_id);
                      seek(start);
                    }}
                  >
                    <div
                      className="filmstrip"
                      style={{
                        backgroundImage: `url("${thumbnailUrl(e.project!.project_id, clip.asset_id)}")`,
                      }}
                    />
                    <span>
                      {String(index + 1).padStart(2, "0")} · {asset?.name}
                    </span>
                  </button>
                );
              })}
              {!rows.length && (
                <div className="timeline-empty">
                  将素材拖到这里，或点击素材上的 ＋
                </div>
              )}
            </div>
            <div className="track original-track">
              {rows
                .filter(
                  (r) =>
                    e.assets.find((a) => a.asset_id === r.clip.asset_id)
                      ?.has_audio,
                )
                .map(({ clip, start, end }) => (
                  <div
                    className="original-block"
                    key={clip.clip_id}
                    style={{ left: start * px, width: (end - start) * px - 2 }}
                  >
                    原声 · {clip.original_audio_gain_db} dB
                  </div>
                ))}
            </div>
            <div className="track music-track">
              {snapshot?.audio_items.map((item) => {
                const [start, end] = anchorRange(item.anchor, snapshot);
                return (
                  <div
                    key={item.audio_id}
                    className="music-block"
                    style={{ left: start * px, width: (end - start) * px }}
                    title={
                      e.assets.find((a) => a.asset_id === item.asset_id)?.name
                    }
                  >
                    ♫ {e.assets.find((a) => a.asset_id === item.asset_id)?.name}
                  </div>
                );
              })}
            </div>
            {total > 0 && (
              <div className="playhead" style={{ left: frame * px }}>
                <span />
              </div>
            )}
          </div>
        </div>
      </div>
      <footer className="timeline-footer">
        <span>{rows.length} 个片段　·　30 fps</span>
        <span>空格 播放 / 暂停　　Ctrl Z 撤销</span>
      </footer>
    </section>
  );
}
