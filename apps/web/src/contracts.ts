// 定义工作台使用的工程、素材和智能体接口类型。
import type {
  Motion,
  Transform,
  Plan,
} from "../../../packages/composition/math";
import type { TimelineTransition } from "../../../packages/composition/transitionPresets";
import type {ColorGrade} from "../../../packages/composition/color";

export type Asset = {
  asset_id: string;
  project_id: string;
  name: string;
  kind: "video" | "image" | "audio";
  total_frames: number;
  width: number;
  height: number;
  has_audio: boolean;
};
export type Clip = {
  clip_id: string;
  asset_id: string;
  kind: "video" | "image";
  source_in_frame: number;
  source_out_frame: number | null;
  still_duration_frames: number | null;
  rate_num: number;
  rate_den: number;
  reverse: boolean;
  original_audio_gain_db: number;
  transform: Transform;
  motion: (Motion & { preset_version: number }) | null;
  color_preset: string | null;
  color_preset_intensity?: number;
  color_grade?: ColorGrade;
};
export type Anchor = {
  kind: "timeline" | "clip";
  clip_id: string | null;
  start_frame: number;
  end_frame: number;
};
export type TextItem = {
  text_id: string;
  text: string;
  anchor: Anchor;
  style_preset: "title" | "subtitle";
  x: number;
  y: number;
  font_size: number;
  color: string;
};
export type AudioItem = {
  audio_id: string;
  asset_id: string;
  anchor: Anchor;
  gain_db: number;
  source_in_frame: number;
  track: number;
  fade_in_frames: number;
  fade_out_frames: number;
};
export type Snapshot = {
  schema_version: number;
  composition: { width: number; height: number; fps: number };
  clips: Clip[];
  transitions: TimelineTransition[];
  text_items: TextItem[];
  audio_items: AudioItem[];
  cover?: Cover | null;
};
export type Cover = {
  source: "timeline" | "image"; frame: number; asset_id: string | null;
  title: string; x: number; y: number; font_size: number; color: string; shade: number;
  fit: 'cover' | 'contain';
};
export type Savepoint = { savepoint_id: string; revision: number; created_at: string };
export type ProjectInfo = {
  project_id: string;
  name: string;
  revision: number;
  last_savepoint?: Savepoint | null;
};
export type Project = ProjectInfo & { snapshot: Snapshot };
export type LibraryFile = {
  asset_id: string;
  project_id: string;
  name: string;
  kind: "video" | "image" | "audio";
  size: number;
  width: number;
  height: number;
  total_frames: number;
};
export type ModelStatus = {
  configured: boolean;
  provider: string;
  model: string;
};
export type Run = {
  live_draft?: { base: number; seq: number; snapshot: Snapshot } | null;
  run_id: string;
  project_id: string;
  session_id?: string;
  status:
    | "running"
    | "completed"
    | "failed"
    | "cancelled"
    | "needs_input"
    | "unsupported";
  result: {
    summary?: string;
    question?: string;
    reason?: string;
    error_code?: string;
    revision?: number;
    changed?: boolean;
    diff?: Record<string, unknown>;
  };
  events: {
    事件: string;
    轮次?: number;
    工具?: string;
    asset_id?: string;
    asset_name?: string;
    cache_hit?: boolean;
    状态?: string;
    原因?: string;
    changed_ids?: string[];
    draft_seq?: number;
  }[];
};
export type Preview = { revision: number; plan: Plan; warnings: string[] };
export type ConversationEntry = Run & {
  instruction: string | null;
  created_at: string | null;
};
export type ConversationPage = {
  items: ConversationEntry[];
  next_cursor: number | null;
};
export type ChatSession = {
  session_id: string;
  project_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  has_messages: boolean;
  running: boolean;
};
export type ExportResult = {
  revision: number;
  frames: number;
  download_url: string;
  warnings: string[];
  width: number;
  height: number;
  fps: number;
  bytes: number;
  filename: string;
};
export type ExportOptions = {
  resolution: "source" | "720p" | "1080p" | "2160p";
  quality: "standard" | "high" | "compact";
  filename: string;
};
export type ExportJob = {
  export_id: string;
  project_id: string;
  revision: number;
  status: "queued" | "running" | "completed" | "failed";
  stage: string;
  progress: number;
  rendered_frames: number;
  total_frames: number;
  created_at: string;
  updated_at: string;
  options: ExportOptions;
  result: ExportResult | null;
  error: { code: string; message: string } | null;
};

export type PerceptionStatus = Record<
  "vision" | "speech" | "audio",
  { configured: boolean; model: string }
>;
export type AudioQueryResult = {
  status: "matched" | "not_found" | "uncertain";
  answer: string;
  matches: {
    label: string;
    start_ms: number;
    end_ms: number;
    anchor_ms: number;
    certainty: "high" | "medium" | "low";
    evidence: string;
  }[];
};
export type Analysis = {
  status: string;
  analysis_id: string;
  asset_id: string;
  stages: Record<
    "visual" | "speech",
    { status: string; reason_code: string | null; message?: string }
  >;
  segments: {
    segment_id: string;
    start_frame: number;
    end_frame: number;
    frame_ids: string[];
    status: string;
    visual: {
      description: string;
      tags: string[];
      needs_review: boolean;
      review_reasons: string[];
    } | null;
  }[];
  speech: {
    presence: string;
    language: string | null;
    utterances: { start_ms: number; end_ms: number; text: string }[];
  };
};
