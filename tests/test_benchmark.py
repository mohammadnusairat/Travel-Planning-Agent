import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmark import build_record, format_summary_table, summarize, validate_tracking
from evaluations.llm_judge import LLMJudge
from src.agent import TravelAgent
from src.core.mcp_client import MCPToolClient
from tests.test_mcp_integration import SCRIPT_EASY1, ScriptedChatModel, load_task

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def easy1_record():
    with MCPToolClient(transport="memory") as client:
        agent = TravelAgent(llm=ScriptedChatModel(script=SCRIPT_EASY1, calls=[]), mcp_client=client)
        task = load_task("easy1")
        result = agent.plan_trip(task)
    return build_record(task, PROJECT_ROOT / "benchmarks/tasks/easy/easy1.json", result)


def test_build_record_carries_validation_and_usage(easy1_record):
    assert easy1_record["task_name"] == "easy1"
    assert easy1_record["success"] is True
    assert easy1_record["constraints_valid"] is True
    assert easy1_record["tracking_valid"] is True
    assert easy1_record["metadata"]["api_calls"] == 7
    json.dumps(easy1_record)  # serializable


def test_validate_tracking_flags_missing_token_counts():
    metadata = {
        "api_calls": 2,
        "llm_calls": [
            {"input_tokens": 100, "output_tokens": 10},
            {"input_tokens": 0, "output_tokens": 0},   # e.g. a call whose usage was never captured
        ],
        "input_tokens": 100,
        "output_tokens": 10,
        "total_tokens": 110,
        "tool_calls": 1,
        "tool_calls_by_name": {"search_hotels": 1},
    }
    assert validate_tracking(metadata) == ["an LLM call reported zero input or output tokens"]

    metadata["api_calls"] = 3
    assert "api_calls=3 but 2 LLM calls logged" in validate_tracking(metadata)


def test_summarize_aggregates_per_tier(easy1_record):
    hard = json.loads(json.dumps(easy1_record))
    hard.update(difficulty="hard", success=False, task_name="hard1")
    hard["judge"] = {"overall_score": 60.0, "passed": False, "usage": {"total_tokens": 5000, "estimated_cost_usd": 0.02}}
    easy = dict(easy1_record, judge={"overall_score": 90.0, "passed": True, "usage": {"total_tokens": 4000, "estimated_cost_usd": 0.015}})

    summary = summarize([hard, easy])

    assert list(summary["by_difficulty"]) == ["easy", "hard"]
    assert summary["by_difficulty"]["easy"]["success_rate"] == 1.0
    assert summary["overall"]["success_rate"] == 0.5
    assert summary["overall"]["avg_api_calls"] == 7
    assert summary["overall"]["total_tokens"] == 2 * easy1_record["metadata"]["total_tokens"]
    assert summary["overall"]["judge_avg_score"] == 75.0
    assert summary["overall"]["judge_total_tokens"] == 9000
    assert summary["overall"]["judge_cost_usd"] == 0.035
    assert "OVERALL" in format_summary_table(summary)


def test_llm_judge_records_token_usage_per_evaluation():
    judge_json = json.dumps({
        key: {"score": 8.0, "reasoning": "ok", "findings": []}
        for key in ("hard_constraints", "required_components", "soft_preferences", "itinerary_coherence")
    } | {"replanning_quality": {"score": None, "reasoning": "N/A", "findings": []}})

    class FakeMessages:
        def create(self, **kwargs):
            return SimpleNamespace(
                content=[SimpleNamespace(text=judge_json)],
                usage=SimpleNamespace(input_tokens=3000, output_tokens=400),
            )

    judge = LLMJudge(api_key="test-key")
    judge.client = SimpleNamespace(messages=FakeMessages())

    task = load_task("easy1")
    first = judge.evaluate(task, {"itinerary": "# FINAL ITINERARY", "conversation": []})
    second = judge.evaluate(task, {"itinerary": "# FINAL ITINERARY", "conversation": []})

    assert first.usage["api_calls"] == 1
    assert first.usage["total_tokens"] == 3400
    assert first.usage["estimated_cost_usd"] == pytest.approx((3000 * 3 + 400 * 15) / 1_000_000)
    assert second.usage["api_calls"] == 1
    assert judge.usage.summary()["total_tokens"] == 6800
    assert first.to_dict()["usage"]["input_tokens"] == 3000
    assert "Judge usage:" in first.summary()
