// 使用共享 Remotion 合成组件预览正式工程并同步播放位置。
import { Player, type PlayerRef } from "@remotion/player";
import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { VlogComposition } from "../../../../packages/composition";
import { post, projectPath } from "../api";
import type { Preview, Project } from "../contracts";
import { timecode } from "../timeline";
import { Button, Icon } from "./Ui";

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
}: {
  project: Project | null;
  player: RefObject<PlayerRef | null>;
  frame: number;
  onFrame: (frame: number) => void;
  onError: (error: unknown) => void;
}) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [playerError, setPlayerError] = useState(false);
  const prepared = useRef(new Map<string, Promise<Preview>>());
  const revision = project ? `${project.project_id}:${project.revision}` : "";
  useEffect(() => {
    let active = true;
    setPreview(null);
    setFailed(false);
    setPlayerError(false);
    setPlaying(false);
    onFrame(0);
    if (!project?.snapshot.clips.length) {
      setLoading(false);
      return;
    }
    setLoading(true);
    const key = `${revision}:${retry}`;
    let promise = prepared.current.get(key);
    if (!promise) {
      promise = post<Preview>(`${projectPath(project.project_id)}/preview`, {
        expected_revision: project.revision,
      });
      prepared.current.set(key, promise);
    }
    promise
      .then((result) => {
        if (active) setPreview(result);
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
    const update = ({ detail }: { detail: { frame: number } }) =>
      onFrame(detail.frame);
    const play = () => setPlaying(true);
    const pause = () => setPlaying(false);
    current.addEventListener("frameupdate", update);
    current.addEventListener("play", play);
    current.addEventListener("pause", pause);
    current.addEventListener("ended", pause);
    return () => {
      current.removeEventListener("frameupdate", update);
      current.removeEventListener("play", play);
      current.removeEventListener("pause", pause);
      current.removeEventListener("ended", pause);
    };
  }, [preview]);
  const inputProps = useMemo(
    () => (preview ? { plan: preview.plan } : null),
    [preview],
  );
  const width = project?.snapshot.composition.width || 1920,
    height = project?.snapshot.composition.height || 1080;
  return (
    <section className="preview-panel panel" aria-label="成片预览">
      <div className="panel-heading">
        <h2>成片预览</h2>
        <div className="row">
          <span className="chip">{width > height ? "16:9" : "9:16"}</span>
          <span className="chip">
            {width} × {height}
          </span>
        </div>
      </div>
      <div className={`preview-stage ${width < height ? "portrait" : ""}`}>
        {preview && inputProps ? (
          <Player
            key={`${revision}-${retry}`}
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
            clickToPlay
            doubleClickToFullscreen
            initialFrame={0}
            initiallyMuted={muted}
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
            disabled={!preview || playerError}
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
          <Button
            tone="ghost"
            disabled={!preview}
            aria-label={muted ? "开启预览声音" : "静音预览"}
            onClick={() => {
              if (muted) player.current?.unmute();
              else player.current?.mute();
              setMuted(!muted);
            }}
          >
            {muted ? "静音" : "声音"}
          </Button>
          <Button
            tone="ghost"
            icon="full"
            aria-label="全屏预览"
            disabled={!preview}
            onClick={() => player.current?.requestFullscreen()}
          />
        </div>
      </div>
      {playerError && (
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
