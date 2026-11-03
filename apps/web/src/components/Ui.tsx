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
  plus: "+",
  close: "×",
  play: "▶",
  pause: "Ⅱ",
  cut: "✂",
  trash: "⌫",
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

/** 展示本地设计图标或新增操作的文字符号。
 * Args:
 *   name: 图标名称。
 * Returns:
 *   装饰性图标。
 */
export function Icon({ name }: { name: string }) {
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
