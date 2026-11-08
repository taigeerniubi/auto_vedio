// 封装官方 CSS 转场及淡黑、闪白，供草稿、播放器与导出共用。
import React from 'react';
import {AbsoluteFill, useVideoConfig} from 'remotion';
import type {TransitionPresentation} from '@remotion/transitions';
import {slide, type SlideDirection} from '@remotion/transitions/slide';
import {wipe} from '@remotion/transitions/wipe';
import {iris} from '@remotion/transitions/iris';
import {flip} from '@remotion/transitions/flip';
import {cube} from '@remotion/transitions/cube';
import {clockWipe} from '@remotion/transitions/clock-wipe';
import {pushCut} from '@remotion/transitions/push-cut';
import type {TimelineTransition} from './transitionPresets';
const DIRECTIONS: Record<string, SlideDirection> = {
  left: 'from-right', right: 'from-left', up: 'from-bottom', down: 'from-top',
};
const noop = () => {};

/** 在现有帧时间线上使用官方展示组件，不重排镜头或改变素材时间。 */
function present<P extends Record<string, unknown>>(
  presentation: TransitionPresentation<P>, progress: number, direction: 'entering' | 'exiting',
  frames: number, children: React.ReactNode,
) {
  const Component = presentation.component;
  return <Component passedProps={presentation.props} presentationProgress={progress}
    presentationDirection={direction} presentationDurationInFrames={frames}
    bothEnteringAndExiting={false} onElementImage={noop} onUnmount={noop}>{children}</Component>;
}

/** 将局部帧映射为同一套入场和出场效果；嵌套包装保留视频节点。 */
export function TransitionEffect({transition, frame, direction, children}: {
  transition?: TimelineTransition; frame: number; direction: 'entering' | 'exiting'; children: React.ReactNode;
}) {
  const {width, height} = useVideoConfig();
  const progress = transition ? Math.max(0, Math.min(1, frame / transition.duration_frames)) : direction === 'entering' ? 1 : 0;
  const preset = transition?.preset_id || 'dissolve';
  const frames = transition?.duration_frames || 1;
  if (preset.startsWith('slide_')) return present(slide({direction: DIRECTIONS[preset.slice(6)], exitStyle: progress === 0 ? {transform: 'none'} : undefined}), progress, direction, frames, children);
  if (preset.startsWith('wipe_')) return present(wipe({direction: DIRECTIONS[preset.slice(5)]}), progress, direction, frames, children);
  if (preset === 'iris_open') return present(iris({width, height}), progress, direction, frames, children);
  if (preset.startsWith('flip_')) return present(flip({direction: DIRECTIONS[preset.slice(5)]}), progress, direction, frames, children);
  if (preset.startsWith('cube_')) return present(cube({direction: DIRECTIONS[preset.slice(5)]}), progress, direction, frames, children);
  if (preset === 'clock_wipe') return present(clockWipe({width, height}), progress, direction, frames, children);
  if (preset === 'push_cut') return present(pushCut({cutProgress: 0.5, outgoingScale: 1.16,
    incomingStartScale: 1.16, incomingEndScale: 1, flashOpacity: 0.26, flashFrames: 2}), progress, direction, frames, children);
  if (preset === 'fade_black' || preset === 'fade_white') {
    const entering = direction === 'entering';
    const backgroundColor = progress > 0 && progress < 1 ? preset === 'fade_black' ? '#000' : '#fff' : undefined;
    return <AbsoluteFill style={{backgroundColor, opacity: entering && progress < 0.5 ? 0 : 1}}>
      <AbsoluteFill style={{opacity: entering ? Math.max(0, progress * 2 - 1) : Math.max(0, 1 - progress * 2)}}>{children}</AbsoluteFill>
    </AbsoluteFill>;
  }
  // 保留旧工程叠化的帧进度和图层混合行为。
  return <AbsoluteFill style={{opacity: direction === 'entering' ? progress : 1}}>{children}</AbsoluteFill>;
}
