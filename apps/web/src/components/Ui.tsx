// 提供工作台共用的图标、按钮和可访问弹窗组件。
import React, { useEffect, useRef } from "react";

const icons: Record<string, string> = {
  spark: "Spark",
  back: "Back",
  down: "Down",
  undo: "Undo",
  clock: "Clock",
  folder: "Folder",
  music: "Music",
  text: "Text",
  layers: "Layers",
  sliders: "Sliders",
  settings: "Settings",
};
const symbols: Record<string, string> = {
  close: "×",
  cut: "✂",
  redo: "↪",
  arrow: "↑",
  download: "↓",
  search: "⌕",
  full: "⛶",
  left: "‹",
  right: "›",
  check: "✓",
  grid: "▦",
  video: "▣",
  image: "▧",
};
const vectors: Record<string, React.ReactNode> = {
  save: <><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h12l4 4v12a2 2 0 0 1-2 2Z"/><path d="M7 3v6h10V3M7 21v-8h10v8"/></>,
  image: <><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8" cy="9" r="1.5"/><path d="m3 17 5-5 4 4 3-3 6 6"/></>,
  plus: <path d="M12 5v14M5 12h14" />,
  trash: <path d="M4 6h16M9 6V4h6v2M6 6l1 14h10l1-14M10 10v6M14 10v6" />,
  play: <path d="m8 5 11 7-11 7Z" fill="currentColor" stroke="none" />,
  pause: <><rect x="6" y="5" width="4" height="14" rx="0.8" fill="currentColor" stroke="none" /><rect x="14" y="5" width="4" height="14" rx="0.8" fill="currentColor" stroke="none" /></>,
};

/** 展示本地设计图标或新增操作的文字符号。
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
  return icons[name] ? (
    <span className={`icon icon-${name}`} aria-hidden="true">
      <img src={`/icons/imgIcon${icons[name]}.svg`} alt="" />
    </span>
  ) : (
    <span className="symbol" aria-hidden="true">
      {symbols[name] || "·"}
    </span>
  );
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
