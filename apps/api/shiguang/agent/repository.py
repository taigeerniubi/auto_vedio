"""持久化智能体运行状态、中文事件和请求去重信息。"""
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging

from ..store import dumps
from ..tools import ToolError, uid
from ..observability import log_event, sanitize


class RunRepository:
    def __init__(self, store):
        """绑定与工程共享事务的存储。

        Args:
            store: SQLite 工程存储。
        """
        self.store = store

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
            row = c.execute("SELECT * FROM agent_runs WHERE id=? AND project_id=?", (rid, pid)).fetchone()
        if not row:
            raise ToolError("RUN_NOT_FOUND", "运行不存在或不属于该项目")
        return {"run_id": row["id"], "project_id": pid, "request_id": row["request_id"],
                "draft_id": row["draft_id"], "status": row["status"],
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
        digest = sha256(dumps(command.model_dump()).encode()).hexdigest()
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
                if project["head"] != command.expected_revision:
                    raise ToolError("REVISION_CONFLICT", "项目已变更，请刷新后重新提交")
                snapshot = c.execute("SELECT snapshot FROM revisions WHERE project_id=? AND revision=?", (pid, project["head"])).fetchone()[0]
                ids = {x["clip_id"] for x in json.loads(snapshot)["clips"]}
                if not set(command.selected_clip_ids) <= ids:
                    raise ToolError("INVALID_SELECTION", "选中的片段不存在于当前工程")
                rid, did, created = uid("run"), uid("draft"), True
                c.execute("INSERT INTO drafts VALUES(?,?,?,0,?,'open')", (did, pid, project["head"], snapshot))
                c.execute("INSERT INTO agent_runs VALUES(?,?,?,?,?,?,'running','{}','[]')", (rid, pid, command.request_id, digest, did, project["head"]))
        return self.get(pid, rid), created

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
