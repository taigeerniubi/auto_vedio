// 加载并维护按工程保存的历史对话、分页和刷新后的运行进度。
import { useEffect, useRef, useState } from "react";
import { api, projectPath } from "../api";
import type { ConversationEntry, ConversationPage, Run } from "../contracts";

/** 合并较早记录和当前记录，保留正在页面上更新的结果。
 * Args:
 *   earlier: 从服务端读取的较早记录。
 *   current: 当前页面已有的记录。
 * Returns:
 *   按时间排列且按运行编号去重的记录。
 */
function merge(earlier: ConversationEntry[], current: ConversationEntry[]) {
  return [
    ...new Map(
      [...earlier, ...current].map((entry) => [entry.run_id, entry]),
    ).values(),
  ];
}

/** 提供工程隔离的历史记录读取和运行状态恢复。
 * Args:
 *   projectId: 当前工程编号。
 *   report: 向页面报告读取错误。
 * Returns:
 *   历史记录、加载状态、分页与追加动作。
 */
export function useConversation(
  projectId: string | undefined,
  report: (error: unknown) => void,
) {
  const [entries, setEntries] = useState<ConversationEntry[]>([]);
  const [owner, setOwner] = useState(projectId);
  const [cursor, setCursor] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const currentProject = useRef(projectId);
  currentProject.current = projectId;
  const generation = useRef(0);
  const loadingOlder = useRef(false);
  const reporter = useRef(report);
  reporter.current = report;

  useEffect(() => {
    const version = ++generation.current;
    setOwner(projectId);
    setEntries([]);
    setCursor(null);
    loadingOlder.current = false;
    if (!projectId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    api<ConversationPage>(`${projectPath(projectId)}/runs`)
      .then((page) => {
        if (generation.current !== version) return;
        setEntries((previous) => merge(page.items, previous));
        setCursor(page.next_cursor);
      })
      .catch((error) => {
        if (generation.current === version) reporter.current(error);
      })
      .finally(() => {
        if (generation.current === version) setLoading(false);
      });
    return () => {
      generation.current++;
    };
  }, [projectId]);

  /** 将当前完成或失败的运行加入历史，避免下一次指令覆盖它。
   * Args:
   *   run: 当前运行结果。
   *   instruction: 当前用户指令。
   */
  function remember(run: Run, instruction: string) {
    if (run.project_id && run.project_id !== currentProject.current) return;
    setEntries((previous) => {
      const entry = {
        ...run,
        instruction,
        created_at: new Date().toISOString(),
      };
      return previous.some((item) => item.run_id === run.run_id)
        ? previous.map((item) =>
            item.run_id === run.run_id ? { ...item, ...entry } : item,
          )
        : [...previous, entry];
    });
  }

  /** 加载较早对话；切换项目后的旧请求不会写入新项目。 */
  async function loadOlder() {
    if (!projectId || cursor === null || loadingOlder.current || loading)
      return;
    const version = generation.current;
    loadingOlder.current = true;
    setLoading(true);
    try {
      const page = await api<ConversationPage>(
        `${projectPath(projectId)}/runs?before=${cursor}`,
      );
      if (generation.current !== version) return;
      setEntries((previous) => merge(page.items, previous));
      setCursor(page.next_cursor);
    } catch (error) {
      if (generation.current === version) reporter.current(error);
    } finally {
      if (generation.current === version) {
        loadingOlder.current = false;
        setLoading(false);
      }
    }
  }

  // 重新打开页面后继续读取已存在的运行，不重复提交模型指令。
  const runningIds = entries
    .filter((entry) => entry.status === "running")
    .map((entry) => entry.run_id)
    .join(",");
  useEffect(() => {
    if (!projectId || !runningIds) return;
    let cancelled = false,
      polling = false;
    const timer = window.setInterval(async () => {
      if (polling) return;
      polling = true;
      try {
        const updates = await Promise.all(
          runningIds
            .split(",")
            .map((id) => api<Run>(`${projectPath(projectId)}/runs/${id}`)),
        );
        if (!cancelled && currentProject.current === projectId)
          setEntries((previous) =>
            previous.map((entry) => {
              const update = updates.find((run) => run.run_id === entry.run_id);
              return entry.status === "running" && update ? { ...entry, ...update } : entry;
            }),
          );
      } catch {
        // 暂时断网不丢弃历史，下一轮继续读取，不发起模型请求。
      } finally {
        polling = false;
      }
    }, 1500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [projectId, runningIds]);

  return {
    entries: owner === projectId ? entries : [],
    loading,
    hasMore: owner === projectId && cursor !== null,
    loadOlder,
    remember,
  };
}
