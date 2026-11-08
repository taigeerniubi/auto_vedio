// 定义转场的稳定编号与界面名称，不加载渲染器。
export const TRANSITION_PRESETS = {
  dissolve: {label: '叠化', family: 'dissolve'},
  slide_left: {label: '向左推移', family: 'slide'},
  slide_right: {label: '向右推移', family: 'slide'},
  slide_up: {label: '向上推移', family: 'slide'},
  slide_down: {label: '向下推移', family: 'slide'},
  wipe_left: {label: '向左擦除', family: 'wipe'},
  wipe_right: {label: '向右擦除', family: 'wipe'},
  wipe_up: {label: '向上擦除', family: 'wipe'},
  wipe_down: {label: '向下擦除', family: 'wipe'},
  fade_black: {label: '淡黑', family: 'fade_black'},
  fade_white: {label: '闪白', family: 'fade_white'},
  iris_open: {label: '圆形展开', family: 'iris_open'},
  flip_left: {label: '向左翻转', family: 'flip'},
  flip_right: {label: '向右翻转', family: 'flip'},
  flip_up: {label: '向上翻转', family: 'flip'},
  flip_down: {label: '向下翻转', family: 'flip'},
  cube_left: {label: '立方体向左', family: 'cube'},
  cube_right: {label: '立方体向右', family: 'cube'},
  cube_up: {label: '立方体向上', family: 'cube'},
  cube_down: {label: '立方体向下', family: 'cube'},
  clock_wipe: {label: '时钟擦除', family: 'clock_wipe'},
  push_cut: {label: '冲击切换', family: 'push_cut'},
  cross_zoom: {label: '缩放模糊', family: 'cross_zoom'},
  blur_slide_left: {label: '向左甩镜', family: 'blur_slide'},
  blur_slide_right: {label: '向右甩镜', family: 'blur_slide'},
  blur_slide_up: {label: '向上甩镜', family: 'blur_slide'},
  blur_slide_down: {label: '向下甩镜', family: 'blur_slide'},
  film_burn: {label: '胶片漏光', family: 'film_burn'},
  glitch: {label: '故障闪切', family: 'glitch'},
} as const;
export type TransitionPresetId = keyof typeof TRANSITION_PRESETS;
export const TRANSITION_FAMILIES = [
  {id: 'dissolve', label: '叠化', description: '两个镜头柔和交融。', directional: false},
  {id: 'slide', label: '推移', description: '下一镜头将前一镜头推出画面。', directional: true},
  {id: 'wipe', label: '擦除', description: '沿指定方向逐步揭示下一镜头。', directional: true},
  {id: 'fade_black', label: '淡黑', description: '先淡入黑场，再显现下一镜头。', directional: false},
  {id: 'fade_white', label: '闪白', description: '通过短暂白场衔接两个镜头。', directional: false},
  {id: 'iris_open', label: '圆形展开', description: '从画面中心向外展开下一镜头。', directional: false},
  {id: 'flip', label: '翻转', description: '像卡片翻面一样切换镜头，可选择四个方向。', directional: true},
  {id: 'cube', label: '立方体', description: '两个镜头像立方体的相邻面一样旋转。', directional: true},
  {id: 'clock_wipe', label: '时钟擦除', description: '沿顺时针方向扫开下一镜头。', directional: false},
  {id: 'push_cut', label: '冲击切换', description: '短促放大和闪光，适合鼓点与动作衔接。', directional: false},
  {id: 'cross_zoom', label: '缩放模糊', description: '向中心缩放并模糊交融，增强速度感。', directional: false},
  {id: 'blur_slide', label: '模糊甩镜', description: '沿指定方向甩动镜头，带运动模糊。', directional: true},
  {id: 'film_burn', label: '胶片漏光', description: '温暖的胶片灼烧光斑连接两个镜头。', directional: false},
  {id: 'glitch', label: '故障闪切', description: '色彩分离与错位闪动，适合科技和潮流内容。', directional: false},
] as const;
export const isShaderTransition = (id?: TransitionPresetId) =>
  id === 'cross_zoom' || id === 'film_burn' || id === 'glitch' || !!id?.startsWith('blur_slide_');
export type TimelineTransition = {
  left_clip_id: string; right_clip_id: string; duration_frames: number;
  // 兼容未带预设编号的旧预览清单。
  preset_id?: TransitionPresetId;
};
export const transitionLabel = (id?: TransitionPresetId) => TRANSITION_PRESETS[id || 'dissolve'].label;
