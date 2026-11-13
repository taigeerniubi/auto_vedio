// 提供顶栏外观选择器，支持键盘操作和点击外部关闭。
import { useEffect, useRef } from "react";
import { useTheme, type ThemePreference } from "../hooks/useTheme";
import { Icon } from "./Ui";

const choices: { id: ThemePreference; label: string; icon: string }[] = [
  { id: "light", label: "浅色", icon: "sun" },
  { id: "dark", label: "深色", icon: "moon" },
  { id: "system", label: "跟随系统", icon: "monitor" },
];

export function ThemeSwitcher() {
  const { preference, setPreference } = useTheme();
  const ref = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const outside = (event: PointerEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node))
        ref.current.open = false;
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && ref.current?.open) {
        ref.current.open = false;
        ref.current.querySelector("summary")?.focus();
      }
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, []);
  return (
    <details className="theme-switcher" ref={ref}>
      <summary aria-label="外观设置" title="外观设置">
        <Icon name={choices.find((choice) => choice.id === preference)!.icon} />
      </summary>
      <fieldset className="theme-menu">
        <legend>界面外观</legend>
        {choices.map((choice) => (
          <label className="theme-option" key={choice.id}>
            <Icon name={choice.icon} />
            <span>{choice.label}</span>
            <input
              type="radio"
              name="theme"
              value={choice.id}
              checked={preference === choice.id}
              onChange={() => setPreference(choice.id)}
            />
          </label>
        ))}
      </fieldset>
    </details>
  );
}
