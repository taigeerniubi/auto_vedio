// 展示可选择、拖动排序和定位播放位置的多轨时间线。
import { useEffect, useRef, useState, type CSSProperties } from "react";
import type { Editor } from "../hooks/useEditor";
import { thumbnailUrl } from "../api";
import { anchorRange, audioLanes, layout, timecode } from "../timeline";
import { Button, Icon } from "./Ui";
import { transitionLabel } from "../../../../packages/composition/transitionPresets";

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
  changedIds,
  onCover,
  onScrubStart,
}: {
  editor: Editor;
  frame: number;
  seek: (frame: number) => void;
  changedIds: string[];
  onCover: () => void;
  onScrubStart?: () => void;
}) {
  const [zoom, setZoom] = useState(1);
  const scroll = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLDivElement>(null);
  const scrubPointer = useRef<number | null>(null);
  const [scrubbing, setScrubbing] = useState(false);
  const [scrollLeft, setScrollLeft] = useState(0);
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
  const musicLanes = audioLanes(snapshot);
  const px = (Math.max(300, available - 40) / Math.max(total, 150)) * zoom;
  const width = Math.max(available, total * px + 40);
  const lastFrame = Math.max(0, total - 1);
  const currentFrame = Math.min(lastFrame, Math.max(0, Math.round(frame)));
  const playheadX = currentFrame * px;
  const seconds = (currentFrame / 30).toFixed(2);
  const tickFrames = [1, 2, 3, 5, 10, 15, 30, 60, 90, 150, 300, 450, 900, 1800]
    .find((step) => step * px >= 72) || Math.ceil(72 / px / 1800) * 1800;
  const minorFrames = tickFrames % 5 === 0 ? tickFrames / 5 : tickFrames / 2;
  const showMinorTicks = Number.isInteger(minorFrames) && minorFrames * px >= 10;
  const badgeWidth = 96;
  const badgeLeft = Math.min(
    Math.max(scrollLeft + 4, playheadX - badgeWidth / 2),
    scrollLeft + Math.max(4, available - badgeWidth - 4),
  ) - playheadX;
  useEffect(() => {
    const el = scroll.current;
    if (!e.liveDraft || !el) return;
    const point = frame * px;
    if (point < el.scrollLeft || point > el.scrollLeft + el.clientWidth - 4)
      el.scrollLeft = Math.max(0, point - el.clientWidth / 2);
  }, [frame, px, e.liveDraft?.seq]);
  const disabled = !!e.busy || !e.project;
  const selected = rows.find((r) => r.clip.clip_id === e.selected);
  const selectedAudio = snapshot?.audio_items.find(item => item.audio_id === e.selected);
  const split = () =>
    selected &&
    e.edit("split_clip", {
      clip_id: e.selected,
      local_frame: frame - selected.start,
    });

  // 使用画布坐标计算拖动位置，横向滚动和缩放后仍能准确定位到帧。
  function seekAt(clientX: number) {
    if (!canvas.current || total === 0) return;
    const position = clientX - canvas.current.getBoundingClientRect().left;
    seek(Math.min(lastFrame, Math.max(0, Math.round(position / px))));
  }

  function startScrub(event: React.PointerEvent<HTMLElement>) {
    if (total === 0 || event.button !== 0 || scrubPointer.current !== null) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.focus({ preventScroll: true });
    event.currentTarget.setPointerCapture(event.pointerId);
    scrubPointer.current = event.pointerId;
    setScrubbing(true);
    onScrubStart?.();
    seekAt(event.clientX);
  }

  function moveScrub(event: React.PointerEvent<HTMLElement>) {
    if (scrubPointer.current !== event.pointerId) return;
    event.preventDefault();
    seekAt(event.clientX);
  }

  function endScrub(event: React.PointerEvent<HTMLElement>) {
    if (scrubPointer.current !== event.pointerId) return;
    seekAt(event.clientX);
    scrubPointer.current = null;
    setScrubbing(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
  }

  function cancelScrub(event: React.PointerEvent<HTMLElement>) {
    if (scrubPointer.current !== event.pointerId) return;
    scrubPointer.current = null;
    setScrubbing(false);
  }

  function seekWithKeyboard(event: React.KeyboardEvent<HTMLElement>) {
    if (total === 0) return;
    const step = event.shiftKey ? 30 : 1;
    const targets: Record<string, number> = {
      ArrowLeft: currentFrame - step,
      ArrowRight: currentFrame + step,
      ArrowDown: currentFrame - step,
      ArrowUp: currentFrame + step,
      Home: 0,
      End: lastFrame,
      PageDown: currentFrame - 30,
      PageUp: currentFrame + 30,
    };
    if (!(event.key in targets)) return;
    event.preventDefault();
    event.stopPropagation();
    onScrubStart?.();
    seek(Math.min(lastFrame, Math.max(0, targets[event.key])));
  }

  const scrubHandlers = {
    onPointerDown: startScrub,
    onPointerMove: moveScrub,
    onPointerUp: endScrub,
    onPointerCancel: cancelScrub,
    onLostPointerCapture: cancelScrub,
    onKeyDown: seekWithKeyboard,
  };
  const sliderValues = {
    "aria-valuemin": 0,
    "aria-valuemax": lastFrame,
    "aria-valuenow": currentFrame,
    "aria-valuetext": `${seconds} 秒，第 ${currentFrame} 帧，共 ${(total / 30).toFixed(2)} 秒`,
    "aria-orientation": "horizontal" as const,
  };

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
      if (asset) {
        const left = scroll.current?.firstElementChild?.getBoundingClientRect().left || 0;
        void e.addAsset(asset, before, Math.round((event.clientX - left) / px));
      }
    } else if (cid && cid !== before)
      void e.edit("move_clip", { clip_id: cid, before_clip_id: before });
  }

  return (
    <section id="workspace-timeline" className="timeline panel" aria-label="时间线">
      <div className="timeline-toolbar">
        <div className="row">
          <h2>时间线</h2>
          {e.liveDraft && <span className="live-label">实时草稿</span>}
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
            aria-label={selectedAudio ? "删除选中音频" : "删除选中片段"}
            title={selectedAudio ? "删除选中音频（Delete）" : "删除选中片段（Delete）"}
            disabled={disabled || (!selected && !selectedAudio)}
            onClick={() => void e.deleteSelected()}
          />
          <span className="divider" />
          <Button
            icon="image"
            className="timeline-cover-button"
            title="设置视频封面"
            aria-haspopup="dialog"
            disabled={disabled}
            onClick={onCover}
          >
            封面
          </Button>
          <span className="timeline-time" title="当前时间 / 总时长 · 分:秒.帧（30 fps）" aria-label={`当前 ${seconds} 秒，总时长 ${(total / 30).toFixed(2)} 秒`}>
            <strong>{timecode(currentFrame)}</strong>
            <span>/ {timecode(total)}</span>
          </span>
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
      <div className="tracks-shell" style={{ "--music-lanes": musicLanes.length } as CSSProperties}>
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
          {musicLanes.map((lane, index) => (
            <div key={index} title={`音轨 ${lane.track} · 重叠片段分行显示`}>
              <Icon name="music" />
              配乐 {index + 1}
            </div>
          ))}
        </div>
        <div
          className="track-scroll"
          ref={scroll}
          onScroll={(event) => setScrollLeft(event.currentTarget.scrollLeft)}
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => drop(event)}
        >
          <div className={`track-canvas${scrubbing ? " is-scrubbing" : ""}`} ref={canvas} style={{ width }}>
            <div
              className="ruler"
              aria-label="时间标尺"
              role="slider"
              tabIndex={total > 0 ? 0 : -1}
              aria-disabled={total === 0}
              {...sliderValues}
              {...scrubHandlers}
            >
              {Array.from(
                { length: Math.floor(total / tickFrames) + 1 },
                (_, i) => (
                  <span className={`ruler-tick${i === 0 ? " first" : ""}`} key={i} style={{ left: i * tickFrames * px }}>
                    <span>{tickFrames < 30 ? timecode(i * tickFrames) : timecode(i * tickFrames).slice(0, 5)}</span>
                    <i />
                  </span>
                ),
              )}
              {showMinorTicks && Array.from(
                { length: Math.floor(total / minorFrames) + 1 },
                (_, i) => i * minorFrames % tickFrames !== 0 && (
                  <i className="ruler-minor-tick" key={i} style={{ left: i * minorFrames * px }} />
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
                    data-changed={changedIds.includes(item.text_id)}
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
                    data-changed={changedIds.includes(clip.clip_id)}
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
              {snapshot?.transitions.map(transition => {
                const incoming = rows.find(row => row.clip.clip_id === transition.right_clip_id);
                if (!incoming) return null;
                return <span key={transition.right_clip_id} className="transition-block"
                  aria-label={`${transitionLabel(transition.preset_id)}转场，${transition.duration_frames} 帧`}
                  title={`${transitionLabel(transition.preset_id)} · ${(transition.duration_frames / 30).toFixed(2)} 秒`}
                  style={{left: incoming.start * px, width: transition.duration_frames * px}}>{transitionLabel(transition.preset_id)}</span>;
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
            {musicLanes.map((lane, index) => (
              <div className="track music-track" key={index} aria-label={`配乐行 ${index + 1}`}>
                {lane.items.map(({ item, start, end }) => {
                  const name = e.assets.find(a => a.asset_id === item.asset_id)?.name || "音频";
                  return (
                    <button
                      key={item.audio_id}
                      className={`music-block ${e.selected === item.audio_id ? "selected" : ""}`}
                      data-changed={changedIds.includes(item.audio_id)}
                      style={{ left: start * px, width: Math.max(1, (end - start) * px - 2) }}
                      title={`${name} · ${timecode(start)} – ${timecode(end)} · 选中后按 Delete 移除`}
                      aria-label={`音频 ${name}，${timecode(start)} 至 ${timecode(end)}`}
                      aria-pressed={e.selected === item.audio_id}
                      onClick={() => {
                        e.setSelected(item.audio_id);
                        seek(start);
                      }}
                    >
                      ♫ {name}
                    </button>
                  );
                })}
              </div>
            ))}
            {total > 0 && (
              <div className="playhead" style={{ left: playheadX }}>
                <button
                  type="button"
                  className="playhead-handle"
                  role="slider"
                  aria-label="播放头"
                  title="拖动预览 · 方向键逐帧 · Shift + 方向键移动一秒"
                  {...sliderValues}
                  {...scrubHandlers}
                />
                <span className="playhead-time" style={{ left: badgeLeft, visibility: playheadX < scrollLeft || playheadX > scrollLeft + available ? "hidden" : "visible" }}>
                  {seconds} 秒
                </span>
              </div>
            )}
          </div>
        </div>
      </div>
      <footer className="timeline-footer">
        <span>{rows.length} 个片段　·　{snapshot?.audio_items.length || 0} 段配乐　·　30 fps</span>
        <span>{e.liveDraft ? "拖动标尺或播放头预览草稿" : "拖动标尺或播放头预览　·　Delete 删除　·　Ctrl Z 撤销"}</span>
      </footer>
    </section>
  );
}
