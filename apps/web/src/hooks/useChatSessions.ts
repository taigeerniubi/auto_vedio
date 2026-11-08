// 打开工程时进入未保存的新聊天，首条消息被接受后才记录会话。
import { useEffect, useRef, useState } from "react";
import { api, projectPath } from "../api";
import type { ChatSession, ConversationPage } from "../contracts";

/** 兼容仍在运行的旧后端；有效时间缺失时不虚构日期。 */
function normalizeSession(item: ChatSession): ChatSession {
  const validTime = (value: string) => Number.isFinite(Date.parse(value)) ? value : "";
  return {
    ...item,
    title: item.has_messages === undefined ? "历史会话" : item.title,
    created_at: validTime(item.created_at),
    updated_at: validTime(item.updated_at) || validTime(item.created_at),
    has_messages: item.has_messages ?? false,
  };
}

/** 旧服务缺少会话摘要时，从真实历史分页获取首条指令，避免显示编号标题。 */
async function readSessions(projectId: string) {
  const items = await api<ChatSession[]>(`${projectPath(projectId)}/sessions`);
  return Promise.all(items.map(async (item) => {
    const normalized = normalizeSession(item);
    if (item.has_messages !== undefined) return normalized;
    let cursor: number | null = null;
    let first: string | null = null;
    let updated = "";
    let hasMessages = false;
    try {
      do {
        const page: ConversationPage = await api(`${projectPath(projectId)}/runs?session_id=${encodeURIComponent(item.session_id)}&limit=100${cursor === null ? "" : `&before=${cursor}`}`);
        hasMessages ||= page.items.length > 0;
        if (!updated) updated = page.items.at(-1)?.created_at || "";
        // 后续分页是更早的记录，因此有原文时覆盖为该页的首条。
        const instruction = page.items.find(entry => entry.instruction?.trim())?.instruction;
        if (instruction) first = instruction;
        cursor = page.next_cursor;
      } while (cursor !== null);
      return normalizeSession({
        ...normalized,
        title: first ? first.trim().replace(/\s+/g, " ") : hasMessages ? "历史会话" : "新会话",
        has_messages: hasMessages,
        updated_at: updated || normalized.updated_at,
      });
    } catch {
      // 历史读取暂时失败时仍可打开工程，不展示已知错误的编号或日期。
      return normalized;
    }
  })).then(sessions => sessions.filter(item => item.has_messages || item.running)
    .sort((a, b) => (Date.parse(b.updated_at) || 0) - (Date.parse(a.updated_at) || 0)));
}

/** 加载持久化会话列表，创建会话时保持工程内容不变。
 * Args:
 *   projectId: 当前工程编号。
 *   opening: 显式打开工程的次数，以及是否刚创建工程。
 *   report: 页面错误处理器。
 * Returns:
 *   会话列表、当前会话及创建和切换动作。
 */
export function useChatSessions(
  projectId: string | undefined,
  opening: { version: number; created: boolean },
  report: (error: unknown) => void,
) {
  const key = `${projectId || ""}:${opening.version}`;
  const [owner, setOwner] = useState(key);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [selected, setSelected] = useState<string>();
  const [blankVersion, setBlankVersion] = useState(0);
  const [loading, setLoading] = useState(false);
  const generation = useRef(0);
  const reporter = useRef(report);
  reporter.current = report;

  useEffect(() => {
    const version = ++generation.current;
    setOwner(key);
    setSessions([]);
    setSelected(undefined);
    if (!projectId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    readSessions(projectId)
      .then((items) => {
        if (generation.current !== version) return;
        // 运行中的任务保留取消入口；其他情况仅显示空白输入，不请求创建会话。
        const active = items.find((item) => item.running);
        setSessions(items);
        setSelected(active?.session_id);
      })
      .catch((error) => {
        if (generation.current === version) reporter.current(error);
      })
      .finally(() => {
        if (generation.current === version) setLoading(false);
      });
    return () => { generation.current++; };
  }, [projectId, opening]);

  function select(sessionId: string) {
    if (!projectId || owner !== key || !sessions.some((item) => item.session_id === sessionId)) return;
    setSelected(sessionId);
  }

  function create() {
    if (!projectId || owner !== key) return;
    setSelected(undefined);
    setBlankVersion(value => value + 1);
  }

  /** 已被服务端接受的首条消息立即成为标题，后续消息只更新最近活动时间。 */
  function noteMessage(sessionId: string | undefined, text: string) {
    if (!projectId || !sessionId || owner !== key) return;
    const version = generation.current;
    setSelected(sessionId);
    setSessions((items) => {
      if (generation.current !== version) return items;
      const old = items.find(item => item.session_id === sessionId);
      const now = new Date().toISOString();
      const next: ChatSession = {
        session_id: sessionId, project_id: projectId,
        title: old?.has_messages ? old.title : text.trim().replace(/\s+/g, " "),
        created_at: old?.created_at || now, updated_at: now, has_messages: true,
        running: old?.running || false,
      };
      return [next, ...items.filter(item => item.session_id !== sessionId)];
    });
  }

  return {
    sessions: owner === key ? sessions : [],
    sessionId: owner === key ? selected : undefined,
    draftKey: `${key}:${blankVersion}`,
    loading: loading || owner !== key,
    create,
    select,
    noteMessage,
  };
}
