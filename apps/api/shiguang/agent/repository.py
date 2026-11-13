"""持久化智能体运行状态、中文事件和请求去重信息。"""
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging

from ..store import dumps
from ..tools import ToolError, uid
from ..observability import log_event, sanitize
from ..http_status import error_status
from .annotations import capture_annotation


def default_session_id(pid):
    """为旧版本未划分会话的运行提供稳定的默认会话编号。"""
    return f"session_{pid}"


class RunRepository:
    def __init__(self, store):
        """绑定与工程共享事务的存储。

        Args:
            store: SQLite 工程存储。
        """
        self.store = store

    def _ensure_default_session(self, con, pid):
        """按需建立默认会话，旧运行无需改写即可归入其中。"""
        con.execute("INSERT OR IGNORE INTO agent_sessions VALUES(?,?,?,?)",
                    (default_session_id(pid), pid, "会话 1", datetime.now(timezone.utc).isoformat()))

    def _require_session(self, con, pid, sid):
        """拒绝不存在或属于其他工程的会话。"""
        if not con.execute("SELECT 1 FROM agent_sessions WHERE id=? AND project_id=?", (sid, pid)).fetchone():
            raise ToolError("SESSION_NOT_FOUND", "会话不存在或不属于该工程")

    def sessions(self, pid):
        """列出工程内的持久化会话，并标记仍在运行的会话。"""
        self.store.project(pid)
        with self.store.connect() as c:
            # 只为确实存在的旧版无映射记录补默认会话，空工程的读取不创建会话。
            if c.execute("""SELECT 1 FROM agent_runs r LEFT JOIN agent_run_sessions m ON m.run_id=r.id
                WHERE r.project_id=? AND m.run_id IS NULL LIMIT 1""", (pid,)).fetchone():
                self._ensure_default_session(c, pid)
            # 从持久化指令派生标题，旧会话也能直接显示首条消息，无需数据迁移。
            rows = c.execute("""WITH history AS (
                SELECT r.rowid AS seq,COALESCE(m.session_id,?) AS sid,r.status,i.instruction,i.created_at
                FROM agent_runs r LEFT JOIN agent_run_sessions m ON m.run_id=r.id
                LEFT JOIN agent_run_inputs i ON i.run_id=r.id WHERE r.project_id=?
                ) SELECT s.id AS session_id,s.project_id,s.created_at,
                (SELECT instruction FROM history WHERE sid=s.id ORDER BY seq LIMIT 1) AS first_instruction,
                COALESCE((SELECT MAX(created_at) FROM history WHERE sid=s.id),s.created_at) AS updated_at,
                EXISTS(SELECT 1 FROM history WHERE sid=s.id) AS has_messages,
                EXISTS(SELECT 1 FROM history WHERE sid=s.id AND status='running') AS running
                FROM agent_sessions s WHERE s.project_id=? ORDER BY updated_at DESC,s.rowid DESC""",
                (default_session_id(pid), pid, pid)).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            first = item.pop("first_instruction")
            item["title"] = " ".join(first.split()) if first else ("历史会话" if item["has_messages"] else "新会话")
            item["running"] = bool(item["running"])
            item["has_messages"] = bool(item["has_messages"])
            items.append(item)
        return items

    def create_session(self, pid):
        """创建空会话，不修改工程版本、素材或之前的对话。"""
        self.store.project(pid)
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            if c.execute("SELECT 1 FROM agent_runs WHERE project_id=? AND status='running'", (pid,)).fetchone():
                raise ToolError("RUN_CONFLICT", "请先等待当前 AI 运行结束，或取消后再创建会话")
            now = datetime.now(timezone.utc).isoformat()
            item = {"session_id": uid("session"), "project_id": pid, "title": "新会话",
                    "created_at": now, "updated_at": now, "has_messages": False, "running": False}
            c.execute("INSERT INTO agent_sessions VALUES(?,?,?,?)",
                      (item["session_id"], pid, item["title"], item["created_at"]))
        return item

    def cleanup_empty_sessions(self):
        """只删除没有运行记录的会话，保留失败、运行中以及旧版隐式归属的历史。"""
        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            deleted = con.execute("""DELETE FROM agent_sessions
                WHERE NOT EXISTS(SELECT 1 FROM agent_run_sessions m WHERE m.session_id=agent_sessions.id)
                AND NOT EXISTS(SELECT 1 FROM agent_runs r LEFT JOIN agent_run_sessions m ON m.run_id=r.id
                    WHERE r.project_id=agent_sessions.project_id AND m.run_id IS NULL
                    AND agent_sessions.id='session_' || r.project_id)""").rowcount
        return deleted

    def get(self, pid, rid):
        """读取指定项目的运行记录。

        Args:
            pid: 项目编号。
            rid: 运行编号。

        Returns:
            不含密钥和模型思考内容的运行记录。

        Raises:
            ToolError: 运行不存在或不属于该项目。
        """
        with self.store.connect() as c:
            row = c.execute("""SELECT r.*, d.seq, d.base, m.session_id, a.data AS annotation,
                CASE WHEN r.status='running' AND d.status='open' AND d.seq>0 THEN d.snapshot END AS live_snapshot
                FROM agent_runs r JOIN drafts d ON d.id=r.draft_id AND d.project_id=r.project_id
                LEFT JOIN agent_run_sessions m ON m.run_id=r.id
                LEFT JOIN agent_run_annotations a ON a.run_id=r.id
                WHERE r.id=? AND r.project_id=?""", (rid, pid)).fetchone()
        if not row:
            raise ToolError("RUN_NOT_FOUND", "运行不存在或不属于该项目")
        return {"run_id": row["id"], "project_id": pid, "request_id": row["request_id"],
                "session_id": row["session_id"] or default_session_id(pid),
                "annotation": json.loads(row["annotation"]) if row["annotation"] else None,
                "draft_id": row["draft_id"], "status": row["status"],
                "live_draft": {"seq": row["seq"], "base": row["base"], "snapshot": json.loads(row["live_snapshot"])}
                    if row["live_snapshot"] else None,
                "result": json.loads(row["result"]), "events": json.loads(row["events"])}

    def begin(self, pid, command):
        """原子创建运行和专属草稿，相同请求只执行一次。

        Args:
            pid: 项目编号。
            command: 带幂等编号和预期版本的用户请求。

        Returns:
            运行记录及是否为新运行的布尔值。

        Raises:
            ToolError: 请求编号复用但内容不同、版本冲突或选区无效。
        """
        # 未传会话时保留旧请求的指纹，升级后重试不会误报幂等冲突。
        excluded = {key for key in ("session_id", "resume_run_id", "annotation") if getattr(command, key, None) is None}
        if not command.new_session:
            excluded.add("new_session")
        payload = command.model_dump(exclude=excluded)
        digest = sha256(dumps(payload).encode()).hexdigest()
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            old = c.execute("SELECT id,hash FROM agent_runs WHERE project_id=? AND request_id=?", (pid, command.request_id)).fetchone()
            if old:
                if old["hash"] != digest:
                    raise ToolError("REQUEST_CONFLICT", "相同 request_id 不得携带不同指令")
                rid, created = old["id"], False
            else:
                project = c.execute("SELECT head FROM projects WHERE id=?", (pid,)).fetchone()
                if not project:
                    raise ToolError("PROJECT_NOT_FOUND", "项目不存在")
                if command.new_session:
                    if command.session_id or command.resume_run_id:
                        raise ToolError("INVALID_ARGUMENTS", "新会话不能同时指定已有会话或恢复任务")
                    sid = uid("session")
                    c.execute("INSERT INTO agent_sessions VALUES(?,?,?,?)",
                              (sid, pid, "新会话", datetime.now(timezone.utc).isoformat()))
                else:
                    if not command.session_id:
                        self._ensure_default_session(c, pid)
                    sid = command.session_id or default_session_id(pid)
                    self._require_session(c, pid, sid)
                if c.execute("SELECT 1 FROM agent_runs WHERE project_id=? AND status='running'", (pid,)).fetchone():
                    raise ToolError("RUN_CONFLICT", "该工程已有运行中的任务，请先等待结束或取消")
                if project["head"] != command.expected_revision:
                    raise ToolError("REVISION_CONFLICT", "项目已变更，请刷新后重新提交")
                snapshot = c.execute("SELECT snapshot FROM revisions WHERE project_id=? AND revision=?", (pid, project["head"])).fetchone()[0]
                resume = self._resume_source(c, pid, sid, command)
                annotation = capture_annotation(json.loads(snapshot), project["head"], command.annotation)
                checkpoint = None
                if resume:
                    if resume["base"] != project["head"]:
                        raise ToolError("RESUME_CONFLICT", "工程在任务中断后已改变，不能直接恢复旧草稿；请基于当前工程重新提出剪辑要求")
                    stored = c.execute("SELECT data FROM agent_checkpoints WHERE run_id=?", (resume["id"],)).fetchone()
                    checkpoint = json.loads(stored[0]) if stored else {
                        "instruction": resume["instruction"] or "继续原剪辑任务",
                        "trace": json.loads(resume["events"]), "legacy": True,
                    }
                    snapshot = dumps(checkpoint["snapshot"]) if "snapshot" in checkpoint else resume["snapshot"]
                    checkpoint["source_run_id"] = resume["id"]
                    checkpoint["snapshot"] = json.loads(snapshot)
                    original_annotation = c.execute("SELECT data FROM agent_run_annotations WHERE run_id=?", (resume["id"],)).fetchone()
                    annotation = json.loads(original_annotation[0]) if original_annotation else None
                ids = {x["clip_id"] for x in json.loads(snapshot)["clips"]}
                # 续跑恢复的是断点草稿；浏览器选区可能仍指向已被分割或删除的正式片段。
                # 该选区不参与续跑上下文，只有新任务才按当前工程严格校验。
                if not resume and not set(command.selected_clip_ids) <= ids:
                    raise ToolError("INVALID_SELECTION", "选中的片段不存在于当前工程")
                rid, did, created = uid("run"), uid("draft"), True
                c.execute("INSERT INTO drafts VALUES(?,?,?,0,?,'open')", (did, pid, project["head"], snapshot))
                c.execute("INSERT INTO agent_runs VALUES(?,?,?,?,?,?,'running','{}','[]')", (rid, pid, command.request_id, digest, did, project["head"]))
                # 指令仅保存在本机对话记录，不写诊断日志；与运行创建使用同一事务。
                c.execute("INSERT INTO agent_run_inputs VALUES(?,?,?)", (rid, command.instruction, datetime.now(timezone.utc).isoformat()))
                c.execute("INSERT INTO agent_run_sessions VALUES(?,?)", (rid, sid))
                if annotation:
                    c.execute("INSERT INTO agent_run_annotations VALUES(?,?)", (rid, dumps(annotation)))
                if checkpoint:
                    c.execute("INSERT INTO agent_checkpoints VALUES(?,?)", (rid, dumps(checkpoint)))
                    c.execute("INSERT INTO agent_resumptions VALUES(?,?)", (resume["id"], rid))
        return self.get(pid, rid), created

    def _resume_source(self, con, pid, sid, command):
        """只恢复当前项目/会话的失败任务，并阻止重复恢复已被接续的草稿。"""
        if command.annotation:
            return None
        explicit = getattr(command, "resume_run_id", None)
        continuation = command.instruction.strip().strip("。！!，,？? ").lower() in {
            "继续", "重试", "继续上次任务", "重试上次任务", "继续执行", "继续剪辑", "retry", "continue", "resume",
        }
        if not explicit and not continuation:
            return None
        row = con.execute("""SELECT r.*,r.rowid AS ordinal,d.snapshot,i.instruction
            FROM agent_runs r JOIN drafts d ON d.id=r.draft_id
            LEFT JOIN agent_run_inputs i ON i.run_id=r.id
            LEFT JOIN agent_run_sessions m ON m.run_id=r.id
            WHERE r.project_id=? AND COALESCE(m.session_id,?)=? AND r.status='failed'
            AND (? IS NULL OR r.id=?)
            AND NOT EXISTS(SELECT 1 FROM agent_resumptions a WHERE a.source_run_id=r.id)
            ORDER BY r.rowid DESC LIMIT 1""", (pid, default_session_id(pid), sid, explicit, explicit)).fetchone()
        if not row:
            if explicit:
                raise ToolError("RESUME_NOT_FOUND", "未找到可继续的失败任务，或该任务已经被接续")
            return None
        later = con.execute("""SELECT 1 FROM agent_runs r LEFT JOIN agent_run_sessions m ON m.run_id=r.id
            WHERE r.project_id=? AND COALESCE(m.session_id,?)=? AND r.rowid>? AND r.status='completed'""",
            (pid, default_session_id(pid), sid, row["ordinal"])).fetchone()
        if later:
            if explicit:
                raise ToolError("RESUME_CONFLICT", "此会话已有更新的已完成任务，请重新说明要编辑的内容")
            return None
        return row

    def checkpoint(self, rid):
        """读取仅供执行器使用的断点，不向浏览器暴露模型协议历史。"""
        with self.store.connect() as con:
            row = con.execute("SELECT data FROM agent_checkpoints WHERE run_id=?", (rid,)).fetchone()
        return json.loads(row[0]) if row else None

    def recover_interrupted(self):
        """本机单进程服务启动时释放遗留运行，保留断点供用户显式继续。"""
        with self.store.connect() as con:
            rows = con.execute("SELECT id,project_id FROM agent_runs WHERE status='running'").fetchall()
        for row in rows:
            self.stop(row["project_id"], row["id"], "failed", {
                "error_code": "RUN_INTERRUPTED", "summary": "服务中断，已保留任务进度，可以继续任务",
            })

    def save_checkpoint(self, rid, state):
        """将执行游标和对应草稿快照一起保存，恢复时不会混用不同进度。"""
        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("""SELECT d.snapshot FROM agent_runs r JOIN drafts d ON d.id=r.draft_id
                WHERE r.id=? AND r.status='running'""", (rid,)).fetchone()
            if row:
                con.execute("INSERT OR REPLACE INTO agent_checkpoints VALUES(?,?)",
                            (rid, dumps({**state, "snapshot": json.loads(row[0])})))

    def conversation(self, pid, *, before=None, limit=50, session_id=None):
        """分页读取工程对话，兼容旧版本未保存指令原文的运行。

        Args:
            pid: 所属工程编号。
            before: 可选的上一页游标，不包含该条记录。
            limit: 本次读取的最大条数。
            session_id: 可选的会话筛选，省略时兼容旧接口的工程级历史。

        Returns:
            按时间正序排列的对话，以及读取更早记录的游标。
        """
        self.store.project(pid)
        with self.store.connect() as c:
            if session_id:
                if session_id == default_session_id(pid) and c.execute("""SELECT 1 FROM agent_runs r
                    LEFT JOIN agent_run_sessions m ON m.run_id=r.id
                    WHERE r.project_id=? AND m.run_id IS NULL LIMIT 1""", (pid,)).fetchone():
                    self._ensure_default_session(c, pid)
                self._require_session(c, pid, session_id)
            rows = c.execute("""SELECT r.*, r.rowid AS cursor, i.instruction, i.created_at,m.session_id,a.data AS annotation
                FROM agent_runs r LEFT JOIN agent_run_inputs i ON i.run_id=r.id
                LEFT JOIN agent_run_sessions m ON m.run_id=r.id
                LEFT JOIN agent_run_annotations a ON a.run_id=r.id
                WHERE r.project_id=? AND (? IS NULL OR r.rowid<?)
                AND (? IS NULL OR COALESCE(m.session_id,?)=?)
                ORDER BY r.rowid DESC LIMIT ?""",
                (pid, before, before, session_id, default_session_id(pid), session_id, limit + 1)).fetchall()
        page = rows[:limit]
        items = [{"run_id": row["id"], "project_id": pid, "request_id": row["request_id"],
                  "session_id": row["session_id"] or default_session_id(pid),
                  "draft_id": row["draft_id"], "status": row["status"],
                  "result": json.loads(row["result"]), "events": json.loads(row["events"]),
                  "instruction": row["instruction"], "created_at": row["created_at"]} for row in reversed(page)]
        for item, row in zip(items, reversed(page)):
            item["annotation"] = json.loads(row["annotation"]) if row["annotation"] else None
        return {"items": items, "next_cursor": page[-1]["cursor"] if len(rows) > limit else None}

    def by_request(self, pid, request_id):
        """用调用方提供的幂等编号定位运行。

        Args:
            pid: 项目编号。
            request_id: 指令请求编号。

        Returns:
            对应的运行记录。

        Raises:
            ToolError: 尚未创建该运行。
        """
        with self.store.connect() as c:
            row = c.execute("SELECT id FROM agent_runs WHERE project_id=? AND request_id=?", (pid, request_id)).fetchone()
        if not row:
            raise ToolError("RUN_NOT_FOUND", "运行尚未创建或请求编号不存在")
        return self.get(pid, row["id"])

    def event(self, rid, event, **fields):
        """追加中文结构化事件，不记录提示词和模型回复正文。

        Args:
            rid: 运行编号。
            event: 中文事件名称。
            **fields: 中文字段名的事件详情。
        """
        item = sanitize({"时间": datetime.now(timezone.utc).isoformat(), "运行编号": rid, "事件": event, **fields})
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            events = json.loads(c.execute("SELECT events FROM agent_runs WHERE id=?", (rid,)).fetchone()[0])
            events.append(item)
            c.execute("UPDATE agent_runs SET events=? WHERE id=?", (dumps(events), rid))
        level = logging.WARNING if fields.get("错误代码") or fields.get("状态") == "失败" else logging.INFO
        if fields.get("错误代码") and error_status(fields["错误代码"]) >= 500:
            level = logging.ERROR
        log_event(event, logger="shiguang.agent", level=level, 运行编号=rid, **fields)

    def stop(self, pid, rid, status, result):
        """终止仍在执行的运行并关闭未提交草稿。

        Args:
            pid: 项目编号。
            rid: 运行编号。
            status: 终态名称。
            result: 对用户可见的结束原因。

        Returns:
            最终运行记录；已提交的运行不会被覆盖。
        """
        self.get(pid, rid)
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            updated = c.execute("UPDATE agent_runs SET status=?,result=? WHERE id=? AND status='running'", (status, dumps(result), rid)).rowcount
            if updated:
                c.execute("UPDATE drafts SET status='abandoned' WHERE id=(SELECT draft_id FROM agent_runs WHERE id=?) AND status='open'", (rid,))
        if updated:
            labels = {"failed": "执行失败", "cancelled": "已取消", "needs_input": "等待澄清", "unsupported": "能力不支持"}
            self.event(rid, labels.get(status, "运行结束"), 错误代码=result.get("error_code"), 原因=result.get("summary"))
        return self.get(pid, rid)
