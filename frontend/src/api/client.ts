import type {
  AnalysisSummary, Anomaly, DependencyGraph, Facets, Health, IncidentDetail, IncidentListItem, IngestResult, LogPage,
  Overview, Signature, SimilarIncident, Timeseries,
} from "./types";

const BASE = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "") ?? "";

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) {
    super(message);
  }
}

type Params = Record<string, string | number | boolean | string[] | undefined | null>;

function qs(params?: Params): string {
  if (!params) return "";
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => sp.append(k, x));
    else sp.append(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

function describe(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail.map((d) => (d?.loc ? `${d.loc.slice(1).join(".")}: ${d.msg}` : JSON.stringify(d))).join("; ");
  if (detail && typeof detail === "object" && "message" in detail) return String((detail as { message: unknown }).message);
  return JSON.stringify(detail);
}

async function request<T>(path: string, init?: RequestInit & { params?: Params }): Promise<T> {
  const res = await fetch(`${BASE}${path}${qs(init?.params)}`, init);
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, describe(detail), detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  health: () => request<Health>("/api/health"),
  overview: () => request<Overview>("/api/stats/overview"),
  timeseries: (params: Params) => request<Timeseries>("/api/stats/timeseries", { params }),
  dependencies: () => request<DependencyGraph>("/api/dependencies"),

  logs: (params: Params) => request<LogPage>("/api/logs", { params }),
  facets: () => request<Facets>("/api/logs/facets"),
  ingestOne: (log: Record<string, unknown>, analyze = false) =>
    request<IngestResult>("/api/logs", { ...json("POST", log), params: { analyze } }),
  ingestBatch: (logs: unknown[], analyze = false) =>
    request<IngestResult>("/api/logs/batch", { ...json("POST", { logs }), params: { analyze } }),
  upload: (file: File, format: string | null, analyze: boolean) => {
    const fd = new FormData();
    fd.append("file", file);
    if (format) fd.append("format", format);
    fd.append("analyze", String(analyze));
    return request<IngestResult>("/api/logs/upload", { method: "POST", body: fd });
  },
  generate: (body: Record<string, unknown>) => request<IngestResult>("/api/samples/generate", json("POST", body)),

  runAnalysis: (body: Record<string, unknown> = {}) => request<AnalysisSummary>("/api/analysis/run", json("POST", body)),
  runs: () => request<(Record<string, unknown> & { id: number })[]>("/api/analysis/runs"),

  incidents: (params?: Params) => request<IncidentListItem[]>("/api/incidents", { params }),
  incident: (id: number) => request<IncidentDetail>(`/api/incidents/${id}`),
  updateIncident: (id: number, body: { status?: string; notes?: string }) =>
    request<IncidentListItem>(`/api/incidents/${id}`, json("PATCH", body)),
  similar: (id: number) => request<SimilarIncident[]>(`/api/incidents/${id}/similar`),
  explain: (id: number, use_llm: boolean) =>
    request<{ narrative: string; narrative_source: string; warning: string | null }>(
      `/api/incidents/${id}/explain`, json("POST", { use_llm }),
    ),

  anomalies: (params?: Params) => request<{ items: Anomaly[]; total: number }>("/api/anomalies", { params }),

  signatures: () => request<Signature[]>("/api/signatures"),
  createSignature: (body: Partial<Signature>) => request<Signature>("/api/signatures", json("POST", body)),
  updateSignature: (key: string, body: Partial<Signature>) => request<Signature>(`/api/signatures/${key}`, json("PUT", body)),
  deleteSignature: (key: string) => request<void>(`/api/signatures/${key}`, { method: "DELETE" }),
  testSignature: (pattern: string, messages: string[]) =>
    request<{ results: { message: string; matched: boolean; match: string | null; span: number[] | null }[] }>(
      "/api/signatures/test", json("POST", { pattern, messages }),
    ),

  reset: () => request<{ deleted_logs: number }>("/api/system/reset", { method: "POST", params: { confirm: true } }),
  streamUrl: () => `${BASE}/api/logs/stream`,
};
