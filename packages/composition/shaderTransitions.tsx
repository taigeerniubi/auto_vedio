// 共享已解码的视频帧，将 GPU 转场用于预览、草稿和逐帧导出，不依赖实验性 HTML-in-canvas。
import React from 'react';
import {useDelayRender} from 'remotion';
import {crossZoomShader} from '@remotion/transitions/cross-zoom';
import {blurSlideShader, type BlurSlideDirection} from '@remotion/transitions/blur-slide';
import {filmBurnShader} from '@remotion/transitions/film-burn';
import {glitchShader} from './glitchShader';
import {geometry, type PreparedClip} from './math';
import type {TransitionPresetId} from './transitionPresets';

const DIRECTIONS: Record<string, BlurSlideDirection> = {left: 'from-right', right: 'from-left', up: 'from-bottom', down: 'from-top'};
type SceneFrame = {canvas: OffscreenCanvas; frame: number; clip: PreparedClip};
export function createFrameStore() {
  return {frames: new Map<string, SceneFrame>(), listeners: new Set<() => void>()};
}
export type FrameStore = ReturnType<typeof createFrameStore>;

/** 复用已有媒体节点；仅在高级转场范围内栅格化，保留裁切、旋转、调色和运镜。 */
export function useSceneCapture(store: FrameStore, clip: PreparedClip, frame: number, width: number, height: number, enabled: boolean) {
  React.useEffect(() => () => { store.frames.delete(clip.clip_id); }, [store, clip.clip_id]);
  return React.useMemo(() => enabled ? (source: CanvasImageSource) => {
    if (source instanceof HTMLVideoElement && (source.readyState < 2 || source.seeking)) return;
    if (source instanceof HTMLImageElement && (!source.complete || !source.naturalWidth)) return;
    const entry = store.frames.get(clip.clip_id);
    const canvas = entry?.canvas.width === width && entry.canvas.height === height ? entry.canvas : new OffscreenCanvas(width, height);
    const ctx = canvas.getContext('2d')!;
    const g = geometry(clip, frame, width, height);
    ctx.resetTransform();
    ctx.filter = 'none';
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, width, height);
    ctx.save();
    ctx.translate(width / 2 + (clip.transform.position_x || 0) * width, height / 2 + (clip.transform.position_y || 0) * height);
    ctx.rotate(g.rotation * Math.PI / 180);
    ctx.drawImage(source, g.left, g.top, g.width, g.height);
    ctx.restore();
    store.frames.set(clip.clip_id, {canvas, frame: clip.start_frame + frame, clip});
    for (const listener of store.listeners) listener();
  } : undefined, [store, clip, frame, width, height, enabled]);
}

/** 只调用官方纯 Shader，统一进度为 0=前镜头、1=后镜头。 */
export function createTransitionRenderer(preset: TransitionPresetId, width: number, height: number) {
  const canvas = new OffscreenCanvas(width, height);
  const shader = preset === 'glitch' ? glitchShader(canvas)
    : preset === 'cross_zoom' ? crossZoomShader(canvas)
      : preset === 'film_burn' ? filmBurnShader(canvas) : blurSlideShader(canvas);
  return {
    draw(ctx: CanvasRenderingContext2D, prev: OffscreenCanvas, next: OffscreenCanvas, progress: number) {
      // 端点直接拷贝，避免数值噪声、柔光或小数采样污染相邻完整镜头。
      if (progress <= 0 || progress >= 1) {
        ctx.drawImage(progress <= 0 ? prev : next, 0, 0);
        return;
      }
      if (preset === 'glitch') (shader as ReturnType<typeof glitchShader>).draw(prev, next, progress);
      else (shader as ReturnType<typeof blurSlideShader>).draw({prevImage: prev, nextImage: next, width, height,
        time: 1 - progress, passedProps: preset.startsWith('blur_slide_') ? {direction: DIRECTIONS[preset.split('_').at(-1)!], blur: 0.35} : {}});
      ctx.drawImage(canvas, 0, 0);
    },
    cleanup() {
      shader.cleanup();
      // 卡片反复切换时及时释放上下文，避免触发浏览器的 WebGL 上限。
      canvas.getContext('webgl2')?.getExtension('WEBGL_lose_context')?.loseContext();
    },
  };
}

export function ShaderTransitionOverlay({store, left, right, preset, frame, duration, width, height}: {
  store: FrameStore; left: PreparedClip; right: PreparedClip; preset: TransitionPresetId;
  frame: number; duration: number; width: number; height: number;
}) {
  const canvas = React.useRef<HTMLCanvasElement>(null);
  const renderer = React.useRef<ReturnType<typeof createTransitionRenderer> | null>(null);
  const {delayRender, continueRender} = useDelayRender();
  React.useLayoutEffect(() => {
    renderer.current = createTransitionRenderer(preset, width, height);
    return () => { renderer.current?.cleanup(); renderer.current = null; };
  }, [preset, width, height]);
  React.useLayoutEffect(() => {
    const handle = delayRender(`Waiting for transition frames at ${frame}`);
    let pending = true;
    function draw() {
      const prev = store.frames.get(left.clip_id), next = store.frames.get(right.clip_id);
      // 导出必须同时收到当前帧，跳转或换工程时不能使用旧素材的缓存。
      if (!prev || !next || prev.frame !== frame || next.frame !== frame || prev.clip !== left || next.clip !== right) return;
      const ctx = canvas.current?.getContext('2d');
      if (!ctx || !renderer.current) return;
      renderer.current.draw(ctx, prev.canvas, next.canvas, (frame - right.start_frame) / duration);
      canvas.current!.dataset.frame = String(frame);
      if (pending) { pending = false; continueRender(handle); }
    }
    store.listeners.add(draw);
    draw();
    return () => {
      store.listeners.delete(draw);
      if (pending) continueRender(handle);
    };
  }, [store, left, right, preset, frame, duration, width, height, delayRender, continueRender]);
  return <canvas ref={canvas} width={width} height={height} data-transition={preset}
    style={{position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none'}}/>;
}
