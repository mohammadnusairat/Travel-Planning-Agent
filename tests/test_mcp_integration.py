import json
from pathlib import Path
from typing import Any, List, Optional

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from src.agent import ACTION_STOP_SEQUENCES, TravelAgent
from src.core.mcp_client import MCPToolClient
from src.mcp_server.server import TOOL_NAMES

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ScriptedChatModel(BaseChatModel):
    """Deterministic LangChain chat model: replays scripted ReAct turns with fixed token usage."""

    script: List[str]
    calls: List[dict] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        index = len(self.calls)
        self.calls.append({"stop": stop, "num_messages": len(messages)})
        text = self.script[index] if index < len(self.script) else "FINAL ITINERARY"
        message = AIMessage(content=text, usage_metadata={"input_tokens": 1000, "output_tokens": 50, "total_tokens": 1050})
        return ChatResult(generations=[ChatGeneration(message=message)])


@pytest.fixture(scope="module")
def mcp_client():
    client = MCPToolClient(transport="memory")
    yield client
    client.close()


def load_task(name: str) -> dict:
    with open(PROJECT_ROOT / "benchmarks" / "tasks" / name.rstrip("0123456789") / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)


def test_mcp_server_exposes_twenty_tools(mcp_client):
    assert len(TOOL_NAMES) == 20
    assert sorted(mcp_client.tool_names) == sorted(TOOL_NAMES)


def test_mcp_tools_are_langchain_tools(mcp_client):
    from langchain_core.tools import BaseTool

    assert all(isinstance(tool, BaseTool) for tool in mcp_client.tools.values())
    described = {tool["name"]: tool for tool in mcp_client.describe_tools()}
    assert described["book_flight"]["required"] == [
        "flight_id", "outbound", "origin_city", "destination_city", "departure_date", "party_size"
    ]


def test_mcp_tool_results_match_underlying_tool_shapes(mcp_client):
    hotels = mcp_client.call("search_hotels", city="Chicago", max_price=150)
    assert isinstance(hotels, list) and hotels
    flights = mcp_client.call("search_flights", original_city="Detroit", destination_city="Chicago", departure_date="2026-02-27")
    assert set(flights) == {"outbound_flights", "return_flights"}
    missing = mcp_client.call("get_activity_details", activity_id="act_NOPE")
    assert missing["status"] == "error"


def test_mcp_connecting_flights_and_availability(mcp_client):
    routings = mcp_client.call("find_connecting_flights", original_city="Paris", destination_city="Venice", departure_date="2026-06-18")
    assert routings["routings"] and routings["routings"][0]["stops"] == 0

    closed = mcp_client.call("check_availability", item_type="activity", item_id="act_CHI_004", date="2026-02-28", time="10:00")
    assert closed["available"] is False
    assert "outside operating hours" in closed["reasons"][0]


def test_mcp_invalid_arguments_surface_as_tool_errors(mcp_client):
    result = mcp_client.call("book_flight", flight_id="flight_DET_CHI_001")
    assert result["status"] == "error"


def test_mcp_stdio_transport_starts_server_subprocess():
    with MCPToolClient(transport="stdio") as client:
        assert len(client.tool_names) == 20
        assert client.call("get_destination_overview", city="Chicago")["hotels"]["count"] > 0


SCRIPT_EASY1 = [
    'THOUGHT: Find flights first.\nACTION: search_flights(original_city="Detroit", destination_city="Chicago", departure_date="2026-02-27", return_date="2026-03-01")',
    'THOUGHT: Book cheapest outbound.\nACTION: book_flight(flight_id="flight_DET_CHI_001", outbound=True, origin_city="Detroit", destination_city="Chicago", departure_date="2026-02-27", party_size=1)',
    'THOUGHT: Book return.\nACTION: book_flight(flight_id="flight_CHI_DET_001", outbound=False, origin_city="Chicago", destination_city="Detroit", departure_date="2026-03-01", party_size=1)',
    'THOUGHT: Try an expensive hotel.\nACTION: book_hotel(hotel_id="hotel_CHI_001", check_in="2026-02-27", check_out="2026-03-01", party_size=1, num_rooms=1)',
    'THOUGHT: Over budget, pick a cheaper one.\nACTION: book_hotel(hotel_id="hotel_CHI_004", check_in="2026-02-27", check_out="2026-03-01", party_size=1, num_rooms=1)',
    'THOUGHT: Live music on arrival day, too early.\nACTION: book_activity(activity_id="act_CHI_004", date="2026-02-27", time="09:30", party_size=1)',
    'THOUGHT: Saturday night instead.\nACTION: book_activity(activity_id="act_CHI_004", date="2026-02-28", time="20:00", party_size=1)',
]


def test_agent_runs_react_loop_over_mcp_with_token_tracking(mcp_client):
    llm = ScriptedChatModel(script=SCRIPT_EASY1, calls=[])
    agent = TravelAgent(llm=llm, mcp_client=mcp_client)
    result = agent.plan_trip(load_task("easy1"))

    meta = result["metadata"]
    booked = {b["booking_id"] if isinstance(b, dict) else b.booking_id for b in agent.tracker.bookings}
    assert "bk_hotel_CHI_004_20260227" in booked
    assert "bk_hotel_CHI_001_20260227" not in booked          # rejected by the budget limit
    assert any(bid.startswith("bk_act_CHI_004_20260228") for bid in booked)
    assert not any(bid.startswith("bk_act_CHI_004_20260227") for bid in booked)

    # every LLM call is counted with exact usage
    assert meta["api_calls"] == len(llm.calls)
    assert meta["input_tokens"] == 1000 * len(llm.calls)
    assert meta["output_tokens"] == 50 * len(llm.calls)
    assert meta["total_tokens"] == 1050 * len(llm.calls)
    assert meta["estimated_cost_usd"] == pytest.approx(len(llm.calls) * (1000 * 3 + 50 * 15) / 1_000_000)
    assert meta["tool_calls"] == 7
    assert meta["tool_calls_by_name"]["book_hotel"] == 2
    assert meta["tool_errors"] == 2
    assert len(meta["llm_calls"]) == len(llm.calls)

    # ReAct turns stop after a single THOUGHT/ACTION step
    assert llm.calls[0]["stop"] is None
    assert llm.calls[1]["stop"] == ACTION_STOP_SEQUENCES

    assert result["validation"] == (True, [])
    assert result["hard_constraints_satisfied"] is True
    assert result["success"] is True
    assert "## 9. Constraint Validation" in result["itinerary"]


def test_agent_system_prompt_lists_mcp_discovered_tools(mcp_client):
    agent = TravelAgent(llm=ScriptedChatModel(script=[], calls=[]), mcp_client=mcp_client)
    for name in TOOL_NAMES:
        assert f"- {name}(" in agent.system_prompt


def test_agent_routes_lookup_and_surcharge_tools_through_mcp(mcp_client):
    agent = TravelAgent(llm=ScriptedChatModel(script=[], calls=[]), mcp_client=mcp_client)
    agent._reset()
    agent.tracker.add_constraint("budget_max", 100)

    details = agent._execute_tool("get_activity_details", {"activity_id": '"act_CHI_004"'})
    assert details["duration_hours"] == 3.0

    fee = agent._execute_tool("record_surcharge", {"amount": "150", "description": '"Rebooking fee"'})
    assert fee["status"] == "success"
    # mandatory fees are always recorded, even past the budget
    assert agent.tracker.budget_used == 150.0
    assert agent.tracker.get_bookings_by_type("surcharge")[0].booking_id == fee["booking_id"]


def test_service_proxy_accepts_positional_arguments(mcp_client):
    from src.core.mcp_client import MCPServiceProxy

    hotels = MCPServiceProxy(mcp_client, cancel="cancel_hotel")
    result = hotels.cancel("bk_hotel_CHI_004_20260227")
    assert result["status"] == "success"
    with pytest.raises(TypeError):
        hotels.cancel("a", "b")


def test_dynamic_event_cancels_invalidated_booking_over_mcp(mcp_client):
    agent = TravelAgent(llm=ScriptedChatModel(script=[], calls=[]), mcp_client=mcp_client)
    task = load_task("easy1")
    agent._reset()
    agent._load_constraints(task)
    agent._scenario = {}  # no implicit flight requirement, so the hotel can be booked directly
    booked = agent._execute_tool("book_hotel", {
        "hotel_id": '"hotel_CHI_004"', "check_in": '"2026-02-27"', "check_out": '"2026-03-01"', "party_size": "1",
    })
    assert booked["status"] == "success"

    event = {
        "event_type": "accommodation_unavailable",
        "description": "Hotel is no longer available.",
        "affected_components": ["hotel"],
        "resolution_requirements": ["Find another hotel"],
    }
    prompt = agent._handle_dynamic_event(event, "", task)

    assert agent.tracker.get_bookings_by_type("hotel") == []
    assert agent._triggered_events[0]["removed_booking_ids"] == [booked["booking_id"]]
    assert "DYNAMIC EVENT OCCURRED" in prompt


def test_surcharge_is_logged_as_confirmed_in_audit_trail(mcp_client):
    agent = TravelAgent(llm=ScriptedChatModel(script=[], calls=[]), mcp_client=mcp_client)
    agent._reset()
    fee = agent._execute_tool("record_surcharge", {"amount": "800", "description": '"Rebooking fee"'})
    assert agent._operation_log[-1]["summary"] == f"confirmed {fee['booking_id']}"
