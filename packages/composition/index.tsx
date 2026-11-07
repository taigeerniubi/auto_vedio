// 提供预览与导出共用的视频、文字、音频和转场合成组件。
import React from 'react';
import {AbsoluteFill, Composition, Img, OffthreadVideo, Sequence, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {Audio} from '@remotion/media';
import {geometry, textRange, type Plan, type PreparedClip} from './math';

const FILTERS = {warm: 'sepia(0.24) saturate(1.15) hue-rotate(-8deg)', cool: 'saturate(0.85) hue-rotate(12deg)', monochrome: 'grayscale(1)'};
/** 解析预览接口地址或导出使用的静态资源路径。
 * Args:
 *   file: 资源地址。
 * Returns:
 *   可供浏览器或渲染器访问的地址。
 */
const mediaUrl = (file: string) => file.startsWith('/api/') ? file : staticFile(file);
/** 草稿监看直接从母版取帧，正式预览与导出仍使用已准备的派生媒体。 */
function SourceFrame({clip, frame, style}: {clip: PreparedClip; frame: number; style: React.CSSProperties}) {
  const ref = React.useRef<HTMLVideoElement>(null);
  const source = clip.sourcePlayback!;
  const offset = Math.floor(frame * source.numerator / source.denominator);
  const sourceFrame = source.reverse ? source.outFrame - 1 - offset : source.inFrame + offset;
  const seconds = (sourceFrame + 0.5) / 30;
  const seek = () => {
    if (ref.current && ref.current.readyState >= 1) ref.current.currentTime = seconds;
  };
  React.useEffect(seek, [seconds, clip.file]);
  return <video ref={ref} muted playsInline preload="auto" src={clip.file} onLoadedMetadata={seek} style={style}/>;
}
function ClipView({clip, incoming}: {clip: PreparedClip; incoming: number}) {
  const frame = useCurrentFrame();
  const {width, height} = useVideoConfig();
  const g = geometry(clip, frame, width, height);
  const style: React.CSSProperties = {position: 'absolute', left: g.left, top: g.top, width: g.width, height: g.height,
    filter: clip.color_preset ? FILTERS[clip.color_preset] : undefined};
  return <AbsoluteFill style={{opacity: incoming ? Math.min(1, frame / incoming) : 1, overflow: 'hidden'}}>
    <div style={{position: 'absolute', left: width / 2, top: height / 2, transform: 'rotate(' + g.rotation + 'deg)'}}>
      {clip.kind === 'image' ? <Img src={mediaUrl(clip.file)} style={style}/> : clip.sourcePlayback ?
        <SourceFrame clip={clip} frame={frame} style={style}/> :
        <OffthreadVideo src={mediaUrl(clip.file)} {...clip.previewPlayback} muted style={style}/>}
    </div>
  </AbsoluteFill>;
}

export function VlogComposition({plan}: {plan: Plan}) {
  const frame = useCurrentFrame();
  return <AbsoluteFill style={{background: '#000'}}>
    {plan.clips.map(clip => <Sequence key={clip.clip_id} from={clip.start_frame} durationInFrames={clip.duration_frames}>
      <ClipView clip={clip} incoming={plan.transitions.find(t => t.right_clip_id === clip.clip_id)?.duration_frames ?? 0}/>
    </Sequence>)}
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
