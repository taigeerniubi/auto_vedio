// 提供工作台共用的图标、按钮和可访问弹窗组件。
import React, { useEffect, useRef } from "react";

const vectors: Record<string, React.ReactNode> = {
  spark: <><path d="m12 3 2.6 6.4L21 12l-6.4 2.6L12 21l-2.6-6.4L3 12l6.4-2.6Z"/><path d="m20 2 .8 2.2L23 5l-2.2.8L20 8l-.8-2.2L17 5l2.2-.8Z"/></>,
  back: <><path d="m10 5-7 7 7 7M3 12h18"/></>,
  down: <path d="m6 9 6 6 6-6" />,
  left: <path d="m14 6-6 6 6 6" />,
  right: <path d="m10 6 6 6-6 6" />,
  undo: <><path d="M3 10h11a6 6 0 0 1 0 12M3 10l5-5M3 10l5 5"/></>,
  redo: <><path d="M21 10H10a6 6 0 0 0 0 12M21 10l-5-5M21 10l-5 5"/></>,
  clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
  folder: <path d="M3 7V5a2 2 0 0 1 2-2h5l3 3h6a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/>,
  music: <><path d="M9 18V5l11-2v13M9 8l11-2"/><ellipse cx="6" cy="18" rx="3" ry="3"/><ellipse cx="17" cy="16" rx="3" ry="3"/></>,
  text: <><path d="M4 7V4h16v3M12 4v16M8 20h8"/></>,
  layers: <><path d="m12 3 10 5-10 5L2 8l10-5ZM2 12l10 5 10-5M2 16l10 5 10-5"/></>,
  sliders: <><path d="M5 3v3m0 4v11M12 3v11m0 4v3M19 3v5m0 4v9"/><path d="M2 6h6v4H2ZM9 14h6v4H9ZM16 8h6v4h-6Z"/></>,
  settings: <><path d="m9 3-1 3-3 1-2 3 2 2-1 3 3 2 1 4h4l1-3 3-1 3 1 2-3-2-2 1-3-3-2-1-4Z"/><circle cx="12" cy="12" r="3"/></>,
  close: <path d="m6 6 12 12M6 18 18 6"/>,
  cut: <><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="m8 8 13 13M8 16 21 3"/></>,
  arrow: <path d="M12 20V4m-6 6 6-6 6 6"/>,
  download: <><path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/></>,
  search: <><circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/></>,
  full: <path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>,
  check: <path d="m5 12 4 4L19 6"/>,
  grid: <><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></>,
  video: <><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3Z"/></>,
  sun: <><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/></>,
  moon: <path d="M20.5 13a9 9 0 0 1-9.5-9.5A9 9 0 1 0 20.5 13Z"/>,
  monitor: <><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M12 17v4m-4 0h8"/></>,
  save: <><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h12l4 4v12a2 2 0 0 1-2 2Z"/><path d="M7 3v6h10V3M7 21v-8h10v8"/></>,
  image: <><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8" cy="9" r="1.5"/><path d="m3 17 5-5 4 4 3-3 6 6"/></>,
  plus: <path d="M12 5v14M5 12h14" />,
  trash: <path d="M4 6h16M9 6V4h6v2M6 6l1 14h10l1-14M10 10v6M14 10v6" />,
  play: <path d="m8 5 11 7-11 7Z" fill="currentColor" stroke="none" />,
  pause: <><rect x="6" y="5" width="4" height="14" rx="0.8" fill="currentColor" stroke="none" /><rect x="14" y="5" width="4" height="14" rx="0.8" fill="currentColor" stroke="none" /></>,
};

/** 用继承当前文字颜色的矢量图标适配两种主题。
 * Args:
 *   name: 图标名称。
 * Returns:
 *   装饰性图标。
 */
export function Icon({ name }: { name: string }) {
  if (vectors[name]) return (
    <svg className={`vector-icon icon-${name}`} width="18" height="18" viewBox="0 0 24 24"
      fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true" focusable="false">{vectors[name]}</svg>
  );
  return <span className="symbol" aria-hidden="true">·</span>;
}

/** 创建带统一状态的操作按钮。
 * Args:
 *   props: 原生按钮属性、图标与外观。
 * Returns:
 *   可访问的按钮。
 */
export function Button({
  icon,
  children,
  tone = "default",
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  icon?: string;
  tone?: "default" | "primary" | "ghost";
}) {
  return (
    <button type="button" className={`button ${tone} ${className}`} {...props}>
      {icon && <Icon name={icon} />} {children}
    </button>
  );
}

/** 创建带焦点约束及关闭操作的原生模态弹窗。
 * Args:
 *   title: 弹窗标题。
 *   children: 弹窗内容。
 *   onClose: 关闭回调。
 * Returns:
 *   模态对话框。
 */
export function Modal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  return (
    <dialog
      ref={ref}
      className="modal"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      aria-label={title}
    >
      <header>
        <h2>{title}</h2>
        <Button
          icon="close"
          tone="ghost"
          aria-label="关闭弹窗"
          onClick={onClose}
        />
      </header>
      {children}
    </dialog>
  );
}
