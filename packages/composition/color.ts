// 定义调色界面与渲染器共用的参数、曲线插值和风格预设。
import presets from "./colorPresets.json";

export type Point = { x: number; y: number };
export type Wheel = { hue: number; saturation: number; luminance: number };
export type Hsl = { hue: number; saturation: number; luminance: number };
export const WHEEL_NAMES = [
  "shadows",
  "midtones",
  "highlights",
  "offset",
] as const;
export const HSL_NAMES = [
  "red",
  "orange",
  "yellow",
  "green",
  "cyan",
  "blue",
  "purple",
  "magenta",
] as const;
export const HSL_HUES = [0, 30, 60, 120, 180, 240, 270, 300];
export const CURVE_NAMES = [
  "master",
  "red",
  "green",
  "blue",
  "hue_hue",
  "hue_sat",
  "hue_lum",
] as const;
export type CurveName = (typeof CURVE_NAMES)[number];
export type ColorGrade = {
  enabled: boolean;
  exposure: number;
  contrast: number;
  temperature: number;
  tint: number;
  saturation: number;
  vibrance: number;
  highlights: number;
  shadows: number;
  whites: number;
  blacks: number;
  wheels: Record<(typeof WHEEL_NAMES)[number], Wheel>;
  hsl: Record<(typeof HSL_NAMES)[number], Hsl>;
  curves: Record<CurveName, Point[]>;
};
export type ColorSettings = {
  color_preset: string | null;
  color_preset_intensity?: number;
  color_grade?: ColorGrade;
};
export const COLOR_PRESETS = presets;
export const defaultGrade = (): ColorGrade => ({
  enabled: true,
  exposure: 0,
  contrast: 1,
  temperature: 0,
  tint: 0,
  saturation: 1,
  vibrance: 0,
  highlights: 0,
  shadows: 0,
  whites: 0,
  blacks: 0,
  wheels: Object.fromEntries(
    WHEEL_NAMES.map((name) => [name, { hue: 0, saturation: 0, luminance: 0 }]),
  ) as ColorGrade["wheels"],
  hsl: Object.fromEntries(
    HSL_NAMES.map((name) => [name, { hue: 0, saturation: 0, luminance: 0 }]),
  ) as ColorGrade["hsl"],
  curves: Object.fromEntries(
    CURVE_NAMES.map((name) => [
      name,
      name.startsWith("hue_")
        ? [
            { x: 0, y: 0.5 },
            { x: 1, y: 0.5 },
          ]
        : [
            { x: 0, y: 0 },
            { x: 1, y: 1 },
          ],
    ]),
  ) as ColorGrade["curves"],
});
export function normalizeGrade(
  partial?: Partial<ColorGrade> | null,
): ColorGrade {
  const base = defaultGrade();
  if (!partial) return base;
  return {
    ...base,
    ...partial,
    wheels: Object.fromEntries(
      WHEEL_NAMES.map((k) => [
        k,
        { ...base.wheels[k], ...partial.wheels?.[k] },
      ]),
    ) as ColorGrade["wheels"],
    hsl: Object.fromEntries(
      HSL_NAMES.map((k) => [k, { ...base.hsl[k], ...partial.hsl?.[k] }]),
    ) as ColorGrade["hsl"],
    curves: { ...base.curves, ...partial.curves },
  };
}
export function hasColor(settings: ColorSettings) {
  return (
    !!(settings.color_preset && (settings.color_preset_intensity ?? 1) > 0) ||
    !!(
      settings.color_grade?.enabled &&
      JSON.stringify(settings.color_grade) !== JSON.stringify(defaultGrade())
    )
  );
}

/** 单调三次插值，控制点之间平滑过渡且不产生额外超调。 */
export function curveValue(points: Point[], x: number) {
  const slopes = points
    .slice(1)
    .map((p, i) => (p.y - points[i].y) / (p.x - points[i].x));
  const tangent = points.map((_, i) =>
    i === 0
      ? slopes[0]
      : i === points.length - 1
        ? slopes.at(-1)!
        : slopes[i - 1] * slopes[i] <= 0
          ? 0
          : 2 / (1 / slopes[i - 1] + 1 / slopes[i]),
  );
  const i = Math.max(0, points.findIndex((p) => p.x >= x) - 1);
  const a = points[i],
    b = points[i + 1],
    width = b.x - a.x,
    t = Math.max(0, Math.min(1, (x - a.x) / width));
  return (
    (2 * t * t * t - 3 * t * t + 1) * a.y +
    (t * t * t - 2 * t * t + t) * width * tangent[i] +
    (-2 * t * t * t + 3 * t * t) * b.y +
    (t * t * t - t * t) * width * tangent[i + 1]
  );
}

export function wheelRgb(wheel: Wheel) {
  const h = wheel.hue / 60;
  const rgb = [0, 4, 2].map((k) =>
    Math.max(0, Math.min(1, Math.abs(((h + k) % 6) - 3) - 1)),
  );
  const luma = rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
  return rgb.map(
    (v) => (v - luma) * wheel.saturation * 0.35 + wheel.luminance * 0.35,
  );
}
