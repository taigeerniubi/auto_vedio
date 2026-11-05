"""提供本机剪辑工具、智能体指令、运行查询和渲染接口。"""
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from .media import ARTIFACTS, DATABASE, import_media, render_snapshot
from .models import Composition, Model
from .store import Store
from .tools import ToolError, definitions, uid
from .agent.contracts import Command
from .agent.loop import Agent
from .http_logging import RequestLogMiddleware, install_error_handlers
from .http_status import error_status
from .observability import LOG_DIR, sanitize
from .workspace_api import workspace_router


class ProjectRequest(Model):
    name: str = Field(min_length=1, max_length=100)
    composition: Composition = Field(default_factory=Composition)


class ImportRequest(Model):
    path: str = Field(min_length=1)


class ToolRequest(Model):
    tool: str
    arguments: dict = Field(default_factory=dict)
    op_id: str | None = None
    expected_seq: Annotated[int, Field(strict=True, ge=0)] | None = None


class RevisionRequest(Model):
    expected_revision: Annotated[int, Field(strict=True, ge=0)]


def create_app(db: Path | None = None, *, session_factory=None, agent_limits=None, log_dir: Path | None = None, perception_factory=None):
    """装配本机 API，并允许测试注入模型会话。

    Args:
        db: 可选的 SQLite 数据库路径。
        session_factory: 测试使用的模型会话工厂。
        agent_limits: 可选的智能体循环预算。
        log_dir: 可选日志目录，默认使用项目根目录下的 logs。

    Returns:
        已注册工具及智能体路由的 FastAPI 应用。
    """
    @asynccontextmanager
    async def lifespan(_app):
        # 仅在实际启动该应用时恢复；导入模块或创建隔离测试应用不会触碰其他运行中的任务。
        service.recover_interrupted()
        yield

    app = FastAPI(title="拾光 · 剪辑工具与智能体 API", version="0.2.0", lifespan=lifespan)
    store = Store(db or DATABASE / "state.sqlite3")
    app.state.store = store
    from .analysis import AnalysisService
    from .analysis_api import analysis_router
    service = AnalysisService(store, **({"provider_factory": perception_factory} if perception_factory else {}))
    store.analysis_service = service
    app.include_router(analysis_router(store, service))
    agent = Agent(store, **({"session_factory": session_factory} if session_factory else {}), limits=agent_limits)
    app.state.agent = agent
    app.add_middleware(RequestLogMiddleware, directory=log_dir or LOG_DIR)
    install_error_handlers(app)
    app.include_router(workspace_router(store))

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": "local-agent"}

    @app.get("/api/v1/tools")
    def tools():
        return {"version": 1, "tools": definitions()}

    @app.post("/api/v1/projects")
    def create_project(req: ProjectRequest):
        return store.create_project(req.name, req.composition)

    @app.get("/api/v1/projects/{pid}")
    def get_project(pid: str):
        return store.project(pid)

    @app.post("/api/v1/projects/{pid}/commands")
    def command(pid: str, req: Command, request: Request):
        """同步执行一次智能体指令。

        Args:
            pid: 项目编号。
            req: 用户指令和幂等请求编号。
            request: 当前 HTTP 请求，提供日志关联编号。

        Returns:
            本次运行的最终状态或重复请求对应的正在运行状态。
        """
        run = agent.execute(pid, req)
        if run["status"] in {"failed", "unsupported"}:
            result = run["result"]
            code = result.get("error_code", "UNSUPPORTED_OPERATION")
            return JSONResponse(status_code=error_status(code), content=sanitize({**run, "ok": False,
                "error_code": code, "summary": result.get("summary") or result.get("reason") or "Operation failed.",
                "command_id": run["request_id"], "request_id": request.state.trace_id}))
        return run

    @app.get("/api/v1/projects/{pid}/commands/{request_id}")
    def command_status(pid: str, request_id: str):
        """按调用方已知的请求编号查询运行。

        Args:
            pid: 项目编号。
            request_id: 提交指令时指定的幂等编号。

        Returns:
            包含运行编号及中文进度事件的记录。
        """
        return agent.runs.by_request(pid, request_id)

    @app.get("/api/v1/projects/{pid}/runs/{rid}")
    def run_status(pid: str, rid: str):
        """读取运行结果。

        Args:
            pid: 项目编号。
            rid: 运行编号。

        Returns:
            可供界面展示的运行记录。
        """
        return agent.runs.get(pid, rid)

    @app.get("/api/v1/projects/{pid}/runs")
    def conversation(pid: str, before: int | None = Query(default=None, ge=1), limit: int = Query(default=50, ge=1, le=100)):
        """读取当前工程的历史指令和执行结果。

        Args:
            pid: 工程编号。
            before: 可选的更早记录游标。
            limit: 每页条数。

        Returns:
            包含历史对话与下一页游标的结果。
        """
        return agent.runs.conversation(pid, before=before, limit=limit)

    @app.post("/api/v1/projects/{pid}/runs/{rid}/cancel")
    def cancel_run(pid: str, rid: str):
        """取消正在执行的运行。

        Args:
            pid: 项目编号。
            rid: 运行编号。

        Returns:
            取消后的状态或已经完成的原结果。
        """
        return agent.cancel(pid, rid)

    @app.post("/api/v1/projects/{pid}/assets/import")
    def add_asset(pid: str, req: ImportRequest):
        return import_media(store, pid, req.path).model_dump()

    @app.get("/api/v1/projects/{pid}/assets")
    def list_assets(pid: str):
        store.project(pid)
        return [a.model_dump() for a in store.assets(pid).values()]

    @app.post("/api/v1/projects/{pid}/drafts")
    def draft(pid: str):
        return store.create_draft(pid)

    def check_draft(pid, did):
        if store.draft(did)["project_id"] != pid:
            raise ToolError("DRAFT_NOT_FOUND", "草稿不属于该项目")

    @app.get("/api/v1/projects/{pid}/drafts/{did}")
    def get_draft(pid: str, did: str):
        check_draft(pid, did)
        return store.draft(did)

    @app.post("/api/v1/projects/{pid}/drafts/{did}/tools")
    def execute(pid: str, did: str, req: ToolRequest, request: Request):
        check_draft(pid, did)
        result = store.call(did, req.tool, req.arguments, req.op_id, req.expected_seq)
        if not result["ok"]:
            return JSONResponse(status_code=error_status(result["error_code"]),
                                content=sanitize({**result, "request_id": request.state.trace_id}))
        return result

    @app.post("/api/v1/projects/{pid}/drafts/{did}/commit")
    def commit(pid: str, did: str):
        check_draft(pid, did)
        return store.commit(did)

    @app.post("/api/v1/projects/{pid}/history/{action}")
    def history(pid: str, action: str, req: RevisionRequest):
        return store.history(pid, action, req.expected_revision)

    @app.post("/api/v1/projects/{pid}/render")
    def render(pid: str, req: RevisionRequest):
        # 本机同步验收接口，持久化后台任务队列留待服务化阶段实现。
        p = store.project(pid)
        if p["revision"] != req.expected_revision:
            raise ToolError("REVISION_CONFLICT", "项目已变更，请指定当前版本")
        from .models import Snapshot
        output = ARTIFACTS / "exports" / pid / f"revision-{req.expected_revision}-{uid('export')}.mp4"
        return {"revision": req.expected_revision, "download_url": f"/api/v1/projects/{pid}/exports/{output.name}",
                **render_snapshot(Snapshot.model_validate(p["snapshot"]), store.assets(pid), output)}

    # 生产构建完成后可由同一个本机服务提供前端。
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse
    web_dist = Path(__file__).resolve().parents[2] / "web" / "dist"
    if web_dist.is_dir():
        @app.get("/", include_in_schema=False)
        def frontend():
            """返回已经构建的工作台首页。

            Returns:
                前端 HTML 入口文件。
            """
            return FileResponse(web_dist / "index.html")
        app.mount("/assets", StaticFiles(directory=web_dist / "assets"), name="web-assets")
        app.mount("/icons", StaticFiles(directory=web_dist / "icons"), name="web-icons")

    return app


app = create_app()
