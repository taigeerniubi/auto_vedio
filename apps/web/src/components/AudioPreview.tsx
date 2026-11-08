// 在后台播放素材音频，把播放状态同步给对应卡片的播放和暂停按钮。
import { useEffect, type RefObject } from "react";

/** 播放音频并在关闭或切换素材时停止，不创建额外播放器面板。
 * Args:
 *   name: 素材名称。
 *   src: 受限的本地音频地址。
 *   audio: 与成片预览协调播放的元素引用。
 *   onPlayingChange: 同步真实播放状态。
 *   onError: 报告加载或播放失败。
 */
export function AudioPreview({ name, src, audio, onPlayingChange, onError }: {
  name: string;
  src: string;
  audio: RefObject<HTMLAudioElement | null>;
  onPlayingChange: (playing: boolean) => void;
  onError: () => void;
}) {
  useEffect(() => {
    const current = audio.current;
    let active = true;
    // 显式启动使开发模式的挂载清理后也能继续响应用户的试听动作。
    void current?.play().catch((error: DOMException) => {
      if (active && error.name !== "AbortError") onError();
    });
    return () => { active = false; current?.pause(); };
  }, [audio]);
  return (
    <audio
      ref={audio}
      hidden
      data-audition
      aria-label={`试听播放器 ${name}`}
      preload="metadata"
      src={src}
      onPlay={() => onPlayingChange(true)}
      onPause={() => onPlayingChange(false)}
      onEnded={() => onPlayingChange(false)}
      onError={() => { onPlayingChange(false); onError(); }}
    />
  );
}
