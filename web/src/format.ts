export const money = (value: number | null | undefined, digits = 2) =>
  value == null ? "—" : `$${value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;

export const tokens = (value: number | null | undefined) => (value == null ? "—" : value.toLocaleString());

export const pct = (value: number | null | undefined) => (value == null ? "—" : `${Math.round(value * 100)}%`);

export const humanize = (value: string) =>
  value.replace(/_/g, " ").replace(/\b(min|max)\b/g, "$1.").replace(/^\w/, (c) => c.toUpperCase());

export function relativeTime(epochSeconds: number | null | undefined): string {
  if (!epochSeconds) return "—";
  const seconds = Math.round(Date.now() / 1000 - epochSeconds);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return new Date(epochSeconds * 1000).toLocaleDateString();
}

export function duration(start: number | null, end: number | null): string {
  if (!start) return "—";
  const seconds = Math.max(0, Math.round((end ?? Date.now() / 1000) - start));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

/** Split a ReAct response into its THOUGHT and ACTION parts. */
export function parseStep(text: string): { thought: string; action: string | null } {
  const actionMatch = text.match(/ACTION:\s*(.+)/);
  const thought = text
    .replace(/ACTION:[\s\S]*$/, "")
    .replace(/^\s*THOUGHT:\s*/i, "")
    .trim();
  return { thought, action: actionMatch ? actionMatch[1].trim() : null };
}
