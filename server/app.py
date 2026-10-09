"""
server/app.py - FastAPI backend for the travel planning web app. No accounts:
anyone who can reach the frontend can use it.

    python -m server            # serves the API (and the built React app, if present) on :8000

API:
    GET    /api/health                  status, model, MCP tool count, Claude API status (e.g. out of credits)
    GET    /api/catalog                 cities, flight routes, interest suggestions
    GET    /api/tasks                   benchmark tasks with their latest run
    GET    /api/tasks/{name}            one benchmark task
    POST   /api/runs                    start a run (benchmark task or custom trip)
    GET    /api/runs                    run history
    GET    /api/runs/{id}               full run (record, itinerary, judge)
    GET    /api/runs/{id}/events        live event stream (server-sent events)
    POST   /api/runs/{id}/stop          finish a run early
    DELETE /api/runs/{id}               delete a finished run
    GET    /api/stats                   benchmark per-tier aggregates and totals
"""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from typing import Any, Callable, Dict, Literal, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, model_validator

from benchmark import summarize
from server.config import Settings
from server.runner import AgentFactory, JudgeFactory, RunManager
from server.store import TERMINAL_STATUSES, RunStore
from server.trips import Catalog, CustomTrip, build_task, list_benchmark_tasks, load_benchmark_task

class RunRequest(BaseModel):
    task_name: Optional[str] = None
    custom: Optional[CustomTrip] = None
    judge: bool = False

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "RunRequest":
        if (self.task_name is None) == (self.custom is None):
            raise ValueError("Provide exactly one of 'task_name' or 'custom'.")
        return self


def create_app(
    settings: Optional[Settings] = None,
    agent_factory: Optional[AgentFactory] = None,
    judge_factory: Optional[JudgeFactory] = None,
    mcp_client: Any = None,
) -> FastAPI:
    settings = settings or Settings()
    state: Dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = RunStore(settings.db_path)
        store.mark_unfinished_interrupted()

        client = mcp_client
        owns_client = client is None
        if client is None:
            from src.core.mcp_client import MCPToolClient
            client = MCPToolClient(transport=settings.mcp_transport)

        def default_agent_factory(on_event: Callable) -> Any:
            if not settings.api_key_configured:
                raise RuntimeError("ANTHROPIC_API_KEY is not set on the server.")
            from src.agent import TravelAgent
            return TravelAgent(model=settings.model, mcp_client=client, event_callback=on_event)

        def default_judge_factory() -> Any:
            from evaluations.llm_judge import LLMJudge
            return LLMJudge(max_tokens=8192)

        state.update(
            store=store,
            mcp=client,
            catalog=Catalog(settings.mock_data_dir),
            manager=RunManager(
                store,
                agent_factory or default_agent_factory,
                judge_factory or default_judge_factory,
                max_workers=settings.max_concurrent_runs,
            ),
        )
        try:
            yield
        finally:
            state["manager"].shutdown()
            if owns_client:
                client.close()

    app = FastAPI(title="TravelBench Planner", lifespan=lifespan)
    if settings.cors_origins or settings.cors_origin_regex:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_origin_regex=settings.cors_origin_regex or None,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Content-Type", "Last-Event-ID"],
            max_age=600,
        )

    def store() -> RunStore:
        return state["store"]

    # ------------------------------------------------------------- reference

    @app.get("/api/health")
    def health() -> Dict[str, Any]:
        return {
            "status": "ok",
            "api_key_configured": settings.api_key_configured or agent_factory is not None,
            "model": settings.model,
            "mcp_tools": len(state["mcp"].tool_names),
            "mcp_transport": getattr(state["mcp"], "transport", None),
            "llm": state["manager"].llm_status(),
        }

    @app.get("/api/catalog")
    def catalog() -> Dict[str, Any]:
        return state["catalog"].describe()

    @app.get("/api/tasks")
    def tasks() -> list:
        latest: Dict[str, Dict[str, Any]] = {}
        for run in store().list_runs(kind="benchmark", limit=1000):
            latest.setdefault(run["task_name"], run)
        return [{**task, "latest_run": latest.get(task["name"])} for task in list_benchmark_tasks(settings.tasks_dir)]

    @app.get("/api/tasks/{name}")
    def task(name: str) -> Dict[str, Any]:
        found = load_benchmark_task(settings.tasks_dir, name)
        if found is None:
            raise HTTPException(404, f"Unknown task '{name}'.")
        return found

    # ------------------------------------------------------------------ runs

    @app.post("/api/runs", status_code=201)
    def create_run(request: RunRequest) -> Dict[str, Any]:
        if agent_factory is None and not settings.api_key_configured:
            raise HTTPException(503, "The server has no ANTHROPIC_API_KEY configured.")
        if request.task_name is not None:
            task_spec = load_benchmark_task(settings.tasks_dir, request.task_name)
            if task_spec is None:
                raise HTTPException(404, f"Unknown task '{request.task_name}'.")
            kind, task_name = "benchmark", request.task_name
        else:
            problems = state["catalog"].validate_trip(request.custom)
            if problems:
                raise HTTPException(422, {"message": "This trip can't be planned with the available inventory.", "problems": problems})
            task_spec = build_task(request.custom, state["catalog"], task_id=f"custom_{uuid.uuid4().hex[:8]}")
            kind, task_name = "custom", None
        run_id = state["manager"].submit(kind=kind, task=task_spec, task_name=task_name, judge=request.judge)
        return {"id": run_id, "status": "queued"}

    @app.get("/api/runs")
    def list_runs(kind: Optional[Literal["benchmark", "custom"]] = None, limit: int = Query(100, ge=1, le=1000)) -> list:
        return store().list_runs(kind=kind, limit=limit)

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> Dict[str, Any]:
        run = store().get_run(run_id)
        if run is None:
            raise HTTPException(404, "Run not found.")
        return run

    @app.post("/api/runs/{run_id}/stop")
    def stop_run(run_id: str) -> Dict[str, Any]:
        if store().run_status(run_id) is None:
            raise HTTPException(404, "Run not found.")
        if not state["manager"].stop(run_id):
            raise HTTPException(409, "Run is not in progress.")
        return {"id": run_id, "stopping": True}

    @app.delete("/api/runs/{run_id}", status_code=204)
    def delete_run(run_id: str) -> None:
        status = store().run_status(run_id)
        if status is None:
            raise HTTPException(404, "Run not found.")
        if status not in TERMINAL_STATUSES:
            raise HTTPException(409, "Stop the run before deleting it.")
        store().delete_run(run_id)

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, request: Request, after: int = 0) -> StreamingResponse:
        if store().run_status(run_id) is None:
            raise HTTPException(404, "Run not found.")
        last_event_id = request.headers.get("last-event-id")
        cursor = int(last_event_id) if last_event_id and last_event_id.isdigit() else after

        async def stream():
            nonlocal cursor
            idle = 0.0
            while True:
                if await request.is_disconnected():
                    return
                events = await asyncio.to_thread(store().events_after, run_id, cursor)
                for event in events:
                    cursor = event["id"]
                    payload = json.dumps({"id": event["id"], "type": event["type"], "ts": event["ts"], "data": event["data"]})
                    yield f"id: {event['id']}\nevent: {event['type']}\ndata: {payload}\n\n"
                if events:
                    idle = 0.0
                else:
                    status = await asyncio.to_thread(store().run_status, run_id)
                    if status is None or status in TERMINAL_STATUSES:
                        yield "event: end\ndata: {}\n\n"
                        return
                    idle += 0.4
                    if idle >= 15:
                        idle = 0.0
                        yield ": keep-alive\n\n"
                await asyncio.sleep(0.4)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/stats")
    def stats() -> Dict[str, Any]:
        records = store().latest_completed_records("benchmark")
        return {"benchmark": summarize(records) if records else None, "totals": store().totals()}

    # -------------------------------------------------------------- frontend

    if settings.web_dist_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=settings.web_dist_dir / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            if path.startswith("api/"):
                raise HTTPException(404)
            candidate = (settings.web_dist_dir / path).resolve()
            if path and candidate.is_file() and settings.web_dist_dir.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(settings.web_dist_dir / "index.html")

    return app
