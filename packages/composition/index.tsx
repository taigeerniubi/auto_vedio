// 提供预览与导出共用的视频、文字、音频和转场合成组件。
import React from 'react';
import {AbsoluteFill, Composition, Img, OffthreadVideo, Sequence, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {Audio} from '@remotion/media';
import {geometry, textRange, type Plan, type PreparedClip} from './math';
import {TransitionEffect} from './transitions';
import {isShaderTransition, type TimelineTransition} from './transitionPresets';
import {createFrameStore, ShaderTransitionOverlay, useSceneCapture, type FrameStore} from './shaderTransitions';
import {useColorFrame} from './useColorFrame';

/** 解析预览接口地址或导出使用的静态资源路径。
 * Args:
 *   file: 资源地址。
 * Returns:
 *   可供浏览器或渲染器访问的地址。
 */
const mediaUrl = (file: string) => /^(\/api\/|data:|blob:|https?:)/.test(file) ? file : staticFile(file);
/** 草稿监看直接从母版取帧，正式预览与导出仍使用已准备的派生媒体。 */
function SourceFrame({clip, frame, style, capture}: {clip: PreparedClip; frame: number; style: React.CSSProperties; capture?: (source: CanvasImageSource) => void}) {
  const ref = React.useRef<HTMLVideoElement>(null);
  const source = clip.sourcePlayback!;
  const offset = Math.floor(frame * source.numerator / source.denominator);
  const sourceFrame = source.reverse ? source.outFrame - 1 - offset : source.inFrame + offset;
  const seconds = (sourceFrame + 0.5) / 30;
  const seek = () => {
    if (ref.current && ref.current.readyState >= 1) ref.current.currentTime = seconds;
  };
  React.useEffect(seek, [seconds, clip.file]);
  React.useEffect(() => { if (ref.current) capture?.(ref.current); }, [capture]);
  return <video ref={ref} muted playsInline preload="auto" src={clip.file} onLoadedMetadata={seek}
    onLoadedData={event => capture?.(event.currentTarget)} onSeeked={event => capture?.(event.currentTarget)} style={style}/>;
}
function ClipView({clip, incoming, outgoing, store, captureFrames}: {clip: PreparedClip; incoming?: TimelineTransition; outgoing?: TimelineTransition; store: FrameStore; captureFrames: boolean}) {
  const frame = useCurrentFrame();
  const {width, height} = useVideoConfig();
  const g = geometry(clip, frame, width, height);
  const capture = useSceneCapture(store, clip, frame, width, height, captureFrames);
  const color = useColorFrame(clip, frame, capture);
  // 素材按合成像素定位；宿主页的 img {max-width:100%} 会按零宽锚点容器把图片压成 0。
  const style: React.CSSProperties = {position: 'absolute', left: g.left, top: g.top, width: g.width, height: g.height,
    maxWidth: 'none', maxHeight: 'none', objectFit: 'fill'};
  const mediaStyle = color.enabled ? {...style, opacity: 0} : style;
  return <TransitionEffect transition={outgoing} frame={frame - clip.duration_frames + (outgoing?.duration_frames || 0)} direction="exiting">
    <TransitionEffect transition={incoming} frame={frame} direction="entering">
    <AbsoluteFill style={{overflow: 'hidden'}}>
    <div style={{position: 'absolute', left: width / 2 + (clip.transform.position_x || 0) * width,
      top: height / 2 + (clip.transform.position_y || 0) * height, transform: 'rotate(' + g.rotation + 'deg)'}}>
      {clip.kind === 'image' ? <Img src={mediaUrl(clip.file)} style={mediaStyle} onImageFrame={color.receive}/> : clip.sourcePlayback ?
        <SourceFrame clip={clip} frame={frame} style={mediaStyle} capture={color.receive}/> :
        <OffthreadVideo src={mediaUrl(clip.file)} {...clip.previewPlayback} muted style={mediaStyle} onVideoFrame={color.receive}
          onSeeked={event => { if (event.currentTarget instanceof HTMLVideoElement) color.receive?.(event.currentTarget); }}
          onLoadedData={event => { if (event.currentTarget instanceof HTMLVideoElement) color.receive?.(event.currentTarget); }}/>}
      {color.enabled && <canvas ref={color.canvas} width={clip.width} height={clip.height} style={style} data-color-clip={clip.clip_id}/>}
    </div>
    </AbsoluteFill>
    </TransitionEffect>
  </TransitionEffect>;
}

export function VlogComposition({plan}: {plan: Plan}) {
  const frame = useCurrentFrame();
  const store = React.useMemo(createFrameStore, []);
  const {width, height} = useVideoConfig();
  const active = plan.transitions.find(t => {
    const right = plan.clips.find(c => c.clip_id === t.right_clip_id);
    return isShaderTransition(t.preset_id) && right && frame >= right.start_frame && frame < right.start_frame + t.duration_frames;
  });
  return <AbsoluteFill style={{background: '#000', overflow: 'hidden'}}>
    {plan.clips.map(clip => <Sequence key={clip.clip_id} from={clip.start_frame} durationInFrames={clip.duration_frames}>
      <ClipView clip={clip} incoming={plan.transitions.find(t => t.right_clip_id === clip.clip_id)}
        outgoing={plan.transitions.find(t => t.left_clip_id === clip.clip_id)} store={store}
        captureFrames={!!active && (active.left_clip_id === clip.clip_id || active.right_clip_id === clip.clip_id)}/>
    </Sequence>)}
    {active && <ShaderTransitionOverlay store={store} preset={active.preset_id!} frame={frame}
      duration={active.duration_frames} width={width} height={height}
      left={plan.clips.find(c => c.clip_id === active.left_clip_id)!}
      right={plan.clips.find(c => c.clip_id === active.right_clip_id)!}/>}
    {plan.text_items.map(item => {
      const [start, end] = textRange(item, plan);
      return frame >= start && frame < end ? <div key={item.text_id} style={{position: 'absolute', left: item.x * 100 + '%', top: item.y * 100 + '%',
        transform: 'translate(-50%, -50%)', color: item.color, fontSize: item.font_size,
        fontFamily: '"Microsoft YaHei", "Noto Sans CJK SC", sans-serif', fontWeight: item.style_preset === 'title' ? 600 : 400,
        whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxWidth: '92%', textAlign: 'center', lineHeight: 1.35,
        textShadow: '0 1px 4px #0009'}}>{item.text}</div> : null;
    })}
    {plan.mix_file && <Audio src={mediaUrl(plan.mix_file)}/>}
  </AbsoluteFill>;
}

const fallback = {composition: {width: 1920, height: 1080, fps: 30}, total_frames: 1, clips: [], transitions: [], text_items: [], mix_file: ''} satisfies Plan;
export function RemotionRoot() {
  return <Composition id="Vlog" component={VlogComposition} width={1920} height={1080} fps={30} durationInFrames={1}
    defaultProps={{plan: fallback as Plan}} calculateMetadata={({props}) => ({width: props.plan.composition.width,
      height: props.plan.composition.height, fps: 30, durationInFrames: props.plan.total_frames})}/>;
}
