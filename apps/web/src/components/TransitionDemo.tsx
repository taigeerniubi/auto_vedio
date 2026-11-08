// 用轻量示例场景展示真实转场，每次只播放当前悬停或聚焦的卡片。
import {useEffect, useMemo, useState} from 'react';
import {Player} from '@remotion/player';
import {VlogComposition} from '../../../../packages/composition';
import type {Plan} from '../../../../packages/composition/math';
import type {TransitionPresetId} from '../../../../packages/composition/transitionPresets';

const svg = (body: string) => `data:image/svg+xml;charset=utf-8,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180" viewBox="0 0 320 180">${body}</svg>`)}`;
const scenes = [
  svg('<rect width="320" height="180" fill="#b9d9d2"/><circle cx="245" cy="45" r="24" fill="#fff3be"/><path d="M0 130L52 78L113 135L167 86L260 180H0Z" fill="#619b8e"/><path d="M170 180L246 94L320 139V180Z" fill="#347369"/><path d="M0 153Q90 120 180 156T320 149V180H0Z" fill="#183f39"/><text x="22" y="36" font-family="sans-serif" font-size="16" fill="#183f39" letter-spacing="3">01 / MORNING</text>'),
  svg('<rect width="320" height="180" fill="#e8ad7c"/><circle cx="76" cy="54" r="28" fill="#ffdfa1"/><path d="M0 180V103H37V67H75V119H109V86H151V48H185V109H224V78H268V116H320V180Z" fill="#9a645c"/><path d="M0 180V143H63V121H96V157H145V102H183V144H235V118H287V147H320V180Z" fill="#483f50"/><path d="M155 119H164V130H155ZM155 139H164V150H155ZM46 84H55V95H46ZM235 94H245V105H235Z" fill="#ffdfa1"/><text x="166" y="33" font-family="sans-serif" font-size="16" fill="#483f50" letter-spacing="2">02 / EVENING</text>'),
];

// 所有卡片共用同一组内置图片，打开面板时预解码，避免每次悬停重复准备。
let preparation: Promise<void> | null = null;
function prepareScenes() {
  if (!preparation) preparation = Promise.all(scenes.map(src => {
    const image = new Image();
    image.src = src;
    return image.decode();
  })).then(() => undefined).catch(error => { preparation = null; throw error; });
  return preparation;
}

export function TransitionDemo({preset, active}: {preset: TransitionPresetId; active: boolean}) {
  const [phase, setPhase] = useState<'loading' | 'ready' | 'error'>('loading');
  const [reducedMotion, setReducedMotion] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    const change = () => setReducedMotion(query.matches);
    query.addEventListener('change', change);
    return () => query.removeEventListener('change', change);
  }, []);
  useEffect(() => {
    let current = true;
    void prepareScenes().then(() => { if (current) setPhase('ready'); }, () => { if (current) setPhase('error'); });
    return () => { current = false; };
  }, [active]);
  const plan = useMemo<Plan>(() => ({
    composition: {width: 320, height: 180, fps: 30}, total_frames: 66, mix_file: '', text_items: [],
    clips: scenes.map((file, i) => ({clip_id: `demo-${i}`, asset_id: `demo-${i}`, file, kind: 'image',
      start_frame: i * 24, end_frame: i * 24 + 42, duration_frames: 42, width: 320, height: 180,
      transform: {fit: 'cover', anchor_x: .5, anchor_y: .5, scale: 1, rotation_deg: 0}, motion: null, color_preset: null})),
    transitions: [{left_clip_id: 'demo-0', right_clip_id: 'demo-1', duration_frames: 18, preset_id: preset}],
  }), [preset]);
  return <div className="transition-demo" aria-hidden="true" data-active={active}>
    {active && phase === 'ready' ? <Player key={`${preset}-${reducedMotion}`} className="transition-demo-player" component={VlogComposition} inputProps={{plan}}
      compositionWidth={320} compositionHeight={180} durationInFrames={66} fps={30}
      // 只循环有转场的区间，直接从效果开始处试看；两幅画面和 GPU 上下文也能持续复用。
      inFrame={24} outFrame={41} initialFrame={reducedMotion ? 33 : 25} playbackRate={0.5} autoPlay={!reducedMotion} loop initiallyMuted controls={false}
      clickToPlay={false} doubleClickToFullscreen={false} spaceKeyToPlayOrPause={false}
      numberOfSharedAudioTags={0}
      renderLoading={() => <span className="transition-demo-status">准备预览…</span>}
      errorFallback={() => <span className="transition-demo-status">预览暂不可用</span>}
      style={{width: '100%', pointerEvents: 'none'}}/> : <>
        <img src={scenes[0]} alt=""/><img className="transition-demo-next" src={scenes[1]} alt=""/>
        {active ? <span className="transition-demo-status">{phase === 'error' ? '预览暂不可用' : '准备预览…'}</span>
          : <span className="transition-demo-play">▶</span>}
      </>}
    {active && phase === 'ready' && reducedMotion && <span className="transition-demo-badge">静态预览</span>}
  </div>;
}
