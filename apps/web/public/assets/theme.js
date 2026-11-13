// 在界面绘制前应用主题，避免刷新时闪烁。
(() => {
  let preference = "system";
  try {
    const saved = localStorage.getItem("shiguang.theme.v1");
    if (["light", "dark", "system"].includes(saved)) preference = saved;
  } catch {
    /* 存储不可用时跟随系统。 */
  }
  const theme =
    preference === "system"
      ? matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light"
      : preference;
  document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.themePreference = preference;
  document.documentElement.style.colorScheme = theme;
})();
