import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, type RunKind, type RunSummary, type Stats } from "../api";
import { Card, Empty, ErrorNote, OutcomeBadge, Stat, StatusBadge } from "../components/ui";
import { duration, money, relativeTime, tokens } from "../format";

export default function History() {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [kind, setKind] = useState<RunKind | "">("");
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    const load = () => {
      api.runs(kind || undefined).then(setRuns).catch(setError);
      api.stats().then(setStats).catch(setError);
    };
    load();
    const timer = setInterval(load, 4000);
    return () => clearInterval(timer);
  }, [kind]);

  const totals = stats?.totals;

  return (
    <div className="stack">
      <div className="page-head">
        <h1>Run history</h1>
        <p className="muted">Every custom trip and benchmark run, with its outcome, judge score, and API spend.</p>
      </div>

      {totals && (
        <div className="stats-row card">
          <Stat label="Runs" value={totals.runs} hint={`${totals.completed} completed`} />
          <Stat label="Succeeded" value={totals.succeeded} />
          <Stat label="API calls" value={tokens(totals.api_calls)} />
          <Stat label="Tokens" value={tokens(totals.tokens)} />
          <Stat label="Total spend" value={money(totals.cost_usd, 2)} hint="agent + judge, estimated" />
        </div>
      )}

      {error && <ErrorNote message={error.message} />}

      <Card
        title="Runs"
        actions={
          <select aria-label="Filter runs" value={kind} onChange={(e) => setKind(e.target.value as RunKind | "")}>
            <option value="">All runs</option>
            <option value="custom">Custom trips</option>
            <option value="benchmark">Benchmark tasks</option>
          </select>
        }
      >
        {!runs ? (
          <p className="muted">Loading…</p>
        ) : runs.length === 0 ? (
          <Empty>
            No runs yet. <Link to="/plan">Plan a trip</Link> or run a <Link to="/benchmarks">benchmark task</Link>.
          </Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Trip</th>
                  <th>Status</th>
                  <th className="num">Judge</th>
                  <th className="num">API calls</th>
                  <th className="num">Tokens</th>
                  <th className="num">Cost</th>
                  <th className="num">Duration</th>
                  <th>Started</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id}>
                    <td>
                      <Link to={`/runs/${run.id}`}>
                        <strong>{run.title}</strong>
                      </Link>
                      <div className="muted small">
                        {run.kind === "benchmark" ? `Benchmark · ${run.task_name} · ${run.difficulty}` : "Custom trip"}
                      </div>
                    </td>
                    <td>
                      <StatusBadge status={run.status} errorKind={run.error_kind} /> <OutcomeBadge success={run.success} status={run.status} />
                    </td>
                    <td className="num">{run.judge_score != null ? run.judge_score.toFixed(1) : "—"}</td>
                    <td className="num">{run.api_calls ?? "—"}</td>
                    <td className="num">{tokens(run.total_tokens)}</td>
                    <td className="num">{money(run.cost_usd, 4)}</td>
                    <td className="num">{duration(run.started_at, run.finished_at)}</td>
                    <td className="small muted">{relativeTime(run.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
