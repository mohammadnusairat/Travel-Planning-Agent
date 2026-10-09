import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { RunStatus } from "../api";

const STATUS_LABEL: Record<RunStatus, string> = {
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  stopped: "Stopped",
  interrupted: "Interrupted",
};

export function StatusBadge({ status, errorKind }: { status: RunStatus; errorKind?: string | null }) {
  if (status === "failed" && errorKind === "credits_exhausted") return <span className="badge badge-credits">Out of credits</span>;
  if (status === "failed" && errorKind === "auth_error") return <span className="badge badge-credits">API key rejected</span>;
  return (
    <span className={`badge badge-${status}`}>
      {status === "running" && <span className="pulse" aria-hidden />}
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}

export function OutcomeBadge({ success, status }: { success: number | boolean | null; status: RunStatus }) {
  if (status !== "completed" && status !== "stopped") return null;
  return success ? <span className="badge badge-pass">Success</span> : <span className="badge badge-fail">Not met</span>;
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

export function Card({ title, actions, children, className = "" }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-header">
          {title && <h2>{title}</h2>}
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Markdown({ children }: { children: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children.replace(/<br>/g, " ")}</ReactMarkdown>
    </div>
  );
}

export function Meter({ value, max, danger }: { value: number; max: number; danger?: boolean }) {
  const ratio = max > 0 ? Math.min(value / max, 1) : 0;
  return (
    <div className="meter" role="meter" aria-valuenow={value} aria-valuemin={0} aria-valuemax={max}>
      <div className={`meter-fill ${danger ? "meter-danger" : ""}`} style={{ width: `${ratio * 100}%` }} />
    </div>
  );
}

export function ErrorNote({ message, problems = [] }: { message: string; problems?: string[] }) {
  return (
    <div className="note note-error" role="alert">
      <strong>{message}</strong>
      {problems.length > 0 && (
        <ul>
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
