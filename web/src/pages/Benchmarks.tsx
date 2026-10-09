import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError, type BenchmarkTask, type Stats, type TierStats } from "../api";
import { CreditsWarning } from "../components/credits";
import { Card, ErrorNote, OutcomeBadge, Stat, StatusBadge } from "../components/ui";
import { humanize, money, pct, relativeTime, tokens } from "../format";

const TIERS = ["easy", "medium", "hard"];

export default function Benchmarks() {
  const navigate = useNavigate();
  const [tasks, setTasks] = useState<BenchmarkTask[] | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [judge, setJudge] = useState(true);
  const [starting, setStarting] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    const load = () => {
      api.tasks().then(setTasks).catch(setError);
      api.stats().then(setStats).catch(setError);
    };
    load();
    const timer = setInterval(load, 5000); // pick up runs finishing in the background
    return () => clearInterval(timer);
  }, []);

  async function start(name: string) {
    setStarting(name);
    setError(null);
    try {
      const { id } = await api.startBenchmark(name, judge);
      navigate(`/runs/${id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, String(e)));
      setStarting(null);
    }
  }

  const tiers = stats?.benchmark?.by_difficulty ?? {};

  return (
    <div className="stack">
      <div className="page-head">
        <h1>Benchmarks</h1>
        <p className="muted">
          21 tasks across three tiers. Medium and hard tasks fire disruptions mid-planning (cancellations, budget cuts,
          party changes) that the agent has to replan around. Tier stats use the latest completed run of each task.
        </p>
      </div>

      <div className="tier-grid">
        {TIERS.map((tier) => (
          <TierCard key={tier} tier={tier} stats={tiers[tier]} total={tasks?.filter((t) => t.difficulty === tier).length ?? 0} />
        ))}
      </div>

      {error && <ErrorNote message={error.message} problems={error.problems} />}
      <CreditsWarning />

      <Card
        title="Tasks"
        actions={
          <label className="check small">
            <input type="checkbox" checked={judge} onChange={(e) => setJudge(e.target.checked)} /> Score with LLM judge
          </label>
        }
      >
        {!tasks ? (
          <p className="muted">Loading…</p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Task</th>
                  <th>Trip</th>
                  <th>Disruptions</th>
                  <th>Latest run</th>
                  <th className="num">Judge</th>
                  <th className="num">Tokens</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {tasks.map((task) => {
                  const latest = task.latest_run;
                  return (
                    <tr key={task.name}>
                      <td>
                        <span className={`tier tier-${task.difficulty}`}>{task.difficulty}</span> <strong>{task.name}</strong>
                        <div className="muted small">{task.title}</div>
                      </td>
                      <td className="small">
                        {task.origin_city} → {task.destination_cities.join(" → ")}
                        <div className="muted">
                          {task.days} days · {money(task.budget_max, 0)} · party of {task.party_size}
                        </div>
                      </td>
                      <td className="small">{task.dynamic_events.length ? task.dynamic_events.map(humanize).join(", ") : <span className="muted">None</span>}</td>
                      <td>
                        {latest ? (
                          <Link to={`/runs/${latest.id}`} className="run-link">
                            <StatusBadge status={latest.status} errorKind={latest.error_kind} /> <OutcomeBadge success={latest.success} status={latest.status} />
                            <div className="muted small">{relativeTime(latest.created_at)}</div>
                          </Link>
                        ) : (
                          <span className="muted small">Never run</span>
                        )}
                      </td>
                      <td className="num">{latest?.judge_score != null ? latest.judge_score.toFixed(1) : "—"}</td>
                      <td className="num">{tokens(latest?.total_tokens)}</td>
                      <td className="num">
                        <button className="button-small" onClick={() => start(task.name)} disabled={starting !== null}>
                          {starting === task.name ? "Starting…" : "Run"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function TierCard({ tier, stats, total }: { tier: string; stats?: TierStats; total: number }) {
  return (
    <Card title={<span className={`tier tier-${tier}`}>{tier}</span>} actions={<span className="muted small">{stats?.tasks ?? 0}/{total} tasks run</span>}>
      {stats ? (
        <div className="mini-stats">
          <Stat label="Success" value={pct(stats.success_rate)} hint={`${pct(stats.constraint_valid_rate)} constraint-valid`} />
          <Stat label="Judge avg" value={stats.judge_avg_score?.toFixed(1) ?? "—"} hint={stats.judge_pass_rate != null ? `${pct(stats.judge_pass_rate)} pass` : undefined} />
          <Stat label="Avg tokens" value={tokens(Math.round(stats.avg_total_tokens ?? 0))} hint={`${stats.avg_api_calls} API calls`} />
          <Stat label="Cost" value={money(stats.total_cost_usd, 2)} hint={stats.tracking_valid ? "accounting ✓" : "accounting ✗"} />
        </div>
      ) : (
        <p className="muted small">No completed runs yet.</p>
      )}
    </Card>
  );
}
