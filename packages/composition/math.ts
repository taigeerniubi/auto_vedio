// 计算共享视频合成中的画面几何、运镜和文字时间区间。
export type Motion = {
  preset_id: 'slow_push_in' | 'slow_pull_out' | 'pan_left' | 'pan_right' | 'pan_up' | 'pan_down';
  local_start_frame: number; local_end_frame: number; intensity: number;
  anchor_x: number; anchor_y: number; easing: 'linear' | 'ease_in_out';
};
export type Transform = {fit: 'cover' | 'contain'; anchor_x: number; anchor_y: number; scale: number; rotation_deg: number};
export type PreparedClip = {
  clip_id: string; asset_id: string; kind: 'video' | 'image'; file: string;
  start_frame: number; end_frame: number; duration_frames: number; width: number; height: number;
  transform: Transform; motion: Motion | null; color_preset: 'warm' | 'cool' | 'monochrome' | null;
  sourcePlayback?: {inFrame: number; outFrame: number; numerator: number; denominator: number; reverse: boolean};
  previewPlayback?: {trimBefore: number; playbackRate: number};
};
export type Plan = {
  composition: {width: number; height: number; fps: number};
  total_frames: number; clips: PreparedClip[]; mix_file: string;
  transitions: {left_clip_id: string; right_clip_id: string; duration_frames: number}[];
  text_items: {text_id: string; text: string; x: number; y: number; font_size: number; color: string;
    style_preset: 'title' | 'subtitle'; anchor: {kind: 'timeline' | 'clip'; clip_id: string | null; start_frame: number; end_frame: number}}[];
};
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/**
 * 计算预览与导出共用的画面变换，保证运镜覆盖视口。
 *
 * Args:
 *   clip: 已准备的素材片段。
 *   frame: 片段内帧位置。
 *   width: 画布宽度。
 *   height: 画布高度。
 *
 * Returns:
 *   素材显示尺寸、位置及旋转角度。
 */
export function geometry(clip: PreparedClip, frame: number, width: number, height: number) {
  const tr = clip.transform;
  const angle = tr.rotation_deg * Math.PI / 180;
  const viewWidth = Math.abs(width * Math.cos(angle)) + Math.abs(height * Math.sin(angle));
  const viewHeight = Math.abs(height * Math.cos(angle)) + Math.abs(width * Math.sin(angle));
  const base = tr.fit === 'cover' ? Math.max(viewWidth / clip.width, viewHeight / clip.height)
    : Math.min(width / clip.width, height / clip.height);
  let zoom = tr.scale, ax = tr.anchor_x, ay = tr.anchor_y, p = 0;
  const m = clip.motion;
  if (m) {
    p = clamp((frame - m.local_start_frame) / Math.max(1, m.local_end_frame - m.local_start_frame - 1), 0, 1);
    if (m.easing === 'ease_in_out') p = p * p * (3 - 2 * p);
    ax = m.anchor_x; ay = m.anchor_y;
    if (m.preset_id === 'slow_push_in') zoom *= 1 + m.intensity * p;
    else if (m.preset_id === 'slow_pull_out') zoom *= 1 + m.intensity * (1 - p);
    else zoom *= 1 + m.intensity;
  }
  const iw = clip.width * base * zoom, ih = clip.height * base * zoom;
  if (tr.fit === 'cover') {
    const minX = viewWidth / (2 * iw), minY = viewHeight / (2 * ih);
    ax = clamp(ax, minX, 1 - minX); ay = clamp(ay, minY, 1 - minY);
    if (m?.preset_id.startsWith('pan_')) {
      const axis = m.preset_id === 'pan_left' || m.preset_id === 'pan_right' ? 'x' : 'y';
      const progress = m.preset_id === 'pan_left' || m.preset_id === 'pan_up' ? 1 - p : p;
      if (axis === 'x') ax = minX + (1 - 2 * minX) * progress;
      else ay = minY + (1 - 2 * minY) * progress;
    }
  }
  return {width: iw, height: ih, left: -ax * iw, top: -ay * ih, rotation: tr.rotation_deg};
}

export function textRange(item: Plan['text_items'][number], plan: Plan) {
  const offset = item.anchor.kind === 'clip' ? plan.clips.find(c => c.clip_id === item.anchor.clip_id)!.start_frame : 0;
  return [offset + item.anchor.start_frame, offset + item.anchor.end_frame];
}
