import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError, type Catalog, type CustomTripInput } from "../api";
import { CreditsWarning } from "../components/credits";
import { Card, ErrorNote } from "../components/ui";
import { humanize } from "../format";

interface Stop {
  city: string;
  nights: number;
}

function isoDateInDays(days: number) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

const WEEKDAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];
const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** Legs with no direct flight, or whose flights don't run on the day that leg is flown. */
export function routeProblems(
  origin: string,
  stops: Stop[],
  routes: Set<string>,
  routeDays: Map<string, string[]> = new Map(),
  departure?: string,
): string[] {
  const cities = [origin, ...stops.map((s) => s.city), origin];
  const problems: string[] = [];
  let legDate = departure ? new Date(`${departure}T12:00:00Z`) : null;
  for (let i = 0; i < cities.length - 1; i++) {
    const [from, to] = [cities[i], cities[i + 1]];
    const key = `${from}→${to}`;
    if (from && to && !routes.has(key)) {
      problems.push(`No direct flight from ${from} to ${to}.`);
    } else if (from && to && legDate && !Number.isNaN(legDate.getTime()) && routeDays.has(key)) {
      const weekday = WEEKDAYS[legDate.getUTCDay()];
      const days = routeDays.get(key)!;
      if (!days.includes(weekday)) {
        const flies = WEEKDAYS.filter((d) => days.includes(d)).map((d) => `${capitalize(d)}s`).join(", ");
        problems.push(`${from} → ${to} doesn't fly on ${capitalize(weekday)} ${legDate.toISOString().slice(0, 10)}; it only flies on ${flies}.`);
      }
    }
    if (legDate && i < stops.length) legDate = new Date(legDate.getTime() + stops[i].nights * 86_400_000);
  }
  return problems;
}

export default function PlanTrip() {
  const navigate = useNavigate();
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [origin, setOrigin] = useState("Detroit");
  const [stops, setStops] = useState<Stop[]>([{ city: "Chicago", nights: 2 }]);
  const [departure, setDeparture] = useState(isoDateInDays(30));
  const [departureTouched, setDepartureTouched] = useState(false);
  const [budget, setBudget] = useState(800);
  const [partySize, setPartySize] = useState(1);
  const [interests, setInterests] = useState<string[]>(["live_music"]);
  const [interestDraft, setInterestDraft] = useState("");
  const [preferences, setPreferences] = useState("");
  const [accessible, setAccessible] = useState(false);
  const [minActivities, setMinActivities] = useState(2);
  const [minRestaurants, setMinRestaurants] = useState(2);
  const [disruption, setDisruption] = useState("none");
  const [judge, setJudge] = useState(true);
  const [title, setTitle] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    api.catalog().then(setCatalog).catch((e) => setError(e));
  }, []);

  const routes = useMemo(() => new Set((catalog?.routes ?? []).map(([a, b]) => `${a}→${b}`)), [catalog]);
  const routeDays = useMemo(() => new Map((catalog?.route_days ?? []).map(([a, b, days]) => [`${a}→${b}`, days])), [catalog]);
  const destinationNames = useMemo(() => (catalog?.destinations ?? []).map((d) => d.name), [catalog]);
  const reachableFrom = (city: string) => destinationNames.filter((d) => routes.has(`${city}→${d}`));
  const problems = catalog ? routeProblems(origin, stops, routes, routeDays, departure) : [];

  // Until the user picks a date, default to the first one ~30+ days out on which the trip's flights operate.
  useEffect(() => {
    if (!catalog || departureTouched) return;
    for (let offset = 30; offset < 60; offset++) {
      const candidate = isoDateInDays(offset);
      if (routeProblems(origin, stops, routes, routeDays, candidate).length === 0) {
        if (candidate !== departure) setDeparture(candidate);
        return;
      }
    }
  }, [catalog, departureTouched, origin, stops, routes, routeDays, departure]);
  const nights = stops.reduce((sum, s) => sum + s.nights, 0);

  const updateStop = (index: number, patch: Partial<Stop>) =>
    setStops((current) => current.map((s, i) => (i === index ? { ...s, ...patch } : s)));

  const addStop = () => {
    const from = stops.length ? stops[stops.length - 1].city : origin;
    const used = new Set([origin, ...stops.map((s) => s.city)]);
    const next = reachableFrom(from).find((c) => !used.has(c)) ?? "";
    setStops((current) => [...current, { city: next, nights: 2 }]);
  };

  const addInterest = (value: string) => {
    const cleaned = value.trim().replace(/\s+/g, "_").toLowerCase();
    if (cleaned && !interests.includes(cleaned) && interests.length < 10) setInterests([...interests, cleaned]);
    setInterestDraft("");
  };

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    const trip: CustomTripInput = {
      title: title.trim() || undefined,
      origin_city: origin,
      destinations: stops,
      departure_date: departure,
      budget_max: budget,
      party_size: partySize,
      interests,
      preferences: preferences.split(",").map((p) => p.trim()).filter(Boolean),
      wheelchair_accessible: accessible,
      min_activities: minActivities,
      min_restaurants: minRestaurants,
      disruption,
    };
    try {
      const { id } = await api.startCustom(trip, judge);
      navigate(`/runs/${id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, String(e)));
      setSubmitting(false);
    }
  }

  return (
    <div className="stack">
      <div className="page-head">
        <h1>Plan a trip</h1>
        <p className="muted">
          Describe the trip and the agent plans it live. It searches and books through the MCP tools while checking
          your budget, timing, and accessibility constraints.
        </p>
      </div>

      <form className="plan-grid" onSubmit={submit}>
        <Card title="Route">
          <div className="field">
            <label htmlFor="origin">Leaving from</label>
            <select id="origin" value={origin} onChange={(e) => setOrigin(e.target.value)}>
              {(catalog?.origins ?? [origin]).map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </div>

          <ol className="stops">
            {stops.map((stop, index) => {
              const from = index === 0 ? origin : stops[index - 1].city;
              const options = Array.from(new Set([...reachableFrom(from), stop.city].filter(Boolean)));
              return (
                <li key={index} className="stop-row">
                  <div className="field grow">
                    <label htmlFor={`stop-${index}`}>Stop {index + 1}</label>
                    <select id={`stop-${index}`} value={stop.city} onChange={(e) => updateStop(index, { city: e.target.value })}>
                      {!stop.city && <option value="">Choose a city…</option>}
                      {options.map((c) => (
                        <option key={c} value={c}>
                          {c}
                          {routes.has(`${from}→${c}`) ? "" : " (no direct flight)"}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="field narrow">
                    <label htmlFor={`nights-${index}`}>Nights</label>
                    <input id={`nights-${index}`} type="number" min={1} max={21} value={stop.nights}
                      onChange={(e) => updateStop(index, { nights: Number(e.target.value) })} />
                  </div>
                  {stops.length > 1 && (
                    <button type="button" className="icon-button" aria-label={`Remove stop ${index + 1}`}
                      onClick={() => setStops(stops.filter((_, i) => i !== index))}>
                      ×
                    </button>
                  )}
                </li>
              );
            })}
          </ol>
          {stops.length < 4 && (
            <button type="button" className="button-ghost" onClick={addStop}>
              + Add a stop
            </button>
          )}
          {problems.length > 0 && (
            <div className="note note-warn">
              {problems.map((p) => (
                <div key={p}>{p}</div>
              ))}
            </div>
          )}
          <p className="muted small">
            Round trip: {[origin, ...stops.map((s) => s.city), origin].join(" → ")} · {nights} night{nights === 1 ? "" : "s"}
          </p>
        </Card>

        <Card title="Dates, budget & party">
          <div className="row">
            <div className="field">
              <label htmlFor="departure">Departure</label>
              <input id="departure" type="date" value={departure} onChange={(e) => {
                setDepartureTouched(true);
                setDeparture(e.target.value);
              }} required />
            </div>
            <div className="field">
              <label htmlFor="budget">Budget (USD)</label>
              <input id="budget" type="number" min={1} step="any" value={budget} onChange={(e) => setBudget(Number(e.target.value))} required />
            </div>
            <div className="field narrow">
              <label htmlFor="party">Travelers</label>
              <input id="party" type="number" min={1} max={12} value={partySize} onChange={(e) => setPartySize(Number(e.target.value))} />
            </div>
          </div>
          <label className="check">
            <input type="checkbox" checked={accessible} onChange={(e) => setAccessible(e.target.checked)} />
            Wheelchair accessible: every hotel, activity, and restaurant must be accessible
          </label>
        </Card>

        <Card title="What to book">
          <div className="row">
            <div className="field">
              <label htmlFor="min-act">Activities (at least)</label>
              <input id="min-act" type="number" min={0} max={20} value={minActivities} onChange={(e) => setMinActivities(Number(e.target.value))} />
            </div>
            <div className="field">
              <label htmlFor="min-rest">Restaurants (at least)</label>
              <input id="min-rest" type="number" min={0} max={20} value={minRestaurants} onChange={(e) => setMinRestaurants(Number(e.target.value))} />
            </div>
          </div>

          <div className="field">
            <label htmlFor="interest">Interests</label>
            <div className="chips">
              {interests.map((i) => (
                <button type="button" key={i} className="chip chip-on" onClick={() => setInterests(interests.filter((x) => x !== i))} aria-label={`Remove ${i}`}>
                  {humanize(i)} ×
                </button>
              ))}
              <input id="interest" className="chip-input" placeholder="Type and press Enter" value={interestDraft}
                onChange={(e) => setInterestDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addInterest(interestDraft);
                  }
                }} />
            </div>
            <div className="chips suggestions">
              {(catalog?.interest_suggestions ?? [])
                .filter((s) => !interests.includes(s))
                .slice(0, 16)
                .map((s) => (
                  <button type="button" key={s} className="chip" onClick={() => addInterest(s)}>
                    + {humanize(s)}
                  </button>
                ))}
            </div>
          </div>

          <div className="field">
            <label htmlFor="prefs">Preferences (comma separated)</label>
            <input id="prefs" placeholder="e.g. walkable, budget hotels, outdoor" value={preferences} onChange={(e) => setPreferences(e.target.value)} />
          </div>
        </Card>

        <Card title="Run options">
          <div className="field">
            <label htmlFor="disruption">Simulate a disruption</label>
            <select id="disruption" value={disruption} onChange={(e) => setDisruption(e.target.value)}>
              {Object.entries(catalog?.disruptions ?? { none: "No disruption" }).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
            <p className="muted small">Fires mid-planning so you can watch the agent replan around it.</p>
          </div>
          <label className="check">
            <input type="checkbox" checked={judge} onChange={(e) => setJudge(e.target.checked)} />
            Score the result with the LLM judge (one extra API call)
          </label>
          <div className="field">
            <label htmlFor="title">Trip name (optional)</label>
            <input id="title" maxLength={120} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Weekend in Chicago" />
          </div>

          {error && <ErrorNote message={error.message} problems={error.problems} />}
          <CreditsWarning />
          <button type="submit" className="button-primary" disabled={submitting || !catalog || problems.length > 0 || stops.some((s) => !s.city)}>
            {submitting ? "Starting…" : "Plan this trip"}
          </button>
          <p className="muted small">Each run makes real Claude API calls. A typical trip costs $0.20–$1.50.</p>
        </Card>
      </form>
    </div>
  );
}
