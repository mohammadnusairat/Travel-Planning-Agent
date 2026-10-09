# TravelBench

A multi-difficulty benchmark and LangChain ReAct agent for evaluating LLM travel planning under dynamic replanning scenarios. The agent reasons over 20 tools served by an MCP server, a constraint engine enforces temporal, budget, and dependency rules, and every run is scored by an LLM judge with token/API-call accounting.

---

## Table of Contents

1. [Overview](#overview)
2. [Project Structure](#project-structure)
3. [Setup](#setup)
4. [Web App](#web-app)
5. [Running the Agent](#running-the-agent)
6. [Reproducing Experiments](#reproducing-experiments)
7. [Baseline](#baseline)
8. [Evaluation](#evaluation)
9. [Benchmark Design](#benchmark-design)
10. [MCP Tools](#mcp-tools)
11. [Mock Data](#mock-data)

---

## Overview

Travel planning requires coordinating flights, hotels, activities, and restaurants under hard budget and date constraints — and adapting when things go wrong mid-trip. TravelBench tests AI agents on:

- End-to-end itinerary planning (single and multi-city)
- Hard constraint satisfaction (budget, dates, accessibility, party size)
- Dynamic replanning when disruptions occur (hotel cancellations, weather, budget cuts)

The benchmark contains **21 structured tasks** across three difficulty tiers. A custom **LLM-as-a-judge** evaluator scores each itinerary across 5 dimensions. A zero-shot **baseline** (same model, single call, real mock data injected as context) is provided for comparison.

| Component | Implementation |
|---|---|
| Agent | ReAct loop (THOUGHT → ACTION → OBSERVATION) on LangChain `ChatAnthropic` (`claude-sonnet-4-6`) |
| Tools | 20 tools served by an MCP server (`src/mcp_server/server.py`), loaded as LangChain tools via `langchain-mcp-adapters` |
| Constraints | `ConstraintTracker`: temporal logic, budget limits, dependency rules, itinerary validation |
| Disruptions | Dynamic events fire mid-planning; affected and dependent bookings are found through the dependency graph |
| Evaluation | LLM judge (5 weighted dimensions) and `benchmark.py` with per-tier token/API/cost tracking |

---

## Project Structure

```
travel-agent/
├── src/
│   ├── agent.py              # LangChain ReAct agent (main implementation)
│   ├── main.py               # CLI runner for a single task
│   ├── mcp_server/
│   │   └── server.py         # MCP server exposing the 20 travel tools
│   ├── tools/                # Mock-data backends wrapped by the MCP server
│   │   ├── flights.py
│   │   ├── hotels.py
│   │   ├── restaurants.py
│   │   └── activities.py
│   ├── core/
│   │   ├── constraints.py    # ConstraintTracker: temporal, budget, dependency rules + validation
│   │   ├── mcp_client.py     # MCP session -> LangChain tools (stdio or in-memory transport)
│   │   └── usage.py          # Token / API-call / tool-call / cost accounting
│   └── utils/
│       ├── prompts.py        # System prompt, planning/replanning/final templates
│       └── types.py          # Booking, Constraint, ConstraintViolation models
│
├── server/                   # FastAPI backend for the web app
│   ├── app.py                # REST + server-sent-events API, serves the built frontend
│   ├── runner.py             # Runs agents on worker threads, records live events
│   ├── store.py              # SQLite persistence (runs + event streams)
│   ├── trips.py              # Inventory catalog, custom trip -> task translation
│   └── config.py             # Settings (.env is loaded automatically)
│
├── web/                      # React + TypeScript frontend (Vite)
│   └── src/
│       ├── pages/            # PlanTrip, RunView (live), Benchmarks, History
│       ├── llmStatus.tsx     # Polls the server's Claude API status (e.g. out of credits)
│       ├── api.ts            # Typed API client
│       └── useRunEvents.ts   # Live event stream hook
│
├── benchmarks/
│   ├── tasks/
│   │   ├── easy/             # easy1–5.json   (task_001–005)
│   │   ├── medium/           # medium1–10.json (task_006–015)
│   │   └── hard/             # hard1–6.json   (task_016–021)
│   └── mock_data/
│       ├── flights.json      # Flight routes between all city pairs
│       ├── hotels.json       # 75 hotels across 24 cities
│       ├── activities.json   # 98 activities across 24 cities
│       └── restaurants.json  # ~175 restaurants across 24 cities
│
├── evaluations/
│   └── llm_judge.py          # LLM-as-a-judge: single task or batch folder mode
│
├── tests/
│   ├── test_agent_class.py   # Integration runner (edit TASKS list to choose tasks)
│   ├── test_llm_judge.py     # Unit tests for judge helper functions
│   ├── test_constraints.py   # Unit tests for ConstraintTracker basics
│   ├── test_constraint_rules.py # Temporal / dependency / validation rules
│   ├── test_mcp_integration.py  # MCP server, LangChain tools, end-to-end ReAct loop
│   ├── test_benchmark.py     # Benchmark aggregation, tracking checks, judge usage
│   └── test_agent_logic.py   # Agent requirement / replanning logic
│
├── agent_planning_results/   # Agent outputs: <taskname>.md + <taskname>.json
├── baseline_results/         # Baseline outputs: <taskname>.md
│
├── benchmark.py              # Benchmark runner: agent + judge + per-tier metrics
├── baseline.py               # Zero-shot single-call baseline with mock data context
```

---

## Setup

### 1. Clone and create a virtual environment

```bash
git clone <repo-url>
cd travel-agent
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Set your API key

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Or put `ANTHROPIC_API_KEY=...` in a `.env` file in the project root (gitignored). The web app loads it automatically.

---

## Web App

A FastAPI + React app for planning trips, watching the agent work live, running benchmark tasks, and reviewing past runs.

| Page | What it does |
|---|---|
| **Plan a trip** | Form for origin, up to 4 stops (with nights at each), dates, budget, party size, interests, accessibility, minimum activities/restaurants, and an optional simulated disruption (hotel cancellation or a 15% budget cut). Stop dropdowns only offer cities with a direct flight from the previous stop. |
| **Run view** | Live timeline of each THOUGHT → ACTION → tool result, plus disruptions and replanning, streamed over server-sent events. A side panel tracks budget, requirements, bookings, constraint violations, and token usage as they change. A *Stop run* button ends a run early. Finished runs add tabs for the itinerary, constraint validation and audit trail, tokens and cost per call, the judge's per-dimension scores, and the task spec. |
| **Benchmarks** | All 21 tasks with each one's latest result. Run any task (optionally judged). Per-tier cards show success rate, average judge score, tokens, and cost. |
| **History** | Every run, with status, judge score, API calls, tokens, cost, and duration, plus lifetime totals. |

Runs are stored in SQLite at `data/travel_app.db`. Every run makes real Claude API calls: about $0.20 for an easy trip and $1–1.50 for a hard multi-city one, plus about $0.05 if judged.

### Access and API credits

There are no accounts: anyone who opens the app can plan trips, and everyone shares one run history. Spending is controlled by the usage limit on your Anthropic API key.

When the key runs out of credits or hits its usage limit, the app says so clearly instead of failing silently:

- **Detection:** a `402 billing_error`, or a request rejected because of the credit balance or usage limit, stops the run immediately. The agent doesn't keep retrying. Bookings made before that point are kept and shown.
- **On the run:** the run is marked **Out of credits** with an explanation, and the timeline shows where it stopped. If only the LLM judge ran out, the itinerary is kept and only the score is missing.
- **Site-wide:** every visitor sees an "Out of API credits" banner, and the Plan and Benchmarks pages warn before a run is started. The banner clears automatically once a Claude API call succeeds again.
- **Bad key:** a rejected API key (401/403) is reported separately as "API key rejected".

### Run it

```bash
pip install -r requirements.txt
cd web && npm install && npm run build && cd ..
python -m server                 # http://127.0.0.1:8000
```

### Develop

```bash
python -m server --reload        # API on :8000
cd web && npm run dev            # UI on :5173 with hot reload; /api is proxied to :8000
```

All backend settings are listed in [`.env.example`](.env.example).

### Deploy: Railway (backend) + Vercel (frontend)

**Railway** builds the backend from the root `Dockerfile` (Python 3.12), using [`railway.json`](railway.json).

1. Create a service from this repo. Railway picks up `railway.json`, which builds the Dockerfile and health-checks `/api/health`.
2. **Add a volume** mounted at `/data`. The database lives at `/data/travel_app.db`; without a volume, run history is wiped on every deploy.
3. Set these variables:
   - `ANTHROPIC_API_KEY`
   - `RAILWAY_RUN_UID=0`. The image runs as a non-root user, and Railway volumes are root-owned, so without this the app can't write its database. If it's missing, the deploy fails at startup with an error that says so.
   - `TRAVEL_CORS_ORIGINS=https://<your-app>.vercel.app`
   - optionally `TRAVEL_CORS_ORIGIN_REGEX=^https://<your-project>-[a-z0-9-]+\.vercel\.app$` for preview deployments
4. Generate a public domain for the service.

Keep the service at **one replica**: in-progress runs (and stopping them) live in that process's memory. A redeploy marks any in-progress run as interrupted.

**Vercel** serves the frontend from [`web/`](web), configured by [`web/vercel.json`](web/vercel.json).

1. Import the repo and set **Root Directory** to `web`.
2. Set `VITE_API_URL=https://<your-service>.up.railway.app` (no trailing slash). It's read at build time, so redeploy after changing it.
3. Deploy, then add the Vercel URL to `TRAVEL_CORS_ORIGINS` on Railway if you haven't already.

Share the Vercel URL. Anyone with it can use the app.

### Tests

```bash
pytest                           # includes API and out-of-credits tests (tests/test_server.py) using a scripted LLM, no API calls
cd web && npm test               # frontend unit tests
```

---

## Running the Agent

### Single task via CLI

```bash
python3 src/main.py --task benchmarks/tasks/easy/easy1.json
```

Prints the itinerary, constraint validation, and token/API/cost usage. Writes the full result to `results/test_runs/<task_id>_output.json`.

### Benchmark runner (recommended)

```bash
# Agent only
python3 benchmark.py benchmarks/tasks/easy/

# Agent + LLM judge across all tiers
python3 benchmark.py benchmarks/tasks/ --judge --summary benchmark_summary.json
```

For each task this writes `agent_planning_results/<task>.md` (itinerary) and `<task>.json`. The JSON holds success flags, constraint violations, the operation log, the judge result, and usage: input/output tokens, API calls, a per-call log, tool calls by name, tool errors, and estimated cost. At the end it prints a per-tier table and writes `benchmark_summary.json`.

Each run's accounting is cross-checked: `api_calls` must equal the number of logged LLM calls, and the token totals must equal the sum of the per-call counts. Any mismatch marks the task `tracking_valid: false` and makes the runner exit non-zero.

### Run tasks and save results

Edit the `TASKS` list in `tests/test_agent_class.py` to select which tasks to run:

```python
TASKS = [
    "benchmarks/tasks/easy/easy1.json",
    "benchmarks/tasks/medium/medium1.json",
    "benchmarks/tasks/hard/hard1.json",
]
```

Then run:

```bash
python3 tests/test_agent_class.py
```

Results are saved to `agent_planning_results/`:
- `<taskname>.md` — full itinerary in Markdown
- `<taskname>.json` — the same record format `benchmark.py` writes

---

## Reproducing Experiments

### Step 1 — Run the ReAct agent on all 21 tasks

Set `TASKS` in `tests/test_agent_class.py` to all 21 tasks:

```python
TASKS = [
    "benchmarks/tasks/easy/easy1.json",
    "benchmarks/tasks/easy/easy2.json",
    "benchmarks/tasks/easy/easy3.json",
    "benchmarks/tasks/easy/easy4.json",
    "benchmarks/tasks/easy/easy5.json",
    "benchmarks/tasks/medium/medium1.json",
    "benchmarks/tasks/medium/medium2.json",
    "benchmarks/tasks/medium/medium3.json",
    "benchmarks/tasks/medium/medium4.json",
    "benchmarks/tasks/medium/medium5.json",
    "benchmarks/tasks/medium/medium6.json",
    "benchmarks/tasks/medium/medium7.json",
    "benchmarks/tasks/medium/medium8.json",
    "benchmarks/tasks/medium/medium9.json",
    "benchmarks/tasks/medium/medium10.json",
    "benchmarks/tasks/hard/hard1.json",
    "benchmarks/tasks/hard/hard2.json",
    "benchmarks/tasks/hard/hard3.json",
    "benchmarks/tasks/hard/hard4.json",
    "benchmarks/tasks/hard/hard5.json",
    "benchmarks/tasks/hard/hard6.json",
]
```

```bash
python3 tests/test_agent_class.py
```

### Step 2 — Evaluate agent results with the LLM judge

```bash
# Per tier
python3 evaluations/llm_judge.py benchmarks/tasks/easy/   agent_planning_results/
python3 evaluations/llm_judge.py benchmarks/tasks/medium/ agent_planning_results/
python3 evaluations/llm_judge.py benchmarks/tasks/hard/   agent_planning_results/
```

### Step 3 — Run the baseline on all tasks

```bash
python3 baseline.py benchmarks/tasks/easy/ benchmarks/tasks/medium/ benchmarks/tasks/hard/
```

Results go to `baseline_results/`.

### Step 4 — Evaluate baseline results

```bash
python3 evaluations/llm_judge.py benchmarks/tasks/easy/   baseline_results/
python3 evaluations/llm_judge.py benchmarks/tasks/medium/ baseline_results/
python3 evaluations/llm_judge.py benchmarks/tasks/hard/   baseline_results/
```

---

## Baseline

`baseline.py` makes a single API call per task. It injects city-filtered mock inventory (flights, hotels, activities, restaurants) as context into the prompt — no tools, no state, no ReAct loop.

```bash
# Single task
python3 baseline.py benchmarks/tasks/easy/easy1.json

# Whole tier
python3 baseline.py benchmarks/tasks/medium/

# All tiers at once
python3 baseline.py benchmarks/tasks/easy/ benchmarks/tasks/medium/ benchmarks/tasks/hard/
```

Output: `baseline_results/<taskname>.md`

| | Baseline | ReAct Agent |
|---|---|---|
| API calls | 1 | 10–50 |
| Tools | None | 20 MCP tools (LangChain) |
| State | None | ConstraintTracker + booking log |
| Dynamic events | Described in prompt | Fired at `trigger_turn`, auto-replanned |
| Booking IDs | May hallucinate | Real `bk_*` IDs from tool results |
| Budget tracking | Estimated | Live per-category tracking |

---

## Evaluation

### LLM-as-a-Judge (`evaluations/llm_judge.py`)

Scores itineraries using `claude-sonnet-4-6` across 5 weighted dimensions. Every judge call's tokens are recorded. Each `JudgeResult.usage` reports API calls, input/output tokens, and estimated cost, and batch mode prints the judge's total usage under the score table.

| Dimension | Weight | What it measures |
|---|---|---|
| Hard Constraints | 30% | Budget, dates, accessibility, party size |
| Required Components | 25% | All required bookings present |
| Soft Preferences | 15% | Interests and preferences reflected |
| Replanning Quality | 20% | Dynamic events handled (N/A if no events) |
| Itinerary Coherence | 10% | Logical schedule, no conflicts, full duration |

Pass threshold: **70 / 100**. Replanning weight is redistributed when not applicable.

#### Single task

```bash
python3 evaluations/llm_judge.py \
    benchmarks/tasks/easy/easy1.json \
    agent_planning_results/easy1.md
```

#### Batch (whole folder)

```bash
python3 evaluations/llm_judge.py benchmarks/tasks/medium/ agent_planning_results/
```

Batch mode matches by filename stem (`medium1.json` ↔ `medium1.md`), prefers `.md` over `.json` when both exist, and prints a scored table with an average row.

#### Options

| Flag | Default | Description |
|---|---|---|
| `--threshold` | `70.0` | Pass/fail score threshold |
| `--max-tokens` | `4096` | Judge response token limit (increase to `8192` for hard tasks) |

### Unit tests

```bash
pytest tests/
```

---

## Benchmark Design

### Difficulty tiers

| Tier | Tasks | Key challenge |
|---|---|---|
| Easy | 5 | Single city, no dynamic events, basic constraints |
| Medium | 10 | Single city + 1 dynamic event requiring partial replanning |
| Hard | 6 | Multi-city + 1–2 complex events with cascading effects |

### Example tasks

| Task | Title | Dynamic Event |
|---|---|---|
| easy1 | Detroit → Chicago weekend, $300, student | None |
| medium1 | Denver → San Diego, family of 4, wheelchair | Hotel unavailable (turn 5) |
| medium6 | New Orleans jazz weekend, $2,500 | Budget cut forced (turn 6) |
| hard1 | NYC → Anchorage + Fairbanks, 8 days | Glacier tour weather-cancelled (turn 8) |
| hard2 | European honeymoon: Paris → Venice → Barcelona | Mid-trip flight cancellation |
| hard5 | California wine & nature, group of 8 | Group size change + venue closes |

### Task schema

```json
{
  "task_id": "task_006",
  "difficulty": "medium",
  "title": "Accessible Family Beach Vacation",
  "scenario": {
    "origin_city": "Denver",
    "destination_cities": ["San Diego"],
    "trip_duration_days": 5
  },
  "user_profile": {
    "party_size": 4,
    "traveler_types": ["adult", "adult", "teen", "child"],
    "special_needs": ["wheelchair_accessible"]
  },
  "initial_constraints": {
    "hard": { "budget_max": 4500, "departure_date": "2026-07-15", "return_date": "2026-07-20", "wheelchair_accessible": true },
    "soft": { "interests": ["beach", "family_activities", "zoo"] }
  },
  "dynamic_events": [{
    "event_id": "event_1",
    "trigger_turn": 5,
    "event_type": "accommodation_unavailable",
    "description": "Hotel accessible rooms fully booked. Find alternative.",
    "affected_components": ["hotel"],
    "resolution_requirements": ["Find wheelchair-accessible hotel for full stay", "Verify within budget"]
  }],
  "required_components": [
    "outbound_flight", "return_flight",
    "hotel_5_nights_wheelchair_accessible",
    "beach_activities_min_2", "family_activities_min_3",
    "accessible_restaurants_min_5"
  ],
  "success_criteria": {
    "total_cost_max": 4500,
    "all_venues_wheelchair_accessible": true,
    "replanning_successful": true
  }
}
```

### Agent architecture

The agent (`src/agent.py`) uses a **ReAct (Reasoning + Acting)** loop. LLM calls go through LangChain. Tool calls go over MCP to the travel tool server:

```
Task JSON
   │
   ▼
THOUGHT → ACTION (tool call) → OBSERVATION (tool result)
   │            │
   │            ▼
   │      ConstraintTracker pre-check (time overlap, flight buffers, trip dates)
   │            │
   │            ▼
   │      LangChain MCP tool ──stdio──▶ MCP server (20 tools) ──▶ mock data
   │            │
   │            ▼
   │      budget limit check → commit booking → dependency graph updated
   └──────────────────────────────┘  (up to 50 iterations)
   │
   ▼  (requirements satisfied, no constraint errors)
FINAL ITINERARY (+ constraint validation section)
```

### Constraint engine (`src/core/constraints.py`)

| Rule family | Rules |
|---|---|
| Temporal logic | Booking time windows (activity duration, 90-minute meals, flight block times, overnight flights); no overlapping bookings; 90 min after arrival before anything at the destination; 120 min before departure; per-city buffers for mid-trip flights in multi-city trips; bookings must fall within the trip dates |
| Budget limits | Live spend vs. the active cap (which can change after a `budget_reduced` event). Bookings that would exceed it are rejected and the hold is released. Mandatory surcharges are always recorded |
| Dependency rules | Inferred graph: a hotel depends on the flights into and out of its city; activities and meals depend on the hotel covering that day and on same-day flights in that city. `find_all_affected_bookings` walks it transitively, so a cancelled mid-trip flight cascades to the hotels and plans around it |
| Validation | `validate_itinerary()` returns structured `ConstraintViolation`s (errors vs. warnings), including the "two cities on the same night" error, late check-in and early check-out warnings, and accessibility. `check_candidate()` screens a booking before it is made |

Hard-constraint satisfaction (and the agent's finalize gate) requires zero error-level violations. Violations are shown to the model every turn and in the final itinerary's "Constraint Validation" section.

Key design decisions:

| Decision | Detail |
|---|---|
| ConstraintTracker | Tracks budget, all bookings, hard constraints live across turns |
| Message windowing | Context = first message + last 8 messages — bounds token growth |
| Dynamic event firing | Events fire at `trigger_turn` in both action and non-action branches |
| Required-component nudge | STILL NEEDED checklist injected mid-conversation |
| Booking order | Enforced: flights → hotel → activities → restaurants |
| One step per turn | Tool turns use stop sequences (`\nOBSERVATION`, `\nTHOUGHT:`) so each response is a single THOUGHT/ACTION and still returns exact token usage |
| Token/API tracking | `UsageTracker` records every LLM call's input/output tokens and latency, every tool call by name, and estimated cost |

---

## MCP Tools

`src/mcp_server/server.py` is a FastMCP server. The agent starts it over stdio (`MCPToolClient(transport="stdio")`) and loads its tools as LangChain tools. The tool list in the system prompt is generated from the server's advertised schemas. Tests use the in-memory transport, which speaks the same MCP protocol without starting a subprocess.

| Group | Tools |
|---|---|
| Flights (5) | `search_flights`, `find_connecting_flights`, `get_flight_details`, `book_flight`, `cancel_flight` |
| Hotels (4) | `search_hotels`, `get_hotel_details`, `book_hotel`, `cancel_hotel` |
| Restaurants (4) | `search_restaurants`, `get_restaurant_details`, `book_restaurant`, `cancel_restaurant` |
| Activities (4) | `search_activities`, `get_activity_details`, `book_activity`, `cancel_activity` |
| Utilities (3) | `check_availability`, `get_destination_overview`, `record_surcharge` |

Run the server on its own (for example, to plug it into another MCP client):

```bash
python3 -m src.mcp_server.server
```

---

## Mock Data

All tools query local JSON files. Data covers **24 cities**:

**US:** Chicago, Miami, Austin, New York, San Francisco, San Diego, Seattle, Washington D.C., Orlando, New Orleans, Las Vegas, Portland, Anchorage, Fairbanks, Napa Valley, Lake Tahoe, Zion NP, Bryce Canyon NP, Moab

**International:** Paris, Venice, Barcelona, Tokyo, Kyoto

| File | Records | Key fields |
|---|---|---|
| `flights.json` | ~160 routes | airline, route, departure/arrival, price_per_seat, stops, wheelchair_accessible |
| `hotels.json` | 75 | stars, price_per_night, amenities, wheelchair_accessible, room_types, tags |
| `activities.json` | 98 | type, duration_hours, price_per_person, wheelchair_accessible, permit_required |
| `restaurants.json` | ~175 | price_level, avg_cost_per_person, meal_type, is_wheelchair_accessible |

---
