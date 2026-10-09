import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, TERMINAL, type LiveState, type RunDetail, type RunEvent } from "../api";
import { RunCreditsNotice } from "../components/credits";
import { Card, Empty, ErrorNote, Markdown, Meter, OutcomeBadge, Stat, StatusBadge } from "../components/ui";
import { useLlmStatus } from "../llmStatus";
import { duration, humanize, money, parseStep, tokens } from "../format";
import { useRunEvents } from "../useRunEvents";

type Tab = "itinerary" | "timeline" | "validation" | "usage" | "judge" | "task";

export default function RunView() {
  const { runId } = useParams();
  const navigate = useNavigate();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("timeline");
  const [stopping, setStopping] = useState(false);
  const stream = useRunEvents(runId);
  const { refresh: refreshLlmStatus } = useLlmStatus();
  // Re-fetch the run whenever it changes phase (started, judged, finished).
  const phase = stream.events.filter((e) => ["started", "judged", "finished"].includes(e.type)).length;

  useEffect(() => {
    if (!runId) return;
    api.run(runId).then(setRun).catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, [runId, phase]);

  // A credits problem in this run should show up in the site-wide banner straight away. React to
  // "finished" / "judge_failed" (sent after the server records the problem), not "llm_unavailable" (sent before).
  const creditsEvent = stream.events.some(
    (e) => (e.type === "finished" && e.data.error_kind) || (e.type === "judge_failed" && e.data.kind),
  );
  useEffect(() => {
    if (creditsEvent) refreshLlmStatus();
  }, [creditsEvent, refreshLlmStatus]);

  const finished = run ? TERMINAL.includes(run.status) : false;
  useEffect(() => {
    if (finished && run?.itinerary) setTab("itinerary");
  }, [finished, run?.itinerary]);

  // keep "elapsed" ticking while the run is live
  const [, tick] = useState(0);
  useEffect(() => {
    if (finished) return;
    const timer = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, [finished]);

  if (error) return <ErrorNote message={error} />;
  if (!run) return <p className="muted">Loading run…</p>;

  const status = run.status;
  const record = run.record;
  const state = stream.state;

  async function stop() {
    setStopping(true);
    try {
      await api.stop(run!.id);
    } catch (e) {
      setStopping(false);
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function remove() {
    if (!window.confirm("Delete this run and its history?")) return;
    await api.remove(run!.id);
    navigate("/history");
  }

  const tabs: [Tab, string, boolean][] = [
    ["itinerary", "Itinerary", Boolean(run.itinerary)],
    ["timeline", "Agent timeline", true],
    ["validation", "Constraints", Boolean(record)],
    ["usage", "Tokens & cost", Boolean(record)],
    ["judge", "Judge", Boolean(record?.judge)],
    ["task", "Task spec", true],
  ];

  return (
    <div className="stack">
      <div className="run-head">
        <div>
          <div className="eyebrow">
            {run.kind === "benchmark" ? (
              <Link to="/benchmarks">
                Benchmark · {run.task_name} · {run.difficulty}
              </Link>
            ) : (
              <Link to="/history">Custom trip</Link>
            )}
          </div>
          <h1>{run.title}</h1>
          <div className="run-badges">
            <StatusBadge status={status} errorKind={run.error_kind} />
            <OutcomeBadge success={run.success} status={run.status} />
            {run.judge_score != null && (
              <span className={`badge ${run.judge_passed ? "badge-pass" : "badge-fail"}`}>Judge {run.judge_score.toFixed(1)}/100</span>
            )}
            <span className="muted small">Elapsed {duration(run.started_at ?? run.created_at, run.finished_at)}</span>
          </div>
        </div>
        <div className="run-actions">
          {!finished && (
            <button className="button-danger" onClick={stop} disabled={stopping || status === "queued"}>
              {stopping ? "Stopping after this step…" : "Stop run"}
            </button>
          )}
          {finished && run.kind === "benchmark" && (
            <button className="button-ghost" onClick={async () => navigate(`/runs/${(await api.startBenchmark(run.task_name!, Boolean(run.judge_requested))).id}`)}>
              Run again
            </button>
          )}
          {finished && (
            <button className="button-ghost" onClick={remove}>
              Delete
            </button>
          )}
        </div>
      </div>

      {run.error_kind ? (
        <RunCreditsNotice
          errorKind={run.error_kind}
          judgeOnly={run.status !== "failed"}
          bookings={stream.state?.bookings.length ?? 0}
        />
      ) : (
        run.error && <ErrorNote message={run.error} />
      )}

      <div className="run-layout">
        <div className="stack">
          <div className="tabs" role="tablist">
            {tabs
              .filter(([, , enabled]) => enabled)
              .map(([key, label]) => (
                <button key={key} role="tab" aria-selected={tab === key} className={tab === key ? "tab tab-on" : "tab"} onClick={() => setTab(key)}>
                  {label}
                </button>
              ))}
          </div>

          {tab === "itinerary" && run.itinerary && (
            <Card>
              <Markdown>{run.itinerary}</Markdown>
            </Card>
          )}
          {tab === "timeline" && <Timeline events={stream.events} live={!finished} />}
          {tab === "validation" && record && <ValidationPanel run={run} />}
          {tab === "usage" && record && <UsagePanel run={run} />}
          {tab === "judge" && record?.judge && <JudgePanel run={run} />}
          {tab === "task" && (
            <Card>
              <pre className="code">{JSON.stringify(run.task, null, 2)}</pre>
            </Card>
          )}
        </div>

        <aside className="stack">
          <LivePanel state={state} />
        </aside>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ timeline

interface StepGroup {
  key: number;
  event: RunEvent;
  result?: RunEvent;
}

function Timeline({ events, live }: { events: RunEvent[]; live: boolean }) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const [follow, setFollow] = useState(true);

  const items = useMemo(() => {
    const grouped: StepGroup[] = [];
    for (const event of events) {
      if (event.type === "tool_result") {
        const owner = [...grouped].reverse().find((g) => g.event.type === "step" && g.event.data.iteration === event.data.iteration);
        if (owner) {
          owner.result = event;
          continue;
        }
      }
      grouped.push({ key: event.id, event });
    }
    return grouped;
  }, [events]);

  useEffect(() => {
    if (live && follow) bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [items.length, live, follow]);

  if (!events.length) return <Empty>Waiting for the agent to start…</Empty>;

  return (
    <div className="timeline">
      {live && (
        <label className="check small follow">
          <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> Follow live
        </label>
      )}
      {items.map(({ key, event, result }) => (
        <TimelineItem key={key} event={event} result={result} />
      ))}
      {live && (
        <div className="timeline-item timeline-pending">
          <span className="dot dot-live" />
          <span className="muted">Agent is working…</span>
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  );
}

function TimelineItem({ event, result }: { event: RunEvent; result?: RunEvent }) {
  const d = event.data;
  switch (event.type) {
    case "queued":
      return <SimpleItem tone="muted" text="Run queued" />;
    case "started":
      return <SimpleItem tone="info" text="Agent started planning" />;
    case "step": {
      const { thought, action } = parseStep(d.text ?? "");
      const ok = result?.data.ok;
      return (
        <div className="timeline-item">
          <span className={`dot ${result ? (ok ? "dot-ok" : "dot-error") : "dot-info"}`} />
          <div className="timeline-body">
            <div className="timeline-meta">
              Step {d.iteration} · {tokens(d.input_tokens)} in / {tokens(d.output_tokens)} out
            </div>
            {thought && <p className="thought">{thought}</p>}
            {action && <code className="action">{action}</code>}
            {!action && d.text && !thought && <p className="thought muted">{String(d.text).slice(0, 400)}</p>}
            {result && <ToolResult event={result} />}
          </div>
        </div>
      );
    }
    case "tool_result":
      return (
        <div className="timeline-item">
          <span className={`dot ${d.ok ? "dot-ok" : "dot-error"}`} />
          <div className="timeline-body">
            <ToolResult event={event} />
          </div>
        </div>
      );
    case "disruption":
      return (
        <div className="timeline-item">
          <span className="dot dot-warn" />
          <div className="timeline-body disruption">
            <div className="timeline-meta">Disruption · step {d.iteration}</div>
            <strong>{humanize(d.event_type)}</strong>
            <p>{d.description}</p>
            {d.removed_booking_ids?.length > 0 && <p className="small">Cancelled: {d.removed_booking_ids.join(", ")}</p>}
            {d.dependent_booking_ids?.length > 0 && <p className="small">Needs review (dependent bookings): {d.dependent_booking_ids.join(", ")}</p>}
            {d.budget_max_after != null && <p className="small">New budget: {money(d.budget_max_after)}</p>}
          </div>
        </div>
      );
    case "llm_unavailable":
      return (
        <div className="timeline-item">
          <span className="dot dot-error" />
          <div className="timeline-body credits-inline">
            <strong>{d.kind === "credits_exhausted" ? "Out of API credits" : "API key rejected"}</strong>
            <p>{d.message}</p>
          </div>
        </div>
      );
    case "judge_failed":
      return <SimpleItem tone="warn" text={d.message} />;
    case "stop_requested":
      return <SimpleItem tone="warn" text="Stop requested. Finishing after the current step." />;
    case "stopped":
      return <SimpleItem tone="warn" text={`Stopped at step ${d.iteration}`} />;
    case "judging":
      return <SimpleItem tone="info" text="LLM judge is scoring the itinerary…" />;
    case "judged":
      return <SimpleItem tone={d.passed ? "ok" : "error"} text={`Judge score ${Number(d.overall_score).toFixed(1)}/100 (${d.passed ? "pass" : "fail"})`} />;
    case "finished":
      return (
        <SimpleItem
          tone={d.status === "failed" ? "error" : d.success ? "ok" : "warn"}
          text={
            d.status === "failed"
              ? d.error_kind === "credits_exhausted"
                ? "Run stopped: out of API credits."
                : `Run failed: ${d.error}`
              : `Run ${d.status}. Requirements ${d.success ? "met" : "not fully met"}.`
          }
        />
      );
    default:
      return <SimpleItem tone="muted" text={event.type} />;
  }
}

function SimpleItem({ tone, text }: { tone: string; text: string }) {
  return (
    <div className="timeline-item">
      <span className={`dot dot-${tone}`} />
      <div className="timeline-body">
        <span className={tone === "muted" ? "muted" : ""}>{text}</span>
      </div>
    </div>
  );
}

function ToolResult({ event }: { event: RunEvent }) {
  const d = event.data;
  if (!d.ok) return <div className="tool-result tool-error">⚠ {d.error}</div>;
  const preview = d.preview ?? {};
  const lists = Object.entries(preview).filter(([, v]) => v && typeof v === "object" && "count" in (v as object)) as [string, { count: number; items: string[] }][];
  if ("count" in preview) lists.unshift(["results", preview as { count: number; items: string[] }]);
  return (
    <div className="tool-result">
      {preview.booking_id && (
        <div>
          ✓ Booked <code>{preview.booking_id}</code> {preview.cost != null && `· ${money(preview.cost)}`}
        </div>
      )}
      {preview.message && !preview.booking_id && <div>{preview.message}</div>}
      {preview.available != null && <div>{preview.available ? "Available" : `Unavailable: ${(preview.reasons ?? []).join("; ")}`}</div>}
      {lists.map(([name, list]) => (
        <div key={name}>
          <span className="muted">
            {humanize(name)}: {list.count} found
          </span>
          {list.items.length > 0 && <span className="small"> · {list.items.join(" · ")}</span>}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- live panel

function LivePanel({ state }: { state: LiveState | null }) {
  if (!state) return <Card title="Trip state"><Empty>No bookings yet.</Empty></Card>;
  const over = state.budget_max != null && state.budget_used > state.budget_max;
  const errors = state.violations.filter((v) => v.severity === "error");
  const warnings = state.violations.filter((v) => v.severity === "warning");
  return (
    <>
      <Card title="Budget">
        <div className="budget-line">
          <strong className={over ? "text-error" : ""}>{money(state.budget_used)}</strong>
          <span className="muted"> of {money(state.budget_max)}</span>
        </div>
        {state.budget_max != null && <Meter value={state.budget_used} max={state.budget_max} danger={over} />}
        <div className="muted small">Party of {state.party_size}</div>
      </Card>

      <Card title="Requirements">
        {state.requirements.length === 0 ? (
          <Empty>None specified.</Empty>
        ) : (
          <ul className="checklist">
            {state.requirements.map((r) => {
              const done = r.matched >= r.required;
              return (
                <li key={r.component} className={done ? "done" : ""}>
                  <span aria-hidden>{done ? "✓" : "○"}</span>
                  <span className="grow">{humanize(r.component)}</span>
                  <span className="muted small">
                    {Math.min(r.matched, r.required)}/{r.required}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      <Card title={`Bookings (${state.bookings.length})`}>
        {state.bookings.length === 0 ? (
          <Empty>Nothing booked yet.</Empty>
        ) : (
          <ul className="bookings">
            {state.bookings.map((b) => (
              <li key={b.booking_id}>
                <span className={`kind kind-${b.type}`}>{b.type}</span>
                <span className="grow">
                  {b.name}
                  <span className="muted small block">
                    {b.date}
                    {b.time ? ` ${b.time}` : ""}
                    {b.end_date ? ` → ${b.end_date}` : ""}
                  </span>
                </span>
                <span>{money(b.cost, 0)}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Constraints">
        {errors.length === 0 && warnings.length === 0 && <p className="text-ok">✓ All rules pass</p>}
        {[...errors, ...warnings].map((v, i) => (
          <p key={i} className={v.severity === "error" ? "text-error small" : "text-warn small"}>
            {v.severity === "error" ? "✗" : "!"} {v.message}
          </p>
        ))}
      </Card>

      <Card title="Usage">
        <div className="mini-stats">
          <Stat label="API calls" value={state.usage.api_calls} />
          <Stat label="Tool calls" value={state.usage.tool_calls} hint={state.usage.tool_errors ? `${state.usage.tool_errors} errors` : undefined} />
          <Stat label="Tokens" value={tokens(state.usage.total_tokens)} />
          <Stat label="Est. cost" value={money(state.usage.estimated_cost_usd, 4)} />
        </div>
      </Card>
    </>
  );
}

// ------------------------------------------------------------- result panels

function Flag({ ok, label }: { ok: boolean; label: string }) {
  return (
    <li className={ok ? "done" : ""}>
      <span aria-hidden>{ok ? "✓" : "✗"}</span> {label}
    </li>
  );
}

function ValidationPanel({ run }: { run: RunDetail }) {
  const r = run.record!;
  return (
    <div className="stack">
      <Card title="Outcome">
        <ul className="checklist">
          <Flag ok={r.final_itinerary_present} label="Final itinerary produced" />
          <Flag ok={r.planning_requirements_satisfied} label="All required components booked" />
          <Flag ok={r.hard_constraints_satisfied} label="Hard constraints (budget, timing, dependencies, accessibility)" />
          <Flag ok={r.success_criteria_satisfied} label="Task success criteria" />
        </ul>
      </Card>
      <Card title="Constraint violations">
        {r.constraint_violations.length === 0 ? (
          <p className="text-ok">✓ No violations</p>
        ) : (
          <table className="table">
            <thead>
              <tr><th>Severity</th><th>Rule</th><th>Detail</th></tr>
            </thead>
            <tbody>
              {r.constraint_violations.map((v, i) => (
                <tr key={i}>
                  <td className={v.severity === "error" ? "text-error" : "text-warn"}>{v.severity}</td>
                  <td>{humanize(v.rule)}</td>
                  <td>{v.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <Card title="Booking audit trail">
        {r.operation_log.length === 0 ? (
          <Empty>No bookings or cancellations.</Empty>
        ) : (
          <table className="table">
            <thead>
              <tr><th>Step</th><th>Action</th><th>Result</th></tr>
            </thead>
            <tbody>
              {r.operation_log.map((op, i) => (
                <tr key={i}>
                  <td>{op.turn}</td>
                  <td><code>{op.tool}</code></td>
                  <td>{op.summary}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

function UsagePanel({ run }: { run: RunDetail }) {
  const m = run.record!.metadata;
  const judge = run.record!.judge?.usage;
  const maxTokens = Math.max(1, ...m.llm_calls.map((c) => c.input_tokens + c.output_tokens));
  return (
    <div className="stack">
      <Card title="Agent usage">
        <div className="stats-row">
          <Stat label="API calls" value={m.api_calls} />
          <Stat label="Input tokens" value={tokens(m.input_tokens)} />
          <Stat label="Output tokens" value={tokens(m.output_tokens)} />
          <Stat label="Est. cost" value={money(m.estimated_cost_usd, 4)} hint={m.model} />
          <Stat label="LLM time" value={`${m.llm_latency_s.toFixed(1)}s`} hint={`${m.time_elapsed}s total`} />
        </div>
        {run.record!.tracking_valid ? (
          <p className="text-ok small">✓ Accounting checks pass: per-call tokens add up to the totals.</p>
        ) : (
          <ErrorNote message="Token accounting mismatch" problems={run.record!.tracking_problems} />
        )}
        {judge && (
          <p className="muted small">
            Judge: {judge.api_calls} call, {tokens(judge.total_tokens)} tokens, {money(judge.estimated_cost_usd, 4)}
          </p>
        )}
      </Card>

      <Card title="Tokens per LLM call">
        <div className="bars" aria-label="Tokens per call">
          {m.llm_calls.map((call, i) => {
            const total = call.input_tokens + call.output_tokens;
            return (
              <div className="bar-row" key={i} title={`Step ${call.turn}: ${call.input_tokens} in / ${call.output_tokens} out · ${call.latency_s}s`}>
                <span className="bar-label">#{call.turn ?? i + 1}</span>
                <div className="bar-track">
                  <div className="bar bar-in" style={{ width: `${(call.input_tokens / maxTokens) * 100}%` }} />
                  <div className="bar bar-out" style={{ width: `${(call.output_tokens / maxTokens) * 100}%` }} />
                </div>
                <span className="bar-value">{tokens(total)}</span>
              </div>
            );
          })}
        </div>
        <div className="legend small">
          <span><i className="swatch bar-in" /> input</span>
          <span><i className="swatch bar-out" /> output</span>
        </div>
      </Card>

      <Card title="Tool calls by name">
        <table className="table">
          <thead>
            <tr><th>Tool</th><th>Calls</th><th>Errors</th></tr>
          </thead>
          <tbody>
            {Object.entries(m.tool_calls_by_name)
              .sort((a, b) => b[1] - a[1])
              .map(([name, count]) => (
                <tr key={name}>
                  <td><code>{name}</code></td>
                  <td>{count}</td>
                  <td className={m.tool_errors_by_name[name] ? "text-error" : "muted"}>{m.tool_errors_by_name[name] ?? 0}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function JudgePanel({ run }: { run: RunDetail }) {
  const judge = run.record!.judge!;
  return (
    <div className="stack">
      <Card title={`Overall ${judge.overall_score.toFixed(1)}/100 (${judge.passed ? "pass" : "fail"}, threshold ${judge.pass_threshold})`}>
        <p className="muted small">Scored by {judge.judge_model}</p>
      </Card>
      {Object.entries(judge.dimensions).map(([name, dim]) => (
        <Card key={name} title={humanize(name)} actions={<strong>{dim.max_score > 0 ? `${dim.score.toFixed(1)}/10` : "N/A"}</strong>}>
          {dim.max_score > 0 && <Meter value={dim.score} max={10} danger={dim.score < 5} />}
          <p>{dim.reasoning}</p>
          {dim.findings.length > 0 && (
            <ul className="findings">
              {dim.findings.map((f, i) => (
                <li key={i}>{f}</li>
              ))}
            </ul>
          )}
        </Card>
      ))}
    </div>
  );
}
