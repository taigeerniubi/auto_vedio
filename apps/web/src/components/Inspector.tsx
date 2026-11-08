// 提供片段裁切、播放、构图、运镜和音量的属性编辑面板。
import type { Editor } from "../hooks/useEditor";
import { anchorRange, duration, timecode } from "../timeline";
import { Button, Icon } from "./Ui";
import { thumbnailUrl } from "../api";

/** 将选中片段的属性提交给后端确定性工具。
 * Args:
 *   editor: 工程状态与工具调用动作。
 * Returns:
 *   属性编辑表单及配乐管理区域。
 */
export function Inspector({ editor: e }: { editor: Editor }) {
  const snapshot = e.project?.snapshot;
  const clip = snapshot?.clips.find((c) => c.clip_id === e.selected);
  const audio = snapshot?.audio_items.find(item => item.audio_id === e.selected);
  const disabled = !!e.busy;
  const asset = e.assets.find((a) => a.asset_id === clip?.asset_id);
  const index =
    snapshot?.clips.findIndex((c) => c.clip_id === clip?.clip_id) ?? -1;
  return (
    <div className="inspector scroll-panel">
      {!clip && !audio && (
        <div className="empty-small">
          <Icon name="sliders" />
          <p>选择一个片段</p>
          <span>点击时间线上的画面或音频查看属性；选中后按 Delete 移除。</span>
        </div>
      )}
      {clip && (
        <div
          key={`${clip.clip_id}-${e.project!.revision}-${e.liveDraft?.seq ?? 0}`}
        >
          <div className="inspector-title">
            {asset && (
              <img
                alt=""
                src={thumbnailUrl(e.project!.project_id, asset.asset_id)}
              />
            )}
            <div>
              <h3 title={asset?.name}>{asset?.name}</h3>
              <small>
                {asset?.width} × {asset?.height}　·　
                {(duration(clip) / 30).toFixed(2)} 秒
              </small>
            </div>
          </div>
          <section>
            <h4>时长与裁切</h4>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                const values = new FormData(event.currentTarget);
                void e.edit("trim_clip", {
                  clip_id: clip.clip_id,
                  ...(clip.kind === "image"
                    ? {
                        still_duration_frames: Math.round(
                          Number(values.get("duration")) * 30,
                        ),
                      }
                    : {
                        source_in_frame: Math.round(
                          Number(values.get("start")) * 30,
                        ),
                        source_out_frame: Math.round(
                          Number(values.get("end")) * 30,
                        ),
                      }),
                });
              }}
            >
              <div className="form-grid">
                {clip.kind === "image" ? (
                  <label>
                    显示时长 / 秒
                    <input
                      aria-label="图片时长"
                      name="duration"
                      type="number"
                      min="0.034"
                      max="180"
                      step="any"
                      defaultValue={duration(clip) / 30}
                      required
                    />
                  </label>
                ) : (
                  <>
                    <label>
                      入点 / 秒
                      <input
                        aria-label="裁切入点"
                        name="start"
                        type="number"
                        min="0"
                        max={(asset?.total_frames || 0) / 30}
                        step="any"
                        defaultValue={clip.source_in_frame / 30}
                        required
                      />
                    </label>
                    <label>
                      出点 / 秒
                      <input
                        aria-label="裁切出点"
                        name="end"
                        type="number"
                        min="0.034"
                        max={(asset?.total_frames || 0) / 30}
                        step="any"
                        defaultValue={(clip.source_out_frame || 0) / 30}
                        required
                      />
                    </label>
                  </>
                )}
              </div>
              <Button type="submit" disabled={disabled}>
                应用裁切
              </Button>
            </form>
          </section>
          {clip.kind === "video" && (
            <section>
              <h4>播放速度</h4>
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  const values = new FormData(event.currentTarget);
                  const [num, den] = String(values.get("rate"))
                    .split("/")
                    .map(Number);
                  void e.edit("set_playback", {
                    clip_id: clip.clip_id,
                    rate_num: num,
                    rate_den: den,
                    reverse: values.get("reverse") === "on",
                  });
                }}
              >
                <div className="form-grid">
                  <label>
                    速度
                    <select
                      name="rate"
                      defaultValue={`${clip.rate_num}/${clip.rate_den}`}
                    >
                      <option value="1/2">0.5 ×</option>
                      <option value="1/1">1 ×</option>
                      <option value="3/2">1.5 ×</option>
                      <option value="2/1">2 ×</option>
                    </select>
                  </label>
                  <label className="check-label">
                    <input
                      name="reverse"
                      type="checkbox"
                      defaultChecked={clip.reverse}
                    />
                    倒放
                  </label>
                </div>
                <Button type="submit" disabled={disabled}>
                  应用播放设置
                </Button>
              </form>
            </section>
          )}
          <section>
            <h4>画面构图</h4>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                const values = new FormData(event.currentTarget);
                void e.edit("set_transform", {
                  clip_id: clip.clip_id,
                  transform: {
                    ...clip.transform,
                    fit: values.get("fit"),
                    scale: Number(values.get("scale")),
                    scale_x: Number(values.get("scale_x")),
                    scale_y: Number(values.get("scale_y")),
                    rotation_deg: Number(values.get("rotation")),
                    position_x: Number(values.get("position_x")),
                    position_y: Number(values.get("position_y")),
                  },
                });
              }}
            >
              <label>
                适配
                <select name="fit" defaultValue={clip.transform.fit}>
                  <option value="cover">填满画布</option>
                  <option value="contain">完整显示</option>
                </select>
              </label>
              <div className="form-grid">
                <label>
                  缩放
                  <input
                    name="scale"
                    aria-label="画面缩放"
                    type="number"
                    min="0.1"
                    max="4"
                    step="0.05"
                    defaultValue={clip.transform.scale}
                  />
                </label>
                <label>
                  旋转 / 度
                  <input
                    name="rotation"
                    type="number"
                    min="-15"
                    max="15"
                    step="1"
                    defaultValue={clip.transform.rotation_deg}
                  />
                </label>
                <label>水平位置<input name="position_x" aria-label="画面水平位置" type="number" min="-2" max="2" step="0.01" defaultValue={clip.transform.position_x||0}/></label>
                <label>宽度拉伸 / 倍<input name="scale_x" aria-label="画面宽度拉伸" type="number" min="0.1" max="4" step="0.05" defaultValue={clip.transform.scale_x ?? 1}/></label>
                <label>高度拉伸 / 倍<input name="scale_y" aria-label="画面高度拉伸" type="number" min="0.1" max="4" step="0.05" defaultValue={clip.transform.scale_y ?? 1}/></label>
                <label>垂直位置<input name="position_y" aria-label="画面垂直位置" type="number" min="-2" max="2" step="0.01" defaultValue={clip.transform.position_y||0}/></label>
              </div>
              <Button type="submit" disabled={disabled}>
                应用构图
              </Button>
            </form>
          </section>
          <section>
            <h4>镜头运动</h4>
            <select
              aria-label="镜头运动"
              disabled={disabled}
              value={clip.motion?.preset_id || ""}
              onChange={(event) =>
                void e.edit("apply_motion_preset", {
                  clip_id: clip.clip_id,
                  motion: event.target.value
                    ? {
                        preset_id: event.target.value,
                        local_end_frame: duration(clip),
                        intensity: 0.12,
                      }
                    : null,
                })
              }
            >
              <option value="">无运镜</option>
              <option value="slow_push_in">缓缓推进</option>
              <option value="slow_pull_out">缓缓拉远</option>
              <option value="pan_left">向左平移</option>
              <option value="pan_right">向右平移</option>
              <option value="pan_up">向上平移</option>
              <option value="pan_down">向下平移</option>
            </select>
          </section>
          {asset?.has_audio && (
            <section>
              <h4>片段原声</h4>
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  void e.edit("set_audio_gain", {
                    target: "original",
                    target_id: clip.clip_id,
                    gain_db: Number(
                      new FormData(event.currentTarget).get("gain"),
                    ),
                  });
                }}
              >
                <label>
                  音量 / dB
                  <input
                    aria-label="原声音量"
                    name="gain"
                    type="number"
                    min="-60"
                    max="12"
                    defaultValue={clip.original_audio_gain_db}
                  />
                </label>
                <Button type="submit" disabled={disabled}>
                  应用音量
                </Button>
              </form>
            </section>
          )}
          <section>
            <h4>片段顺序</h4>
            <div className="row">
              <Button
                disabled={disabled || index <= 0}
                onClick={() =>
                  void e.edit("move_clip", {
                    clip_id: clip.clip_id,
                    before_clip_id: snapshot!.clips[index - 1].clip_id,
                  })
                }
              >
                向前移动
              </Button>
              <Button
                disabled={disabled || index >= snapshot!.clips.length - 1}
                onClick={() =>
                  void e.edit("move_clip", {
                    clip_id: clip.clip_id,
                    before_clip_id: snapshot!.clips[index + 2]?.clip_id || null,
                  })
                }
              >
                向后移动
              </Button>
            </div>
          </section>
        </div>
      )}
      {!!snapshot?.audio_items.length && (
        <section>
          <h4>{audio ? "选中音频" : "时间线配乐"}</h4>
          {(audio ? [audio] : snapshot.audio_items).map((item) => (
            <form
              key={`${item.audio_id}-${e.project!.revision}`}
              className="music-editor"
              onSubmit={(event) => {
                event.preventDefault();
                void e.edit("set_audio_gain", {
                  target: "audio",
                  target_id: item.audio_id,
                  gain_db: Number(
                    new FormData(event.currentTarget).get("gain"),
                  ),
                });
              }}
            >
              <p>{e.assets.find((a) => a.asset_id === item.asset_id)?.name}</p>
              <p>{anchorRange(item.anchor, snapshot).map(timecode).join(" – ")}</p>
              <label>
                音量 / dB
                <input
                  name="gain"
                  type="number"
                  min="-60"
                  max="12"
                  defaultValue={item.gain_db}
                />
              </label>
              <div className="row">
                <Button type="submit" disabled={disabled}>
                  保存音量
                </Button>
                <Button
                  icon="trash"
                  aria-label="移除配乐"
                  disabled={disabled}
                  onClick={() =>
                    void e.edit("remove_audio", { audio_id: item.audio_id })
                  }
                />
              </div>
            </form>
          ))}
        </section>
      )}
    </div>
  );
}
