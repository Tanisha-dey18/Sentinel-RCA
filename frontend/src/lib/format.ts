export const fmtTime = (iso: string | null | undefined, withDate = false): string => {
  if (!iso) return "—";
  const d = new Date(iso);
  return withDate
    ? d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
};

export const fmtDateTime = (iso: string | null | undefined) => fmtTime(iso, true);

export const fmtNum = (n: number | null | undefined, digits = 0): string =>
  n === null || n === undefined ? "—" : n.toLocaleString(undefined, { maximumFractionDigits: digits });

export const fmtPct = (n: number, digits = 0) => `${(n * 100).toFixed(digits)}%`;

export const fmtDuration = (startIso: string, endIso: string): string => {
  const s = Math.max(0, (new Date(endIso).getTime() - new Date(startIso).getTime()) / 1000);
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${(s / 3600).toFixed(1)}h`;
};

/** Convert an ISO string to a value for <input type="datetime-local"> in local time. */
export const toLocalInput = (iso: string | null | undefined): string => {
  if (!iso) return "";
  const d = new Date(iso);
  const off = d.getTimezoneOffset() * 60000;
  return new Date(d.getTime() - off).toISOString().slice(0, 16);
};

export const fromLocalInput = (v: string): string | undefined => (v ? new Date(v).toISOString() : undefined);

export const SERVICE_COLORS = ["#38bdf8", "#a78bfa", "#f472b6", "#34d399", "#fbbf24", "#fb923c", "#60a5fa", "#4ade80"];
export const serviceColor = (name: string, all: string[]) => SERVICE_COLORS[Math.max(0, all.indexOf(name)) % SERVICE_COLORS.length];

export const SEVERITY_COLORS: Record<string, string> = {
  DEBUG: "#64748b",
  INFO: "#38bdf8",
  WARNING: "#fbbf24",
  ERROR: "#f87171",
  CRITICAL: "#e11d48",
};
