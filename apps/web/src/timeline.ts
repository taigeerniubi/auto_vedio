// 计算浏览器时间线的位置、片段时长和帧时间码。
import type { Clip, Snapshot, Anchor } from "./contracts";

/** 计算片段经变速后的输出帧数。
 * Args:
 *   clip: 工程片段。
 * Returns:
 *   输出帧数。
 */
export const duration = (clip: Clip) =>
  clip.kind === "image"
    ? clip.still_duration_frames || 0
    : Math.ceil(
        (((clip.source_out_frame || 0) - clip.source_in_frame) *
          clip.rate_den) /
          clip.rate_num,
      );

/** 将片段排布到含转场重叠的时间线上。
 * Args:
 *   snapshot: 工程快照。
 * Returns:
 *   片段起止位置和时间线总帧数。
 */
export function layout(snapshot: Snapshot) {
  let cursor = 0;
  const rows = snapshot.clips.map((clip, i) => {
    if (i)
      cursor -=
        snapshot.transitions.find(
          (t) =>
            t.left_clip_id === snapshot.clips[i - 1].clip_id &&
            t.right_clip_id === clip.clip_id,
        )?.duration_frames || 0;
    const start = cursor;
    cursor += duration(clip);
    return { clip, start, end: cursor };
  });
  return { rows, total: cursor };
}

/** 将帧数显示为分钟、秒和帧。
 * Args:
 *   frame: 非负帧位置。
 * Returns:
 *   三十帧制时间码。
 */
export function timecode(frame: number) {
  const f = Math.max(0, Math.floor(frame));
  return `${String(Math.floor(f / 1800)).padStart(2, "0")}:${String(Math.floor(f / 30) % 60).padStart(2, "0")}.${String(f % 30).padStart(2, "0")}`;
}

/** 取得效果锚点在主时间线中的位置。
 * Args:
 *   anchor: 效果锚点。
 *   snapshot: 当前工程。
 * Returns:
 *   绝对起止帧。
 */
export function anchorRange(anchor: Anchor, snapshot: Snapshot) {
  const offset =
    anchor.kind === "clip"
      ? layout(snapshot).rows.find((r) => r.clip.clip_id === anchor.clip_id)
          ?.start || 0
      : 0;
  return [offset + anchor.start_frame, offset + anchor.end_frame];
}
