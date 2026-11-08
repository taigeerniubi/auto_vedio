"""保存用户主动标记的工程快照，并通过现有撤销历史恢复暂存内容。"""
from datetime import datetime, timezone
import json

from .models import Snapshot, validate_snapshot
from .tools import ToolError, uid


def summary(row):
    """只向界面提供暂存标识、原编辑序号和真实保存时间。"""
    return {"savepoint_id": row["id"], "revision": row["revision"], "created_at": row["created_at"]} if row else None


def latest_savepoint(con, pid):
    """读取最近一次用户暂存，自动编辑和恢复不改写该时间。"""
    return summary(con.execute("SELECT id,revision,created_at FROM project_savepoints WHERE project_id=? ORDER BY rowid DESC LIMIT 1", (pid,)).fetchone())


class SavepointService:
    def __init__(self, store):
        self.store = store

    def list(self, pid):
        """列出当前工程所有暂存，最新记录优先，不返回完整快照。"""
        self.store.project(pid)
        with self.store.connect() as con:
            return [summary(row) for row in con.execute("SELECT id,revision,created_at FROM project_savepoints WHERE project_id=? ORDER BY rowid DESC", (pid,))]

    def _project(self, con, pid, expected_revision):
        """在写事务中核对项目版本和 AI 占用，避免保存尚未提交的草稿。"""
        project = con.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        if not project:
            raise ToolError("PROJECT_NOT_FOUND", "项目不存在或已被删除")
        if project["head"] != expected_revision:
            raise ToolError("REVISION_CONFLICT", "项目已更新，请确认最新内容后重试")
        if con.execute("SELECT 1 FROM agent_runs WHERE project_id=? AND status='running'", (pid,)).fetchone():
            raise ToolError("SAVEPOINT_CONFLICT", "AI 正在编辑，请等待结束或取消任务后再操作暂存")
        return project

    def create(self, pid, expected_revision, request_id):
        """冻结正式工程，不新增编辑版本，不覆盖之前的手动暂存。"""
        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            previous = con.execute("SELECT * FROM project_savepoints WHERE project_id=? AND request_id=?", (pid, request_id)).fetchone()
            if previous:
                if previous["revision"] != expected_revision:
                    raise ToolError("REQUEST_CONFLICT", "相同暂存请求不能对应不同的工程版本")
                return summary(previous)
            self._project(con, pid, expected_revision)
            snapshot = con.execute("SELECT snapshot FROM revisions WHERE project_id=? AND revision=?", (pid, expected_revision)).fetchone()[0]
            record = {"id": uid("savepoint"), "revision": expected_revision,
                      "created_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds")}
            con.execute("INSERT INTO project_savepoints VALUES(?,?,?,?,?,?)", (
                record["id"], pid, request_id, expected_revision, record["created_at"], snapshot))
            return summary(record)

    def restore(self, pid, savepoint_id, expected_revision):
        """恢复为新的正式编辑，撤销栈保留恢复前状态，素材与会话独立保留。"""
        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            project = self._project(con, pid, expected_revision)
            saved = con.execute("SELECT * FROM project_savepoints WHERE project_id=? AND id=?", (pid, savepoint_id)).fetchone()
            if not saved:
                raise ToolError("SAVEPOINT_NOT_FOUND", "该项目中没有此暂存记录")
            snapshot = Snapshot.model_validate_json(saved["snapshot"])
            errors = validate_snapshot(snapshot, self.store.assets(pid, con))
            if errors:
                raise ToolError("INVALID_DRAFT", "; ".join(errors))
            current = con.execute("SELECT snapshot FROM revisions WHERE project_id=? AND revision=?", (pid, expected_revision)).fetchone()[0]
            changed = Snapshot.model_validate_json(current).model_dump() != snapshot.model_dump()
            revision = expected_revision
            if changed:
                revision += 1
                undo = json.loads(project["undo"]) + [expected_revision]
                con.execute("INSERT INTO revisions VALUES(?,?,?)", (pid, revision, snapshot.model_dump_json()))
                con.execute("UPDATE projects SET head=?,undo=?,redo='[]' WHERE id=?", (revision, json.dumps(undo), pid))
            return {"project_id": pid, "revision": revision, "changed": changed, "restored_savepoint": summary(saved)}

    def remove(self, pid, savepoint_id):
        """移除一个暂存标记，当前工程和撤销历史保持不变。"""
        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if not con.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
                raise ToolError("PROJECT_NOT_FOUND", "项目不存在或已被删除")
            if not con.execute("DELETE FROM project_savepoints WHERE project_id=? AND id=?", (pid, savepoint_id)).rowcount:
                raise ToolError("SAVEPOINT_NOT_FOUND", "该项目中没有此暂存记录")
        return {"savepoint_id": savepoint_id, "deleted": True}
