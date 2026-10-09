"""
server/runner.py - Executes agent runs on worker threads and records their events.

One MCP session (and server subprocess) is shared by every run; each run gets
its own TravelAgent so per-run state and token accounting stay isolated.
"""

import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from benchmark import build_record
from server.store import RunStore
from src.core.llm_errors import USER_MESSAGES, classify_llm_error

EventCallback = Callable[[str, Dict[str, Any]], None]
AgentFactory = Callable[[EventCallback], Any]
JudgeFactory = Callable[[], Any]


class RunManager:

    def __init__(
        self,
        store: RunStore,
        agent_factory: AgentFactory,
        judge_factory: JudgeFactory,
        max_workers: int = 2,
    ):
        self.store = store
        self.agent_factory = agent_factory
        self.judge_factory = judge_factory
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="agent-run")
        self._active: Dict[str, Any] = {}
        self._lock = threading.Lock()
        # Latest fatal Claude API problem (e.g. out of credits), shown to every visitor until a call succeeds again.
        self._llm_problem: Optional[Dict[str, Any]] = None

    def submit(self, *, kind: str, task: Dict[str, Any], task_name: Optional[str], judge: bool) -> str:
        run_id = self.store.create_run(kind=kind, task=task, task_name=task_name, judge=judge)
        self.store.add_event(run_id, "queued", {"title": task.get("title", "")})
        self.executor.submit(self._execute, run_id, task, task_name, judge)
        return run_id

    def is_active(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._active

    def stop(self, run_id: str) -> bool:
        with self._lock:
            agent = self._active.get(run_id)
        if agent is None:
            return False
        agent.request_stop()
        self.store.add_event(run_id, "stop_requested", {})
        return True

    def shutdown(self) -> None:
        with self._lock:
            agents = list(self._active.values())
        for agent in agents:
            agent.request_stop()
        self.executor.shutdown(wait=False, cancel_futures=True)

    # --------------------------------------------------------- API status

    def llm_status(self) -> Dict[str, Any]:
        with self._lock:
            problem = dict(self._llm_problem) if self._llm_problem else None
        if problem is None:
            return {"status": "ok"}
        return {"status": problem["kind"], "message": problem["message"], "since": problem["since"]}

    def _record_llm_problem(self, kind: str) -> None:
        with self._lock:
            if self._llm_problem is None or self._llm_problem["kind"] != kind:
                self._llm_problem = {"kind": kind, "message": USER_MESSAGES[kind], "since": time.time()}

    def _clear_llm_problem(self) -> None:
        with self._lock:
            self._llm_problem = None

    # ------------------------------------------------------------------

    def _execute(self, run_id: str, task: Dict[str, Any], task_name: Optional[str], judge: bool) -> None:
        calls_seen = {"count": 0}

        def emit(event_type: str, payload: Dict[str, Any]) -> None:
            self.store.add_event(run_id, event_type, payload)
            usage = (payload.get("state") or {}).get("usage")
            if usage and usage.get("estimated_cost_usd") is not None:
                # Keep run-list usage current while the run is in progress.
                self.store.update_run(run_id, cost_usd=usage["estimated_cost_usd"], total_tokens=usage["total_tokens"],
                                      api_calls=usage["api_calls"])
                if event_type != "finished" and usage["api_calls"] > calls_seen["count"]:
                    calls_seen["count"] = usage["api_calls"]
                    self._clear_llm_problem()  # a call just succeeded, so credits are available again

        self.store.update_run(run_id, status="running", started_at=time.time())
        try:
            agent = self.agent_factory(emit)
            with self._lock:
                self._active[run_id] = agent
            result = agent.plan_trip(task)
            stopped = agent._stop_requested
            llm_error = result.get("llm_error")
            record = build_record(task, Path(task_name or "custom"), result)
            record["conversation"] = result["conversation"]
            record["llm_error"] = llm_error
            error_kind = error = None

            if llm_error:
                self._record_llm_problem(llm_error["kind"])
                error_kind, error = llm_error["kind"], llm_error["message"]
            elif judge and not stopped:
                emit("judging", {})
                try:
                    judged = self.judge_factory().evaluate(task, {"itinerary": result["itinerary"], "conversation": result["conversation"]})
                    record["judge"] = judged.to_dict()
                    emit("judged", {"overall_score": judged.overall_score, "passed": judged.passed})
                except Exception as exc:
                    # The itinerary is still valid; only the score is missing.
                    kind = classify_llm_error(exc)
                    if kind:
                        self._record_llm_problem(kind)
                        error_kind = kind
                    message = USER_MESSAGES[kind] if kind else f"The judge couldn't score this itinerary: {exc}"
                    record["judge_error"] = {"kind": kind, "message": message}
                    error = message
                    emit("judge_failed", {"kind": kind, "message": message})

            status = "failed" if llm_error else "stopped" if stopped else "completed"
            meta = record["metadata"]
            self.store.update_run(
                run_id,
                status=status,
                finished_at=time.time(),
                record_json=record,
                itinerary=result["itinerary"],
                error=error,
                error_kind=error_kind,
                success=int(bool(record["success"])),
                total_tokens=meta["total_tokens"],
                api_calls=meta["api_calls"],
                cost_usd=(meta.get("estimated_cost_usd") or 0) + ((record.get("judge") or {}).get("usage", {}).get("estimated_cost_usd") or 0),
                judge_score=(record.get("judge") or {}).get("overall_score"),
                judge_passed=None if not record.get("judge") else int(record["judge"]["passed"]),
            )
            emit("finished", {
                "status": status,
                "success": record["success"],
                "error": error,
                "error_kind": error_kind,
                "state": agent.state_snapshot(),
            })
        except Exception as exc:
            kind = classify_llm_error(exc)
            if kind:
                self._record_llm_problem(kind)
            message = USER_MESSAGES[kind] if kind else f"{type(exc).__name__}: {exc}"
            self.store.update_run(run_id, status="failed", finished_at=time.time(), error=message, error_kind=kind)
            emit("finished", {"status": "failed", "error": message, "error_kind": kind, "traceback": traceback.format_exc(limit=8)})
        finally:
            with self._lock:
                self._active.pop(run_id, None)
