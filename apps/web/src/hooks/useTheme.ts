// 维护浅色、深色及跟随系统的外观偏好，支持跨标签同步。
import { useEffect, useState } from "react";

export type ThemePreference = "light" | "dark" | "system";
const storageKey = "shiguang.theme.v1";
const valid = (value: string | null | undefined): ThemePreference =>
  value === "light" || value === "dark" ? value : "system";

/** 应用主题并在系统外观变化时实时更新。 */
export function useTheme() {
  const [preference, setPreference] = useState<ThemePreference>(() =>
    valid(document.documentElement.dataset.themePreference),
  );
  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      const theme =
        preference === "system"
          ? media.matches
            ? "dark"
            : "light"
          : preference;
      document.documentElement.dataset.theme = theme;
      document.documentElement.dataset.themePreference = preference;
      document.documentElement.style.colorScheme = theme;
      document
        .querySelector('meta[name="theme-color"]')
        ?.setAttribute("content", theme === "dark" ? "#151719" : "#ffffff");
    };
    apply();
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [preference]);
  useEffect(() => {
    const sync = (event: StorageEvent) => {
      if (event.key === storageKey || event.key === null)
        setPreference(valid(event.newValue));
    };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, []);
  return {
    preference,
    setPreference: (next: ThemePreference) => {
      setPreference(next);
      try {
        localStorage.setItem(storageKey, next);
      } catch {
        /* 当前页面仍可切换。 */
      }
    },
  };
}
