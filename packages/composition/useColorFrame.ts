// 将视频和静帧送入同一个调色器，缓存当前帧以支持暂停时即时调参。
import { useCallback, useEffect, useLayoutEffect, useRef } from "react";
import { useDelayRender } from "remotion";
import { createColorRenderer } from "./colorShader";
import type { PreparedClip } from "./math";
import { hasColor } from "./color";

export function useColorFrame(
  clip: PreparedClip,
  frame: number,
  capture?: (source: CanvasImageSource) => void,
) {
  const enabled = hasColor(clip),
    canvas = useRef<HTMLCanvasElement>(null);
  const renderer = useRef<ReturnType<typeof createColorRenderer> | null>(null);
  const saved = useRef<{ raw: OffscreenCanvas; frame: number } | null>(null);
  const pending = useRef<number | null>(null);
  const { delayRender, continueRender } = useDelayRender();
  const finish = useCallback(() => {
    if (pending.current !== null) {
      continueRender(pending.current);
      pending.current = null;
    }
  }, [continueRender]);
  const paint = useCallback(
    (source: CanvasImageSource) => {
      let output = source;
      if (enabled) {
        renderer.current ||= createColorRenderer(clip.width, clip.height);
        output = renderer.current.draw(source, clip);
        const ctx = canvas.current?.getContext("2d");
        if (ctx) {
          ctx.drawImage(output, 0, 0);
          canvas.current!.dataset.frame = String(clip.start_frame + frame);
        }
      }
      capture?.(output);
      finish();
    },
    [enabled, clip, frame, capture, finish],
  );
  const receive = useCallback(
    (source: CanvasImageSource) => {
      if (
        source instanceof HTMLVideoElement &&
        (source.readyState < 2 || source.seeking)
      )
        return;
      if (
        source instanceof HTMLImageElement &&
        (!source.complete || !source.naturalWidth)
      )
        return;
      const raw =
        saved.current?.raw || new OffscreenCanvas(clip.width, clip.height);
      raw.getContext("2d")!.drawImage(source, 0, 0, clip.width, clip.height);
      saved.current = { raw, frame };
      paint(raw);
    },
    [clip.width, clip.height, frame, paint],
  );
  useLayoutEffect(() => {
    if (enabled)
      pending.current = delayRender(
        `Waiting for graded frame ${clip.clip_id}:${frame}`,
      );
    if (saved.current?.frame === frame) paint(saved.current.raw);
    return finish;
  }, [enabled, frame, paint, delayRender, finish, clip.clip_id]);
  useEffect(
    () => () => {
      renderer.current?.cleanup();
      renderer.current = null;
    },
    [clip.width, clip.height],
  );
  return { enabled, canvas, receive: enabled || capture ? receive : undefined };
}
