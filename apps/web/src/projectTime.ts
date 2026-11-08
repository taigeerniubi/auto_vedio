// 以用户本地时区展示真实暂存时间，不把内部编辑序号作为用户版本名。
import type { Savepoint } from "./contracts";

export function formatSavepointTime(value: string | undefined | null) {
  if (!value) return "";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(date);
}

export function savepointLabel(saved?: Savepoint | null) {
  const time = formatSavepointTime(saved?.created_at);
  return time ? `暂存于 ${time}` : saved ? "已有暂存" : "已自动保存 · 尚未暂存";
}
