import json
import threading
import time
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from server.app import create_app
from server.config import Settings
from server.trips import Catalog, CustomTrip, build_task
from src.agent import TravelAgent
from src.core.mcp_client import MCPToolClient
from tests.test_mcp_integration import SCRIPT_EASY1, ScriptedChatModel


class SlowLoopingModel(BaseChatModel):
    """Keeps searching forever (slowly) so a run can be stopped mid-flight."""

    @property
    def _llm_type(self) -> str:
        return "slow-loop"

    def _generate(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        time.sleep(0.1)
        text = 'THOUGHT: look again\nACTION: get_destination_overview(city="Chicago")'
        message = AIMessage(content=text, usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
        return ChatResult(generations=[ChatGeneration(message=message)])


class FakeJudge:
    def evaluate(self, task, agent_output):
        return SimpleNamespace(
            overall_score=88.0,
            passed=True,
            to_dict=lambda: {"overall_score": 88.0, "passed": True, "dimensions": {},
                             "usage": {"api_calls": 1, "total_tokens": 1200, "estimated_cost_usd": 0.01}},
        )


@pytest.fixture(scope="module")
def mcp_client():
    client = MCPToolClient(transport="memory")
    yield client
    client.close()



def anthropic_error(status, error_type, message):
    """A real Anthropic SDK exception, as ChatAnthropic would raise it."""
    import anthropic
    import httpx2

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    body = {"type": "error", "error": {"type": error_type, "message": message}}
    classes = {400: anthropic.BadRequestError, 401: anthropic.AuthenticationError, 429: anthropic.RateLimitError}
    error_class = classes.get(status, anthropic.APIStatusError)
    return error_class(f"Error code: {status} - {body}", response=httpx2.Response(status, request=request), body=body)


LOW_BALANCE = (400, "invalid_request_error",
               "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits.")


class CreditsRunOutModel(ScriptedChatModel):
    """Replays the scripted turns, then fails the way the API does when credits run out."""

    fail_after: int = 3
    error: tuple = LOW_BALANCE

    def _generate(self, *args, **kwargs):
        if len(self.calls) >= self.fail_after:
            raise anthropic_error(*self.error)
        return super()._generate(*args, **kwargs)


@pytest.fixture
def make_app(tmp_path, mcp_client):
    llm_choice = {"llm": lambda: ScriptedChatModel(script=SCRIPT_EASY1, calls=[])}
    judge_choice = {"judge": FakeJudge}

    def agent_factory(on_event):
        return TravelAgent(llm=llm_choice["llm"](), mcp_client=mcp_client, event_callback=on_event)

    settings = replace(Settings(), db_path=tmp_path / "test.db", web_dist_dir=tmp_path / "no-dist",
                       cors_origins=("https://planner.example",))
    app = create_app(settings, agent_factory=agent_factory, judge_factory=lambda: judge_choice["judge"](), mcp_client=mcp_client)
    app.state.llm_choice = llm_choice
    app.state.judge_choice = judge_choice
    return app


@pytest.fixture
def client(make_app):
    with TestClient(make_app) as test_client:
        test_client.llm_choice = make_app.state.llm_choice
        test_client.judge_choice = make_app.state.judge_choice
        yield test_client


def wait_for(client, run_id, statuses=("completed", "failed", "stopped"), timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in statuses:
            return run
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish: {run['status']}")


def read_sse(client, run_id):
    events = []
    with client.stream("GET", f"/api/runs/{run_id}/events") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data: ") and line != "data: {}":
                events.append(json.loads(line[len("data: "):]))
            if line == "event: end":
                break
    return events


def test_health_and_catalog(client):
    health = client.get("/api/health").json()
    assert health["status"] == "ok"
    assert health["mcp_tools"] == 20
    assert health["llm"] == {"status": "ok"}

    catalog = client.get("/api/catalog").json()
    assert "Chicago" in [c["name"] for c in catalog["destinations"]]
    assert ["Detroit", "Chicago"] in catalog["routes"]
    assert set(catalog["disruptions"]) == {"none", "hotel_unavailable", "budget_cut"}


def test_benchmark_tasks_listed_in_tier_order(client):
    tasks = client.get("/api/tasks").json()
    assert len(tasks) == 21
    assert [t["name"] for t in tasks[:2]] == ["easy1", "easy2"]
    assert tasks[-1]["name"] == "hard6"
    assert client.get("/api/tasks/medium1").json()["task_id"] == "task_006"
    assert client.get("/api/tasks/nope").status_code == 404


def test_benchmark_run_streams_events_and_persists_record(client):
    created = client.post("/api/runs", json={"task_name": "easy1", "judge": True})
    assert created.status_code == 201
    run_id = created.json()["id"]

    events = read_sse(client, run_id)
    types = [e["type"] for e in events]
    assert types[0] == "queued" and "started" in types
    assert types.count("step") == 7 and types.count("tool_result") == 7
    assert types[-3:] == ["judging", "judged", "finished"]
    rejected = [e for e in events if e["type"] == "tool_result" and not e["data"]["ok"]]
    assert "Budget limit" in rejected[0]["data"]["error"]
    assert events[-1]["data"]["status"] == "completed"
    assert events[-1]["data"]["state"]["usage"]["total_tokens"] == 7 * 1050

    run = wait_for(client, run_id)
    assert run["status"] == "completed" and run["success"] == 1
    assert run["judge_score"] == 88.0
    assert run["total_tokens"] == 7 * 1050 and run["api_calls"] == 7
    assert run["record"]["tracking_valid"] is True
    assert run["record"]["conversation"][0]["role"] == "user"
    assert run["itinerary"].startswith("# FINAL ITINERARY")

    # resuming the stream after the last event only replays newer ones
    assert read_sse(client, run_id) and client.get(f"/api/runs/{run_id}/events?after={events[-1]['id']}").text.strip() == "event: end\ndata: {}"

    listed = client.get("/api/runs?kind=benchmark").json()
    assert listed[0]["id"] == run_id
    assert client.get("/api/tasks").json()[0]["latest_run"]["id"] == run_id

    stats = client.get("/api/stats").json()
    assert stats["benchmark"]["by_difficulty"]["easy"]["success_rate"] == 1.0
    assert stats["totals"]["runs"] == 1


def test_custom_trip_run(client):
    trip = {
        "origin_city": "Detroit",
        "destinations": [{"city": "chicago", "nights": 2}],
        "departure_date": "2026-02-27",
        "budget_max": 500,
        "party_size": 1,
        "interests": ["live music"],
        "min_activities": 1,
        "min_restaurants": 0,
    }
    created = client.post("/api/runs", json={"custom": trip})
    assert created.status_code == 201
    run = wait_for(client, created.json()["id"])
    assert run["kind"] == "custom"
    assert run["task"]["scenario"]["destination_cities"] == ["Chicago"]
    assert run["task"]["required_components"] == ["hotel_2_nights", "activities_min_1"]
    assert run["status"] == "completed"


def test_unroutable_custom_trip_is_rejected(client):
    trip = {
        "origin_city": "Detroit",
        "destinations": [{"city": "Kyoto", "nights": 3}],
        "departure_date": "2026-05-01",
        "budget_max": 4000,
        "party_size": 2,
    }
    response = client.post("/api/runs", json={"custom": trip})
    assert response.status_code == 422
    assert "No direct flight from Detroit to Kyoto." in response.json()["detail"]["problems"]

    assert client.post("/api/runs", json={}).status_code == 422
    assert client.post("/api/runs", json={"task_name": "easy1", "custom": trip}).status_code == 422


def test_running_run_can_be_stopped_then_deleted(client):
    client.llm_choice["llm"] = SlowLoopingModel
    run_id = client.post("/api/runs", json={"task_name": "easy1"}).json()["id"]
    wait_for(client, run_id, statuses=("running",))

    assert client.delete(f"/api/runs/{run_id}").status_code == 409
    deadline = time.time() + 10
    while client.post(f"/api/runs/{run_id}/stop").status_code == 409 and time.time() < deadline:
        time.sleep(0.05)  # agent registers just after the status flips to running

    run = wait_for(client, run_id)
    assert run["status"] == "stopped"
    assert run["record"] is not None
    assert client.post(f"/api/runs/{run_id}/stop").status_code == 409
    assert client.delete(f"/api/runs/{run_id}").status_code == 204
    assert client.get(f"/api/runs/{run_id}").status_code == 404


def test_build_task_for_multi_city_trip_with_disruption():
    from pathlib import Path

    catalog = Catalog(Path(__file__).resolve().parents[1] / "benchmarks" / "mock_data")
    trip = CustomTrip(
        origin_city="New York",
        destinations=[{"city": "Paris", "nights": 3}, {"city": "Venice", "nights": 2}, {"city": "Barcelona", "nights": 4}],
        departure_date=date(2026, 6, 15),
        budget_max=11000,
        party_size=2,
        disruption="budget_cut",
    )
    assert catalog.validate_trip(trip) == []
    task = build_task(trip, catalog, "custom_x")

    assert task["initial_constraints"]["hard"]["return_date"] == "2026-06-24"
    assert task["required_components"][:5] == [
        "outbound_flight", "mid_trip_flight_paris_to_venice", "mid_trip_flight_venice_to_barcelona",
        "return_flight", "hotel_paris_3_nights",
    ]
    assert task["dynamic_events"][0]["event_type"] == "budget_reduced"
    assert task["success_criteria"]["total_cost_max"] == 9350.0

    with pytest.raises(ValueError):
        CustomTrip(origin_city="Paris", destinations=[{"city": "Paris", "nights": 1}], departure_date=date(2026, 1, 1), budget_max=1, party_size=1)


def test_cors_allows_only_the_configured_frontend(client):
    allowed = client.options("/api/runs", headers={"Origin": "https://planner.example", "Access-Control-Request-Method": "GET",
                                                   "Access-Control-Request-Headers": "authorization"})
    assert allowed.headers["access-control-allow-origin"] == "https://planner.example"
    denied = client.options("/api/runs", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in denied.headers


def test_cors_origin_regex_admits_preview_deployments(tmp_path, mcp_client):
    settings = replace(Settings(), db_path=tmp_path / "cors.db", web_dist_dir=tmp_path / "none",
                       cors_origins=(), cors_origin_regex=r"^https://planner-[a-z0-9-]+\.vercel\.app$")
    with TestClient(create_app(settings, agent_factory=lambda cb: None, mcp_client=mcp_client)) as anon:
        preflight = lambda origin: anon.options("/api/auth/login", headers={"Origin": origin, "Access-Control-Request-Method": "POST"})
        assert preflight("https://planner-git-feature-abc.vercel.app").headers.get("access-control-allow-origin")
        assert "access-control-allow-origin" not in preflight("https://planner-x.vercel.app.evil.com").headers


# ------------------------------------------------------------- API credits


def test_running_out_of_credits_mid_run_keeps_partial_itinerary(client):
    client.llm_choice["llm"] = lambda: CreditsRunOutModel(script=SCRIPT_EASY1, calls=[], fail_after=3)
    run_id = client.post("/api/runs", json={"task_name": "easy1", "judge": True}).json()["id"]
    events = read_sse(client, run_id)
    run = wait_for(client, run_id)

    assert run["status"] == "failed"
    assert run["error_kind"] == "credits_exhausted"
    assert "run out of Anthropic API credits" in run["error"]
    # the two flights booked before credits ran out are kept and rendered
    assert run["api_calls"] == 3 and run["success"] == 0
    assert "bk_flight_DET_CHI_001_20260227" in run["itinerary"]
    assert run["record"]["llm_error"]["kind"] == "credits_exhausted"
    assert run["judge_score"] is None  # no judge call once credits are gone

    types = [e["type"] for e in events]
    assert "llm_unavailable" in types and "judging" not in types
    assert types.count("step") == 3  # stopped immediately instead of looping on errors
    assert events[-1]["data"]["error_kind"] == "credits_exhausted"

    health = client.get("/api/health").json()["llm"]
    assert health["status"] == "credits_exhausted"
    assert "run out of Anthropic API credits" in health["message"]


def test_credit_status_clears_once_a_call_succeeds_again(client):
    client.llm_choice["llm"] = lambda: CreditsRunOutModel(script=SCRIPT_EASY1, calls=[], fail_after=0)
    first = client.post("/api/runs", json={"task_name": "easy1"}).json()["id"]
    run = wait_for(client, first)
    assert run["error_kind"] == "credits_exhausted" and run["api_calls"] in (0, None)
    assert client.get("/api/health").json()["llm"]["status"] == "credits_exhausted"

    client.llm_choice["llm"] = lambda: ScriptedChatModel(script=SCRIPT_EASY1, calls=[])  # credits topped up
    second = client.post("/api/runs", json={"task_name": "easy1"}).json()["id"]
    assert wait_for(client, second)["status"] == "completed"
    assert client.get("/api/health").json()["llm"] == {"status": "ok"}


def test_billing_402_and_bad_key_are_reported_distinctly(client):
    client.llm_choice["llm"] = lambda: CreditsRunOutModel(script=SCRIPT_EASY1, calls=[], fail_after=1,
                                                         error=(402, "billing_error", "Payment required."))
    assert wait_for(client, client.post("/api/runs", json={"task_name": "easy1"}).json()["id"])["error_kind"] == "credits_exhausted"

    client.llm_choice["llm"] = lambda: CreditsRunOutModel(script=SCRIPT_EASY1, calls=[], fail_after=0,
                                                         error=(401, "authentication_error", "invalid x-api-key"))
    run = wait_for(client, client.post("/api/runs", json={"task_name": "easy1"}).json()["id"])
    assert run["error_kind"] == "auth_error"
    assert "API key was rejected" in run["error"]
    assert client.get("/api/health").json()["llm"]["status"] == "auth_error"


def test_judge_running_out_of_credits_keeps_the_completed_itinerary(client):
    class BrokeJudge:
        def evaluate(self, task, agent_output):
            raise anthropic_error(*LOW_BALANCE)

    client.judge_choice["judge"] = BrokeJudge
    run_id = client.post("/api/runs", json={"task_name": "easy1", "judge": True}).json()["id"]
    events = read_sse(client, run_id)
    run = wait_for(client, run_id)

    assert run["status"] == "completed" and run["success"] == 1
    assert run["error_kind"] == "credits_exhausted" and run["judge_score"] is None
    assert run["record"]["judge_error"]["kind"] == "credits_exhausted"
    assert "judge_failed" in [e["type"] for e in events]
    assert client.get("/api/health").json()["llm"]["status"] == "credits_exhausted"


def test_transient_errors_are_not_mistaken_for_credit_problems():
    from src.core.llm_errors import classify_llm_error

    assert classify_llm_error(anthropic_error(*LOW_BALANCE)) == "credits_exhausted"
    assert classify_llm_error(anthropic_error(400, "invalid_request_error", "You have reached your specified API usage limits.")) == "credits_exhausted"
    assert classify_llm_error(anthropic_error(429, "rate_limit_error", "Number of request tokens has exceeded your per-minute rate limit")) is None
    assert classify_llm_error(anthropic_error(529, "overloaded_error", "Overloaded")) is None
    assert classify_llm_error(TimeoutError("read timed out")) is None
    wrapped = RuntimeError("agent failed")
    wrapped.__cause__ = anthropic_error(*LOW_BALANCE)
    assert classify_llm_error(wrapped) == "credits_exhausted"


def test_database_from_before_error_kind_is_migrated(tmp_path):
    import sqlite3
    from server.store import RunStore

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE runs (id TEXT PRIMARY KEY, created_at REAL NOT NULL, started_at REAL, finished_at REAL, status TEXT NOT NULL, "
                 "kind TEXT NOT NULL, task_name TEXT, title TEXT, difficulty TEXT, judge_requested INTEGER NOT NULL DEFAULT 0, "
                 "task_json TEXT NOT NULL, record_json TEXT, itinerary TEXT, error TEXT, success INTEGER, total_tokens INTEGER, "
                 "api_calls INTEGER, cost_usd REAL, judge_score REAL, judge_passed INTEGER)")
    conn.execute("INSERT INTO runs (id, created_at, status, kind, task_json) VALUES ('old', 1, 'completed', 'custom', '{}')")
    conn.commit()
    conn.close()

    store = RunStore(db)
    assert store.list_runs()[0]["error_kind"] is None


def test_disruption_events_stream_without_crashing(client):
    """Regression: firing a disruption with an event callback attached raised
    TypeError: _emit() got multiple values for argument 'event_type'."""
    trip = {
        "origin_city": "Detroit",
        "destinations": [{"city": "Chicago", "nights": 2}],
        "departure_date": "2026-02-27",
        "budget_max": 500,
        "party_size": 1,
        "min_activities": 1,
        "min_restaurants": 0,
        "disruption": "budget_cut",
    }
    run_id = client.post("/api/runs", json={"custom": trip}).json()["id"]
    events = read_sse(client, run_id)
    run = wait_for(client, run_id)

    assert run["status"] == "completed", run["error"]
    disruption = next(e for e in events if e["type"] == "disruption")
    assert disruption["data"]["event_type"] == "budget_reduced"
    assert disruption["data"]["budget_max_after"] == 425.0
    assert disruption["data"]["state"]["budget_max"] == 425.0


def test_trip_whose_flights_dont_run_that_day_is_rejected_up_front(client):
    """The failed production trip: out Sunday 2026-11-08, back Tuesday, but Chicago -> Detroit only flies Sun/Mon."""
    trip = {
        "origin_city": "Detroit",
        "destinations": [{"city": "Chicago", "nights": 2}],
        "departure_date": "2026-11-08",
        "budget_max": 800,
        "party_size": 1,
    }
    response = client.post("/api/runs", json={"custom": trip})
    assert response.status_code == 422
    assert response.json()["detail"]["problems"] == [
        "Chicago → Detroit doesn't fly on Tuesday 2026-11-10; it only flies on Mondays, Sundays. Adjust the departure date or nights."
    ]
    assert client.get("/api/runs").json() == []  # nothing was started, so nothing was spent

    trip["destinations"][0]["nights"] = 1  # back Monday: flights operate
    assert client.post("/api/runs", json={"custom": trip}).status_code == 201

    route_days = {(a, b): days for a, b, days in client.get("/api/catalog").json()["route_days"]}
    assert route_days[("Chicago", "Detroit")] == ["monday", "sunday"]
