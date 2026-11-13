// 管理工作台的面板分隔线和当前浏览器的布局偏好。
import {
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent,
  type ReactNode,
} from "react";

type Mode = "desktop" | "tablet" | "phone";
type Panel = "library" | "dock" | "timeline" | "monitor";
type Edge = "left" | "right" | "top" | "bottom";
type Bounds = {
  mode: Mode;
  width: number;
  height: number;
  viewportHeight: number;
  gap: number;
};
type Divider = {
  panel: Panel;
  edge: Edge;
  area: string;
  min: number;
  max: number;
  size: number;
};
type Preferences = Record<string, number>;
const storageKey = "shiguang.workspace-layout.v1";
const names = {
  library: "素材栏",
  dock: "助手与属性栏",
  timeline: "时间线",
  monitor: "预览区",
};
const clamp = (value: number, min: number, max: number) =>
  Math.max(min, Math.min(max, Math.round(value)));

function readPreferences(): Preferences {
  try {
    const data: unknown = JSON.parse(localStorage.getItem(storageKey) || "{}");
    if (data && typeof data === "object" && !Array.isArray(data))
      return Object.fromEntries(
        Object.entries(data).filter(
          ([, value]) => typeof value === "number" && Number.isFinite(value),
        ),
      );
  } catch {
    /* 禁用存储时仍可在当前页面调整布局。 */
  }
  return {};
}

/** 按可用空间限制面板尺寸，为预览和时间线控件保留操作空间。 */
function getLayout(bounds: Bounds, preferences: Preferences) {
  const { mode, width, height, viewportHeight: vh, gap } = bounds;
  const dividers: Divider[] = [];
  function track(
    panel: Panel,
    edge: Edge,
    fallback: number,
    min: number,
    max: number,
    area: string = panel,
  ) {
    max = Math.max(min, Math.floor(max));
    const size = clamp(preferences[`${mode}.${panel}`] ?? fallback, min, max);
    const divider = { panel, edge, area, min, max, size };
    dividers.push(divider);
    return divider;
  }

  const style: CSSProperties = {};
  if (mode === "desktop") {
    const sideSpace = width - 2 * gap - 340;
    const compact = width + 12 <= 1150;
    const left = track(
      "library",
      "right",
      compact ? 270 : clamp(width * 0.245, 300, 380),
      250,
      sideSpace - 260,
    );
    const right = track(
      "dock",
      "left",
      compact ? 280 : clamp(width * 0.235, 310, 390),
      260,
      sideSpace - left.size,
    );
    left.max = Math.floor(sideSpace - right.size);
    const timeline = track(
      "timeline",
      "top",
      clamp(vh * 0.3, 264, 320),
      156,
      height - gap - 225,
    );
    style.gridTemplateColumns = `${left.size}px minmax(0, 1fr) ${right.size}px`;
    style.gridTemplateRows = `minmax(0, 1fr) ${timeline.size}px`;
  } else if (mode === "tablet") {
    const left = track(
      "library",
      "right",
      Math.max(240, width * 0.32),
      214,
      width - gap - 320,
      "1 / 1 / 3 / 2",
    );
    const defaultTop = clamp(vh * 0.52, 340, 520);
    const total = defaultTop + Math.max(350, Math.round(vh * 0.44));
    const top = track(
      "monitor",
      "bottom",
      defaultTop,
      225,
      total - 245,
      "1 / 1 / 2 / 3",
    );
    style.gridTemplateColumns = `${left.size}px minmax(0, 1fr)`;
    style.gridTemplateRows = `${top.size}px ${total - top.size}px`;
  } else {
    const max = Math.max(540, vh);
    const preview = track(
      "monitor",
      "bottom",
      clamp(width * 0.77, 290, 415),
      215,
      max,
    );
    const library = track("library", "bottom", 320, 195, max);
    const timeline = track("timeline", "bottom", 310, 156, max);
    const dock = track("dock", "bottom", clamp(vh * 0.6, 380, 530), 245, max);
    style.gridTemplateRows = [preview, library, timeline, dock]
      .map((d) => `${d.size}px`)
      .join(" ");
  }
  return { style, dividers };
}

function PanelDivider({
  divider,
  onPreview,
  onCommit,
  onCancel,
}: {
  divider: Divider;
  onPreview: (size: number) => void;
  onCommit: (size: number | null) => void;
  onCancel: () => void;
}) {
  const { panel, edge, area, min, max, size } = divider;
  const vertical = edge === "left" || edge === "right";
  const sign = edge === "left" || edge === "top" ? -1 : 1;
  const drag = useRef<{ id: number; coordinate: number; size: number } | null>(
    null,
  );
  const coordinate = (event: PointerEvent) =>
    vertical ? event.clientX : event.clientY;
  const deltaSize = (event: PointerEvent) => {
    const start = drag.current!;
    return clamp(
      start.size + sign * (coordinate(event) - start.coordinate),
      min,
      max,
    );
  };
  const label = names[panel] + (vertical ? "宽度" : "高度");

  return (
    <div
      role="separator"
      tabIndex={0}
      className="panel-divider"
      data-edge={edge}
      style={{ gridArea: area }}
      aria-label={label}
      aria-controls={`workspace-${panel}`}
      aria-orientation={vertical ? "vertical" : "horizontal"}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={size}
      aria-valuetext={`${size} 像素`}
      title={`拖动调整${label}，双击恢复默认`}
      onPointerDown={(event) => {
        if (!event.isPrimary || event.button !== 0 || drag.current) return;
        event.currentTarget.focus({ preventScroll: true });
        event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = {
          id: event.pointerId,
          coordinate: coordinate(event),
          size,
        };
      }}
      onPointerMove={(event) => {
        if (drag.current?.id === event.pointerId) onPreview(deltaSize(event));
      }}
      onPointerUp={(event) => {
        if (drag.current?.id !== event.pointerId) return;
        if (coordinate(event) !== drag.current.coordinate)
          onCommit(deltaSize(event));
        else onCancel();
        drag.current = null;
      }}
      onLostPointerCapture={() => {
        drag.current = null;
        onCancel();
      }}
      onDoubleClick={() => onCommit(null)}
      onKeyDown={(event) => {
        const backwards = vertical ? "ArrowLeft" : "ArrowUp";
        const forwards = vertical ? "ArrowRight" : "ArrowDown";
        if (event.key === "Enter") onCommit(null);
        else if (event.key === "Home") onCommit(min);
        else if (event.key === "End") onCommit(max);
        else if (event.key === backwards || event.key === forwards)
          onCommit(
            clamp(
              size + (event.key === forwards ? 1 : -1) * sign * 16,
              min,
              max,
            ),
          );
        else return;
        event.preventDefault();
      }}
    />
  );
}

export function ResizableWorkspace({ children }: { children: ReactNode }) {
  const root = useRef<HTMLElement>(null);
  const [bounds, setBounds] = useState<Bounds | null>(null);
  const [preferences, setPreferences] = useState(readPreferences);
  const [preview, setPreview] = useState<Preferences>({});

  useLayoutEffect(() => {
    const node = root.current!;
    const measure = () => {
      const css = getComputedStyle(node);
      const mode = css.getPropertyValue("--workspace-mode").trim() as Mode;
      setBounds({
        mode,
        width:
          node.clientWidth -
          parseFloat(css.paddingLeft) -
          parseFloat(css.paddingRight),
        height:
          node.clientHeight -
          parseFloat(css.paddingTop) -
          parseFloat(css.paddingBottom),
        viewportHeight: window.innerHeight,
        gap: parseFloat(css.columnGap),
      });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    window.addEventListener("resize", measure);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, []);
  useLayoutEffect(() => {
    setPreview({});
  }, [bounds?.mode]);

  const layout = bounds && getLayout(bounds, { ...preferences, ...preview });
  function commit(key: string, size: number | null) {
    const next = { ...preferences };
    if (size === null) delete next[key];
    else next[key] = size;
    setPreview({});
    setPreferences(next);
    try {
      localStorage.setItem(storageKey, JSON.stringify(next));
    } catch {
      /* 当前页面保留设置。 */
    }
  }
  return (
    <main ref={root} className="workspace" style={layout?.style}>
      {children}
      {bounds &&
        layout?.dividers.map((divider) => {
          const key = `${bounds.mode}.${divider.panel}`;
          return (
            <PanelDivider
              key={key}
              divider={divider}
              onPreview={(size) => setPreview({ [key]: size })}
              onCommit={(size) => commit(key, size)}
              onCancel={() => setPreview({})}
            />
          );
        })}
    </main>
  );
}
