// 将服务端工具事件翻译成工作台上的剪辑动作。
import type { Run } from "./contracts";

const toolLabels: Record<string, string> = {
  assemble_sequence: "编排镜头",
  insert_clip: "插入片段",
  trim_clip: "裁切镜头",
  split_clip: "分割片段",
  move_clip: "调整片段顺序",
  delete_clip: "删除片段",
  replace_clip: "替换镜头",
  set_playback: "调整播放速度与方向",
  set_transform: "调整构图",
  apply_motion_preset: "设置运镜",
  set_transition: "设置转场",
  set_color_preset: "调整色调",
  add_text: "添加文字",
  update_text: "修改文字",
  remove_text: "移除文字",
  add_audio: "添加配乐",
  remove_audio: "移除配乐",
  set_audio_gain: "调整音量",
  set_audio_fade: "设置淡入淡出",
  duck_music_for_clip: "压低片段配乐",
  get_project_state: "读取时间线",
  list_assets: "查看素材",
  list_presets: "查看预设",
  validate_draft: "检查草稿",
  get_draft_diff: "核对修改",
  query_audio: "听取素材音频",
  analyze_asset: "分析素材",
  inspect_segment: "检查画面细节",
  search_segments: "查找合适镜头",
  get_asset_analysis: "读取素材分析",
  generate_subtitles: "生成字幕",
  apply_style_preset: "应用成片风格",
  set_composition: "调整画幅",
};

export function progressLabel(event: Run["events"][number]) {
  if (event.事件 === "请求模型") return `第 ${event.轮次} 轮规划`;
  return toolLabels[event.工具 || ""] || event.工具 || event.事件;
}
