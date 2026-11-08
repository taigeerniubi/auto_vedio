"""使用 SQLite 原子保存工程、草稿、智能体运行及可撤销版本。"""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import logging
from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING

from pydantic import ValidationError

from .models import Asset, Snapshot, timeline, validate_snapshot
from .tools import TOOLS, ToolError, presets, uid
from .observability import log_event, validation_details
from .savepoints import latest_savepoint
from . import editing_features  # 注册需要工程上下文的扩展工具。

if TYPE_CHECKING:
    from .analysis import AnalysisService


def dumps(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class Store:
    def __init__(self, db: Path):
        db.parent.mkdir(parents=True, exist_ok=True)
        self.db = db
        # API 装配时注入共享服务；独立使用 Store 时仍可按需创建。
        self.analysis_service: AnalysisService | None = None
        with self.connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, name TEXT, head INTEGER NOT NULL, undo TEXT NOT NULL, redo TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS revisions(project_id TEXT, revision INTEGER, snapshot TEXT NOT NULL, PRIMARY KEY(project_id,revision));
                CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, base INTEGER NOT NULL, seq INTEGER NOT NULL, snapshot TEXT NOT NULL, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS operations(draft_id TEXT, op_id TEXT, hash TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(draft_id,op_id));
                CREATE TABLE IF NOT EXISTS agent_runs(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, request_id TEXT NOT NULL, hash TEXT NOT NULL, draft_id TEXT NOT NULL UNIQUE, base INTEGER NOT NULL, status TEXT NOT NULL, result TEXT NOT NULL, events TEXT NOT NULL, UNIQUE(project_id,request_id));
                CREATE TABLE IF NOT EXISTS agent_run_inputs(run_id TEXT PRIMARY KEY REFERENCES agent_runs(id) ON DELETE CASCADE, instruction TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_sessions(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), title TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_run_sessions(run_id TEXT PRIMARY KEY REFERENCES agent_runs(id) ON DELETE CASCADE, session_id TEXT NOT NULL REFERENCES agent_sessions(id));
                CREATE INDEX IF NOT EXISTS agent_sessions_project ON agent_sessions(project_id);
                CREATE INDEX IF NOT EXISTS agent_run_sessions_session ON agent_run_sessions(session_id);
                CREATE TABLE IF NOT EXISTS agent_checkpoints(run_id TEXT PRIMARY KEY REFERENCES agent_runs(id), data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_resumptions(source_run_id TEXT PRIMARY KEY REFERENCES agent_runs(id), new_run_id TEXT NOT NULL UNIQUE REFERENCES agent_runs(id));
                CREATE TABLE IF NOT EXISTS asset_analysis(project_id TEXT,asset_id TEXT,data TEXT NOT NULL,PRIMARY KEY(project_id,asset_id));
                CREATE TABLE IF NOT EXISTS analysis_versions(id TEXT PRIMARY KEY,project_id TEXT,asset_id TEXT,data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audio_query_cache(cache_key TEXT PRIMARY KEY,project_id TEXT NOT NULL,asset_id TEXT NOT NULL,asset_sha256 TEXT NOT NULL,data TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS project_savepoints(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,request_id TEXT NOT NULL,revision INTEGER NOT NULL,created_at TEXT NOT NULL,snapshot TEXT NOT NULL,UNIQUE(project_id,request_id));
                CREATE INDEX IF NOT EXISTS project_savepoints_project ON project_savepoints(project_id);
            """)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.db, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def create_project(self, name, composition=None):
        pid = uid("project")
        s = Snapshot(composition=composition) if composition else Snapshot()
        with self.connect() as c:
            c.execute("INSERT INTO projects VALUES(?,?,0,'[]','[]')", (pid, name))
            c.execute("INSERT INTO revisions VALUES(?,0,?)", (pid, s.model_dump_json()))
        return {"project_id": pid, "revision": 0, "snapshot": s.model_dump(), "last_savepoint": None}

    def project(self, pid):
        with self.connect() as c:
            p = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
            if not p:
                raise ToolError("PROJECT_NOT_FOUND", "项目不存在")
            row = c.execute("SELECT snapshot FROM revisions WHERE project_id=? AND revision=?", (pid, p["head"])).fetchone()
            saved = latest_savepoint(c, pid)
        return {"project_id": pid, "name": p["name"], "revision": p["head"], "snapshot": Snapshot.model_validate_json(row[0]).model_dump(), "last_savepoint": saved}

    def delete_project(self, pid, expected_revision):
        """原子删除工程及关联编辑记录，保留磁盘媒体与其他工程。

        Args:
            pid: 用户确认删除的工程编号。
            expected_revision: 确认窗口所展示的工程版本。

        Returns:
            已删除的工程编号与完成状态。
        """
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            project = con.execute("SELECT head FROM projects WHERE id=?", (pid,)).fetchone()
            if not project:
                raise ToolError("PROJECT_NOT_FOUND", "项目不存在或已被删除")
            if project["head"] != expected_revision:
                raise ToolError("REVISION_CONFLICT", "项目已更新，请返回项目列表后重新选择要删除的项目")
            if con.execute("SELECT 1 FROM agent_runs WHERE project_id=? AND status='running'", (pid,)).fetchone():
                raise ToolError("PROJECT_DELETE_CONFLICT", "该项目正在进行 AI 剪辑，请等待结束或取消任务后再删除")
            # Store 也可独立使用；导出表由 ExportService 安装。
            has_exports = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='export_jobs'").fetchone()
            if has_exports and con.execute("SELECT 1 FROM export_jobs WHERE project_id=? AND status IN ('queued','running')", (pid,)).fetchone():
                raise ToolError("PROJECT_DELETE_CONFLICT", "该项目正在导出，请等待完成后再删除")
            for row in con.execute("SELECT data FROM asset_analysis WHERE project_id=?", (pid,)):
                record = json.loads(row[0])
                if record.get("status") == "running" or any(stage.get("status") == "running" for stage in record.get("stages", {}).values()):
                    raise ToolError("PROJECT_DELETE_CONFLICT", "该项目正在分析素材，请等待完成后再删除")

            # 先清理带外键的子记录，整个删除过程只提交一次。
            runs = "SELECT id FROM agent_runs WHERE project_id=?"
            con.execute(f"DELETE FROM agent_resumptions WHERE source_run_id IN ({runs}) OR new_run_id IN ({runs})", (pid, pid))
            for table in ("agent_checkpoints", "agent_run_inputs", "agent_run_sessions"):
                con.execute(f"DELETE FROM {table} WHERE run_id IN ({runs})", (pid,))
            con.execute("DELETE FROM operations WHERE draft_id IN (SELECT id FROM drafts WHERE project_id=?)", (pid,))
            for table in ("agent_runs", "agent_sessions", "asset_analysis", "analysis_versions", "audio_query_cache", "project_savepoints", "drafts", "revisions", "assets"):
                con.execute(f"DELETE FROM {table} WHERE project_id=?", (pid,))
            if has_exports:
                con.execute("DELETE FROM export_jobs WHERE project_id=?", (pid,))
            con.execute("DELETE FROM projects WHERE id=?", (pid,))
        log_event("project_deleted", project_id=pid, revision=expected_revision)
        return {"project_id": pid, "deleted": True}

    def add_asset(self, asset: Asset):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            if not c.execute("SELECT 1 FROM projects WHERE id=?", (asset.project_id,)).fetchone():
                raise ToolError("PROJECT_NOT_FOUND", "项目不存在或已被删除")
            c.execute("INSERT INTO assets VALUES(?,?,?)", (asset.asset_id, asset.project_id, asset.model_dump_json()))

    def assets(self, pid, con=None):
        if con is None:
            with self.connect() as c:
                return self.assets(pid, c)
        return {a["id"]: Asset.model_validate_json(a["data"]) for a in con.execute("SELECT * FROM assets WHERE project_id=?", (pid,))}

    def create_draft(self, pid):
        did = uid("draft")
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            project = c.execute("SELECT head FROM projects WHERE id=?", (pid,)).fetchone()
            if not project:
                raise ToolError("PROJECT_NOT_FOUND", "项目不存在或已被删除")
            snapshot = c.execute("SELECT snapshot FROM revisions WHERE project_id=? AND revision=?", (pid, project["head"])).fetchone()[0]
            c.execute("INSERT INTO drafts VALUES(?,?,?,0,?,'open')", (did, pid, project["head"], snapshot))
        return self.draft(did)

    def draft(self, did):
        with self.connect() as c:
            row = c.execute("SELECT * FROM drafts WHERE id=?", (did,)).fetchone()
        if not row:
            raise ToolError("DRAFT_NOT_FOUND", "草稿不存在")
        return {**dict(row), "snapshot": json.loads(row["snapshot"])}

    def call(self, did, name, arguments, op_id=None, expected_seq=None, *, run_id=None):
        """校验参数并原子执行草稿工具，失败时保留原状态。

        Args:
            did: 草稿编号。
            name: 注册工具名称。
            arguments: 待校验的工具参数。
            op_id: 用于写操作去重的编号。
            expected_seq: 执行器读取到的草稿序号。
            run_id: 智能体专属草稿的所有者编号。

        Returns:
            含状态、变更编号及错误信息的执行结果。
        """
        result = self._execute_tool(did, name, arguments, op_id, expected_seq, run_id=run_id)
        if not result["ok"]:
            log_event("工具调用失败", level=logging.WARNING, 草稿编号=did, 运行编号=run_id, 工具=name,
                      操作编号=op_id, 错误代码=result.get("error_code"), 原因=result.get("summary"),
                      参数错误=result.get("details"))
        return result

    def _execute_tool(self, did, name, arguments, op_id, expected_seq, *, run_id):
        """在草稿事务内执行已注册工具。

        Args:
            did: 草稿编号。
            name: 工具名称。
            arguments: 待校验参数。
            op_id: 幂等操作编号。
            expected_seq: 预期草稿序号。
            run_id: 可选的草稿所有者编号。

        Returns:
            工具结果或可供模型修正的错误。
        """
        if name not in TOOLS:
            return {"ok": False, "error_code": "UNKNOWN_TOOL", "summary": "工具尚未实现", "changed_ids": [], "warnings": []}
        spec = TOOLS[name]
        try:
            args = spec.arguments.model_validate(arguments)
        except ValidationError as exc:
            return {"ok": False, "error_code": "INVALID_ARGUMENTS", "summary": dumps(validation_details(exc.errors())), "changed_ids": [], "warnings": []}
        if name in {"analyze_asset", "inspect_segment", "query_audio"}:
            from .analysis import AnalysisService
            draft = self.draft(did)
            with self.connect() as con:
                self.check_run_owner(con, did, run_id)
            if draft["status"] != "open":
                raise ToolError("DRAFT_CLOSED", "草稿已关闭")
            service = self.analysis_service or AnalysisService(self)
            try:
                if name == "query_audio":
                    data = service.query_audio(draft["project_id"], args.asset_id, args)
                    summary = "已复用保存的音频分析" if data.get("cache_hit") else "音频查询完成"
                elif name == "analyze_asset":
                    data = service.analyze(draft["project_id"], args.asset_id, visual=args.visual, speech=args.speech)
                    summary = "素材分析结果已更新，请检查各阶段状态"
                else:
                    data = service.inspect(draft["project_id"], args.asset_id, args.start_frame, args.end_frame)
                    summary = "区间精查完成"
                return {"ok": True, "draft_seq": draft["seq"], "data": data, "summary": summary, "warnings": [], "changed_ids": []}
            except ToolError as exc:
                return {"ok": False, "error_code": exc.code, "summary": str(exc), "warnings": [], "changed_ids": [],
                        "details": exc.details}
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM drafts WHERE id=?", (did,)).fetchone()
            if not row:
                raise ToolError("DRAFT_NOT_FOUND", "草稿不存在")
            s = Snapshot.model_validate_json(row["snapshot"])
            assets = self.assets(row["project_id"], c)
            def failure(code, message):
                return {"ok": False, "draft_seq": row["seq"], "error_code": code, "summary": message, "changed_ids": [], "warnings": []}
            if spec.handler is None:
                if name == "get_project_state":
                    rows, total = timeline(s)
                    data = {"snapshot": s.model_dump(), "timeline": rows, "total_frames": total, "base_revision": row["base"], "capability_version": 1}
                elif name == "list_assets":
                    data = [a.model_dump() for a in assets.values() if a.asset_id not in s.removed_asset_ids]
                elif name == "list_presets":
                    data = presets()
                elif name in {"search_segments", "get_asset_analysis"}:
                    from .analysis import analysis_records, search_segments
                    if name == "search_segments":
                        data = [item for item in search_segments(self, row["project_id"], args.query, c)
                                if item["asset_id"] not in s.removed_asset_ids]
                    else:
                        if args.asset_id not in assets:
                            return failure("ASSET_NOT_FOUND", "该工程中没有此素材")
                        data = analysis_records(self, row["project_id"], c).get(args.asset_id)
                        from .audio_observations import audio_observations
                        observations = audio_observations(self, row["project_id"], {args.asset_id: assets[args.asset_id]}, limit=100)
                        if observations:
                            data = {**(data or {}), "audio_queries": observations}
                elif name == "validate_draft":
                    data = {"errors": validate_snapshot(s, assets), "valid": not validate_snapshot(s, assets)}
                else:
                    base = Snapshot.model_validate_json(c.execute("SELECT snapshot FROM revisions WHERE project_id=? AND revision=?", (row["project_id"], row["base"])).fetchone()[0]).model_dump()
                    now = s.model_dump()
                    data = {k: {"before": base[k], "after": now[k]} for k in now if now[k] != base[k]}
                return {"ok": True, "draft_seq": row["seq"], "changed_ids": [], "warnings": [], "error_code": None, "summary": spec.description, "data": data}
            self.check_run_owner(c, did, run_id)
            if not op_id or expected_seq is None:
                return failure("EXECUTION_CONTEXT_REQUIRED", "写操作需要执行器提供 op_id 与 expected_seq")
            digest = sha256(dumps({"tool": name, "args": args.model_dump()}).encode()).hexdigest()
            old = c.execute("SELECT * FROM operations WHERE draft_id=? AND op_id=?", (did, op_id)).fetchone()
            if old:
                return json.loads(old["result"]) if old["hash"] == digest else failure("OP_ID_CONFLICT", "相同 op_id 不得携带不同参数")
            if row["status"] != "open":
                return failure("DRAFT_CLOSED", "草稿已提交")
            if row["seq"] != expected_seq:
                return failure("DRAFT_CONFLICT", "草稿已变更，请重新读取")
            try:
                if name == "remove_asset" and (args.asset_id not in assets or args.asset_id in s.removed_asset_ids):
                    return failure("ASSET_NOT_FOUND", "Asset is not available in this project.")
                if name == "generate_subtitles":
                    from .analysis import analysis_records
                    changed, warnings = spec.handler(s, args, analysis_records(self, row["project_id"], c))
                else:
                    changed, warnings = spec.handler(s, args)
                if name != "set_cover" and s.cover and s.cover.source == "timeline":
                    total = timeline(s)[1]
                    if s.cover.frame >= total:
                        if total:
                            s.cover.frame = total - 1
                        else:
                            s.cover = None
                        changed.append("cover")
                # 重新解析可捕获绕过赋值校验的复制和列表变更。
                s = Snapshot.model_validate(s.model_dump())
                errors = validate_snapshot(s, assets)
                if errors:
                    return failure("INVALID_DRAFT", "; ".join(errors))
            except (ToolError, ValueError, ValidationError) as exc:
                message = dumps(validation_details(exc.errors())) if isinstance(exc, ValidationError) else str(exc)
                return failure(getattr(exc, "code", "INVALID_ARGUMENTS"), message)
            result = {"ok": True, "draft_seq": row["seq"] + 1, "changed_ids": changed, "warnings": warnings,
                      "error_code": None, "summary": f"已执行 {name}"}
            c.execute("UPDATE drafts SET snapshot=?,seq=seq+1 WHERE id=?", (s.model_dump_json(), did))
            c.execute("INSERT INTO operations VALUES(?,?,?,?)", (did, op_id, digest, dumps(result)))
            return result

    def check_run_owner(self, con, did, run_id):
        """阻止外部写入智能体草稿或在取消后继续提交。

        Args:
            con: 当前事务连接。
            did: 草稿编号。
            run_id: 调用方的运行编号，手动操作传入空值。

        Raises:
            ToolError: 所有者不匹配或运行已经结束。
        """
        owner = con.execute("SELECT id,status FROM agent_runs WHERE draft_id=?", (did,)).fetchone()
        if owner and owner["id"] != run_id:
            raise ToolError("DRAFT_OWNER_CONFLICT", "智能体草稿仅允许所属运行修改")
        if run_id and (not owner or owner["id"] != run_id or owner["status"] != "running"):
            raise ToolError("RUN_STOPPED", "运行已经结束，不能继续修改草稿")

    def commit(self, did, *, run_id=None, run_result=None):
        """校验基础版本并在同一事务内提交工程和智能体结果。

        Args:
            did: 待提交的草稿编号。
            run_id: 可选的智能体运行编号。
            run_result: 已完成校验的运行摘要和变更信息。

        Returns:
            精确对应本次提交版本的工程状态。

        Raises:
            ToolError: 草稿无效、运行终止或基础版本已过期。
        """
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            d = c.execute("SELECT * FROM drafts WHERE id=?", (did,)).fetchone()
            if not d:
                raise ToolError("DRAFT_NOT_FOUND", "草稿不存在")
            self.check_run_owner(c, did, run_id)
            p = c.execute("SELECT * FROM projects WHERE id=?", (d["project_id"],)).fetchone()
            if d["status"] != "open":
                raise ToolError("DRAFT_CLOSED", "草稿已提交")
            if p["head"] != d["base"]:
                raise ToolError("REVISION_CONFLICT", "基础版本已过期")
            s = Snapshot.model_validate_json(d["snapshot"])
            errors = validate_snapshot(s, self.assets(d["project_id"], c))
            if errors:
                raise ToolError("INVALID_DRAFT", "; ".join(errors))
            base = c.execute("SELECT snapshot FROM revisions WHERE project_id=? AND revision=?", (p["id"], p["head"])).fetchone()[0]
            changed = Snapshot.model_validate_json(base).model_dump() != s.model_dump()
            revision = p["head"] + (1 if changed or not run_id else 0)
            if revision != p["head"]:
                undo = json.loads(p["undo"]) + [p["head"]]
                c.execute("INSERT INTO revisions VALUES(?,?,?)", (p["id"], revision, s.model_dump_json()))
                c.execute("UPDATE projects SET head=?,undo=?,redo='[]' WHERE id=?", (revision, dumps(undo), p["id"]))
            c.execute("UPDATE drafts SET status='committed' WHERE id=?", (did,))
            if run_id:
                result = {**(run_result or {}), "revision": revision, "changed": changed}
                c.execute("UPDATE agent_runs SET status='completed',result=? WHERE id=?", (dumps(result), run_id))
            saved = latest_savepoint(c, p["id"])
        return {"project_id": p["id"], "name": p["name"], "revision": revision, "snapshot": s.model_dump(), "last_savepoint": saved}

    def history(self, pid, action, expected_revision):
        if action not in {"undo", "redo"}:
            raise ToolError("INVALID_ARGUMENTS", "无效历史动作")
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            p = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
            if not p:
                raise ToolError("PROJECT_NOT_FOUND", "项目不存在")
            if p["head"] != expected_revision:
                raise ToolError("REVISION_CONFLICT", "项目已变更")
            undo, redo = json.loads(p["undo"]), json.loads(p["redo"])
            origin, other = (undo, redo) if action == "undo" else (redo, undo)
            if not origin:
                raise ToolError("EMPTY_HISTORY", "没有可恢复的版本")
            target = origin.pop()
            other.append(p["head"])
            snapshot = c.execute("SELECT snapshot FROM revisions WHERE project_id=? AND revision=?", (pid, target)).fetchone()[0]
            revision = p["head"] + 1
            c.execute("INSERT INTO revisions VALUES(?,?,?)", (pid, revision, snapshot))
            c.execute("UPDATE projects SET head=?,undo=?,redo=? WHERE id=?", (revision, dumps(undo), dumps(redo), pid))
        return self.project(pid)
