// 提供键盘和指针均可操作的色轮及平滑曲线编辑器。
import { useId, useRef, useState } from "react";
import {
  curveValue,
  type Point,
  type Wheel,
} from "../../../../packages/composition/color";

export function ColorSlider({
  label,
  value,
  min = -1,
  max = 1,
  step = 0.01,
  onChange,
  disabled = false,
}: {
  label: string;
  value: number;
  min?: number;
  max?: number;
  step?: number;
  onChange: (v: number) => void;
  disabled?: boolean;
}) {
  return (
    <label className="color-slider">
      <span>{label}</span>
      <input
        type="range"
        aria-label={label}
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      <input
        type="number"
        aria-label={`${label}数值`}
        min={min}
        max={max}
        step={step}
        value={Number(value.toFixed(3))}
        disabled={disabled}
        onChange={(event) => {
          if (
            event.target.value !== "" &&
            Number.isFinite(event.target.valueAsNumber)
          )
            onChange(Math.max(min, Math.min(max, event.target.valueAsNumber)));
        }}
      />
    </label>
  );
}

export function ColorWheel({
  label,
  value,
  onChange,
  disabled,
}: {
  label: string;
  value: Wheel;
  onChange: (v: Wheel) => void;
  disabled: boolean;
}) {
  const active = useRef(false);
  const update = (event: React.PointerEvent<HTMLDivElement>) => {
    const r = event.currentTarget.getBoundingClientRect(),
      x = (event.clientX - r.left - r.width / 2) / (r.width / 2),
      y = (event.clientY - r.top - r.height / 2) / (r.height / 2);
    onChange({
      ...value,
      hue: ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360,
      saturation: Math.min(1, Math.hypot(x, y)),
    });
  };
  const angle = (value.hue * Math.PI) / 180;
  return (
    <div className="grade-wheel-control">
      <div
        className="grade-wheel"
        role="slider"
        tabIndex={disabled ? -1 : 0}
        aria-label={`${label}色轮`}
        aria-disabled={disabled}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(value.saturation * 100)}
        aria-valuetext={`色相 ${Math.round(value.hue)} 度，强度 ${Math.round(value.saturation * 100)}%`}
        onPointerDown={(event) => {
          if (disabled) return;
          event.preventDefault();
          active.current = true;
          event.currentTarget.setPointerCapture(event.pointerId);
          update(event);
        }}
        onPointerMove={(event) => {
          if (active.current) update(event);
        }}
        onPointerUp={() => {
          active.current = false;
        }}
        onPointerCancel={() => {
          active.current = false;
        }}
        onDoubleClick={() =>
          !disabled && onChange({ hue: 0, saturation: 0, luminance: 0 })
        }
        onKeyDown={(event) => {
          if (disabled) return;
          let next = value;
          if (event.key === "ArrowLeft" || event.key === "ArrowRight")
            next = {
              ...value,
              hue:
                (value.hue + (event.key === "ArrowRight" ? 5 : -5) + 360) % 360,
            };
          else if (event.key === "ArrowUp" || event.key === "ArrowDown")
            next = {
              ...value,
              saturation: Math.max(
                0,
                Math.min(
                  1,
                  value.saturation + (event.key === "ArrowUp" ? 0.02 : -0.02),
                ),
              ),
            };
          else if (event.key === "Home")
            next = { hue: 0, saturation: 0, luminance: 0 };
          else return;
          event.preventDefault();
          event.stopPropagation();
          onChange(next);
        }}
      >
        <i
          style={{
            left: `${50 + 46 * Math.cos(angle) * value.saturation}%`,
            top: `${50 + 46 * Math.sin(angle) * value.saturation}%`,
          }}
        />
      </div>
      <ColorSlider
        label={`${label}色相`}
        value={value.hue}
        min={0}
        max={360}
        step={1}
        disabled={disabled}
        onChange={(hue) => onChange({ ...value, hue })}
      />
      <ColorSlider
        label={`${label}强度`}
        value={value.saturation}
        min={0}
        max={1}
        disabled={disabled}
        onChange={(saturation) => onChange({ ...value, saturation })}
      />
      <ColorSlider
        label={`${label}亮度`}
        value={value.luminance}
        disabled={disabled}
        onChange={(luminance) => onChange({ ...value, luminance })}
      />
    </div>
  );
}

export function CurveEditor({
  points,
  onChange,
  hue,
  disabled,
  label,
}: {
  points: Point[];
  onChange: (p: Point[]) => void;
  hue: boolean;
  disabled: boolean;
  label: string;
}) {
  const [selected, setSelected] = useState(0),
    drag = useRef<number | null>(null),
    id = useId().replaceAll(":", "");
  function replace(index: number, x: number, y: number) {
    const next = points.map((p) => ({ ...p }));
    next[index] = {
      x:
        index === 0
          ? 0
          : index === points.length - 1
            ? 1
            : Math.max(
                points[index - 1].x + 0.002,
                Math.min(points[index + 1].x - 0.002, x),
              ),
      y: Math.max(0, Math.min(1, y)),
    };
    if (hue && (index === 0 || index === points.length - 1)) {
      next[0].y = next[index].y;
      next[next.length - 1].y = next[index].y;
    }
    onChange(next);
  }
  function position(event: React.PointerEvent<SVGSVGElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)),
      y: Math.max(0, Math.min(1, 1 - (event.clientY - box.top) / box.height)),
    };
  }
  function remove(index: number) {
    if (disabled || index === 0 || index === points.length - 1) return;
    onChange(points.filter((_, i) => i !== index));
    setSelected(0);
  }
  const path = Array.from(
    { length: 129 },
    (_, i) =>
      `${i ? "L" : "M"}${(i / 128) * 240},${(1 - curveValue(points, i / 128)) * 180}`,
  ).join(" ");
  return (
    <div className="curve-editor">
      <svg
        viewBox="0 0 240 180"
        preserveAspectRatio="none"
        role="group"
        aria-label={`${label}编辑器`}
        data-curve-editor={label}
        onPointerDown={(event) => {
          if (disabled) return;
          event.preventDefault();
          const hit = (event.target as Element).getAttribute("data-point");
          let index = hit === null ? -1 : Number(hit);
          if (index < 0) {
            const p = position(event);
            if (
              points.length >= 16 ||
              points.some((item) => Math.abs(item.x - p.x) < 0.008)
            )
              return;
            const next = [...points, p].sort((a, b) => a.x - b.x);
            index = next.indexOf(p);
            onChange(next);
          }
          drag.current = index;
          setSelected(index);
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          if (drag.current !== null) {
            const p = position(event);
            replace(drag.current, p.x, p.y);
          }
        }}
        onPointerUp={() => {
          drag.current = null;
        }}
        onPointerCancel={() => {
          drag.current = null;
        }}
      >
        <defs>
          <linearGradient id={id}>
            <stop offset="0%" stopColor="#ff6a68" />
            <stop offset="17%" stopColor="#e4cd62" />
            <stop offset="33%" stopColor="#6dd38a" />
            <stop offset="50%" stopColor="#6bdada" />
            <stop offset="67%" stopColor="#778aef" />
            <stop offset="83%" stopColor="#cd7cea" />
            <stop offset="100%" stopColor="#ff6a68" />
          </linearGradient>
        </defs>
        <rect width="240" height="180" fill="#101615" />
        {[0.25, 0.5, 0.75].map((v) => (
          <path
            key={v}
            d={`M${240 * v},0V180 M0,${180 * v}H240`}
            stroke="#ffffff12"
          />
        ))}
        <path
          d={hue ? "M0,90H240" : "M0,180L240,0"}
          stroke="#ffffff28"
          strokeDasharray="4 4"
        />
        <path
          d={path}
          stroke={hue ? `url(#${id})` : "#b8e695"}
          fill="none"
          strokeWidth="2"
        />
        {points.map((p, i) => (
          <circle
            key={i}
            cx={p.x * 240}
            cy={(1 - p.y) * 180}
            r={i === selected ? 5 : 4}
            data-point={i}
            tabIndex={disabled ? -1 : 0}
            role="button"
            aria-label={`${label}控制点 ${i + 1}`}
            fill={i === selected ? "#d4f7bc" : "#6f8d63"}
            stroke="#101615"
            strokeWidth="1"
            onFocus={() => setSelected(i)}
            onDoubleClick={(event) => {
              event.stopPropagation();
              remove(i);
            }}
            onKeyDown={(event) => {
              event.stopPropagation();
              if (disabled) return;
              if (event.key === "Delete" || event.key === "Backspace") {
                event.preventDefault();
                remove(i);
              } else if (event.key.startsWith("Arrow")) {
                event.preventDefault();
                const d = event.shiftKey ? 0.05 : 0.01;
                replace(
                  i,
                  p.x +
                    (event.key === "ArrowRight"
                      ? d
                      : event.key === "ArrowLeft"
                        ? -d
                        : 0),
                  p.y +
                    (event.key === "ArrowUp"
                      ? d
                      : event.key === "ArrowDown"
                        ? -d
                        : 0),
                );
              }
            }}
          />
        ))}
      </svg>
      <small>点击添加控制点 · 拖动调整 · Delete 删除中间点</small>
      {points[selected] && (
        <div className="curve-coordinates">
          <span>
            输入 {points[selected].x.toFixed(2)} / 输出{" "}
            {points[selected].y.toFixed(2)}
          </span>
          <button
            type="button"
            disabled={
              disabled || selected === 0 || selected === points.length - 1
            }
            onClick={() => remove(selected)}
          >
            删除点
          </button>
        </div>
      )}
    </div>
  );
}
