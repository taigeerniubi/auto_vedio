// 选择相邻镜头的转场类型、方向和时长，并复用同一个确定性工具保存。
import { lazy, Suspense, useEffect, useState } from "react";
import { TRANSITION_FAMILIES as families, TRANSITION_PRESETS, transitionLabel, type TransitionPresetId } from "../../../../packages/composition/transitionPresets";
import type { Editor } from "../hooks/useEditor";
import { duration, layout } from "../timeline";
import { Button } from "./Ui";
const TransitionDemo = lazy(() => import('./TransitionDemo').then(module => ({default: module.TransitionDemo})));

export function TransitionsPanel({ editor: e, onPreview }: { editor: Editor; onPreview: (frame: number) => void }) {
  const snapshot = e.project?.snapshot;
  const clip = snapshot?.clips.find(item => item.clip_id === e.selected);
  const next = clip && snapshot?.clips[snapshot.clips.indexOf(clip) + 1];
  const current = snapshot?.transitions.find(item => item.left_clip_id === clip?.clip_id && item.right_clip_id === next?.clip_id);
  const before = snapshot?.transitions.find(item => item.right_clip_id === clip?.clip_id)?.duration_frames || 0;
  const after = snapshot?.transitions.find(item => item.left_clip_id === next?.clip_id)?.duration_frames || 0;
  const maxFrames = clip && next ? Math.max(0, Math.min(duration(clip) - before - 1, duration(next) - after - 1)) : 0;
  const [preset, setPreset] = useState<TransitionPresetId>("dissolve");
  const [frames, setFrames] = useState(12);
  const [hovered, setHovered] = useState<string | null>(null);
  const [focused, setFocused] = useState<string | null>(null);
  const previewing = hovered || focused;
  useEffect(() => {
    setPreset(current?.preset_id || "dissolve");
    setFrames(current?.duration_frames || (maxFrames ? Math.min(12, maxFrames) : 12));
  }, [e.project?.project_id, e.project?.revision, e.selected, maxFrames]);
  const family = TRANSITION_PRESETS[preset].family;
  const disabled = !!e.busy || !clip || !next;
  const selectedFamily = families.find(item => item.id === family)!;
  async function apply(remove = false) {
    if (!snapshot || !clip || !next || disabled || (!remove && maxFrames < 1)) return;
    const ok = await e.edit("set_transition", {
      left_clip_id: clip.clip_id, right_clip_id: next.clip_id,
      preset_id: preset, duration_frames: remove ? 0 : frames,
    });
    if (ok && !remove) {
      const row = layout(snapshot).rows.find(item => item.clip.clip_id === clip.clip_id)!;
      onPreview(row.end - frames + Math.floor(frames / 2));
    }
  }
  return <div className="effect-content transition-content">
    <div className="transition-catalog-count">{families.length} 类 <span>·</span> {Object.keys(TRANSITION_PRESETS).length} 个预设</div>
    <p className="muted">悬停或聚焦试看，点击选择。{next ? "应用后才修改工程。" : "应用前请选择有下一镜头的片段。"}</p>
    <div className="transition-presets">
      {families.map(item => {
        const cardPreset = family === item.id ? preset : (item.directional ? `${item.id}_left` : item.id) as TransitionPresetId;
        return <button key={item.id} type="button" className="transition-card" aria-label={item.label}
          aria-pressed={family === item.id} onMouseEnter={() => setHovered(item.id)}
          onMouseLeave={() => setHovered(value => value === item.id ? null : value)}
          onFocus={() => setFocused(item.id)} onBlur={() => setFocused(value => value === item.id ? null : value)}
          onClick={() => setPreset(cardPreset)}>
          <Suspense fallback={<div className="transition-demo">{previewing === item.id && <span className="transition-demo-status">准备预览…</span>}</div>}>
            <TransitionDemo preset={cardPreset} active={previewing === item.id}/>
          </Suspense>
          <span className="transition-card-label"><strong>{item.label}</strong>{item.directional && <small>4 向</small>}</span>
        </button>;
      })}
    </div>
    <p className="muted">{selectedFamily.description}</p>
    <form onSubmit={event => { event.preventDefault(); void apply(); }}>
      {selectedFamily.directional && <label className="transition-direction">运动方向
        <select aria-label="转场方向" value={preset.split("_").at(-1)}
          onChange={event => setPreset(`${family}_${event.target.value}` as TransitionPresetId)}>
          <option value="left">向左</option><option value="right">向右</option>
          <option value="up">向上</option><option value="down">向下</option>
        </select>
      </label>}
      <label><span className="transition-duration">转场时长 <output>{(frames / 30).toFixed(2)} 秒</output></span>
        <input aria-label="转场时长" type="range" min="1" max={Math.max(1, maxFrames)} step="1" value={frames}
          disabled={disabled || maxFrames < 1} onChange={event => setFrames(Number(event.target.value))} />
      </label>
      <div className="transition-actions">
        <Button type="submit" tone="primary" disabled={disabled || maxFrames < 1}>应用到下一片段</Button>
        <Button disabled={disabled || !current} onClick={() => void apply(true)}>移除转场</Button>
      </div>
    </form>
    {current && <small>已应用：{transitionLabel(current.preset_id)} · {(current.duration_frames / 30).toFixed(2)} 秒 · 可撤销</small>}
    {next && maxFrames < 1 ? <small>相邻镜头没有剩余时长，请先延长镜头或缩短其他转场。</small>
      : !current && <small>应用后定位到转场中间；可播放查看，也可撤销。</small>}
  </div>;
}
