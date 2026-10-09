"""
benchmark.py - Run the ReAct agent over benchmark tasks, optionally score each
itinerary with the LLM judge, and aggregate success / token / API-call / cost
metrics per difficulty tier.

Usage:
  python3 benchmark.py benchmarks/tasks/easy/easy1.json
  python3 benchmark.py benchmarks/tasks/easy/ benchmarks/tasks/medium/ --judge
  python3 benchmark.py benchmarks/tasks/ --judge --summary benchmark_summary.json

Per-task outputs go to --output-dir (<task>.md itinerary + <task>.json record).
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DIFFICULTY_ORDER = ["easy", "medium", "hard"]


def collect_task_files(inputs: List[str]) -> List[Path]:
    files: List[Path] = []
    for item in inputs:
        path = Path(item)
        if path.is_dir():
            files.extend(sorted(path.rglob("*.json"), key=lambda p: (p.parent.name, len(p.stem), p.stem)))
        elif path.suffix == ".json":
            files.append(path)
        else:
            raise SystemExit(f"Not a task file or directory: {item}")
    return files


def validate_tracking(metadata: Dict[str, Any]) -> List[str]:
    """Consistency checks on a run's token/API accounting. Returns a list of problems."""
    problems = []
    calls = metadata.get("llm_calls", [])
    if metadata.get("api_calls") != len(calls):
        problems.append(f"api_calls={metadata.get('api_calls')} but {len(calls)} LLM calls logged")
    if metadata.get("input_tokens") != sum(c["input_tokens"] for c in calls):
        problems.append("input_tokens does not match per-call log")
    if metadata.get("output_tokens") != sum(c["output_tokens"] for c in calls):
        problems.append("output_tokens does not match per-call log")
    if metadata.get("total_tokens") != metadata.get("input_tokens", 0) + metadata.get("output_tokens", 0):
        problems.append("total_tokens != input_tokens + output_tokens")
    if any(c["input_tokens"] <= 0 or c["output_tokens"] <= 0 for c in calls):
        problems.append("an LLM call reported zero input or output tokens")
    if metadata.get("tool_calls") != sum(metadata.get("tool_calls_by_name", {}).values()):
        problems.append("tool_calls does not match per-tool counts")
    return problems


def build_record(task: Dict[str, Any], task_file: Path, result: Dict[str, Any]) -> Dict[str, Any]:
    metadata = result["metadata"]
    is_valid, errors = result["validation"]
    tracking_problems = validate_tracking(metadata)
    return {
        "task_id": task.get("task_id", task_file.stem),
        "task_name": task_file.stem,
        "task_file": str(task_file),
        "title": task.get("title", ""),
        "difficulty": task.get("difficulty", "unknown"),
        "success": result["success"],
        "final_itinerary_present": result["final_itinerary_present"],
        "planning_requirements_satisfied": result["planning_requirements_satisfied"],
        "hard_constraints_satisfied": result["hard_constraints_satisfied"],
        "success_criteria_satisfied": result["success_criteria_satisfied"],
        "constraints_valid": is_valid,
        "constraint_errors": errors,
        "constraint_violations": result.get("constraint_violations", []),
        "operation_log": result.get("operation_log", []),
        "tracking_valid": not tracking_problems,
        "tracking_problems": tracking_problems,
        "metadata": metadata,
    }


def _mean(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 2) if values else None


def summarize(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate per-tier and overall benchmark metrics."""
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[record["difficulty"]].append(record)
    ordered = [d for d in DIFFICULTY_ORDER if d in groups] + sorted(d for d in groups if d not in DIFFICULTY_ORDER)

    def aggregate(items: List[Dict[str, Any]]) -> Dict[str, Any]:
        meta = [r["metadata"] for r in items]
        judged = [r["judge"] for r in items if r.get("judge")]
        costs = [m.get("estimated_cost_usd") for m in meta if m.get("estimated_cost_usd") is not None]
        judge_costs = [j["usage"].get("estimated_cost_usd") for j in judged if j.get("usage", {}).get("estimated_cost_usd") is not None]
        return {
            "tasks": len(items),
            "success_rate": round(sum(r["success"] for r in items) / len(items), 3),
            "constraint_valid_rate": round(sum(r["constraints_valid"] for r in items) / len(items), 3),
            "tracking_valid": all(r["tracking_valid"] for r in items),
            "avg_api_calls": _mean([m["api_calls"] for m in meta]),
            "avg_tool_calls": _mean([m["tool_calls"] for m in meta]),
            "avg_tool_errors": _mean([m.get("tool_errors", 0) for m in meta]),
            "avg_input_tokens": _mean([m["input_tokens"] for m in meta]),
            "avg_output_tokens": _mean([m["output_tokens"] for m in meta]),
            "avg_total_tokens": _mean([m["total_tokens"] for m in meta]),
            "total_tokens": sum(m["total_tokens"] for m in meta),
            "total_cost_usd": round(sum(costs), 4) if costs else None,
            "avg_time_s": _mean([m.get("time_elapsed", 0.0) for m in meta]),
            "judge_avg_score": _mean([j["overall_score"] for j in judged]),
            "judge_pass_rate": round(sum(j["passed"] for j in judged) / len(judged), 3) if judged else None,
            "judge_total_tokens": sum(j.get("usage", {}).get("total_tokens", 0) for j in judged) if judged else None,
            "judge_cost_usd": round(sum(judge_costs), 4) if judge_costs else None,
        }

    return {
        "by_difficulty": {difficulty: aggregate(groups[difficulty]) for difficulty in ordered},
        "overall": aggregate(records) if records else {},
    }


def format_summary_table(summary: Dict[str, Any]) -> str:
    headers = ["Tier", "Tasks", "Success", "Valid", "API calls", "Tool calls", "Tokens (avg)", "Cost $", "Judge", "Pass"]
    rows = []
    for name, stats in list(summary["by_difficulty"].items()) + [("OVERALL", summary["overall"])]:
        if not stats:
            continue
        rows.append([
            name,
            str(stats["tasks"]),
            f"{stats['success_rate']:.0%}",
            f"{stats['constraint_valid_rate']:.0%}",
            f"{stats['avg_api_calls']}",
            f"{stats['avg_tool_calls']}",
            f"{stats['avg_total_tokens']:,.0f}",
            "-" if stats["total_cost_usd"] is None else f"{stats['total_cost_usd']:.4f}",
            "-" if stats["judge_avg_score"] is None else f"{stats['judge_avg_score']:.1f}",
            "-" if stats["judge_pass_rate"] is None else f"{stats['judge_pass_rate']:.0%}",
        ])
    widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(len(headers))]
    line = lambda cells: "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells))
    return "\n".join([line(headers), line(["-" * w for w in widths])] + [line(row) for row in rows])


def run(args: argparse.Namespace) -> Dict[str, Any]:
    from src.agent import TravelAgent

    task_files = collect_task_files(args.tasks)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    judge = None
    if args.judge:
        from evaluations.llm_judge import LLMJudge
        judge = LLMJudge(pass_threshold=args.threshold, max_tokens=args.judge_max_tokens)

    records = []
    with TravelAgent(api_key=os.getenv("ANTHROPIC_API_KEY"), mcp_transport=args.mcp_transport) as agent:
        for index, task_file in enumerate(task_files, start=1):
            task = json.loads(task_file.read_text(encoding="utf-8"))
            print(f"\n[{index}/{len(task_files)}] {task_file.stem}: {task.get('title', '')}", flush=True)

            started = time.time()
            result = agent.plan_trip(task)
            result["metadata"]["wall_time_seconds"] = round(time.time() - started, 2)
            record = build_record(task, task_file, result)

            if judge is not None:
                judged = judge.evaluate(task, {"itinerary": result["itinerary"], "conversation": result["conversation"]})
                record["judge"] = judged.to_dict()

            (output_dir / f"{task_file.stem}.md").write_text(result["itinerary"], encoding="utf-8")
            (output_dir / f"{task_file.stem}.json").write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")

            meta = record["metadata"]
            judge_text = f"  judge={record['judge']['overall_score']:.1f}" if record.get("judge") else ""
            print(
                f"  success={record['success']}  valid={record['constraints_valid']}  "
                f"api_calls={meta['api_calls']}  tools={meta['tool_calls']}  tokens={meta['total_tokens']:,}  "
                f"cost=${meta.get('estimated_cost_usd') or 0:.4f}{judge_text}"
                + ("" if record["tracking_valid"] else f"  TRACKING PROBLEMS: {record['tracking_problems']}"),
                flush=True,
            )
            records.append(record)

    summary = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": agent.model, **summarize(records), "tasks": [
        {k: r[k] for k in ("task_name", "difficulty", "success", "constraints_valid", "tracking_valid")}
        | {"total_tokens": r["metadata"]["total_tokens"], "api_calls": r["metadata"]["api_calls"],
           "judge_score": r.get("judge", {}).get("overall_score")}
        for r in records
    ]}
    Path(args.summary).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\n" + format_summary_table(summary))
    print(f"\nSummary written to {args.summary}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the travel agent benchmark.")
    parser.add_argument("tasks", nargs="+", help="Task JSON files and/or directories")
    parser.add_argument("--judge", action="store_true", help="Score each itinerary with the LLM judge")
    parser.add_argument("--output-dir", default="agent_planning_results")
    parser.add_argument("--summary", default="benchmark_summary.json")
    parser.add_argument("--mcp-transport", choices=["stdio", "memory"], default="stdio")
    parser.add_argument("--threshold", type=float, default=70.0)
    parser.add_argument("--judge-max-tokens", type=int, default=8192)
    args = parser.parse_args()

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is required to run the benchmark.")
    summary = run(args)
    sys.exit(0 if summary["overall"].get("tracking_valid", False) else 1)


if __name__ == "__main__":
    main()
