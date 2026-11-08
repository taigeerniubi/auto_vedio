// 组织预设、基础调节、色轮、分色及曲线，先实时预览再原子保存。
import { useEffect, useState } from "react";
import {
  COLOR_PRESETS,
  defaultGrade,
  normalizeGrade,
  HSL_NAMES,
  HSL_HUES,
  WHEEL_NAMES,
  type ColorGrade,
  type ColorSettings,
  type CurveName,
} from "../../../../packages/composition/color";
import type { Editor } from "../hooks/useEditor";
import { Button } from "./Ui";
import { ColorSlider, ColorWheel, CurveEditor } from "./ColorControls";

export type ColorDraft = (ColorSettings & { clip_id: string }) | null;
const wheelLabels = {
  shadows: "暗部",
  midtones: "中间调",
  highlights: "亮部",
  offset: "全局",
};
const hslLabels = ["红", "橙", "黄", "绿", "青", "蓝", "紫", "品红"];
const curveLabels = {
  master: "总亮度曲线",
  red: "红色曲线",
  green: "绿色曲线",
  blue: "蓝色曲线",
  hue_hue: "色相对色相",
  hue_sat: "色相对饱和度",
  hue_lum: "色相对亮度",
};
const basics: [
  keyof Pick<
    ColorGrade,
    | "exposure"
    | "contrast"
    | "temperature"
    | "tint"
    | "saturation"
    | "vibrance"
    | "highlights"
    | "shadows"
    | "whites"
    | "blacks"
  >,
  string,
  number,
  number,
][] = [
  ["exposure", "曝光 / EV", -5, 5],
  ["contrast", "对比度", 0, 2],
  ["temperature", "色温", -1, 1],
  ["tint", "色调", -1, 1],
  ["saturation", "饱和度", 0, 2],
  ["vibrance", "鲜艳度", -1, 1],
  ["highlights", "高光", -1, 1],
  ["shadows", "阴影", -1, 1],
  ["whites", "白色色阶", -1, 1],
  ["blacks", "黑色色阶", -1, 1],
];
export function ColorPanel({
  editor: e,
  onDraft,
}: {
  editor: Editor;
  onDraft: (value: ColorDraft) => void;
}) {
  const clip = e.project?.snapshot.clips.find((c) => c.clip_id === e.selected);
  const [grade, setGrade] = useState(defaultGrade),
    [preset, setPreset] = useState<string | null>(null),
    [intensity, setIntensity] = useState(1);
  const [tab, setTab] = useState("basic"),
    [family, setFamily] = useState("全部"),
    [wheel, setWheel] = useState<(typeof WHEEL_NAMES)[number]>("shadows");
  const [hsl, setHsl] = useState<(typeof HSL_NAMES)[number]>("red"),
    [curve, setCurve] = useState<CurveName>("master"),
    [compare, setCompare] = useState(false);
  const [dirty, setDirty] = useState(false);
  const disabled = !!e.busy || !clip;
  useEffect(() => {
    setGrade(normalizeGrade(clip?.color_grade));
    setPreset(clip?.color_preset || null);
    setIntensity(clip?.color_preset_intensity ?? 1);
    setDirty(false);
    setCompare(false);
    onDraft(null);
  }, [e.selected, e.project?.revision, e.project?.project_id]);
  useEffect(() => () => onDraft(null), []);
  useEffect(() => {
    onDraft(
      clip && (dirty || compare)
        ? {
            clip_id: clip.clip_id,
            color_grade: compare ? defaultGrade() : grade,
            color_preset: compare ? null : preset,
            color_preset_intensity: intensity,
          }
        : null,
    );
  }, [grade, preset, intensity, compare, dirty, clip?.clip_id]);
  const change = (next: ColorGrade) => {
    setDirty(true);
    setGrade(next);
  };
  async function apply(all = false) {
    if (!clip) return;
    const ids = all
      ? e.project!.snapshot.clips.map((c) => c.clip_id)
      : [clip.clip_id];
    if (
      await e.edit("set_color_grade", {
        clip_ids: ids,
        adjustments: grade,
        replace: true,
        preset_id: preset,
        intensity,
      })
    ) {
      setDirty(false);
      onDraft(null);
    }
  }
  return (
    <section className="color-panel" aria-label="画面调色">
      <div className="panel-heading">
        <h2>画面调色</h2>
        <span className="chip">SDR · 浮点</span>
      </div>
      <div className="color-target">
        <span>
          {clip
            ? e.assets.find((a) => a.asset_id === clip.asset_id)?.name
            : "请先选择时间线中的画面"}
        </span>
        {dirty && <small>尚未应用</small>}
      </div>
      <nav className="color-tabs" aria-label="调色工具">
        {[
          ["basic", "基础"],
          ["wheels", "色轮"],
          ["hsl", "HSL"],
          ["curves", "曲线"],
          ["presets", "预设"],
        ].map(([id, label]) => (
          <button key={id} aria-pressed={tab === id} onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
      </nav>
      <fieldset className="color-controls" disabled={disabled}>
        {tab === "basic" && (
          <>
            <div className="color-group-heading">
              <strong>明暗与色彩</strong>
              <button onClick={() => change(defaultGrade())}>重置调节</button>
            </div>
            {basics.map(([key, label, min, max]) => (
              <ColorSlider
                key={key}
                label={label}
                value={grade[key]}
                min={min}
                max={max}
                onChange={(v) => change({ ...grade, [key]: v })}
              />
            ))}
            <label className="check-label">
              <input
                type="checkbox"
                checked={grade.enabled}
                onChange={(event) =>
                  change({ ...grade, enabled: event.target.checked })
                }
              />
              启用手动调色
            </label>
          </>
        )}
        {tab === "wheels" && (
          <>
            <div className="color-subtabs">
              {WHEEL_NAMES.map((k) => (
                <button
                  key={k}
                  aria-pressed={wheel === k}
                  onClick={() => setWheel(k)}
                >
                  {wheelLabels[k]}
                </button>
              ))}
            </div>
            <p className="muted">将中心点拖向所需颜色，离中心越远效果越强。</p>
            <ColorWheel
              label={wheelLabels[wheel]}
              value={grade.wheels[wheel]}
              disabled={disabled}
              onChange={(v) =>
                change({ ...grade, wheels: { ...grade.wheels, [wheel]: v } })
              }
            />
            <Button
              tone="ghost"
              onClick={() =>
                change({
                  ...grade,
                  wheels: {
                    ...grade.wheels,
                    [wheel]: { hue: 0, saturation: 0, luminance: 0 },
                  },
                })
              }
            >
              重置{wheelLabels[wheel]}色轮
            </Button>
          </>
        )}
        {tab === "hsl" && (
          <>
            <div className="hsl-channels">
              {HSL_NAMES.map((k, i) => (
                <button
                  key={k}
                  aria-label={`${hslLabels[i]}色通道`}
                  aria-pressed={hsl === k}
                  onClick={() => setHsl(k)}
                  style={
                    {
                      "--channel": `hsl(${HSL_HUES[i]} 65% 55%)`,
                    } as React.CSSProperties
                  }
                >
                  {hslLabels[i]}
                </button>
              ))}
            </div>
            <p className="muted">只调整选定颜色，邻近颜色平滑过渡。</p>
            <ColorSlider
              label="分色色相 / 度"
              value={grade.hsl[hsl].hue}
              min={-180}
              max={180}
              step={1}
              onChange={(v) =>
                change({
                  ...grade,
                  hsl: { ...grade.hsl, [hsl]: { ...grade.hsl[hsl], hue: v } },
                })
              }
            />
            <ColorSlider
              label="分色饱和度"
              value={grade.hsl[hsl].saturation}
              onChange={(v) =>
                change({
                  ...grade,
                  hsl: {
                    ...grade.hsl,
                    [hsl]: { ...grade.hsl[hsl], saturation: v },
                  },
                })
              }
            />
            <ColorSlider
              label="分色亮度"
              value={grade.hsl[hsl].luminance}
              onChange={(v) =>
                change({
                  ...grade,
                  hsl: {
                    ...grade.hsl,
                    [hsl]: { ...grade.hsl[hsl], luminance: v },
                  },
                })
              }
            />
            <Button
              tone="ghost"
              onClick={() =>
                change({
                  ...grade,
                  hsl: {
                    ...grade.hsl,
                    [hsl]: { hue: 0, saturation: 0, luminance: 0 },
                  },
                })
              }
            >
              重置当前通道
            </Button>
          </>
        )}
        {tab === "curves" && (
          <>
            <label>
              曲线通道
              <select
                aria-label="曲线通道"
                value={curve}
                onChange={(event) => setCurve(event.target.value as CurveName)}
              >
                {Object.entries(curveLabels).map(([id, label]) => (
                  <option key={id} value={id}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <CurveEditor
              key={curve}
              label={curveLabels[curve]}
              points={grade.curves[curve]}
              hue={curve.startsWith("hue_")}
              disabled={disabled}
              onChange={(v) =>
                change({ ...grade, curves: { ...grade.curves, [curve]: v } })
              }
            />
            <Button
              tone="ghost"
              onClick={() =>
                change({
                  ...grade,
                  curves: {
                    ...grade.curves,
                    [curve]: defaultGrade().curves[curve],
                  },
                })
              }
            >
              重置当前曲线
            </Button>
          </>
        )}
        {tab === "presets" && (
          <>
            <label>
              风格分类
              <select
                aria-label="滤镜分类"
                value={family}
                onChange={(event) => setFamily(event.target.value)}
              >
                {["全部", ...new Set(COLOR_PRESETS.map((p) => p.family))].map(
                  (f) => (
                    <option key={f}>{f}</option>
                  ),
                )}
              </select>
            </label>
            <div className="grade-presets">
              <button
                className={!preset ? "chosen" : ""}
                onClick={() => {
                  setPreset(null);
                  setDirty(true);
                }}
              >
                原色
              </button>
              {COLOR_PRESETS.filter(
                (p) => family === "全部" || p.family === family,
              ).map((p) => (
                <button
                  key={p.id}
                  className={preset === p.id ? "chosen" : ""}
                  onClick={() => {
                    setPreset(p.id);
                    setDirty(true);
                  }}
                >
                  <i className={`grade-swatch grade-${p.family}`} />
                  {p.label}
                </button>
              ))}
            </div>
            <ColorSlider
              label="滤镜强度 / %"
              value={intensity * 100}
              min={0}
              max={100}
              step={1}
              onChange={(v) => {
                setIntensity(v / 100);
                setDirty(true);
              }}
            />
          </>
        )}
      </fieldset>
      <div className="color-footer">
        <div className="row">
          <Button
            tone="ghost"
            disabled={disabled}
            aria-pressed={compare}
            onClick={() => setCompare((v) => !v)}
          >
            {compare ? "返回调色" : "对比原图"}
          </Button>
          <Button
            tone="ghost"
            disabled={disabled}
            onClick={() => {
              change(defaultGrade());
              setPreset(null);
              setIntensity(1);
            }}
          >
            全部重置
          </Button>
        </div>
        <Button
          tone="primary"
          disabled={disabled || !dirty}
          onClick={() => void apply()}
        >
          应用调色
        </Button>
        <Button
          tone="ghost"
          disabled={disabled || !e.project?.snapshot.clips.length}
          onClick={() => void apply(true)}
        >
          应用到所有片段
        </Button>
      </div>
    </section>
  );
}
