import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { CreditsBanner } from "./components/credits";
import { useLlmStatus } from "./llmStatus";
import Benchmarks from "./pages/Benchmarks";
import History from "./pages/History";
import PlanTrip from "./pages/PlanTrip";
import RunView from "./pages/RunView";

export default function App() {
  const { health, offline } = useLlmStatus();

  return (
    <div className="shell">
      <header className="topbar">
        <NavLink to="/" className="brand">
          <span aria-hidden>✈</span> TravelBench Planner
        </NavLink>
        <nav>
          <NavLink to="/plan">Plan a trip</NavLink>
          <NavLink to="/benchmarks">Benchmarks</NavLink>
          <NavLink to="/history">History</NavLink>
        </nav>
        {health && (
          <div className="topbar-meta" title={`MCP transport: ${health.mcp_transport}`}>
            {health.model} · {health.mcp_tools} MCP tools
          </div>
        )}
      </header>

      {offline && <div className="banner banner-error">Can't reach the planning server right now. Please try again in a moment.</div>}
      {health && !health.api_key_configured && (
        <div className="banner banner-warn">The server has no Anthropic API key configured, so trips can't be planned.</div>
      )}
      <CreditsBanner />

      <main className="page">
        <Routes>
          <Route path="/" element={<Navigate to="/plan" replace />} />
          <Route path="/plan" element={<PlanTrip />} />
          <Route path="/benchmarks" element={<Benchmarks />} />
          <Route path="/history" element={<History />} />
          <Route path="/runs/:runId" element={<RunView />} />
          <Route path="*" element={<p>Page not found.</p>} />
        </Routes>
      </main>
    </div>
  );
}
