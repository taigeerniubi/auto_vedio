// 使用共享 Remotion 合成组件预览正式工程并同步播放位置。
import { Player, type PlayerRef } from "@remotion/player";
import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { VlogComposition } from "../../../../packages/composition";
import { post, projectPath } from "../api";
import type { Preview, Project } from "../contracts";
import { timecode } from "../timeline";
import { Button, Icon } from "./Ui";
import type { MotionDraft } from "./TemplatesPanel";
import type {ColorDraft} from './ColorPanel';
import {CanvasTransform, type TransformDraft} from './CanvasTransform';
import type {Editor} from '../hooks/useEditor';

/** 准备并播放与后端导出相同的合成清单。
 * Args:
 *   project: 当前工程。
 *   player: 外部使用的播放器引用。
 *   frame: 当前播放位置。
 *   onFrame: 播放位置变化回调。
 *   onError: 请求失败回调。
 * Returns:
 *   成片预览区域。
 */
export function PreviewPanel({
  project,
  player,
  frame,
  onFrame,
  onError,
  onPlay,
  motionDraft,
  colorDraft,
  editor,
}: {
  project: Project | null;
  player: RefObject<PlayerRef | null>;
  frame: number;
  onFrame: (frame: number) => void;
  onError: (error: unknown) => void;
  onPlay: () => void;
  motionDraft?: MotionDraft;
  colorDraft?: ColorDraft;
  editor: Editor;
}) {
  const [transformDraft,setTransformDraft]=useState<TransformDraft>(null);
  const [adjusting,setAdjusting]=useState(true);
  const stage=useRef<HTMLDivElement>(null);
  const [stageSize,setStageSize]=useState({width:0,height:0});
  useEffect(()=>{const node=stage.current;if(!node)return;const observer=new ResizeObserver(([entry])=>setStageSize({width:entry.contentRect.width,height:entry.contentRect.height}));observer.observe(node);return()=>observer.disconnect();},[]);
  useEffect(()=>setTransformDraft(null),[editor.selected,project?.project_id,project?.revision]);
  const [preparedPreview, setPreview] = useState<(Preview & { projectId: string }) | null>(null);
  const preview = preparedPreview?.projectId === project?.project_id ? preparedPreview : null;
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [volume, setVolume] = useState(1);
  const [volumeOpen, setVolumeOpen] = useState(false);
  const volumeControl = useRef<HTMLDivElement>(null);
  const [playerError, setPlayerError] = useState(false);
  const prepared = useRef<{ key: string; promise: Promise<Preview> } | null>(null);
  const previousProject = useRef<string | undefined>(undefined);
  const revision = project ? `${project.project_id}:${project.revision}` : "";
  useEffect(() => {
    if (!volumeOpen) return;
    const outside = (event: PointerEvent) => {
      if (!volumeControl.current?.contains(event.target as Node)) setVolumeOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setVolumeOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [volumeOpen]);
  useEffect(() => {
    let active = true;
    setFailed(false);
    setPlayerError(false);
    if (previousProject.current !== project?.project_id) {
      setPreview(null);
      setPlaying(false);
      onFrame(0);
      previousProject.current = project?.project_id;
    }
    if (!project?.snapshot.clips.length) {
      setPreview(null);
      setPlaying(false);
      onFrame(0);
      setLoading(false);
      return;
    }
    setLoading(true);
    const key = `${revision}:${retry}`;
    let promise = prepared.current?.key === key ? prepared.current.promise : null;
    if (!promise) {
      promise = post<Preview>(`${projectPath(project.project_id)}/preview`, {
        expected_revision: project.revision,
      });
      prepared.current = { key, promise };
    }
    promise
      .then((result) => {
        if (active) setPreview({ ...result, projectId: project.project_id });
      })
      .catch((error) => {
        if (active) {
          setFailed(true);
          onError(error);
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [revision, retry]);
  useEffect(() => {
    const current = player.current;
    if (!current || !preview) return;
    const nextFrame = Math.min(current.getCurrentFrame(), preview.plan.total_frames - 1);
    if (nextFrame !== current.getCurrentFrame()) current.seekTo(nextFrame);
    onFrame(nextFrame);
    setPlaying(current.isPlaying());
    setVolume(current.getVolume());
    setMuted(current.isMuted());
    const update = ({ detail }: { detail: { frame: number } }) =>
      onFrame(detail.frame);
    const play = () => { setPlaying(true); onPlay(); };
    const pause = () => setPlaying(false);
    const volumeChange = ({ detail }: { detail: { volume: number } }) => setVolume(detail.volume);
    const muteChange = ({ detail }: { detail: { isMuted: boolean } }) => setMuted(detail.isMuted);
    current.addEventListener("frameupdate", update);
    current.addEventListener("play", play);
    current.addEventListener("pause", pause);
    current.addEventListener("ended", pause);
    current.addEventListener("volumechange", volumeChange);
    current.addEventListener("mutechange", muteChange);
    return () => {
      current.removeEventListener("frameupdate", update);
      current.removeEventListener("play", play);
      current.removeEventListener("pause", pause);
      current.removeEventListener("ended", pause);
      current.removeEventListener("volumechange", volumeChange);
      current.removeEventListener("mutechange", muteChange);
    };
  }, [preview, retry]);
  const inputProps = useMemo(
    () =>
      preview
        ? {
            plan: {
              ...preview.plan,
              clips: preview.plan.clips.map((clip) => {
                const saved=project?.snapshot.clips.find(c=>c.clip_id===clip.clip_id);
                return {...clip,...(saved?{transform:saved.transform,motion:saved.motion,color_grade:saved.color_grade,color_preset:saved.color_preset,color_preset_intensity:saved.color_preset_intensity}:{}),
                  ...(motionDraft?.clip_id===clip.clip_id?{motion:motionDraft.motion}:{}),
                  ...(colorDraft?.clip_id===clip.clip_id?colorDraft:{}),
                  ...(transformDraft?.clip_id===clip.clip_id?{transform:transformDraft.transform}:{})};
              }),
            },
          }
        : null,
    [preview, motionDraft, colorDraft, transformDraft,project],
  );
  const width = project?.snapshot.composition.width || 1920,
    height = project?.snapshot.composition.height || 1080;
  const viewportWidth=Math.min(stageSize.width,stageSize.height*width/height);
  const activeClips=inputProps?.plan.clips.filter(c=>frame>=c.start_frame&&frame<c.end_frame)||[];
  const selectedClip=activeClips.find(c=>c.clip_id===editor.selected);
  return (
    <section id="workspace-monitor" className="preview-panel panel" aria-label="成片预览">
      <div className="panel-heading">
        <h2>{motionDraft ? "运镜预览 · 尚未应用" : colorDraft ? "调色预览" : "成片预览"}</h2>
        <div className="row">
          <Button tone="ghost" aria-pressed={adjusting} disabled={!preview} onClick={()=>setAdjusting(v=>!v)}>画面调整</Button>
          <span className="chip">{width > height ? "16:9" : "9:16"}</span>
          <span className="chip">
            {width} × {height}
          </span>
        </div>
      </div>
      <div ref={stage} className={`preview-stage ${width < height ? "portrait" : ""}`}>
        {preview && inputProps ? (
          <div className="preview-viewport" style={{width:viewportWidth||'100%',height:viewportWidth?viewportWidth*height/width:undefined,aspectRatio:`${width}/${height}`}}>
          <Player
            key={`${project?.project_id}-${retry}`}
            ref={player}
            component={VlogComposition}
            inputProps={inputProps}
            durationInFrames={preview.plan.total_frames}
            compositionWidth={width}
            compositionHeight={height}
            fps={30}
            style={{
              width: "100%",
              maxHeight: "100%",
              aspectRatio: `${width}/${height}`,
            }}
            clickToPlay={!adjusting}
            doubleClickToFullscreen={!adjusting}
            initialFrame={Math.min(frame, preview.plan.total_frames - 1)}
            initiallyMuted={muted}
            initialVolume={volume}
            showVolumeControls={false}
            errorFallback={({ error }) => {
              if (!playerError)
                queueMicrotask(() => {
                  setPlayerError(true);
                  onError(error);
                });
              return (
                <div className="empty-preview">
                  <p>媒体播放失败，请重新准备预览。</p>
                </div>
              );
            }}
          />
          {adjusting&&!playing&&!motionDraft&&(selectedClip?<CanvasTransform clip={selectedClip} frame={frame} width={width} height={height} editor={editor}
            onDraft={setTransformDraft} onPause={()=>player.current?.pause()}/>:activeClips.length>0&&<button className="select-canvas-clip" aria-label="选择当前画面进行调整" disabled={!!editor.busy}
              onClick={()=>{player.current?.pause();editor.setSelected(activeClips.at(-1)!.clip_id);}}/>)}
          </div>
        ) : (
          <div className="empty-preview">
            <div className="empty-mark">
              <Icon name={loading ? "clock" : "video"} />
            </div>
            <h3>
              {loading
                ? "正在准备你的画面"
                : failed
                  ? "预览准备失败"
                  : "每段日常，都值得被记住"}
            </h3>
            <p>
              {loading
                ? "正在同步画面、文字和声音…"
                : failed
                  ? "请查看错误信息后重试"
                  : "将素材添加到时间线，开始你的第一支短片"}
            </p>
            {failed && (
              <Button onClick={() => setRetry((v) => v + 1)}>
                重新准备预览
              </Button>
            )}
          </div>
        )}
      </div>
      <div className="playback-bar">
        <div className="time-display">
          <strong>{timecode(frame)}</strong>
          <span>/ {timecode(preview?.plan.total_frames || 0)}</span>
        </div>
        <div className="row playback-actions">
          <Button
            tone="ghost"
            icon="left"
            aria-label="上一帧"
            disabled={!preview}
            onClick={() => player.current?.seekTo(Math.max(0, frame - 1))}
          />
          <Button
            className="play-toggle"
            icon={playing ? "pause" : "play"}
            aria-label={playing ? "暂停" : "播放"}
            disabled={!preview || playerError || loading || failed}
            onClick={() => player.current?.toggle()}
          />
          <Button
            tone="ghost"
            icon="right"
            aria-label="下一帧"
            disabled={!preview}
            onClick={() =>
              player.current?.seekTo(
                Math.min((preview?.plan.total_frames || 1) - 1, frame + 1),
              )
            }
          />
        </div>
        <div className="row">
          <div className="volume-control" ref={volumeControl}>
            <Button tone="ghost" disabled={!preview} aria-label="调节预览音量"
              aria-expanded={volumeOpen} aria-controls="preview-volume-controls"
              onClick={() => setVolumeOpen((open) => !open)}>
              {muted || volume === 0 ? "静音" : "声音"}
            </Button>
            {preview && volumeOpen && (
              <div id="preview-volume-controls" className="volume-popover" role="group" aria-label="预览音量控制">
                <div className="row">
                  <span>预览音量</span>
                  <output htmlFor="preview-volume">{Math.round((muted ? 0 : volume) * 100)}%</output>
                </div>
                <input id="preview-volume" type="range" min="0" max="100" step="1"
                  aria-label="预览音量" value={Math.round((muted ? 0 : volume) * 100)}
                  onChange={(event) => {
                    const value = Number(event.target.value) / 100;
                    player.current?.setVolume(value);
                    player.current?.unmute();
                    setVolume(value);
                    setMuted(false);
                  }} />
                <Button tone="ghost" aria-label={muted || volume === 0 ? "恢复预览声音" : "静音预览"}
                  onClick={() => {
                    if (muted || volume === 0) {
                      if (volume === 0) { player.current?.setVolume(1); setVolume(1); }
                      player.current?.unmute();
                      setMuted(false);
                    } else { player.current?.mute(); setMuted(true); }
                  }}>{muted || volume === 0 ? "恢复声音" : "静音"}</Button>
              </div>
            )}
          </div>
          <Button
            tone="ghost"
            icon="full"
            aria-label="全屏预览"
            disabled={!preview}
            onClick={() => player.current?.requestFullscreen()}
          />
        </div>
      </div>
      {preview && loading && <div className="preview-warning" role="status">正在同步最新剪辑…</div>}
      {preview && failed && <div className="preview-warning" role="alert">最新剪辑预览失败，当前画面为上一个版本。</div>}
      {(playerError || (preview && failed)) && (
        <Button
          className="preview-retry"
          onClick={() => setRetry((v) => v + 1)}
        >
          重新加载媒体
        </Button>
      )}
      {!!preview?.warnings.length && (
        <div className="preview-warning">{preview.warnings.join("；")}</div>
      )}
    </section>
  );
}
