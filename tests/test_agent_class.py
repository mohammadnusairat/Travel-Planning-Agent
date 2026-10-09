import os
import json
import time
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmark import build_record
from src.agent import TravelAgent

TASKS = [
    #"benchmarks/tasks/easy/easy1.json",
    "benchmarks/tasks/easy/easy2.json",
    #"benchmarks/tasks/easy/easy3.json",
    #"benchmarks/tasks/easy/easy4.json",
    #"benchmarks/tasks/easy/easy5.json",
    "benchmarks/tasks/medium/medium6.json",
    #"benchmarks/tasks/medium/medium7.json",
    #"benchmarks/tasks/medium/medium8.json",
    #"benchmarks/tasks/medium/medium9.json",
    #"benchmarks/tasks/medium/medium10.json"
]

OUTPUT_DIR = PROJECT_ROOT / "agent_planning_results"
OUTPUT_DIR.mkdir(exist_ok=True)

def main():
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required to run this integration script.")

    with TravelAgent(api_key=api_key) as agent:
        for task_path in TASKS:
            run_one(agent, task_path)


def run_one(agent, task_path):
    task_name = Path(task_path).stem  # e.g. "easy1"
    task_file = PROJECT_ROOT / task_path
    print(f"\n{'='*60}")
    print(f"Running: {task_name}  ({task_path})")
    print(f"{'='*60}")

    with open(task_file) as f:
        task = json.load(f)

    wall_start = time.time()
    result = agent.plan_trip(task)
    wall_elapsed = round(time.time() - wall_start, 2)

    result["metadata"]["wall_time_seconds"] = wall_elapsed

    metadata_output = build_record(task, Path(task_path), result)
    json_path = OUTPUT_DIR / f"{task_name}.json"
    with open(json_path, "w") as f:
        json.dump(metadata_output, f, indent=2)

    md_path = OUTPUT_DIR / f"{task_name}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(result["itinerary"])

    print(f"  Success:   {result['success']}")
    print(f"  API calls: {result['metadata']['api_calls']}")
    print(f"  Tokens:    {result['metadata']['total_tokens']}")
    print(f"  Time:      {wall_elapsed}s")
    print(f"  Metadata:  {json_path}")
    print(f"  Itinerary: {md_path}")


if __name__ == "__main__":
    main()
