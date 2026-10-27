export type Severity = "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL";
export type Level = "low" | "medium" | "high" | "critical";
export type IncidentStatus = "open" | "investigating" | "resolved";

export interface LogEntry {
  id: number;
  timestamp: string;
  severity: Severity;
  service: string;
  message: string;
  template: string;
  host: string | null;
  trace_id: string | null;
  metadata: Record<string, unknown>;
  source: string;
  ingested_at: string;
}

export interface LogPage {
  items: LogEntry[];
  total: number;
  limit: number;
  offset: number;
}

export interface Facets {
  services: { name: string; count: number }[];
  severities: { name: Severity; count: number }[];
  time_range: { start: string | null; end: string | null };
}

export interface AnalysisSummary {
  run_id: number;
  range_start: string | null;
  range_end: string | null;
  logs_analyzed: number;
  anomalies_detected: number;
  statistical_anomalies: number;
  signature_anomalies: number;
  incidents: number;
  duration_ms: number;
  params: Record<string, unknown>;
}

export interface IngestResult {
  received: number;
  accepted: number;
  duplicates: number;
  rejected: number;
  errors: { index: number; error: string; record: string | null }[];
  first_timestamp: string | null;
  last_timestamp: string | null;
  analysis: AnalysisSummary | null;
}

export interface Anomaly {
  id: number;
  kind: "statistical" | "signature";
  detector: string;
  metric: string;
  service: string;
  signature_key: string | null;
  category: string | null;
  window_start: string;
  window_end: string;
  observed_value: number;
  baseline_median: number | null;
  mad: number | null;
  robust_z: number | null;
  score: number;
  severity: Level;
  description: string;
  evidence_log_ids: number[];
  evidence_count: number;
  trace_ids: string[];
  incident_id: number | null;
}

export interface Hypothesis {
  rank: number;
  service: string;
  signature_key: string | null;
  category: string | null;
  title: string;
  explanation: string;
  score: number;
  confidence: number;
  confidence_label: "low" | "medium" | "high";
  factors: Record<string, number>;
  reasoning: string[];
  recommendations: string[];
  evidence: { anomaly_ids: number[]; log_ids: number[]; trace_ids: string[] };
}

export interface Fact {
  id: string;
  statement: string;
  evidence: { log_ids?: number[]; trace_ids?: string[]; anomaly_ids?: number[] };
}

export interface Signal {
  id: string;
  anomaly_id: number;
  service: string;
  metric: string;
  robust_z: number | null;
  observed: number;
  baseline_median: number | null;
  mad: number | null;
  window_start: string;
  window_end: string;
  statement: string;
  log_ids: number[];
}

export interface CausalLink {
  cause: string;
  effect: string;
  cause_name: string;
  effect_name: string;
  cause_services: string[];
  effect_services: string[];
  lag_seconds: number;
  trace_linked: boolean;
  shared_trace_ids: string[];
  explanation: string;
  evidence: { cause_log_ids: number[]; effect_log_ids: number[]; fact_ids: string[] };
}

export interface RootCause {
  primary: Hypothesis | null;
  hypotheses: Hypothesis[];
  facts: Fact[];
  signals: Signal[];
  causal_chain: CausalLink[];
  affected_services: {
    service: string;
    first_seen: string;
    first_error_log_id: number | null;
    first_error_message: string | null;
    error_logs: number;
    signatures: string[];
    anomaly_ids: number[];
  }[];
  propagation_traces: { trace_id: string; path: string[]; first_log_id: number; log_ids: number[] }[];
  signatures: { key: string; name: string; category: string; count: number; first_seen: string; last_seen: string; services: string[] }[];
  correlation_links: Record<string, number>;
  dependency_graph: { edges: { source: string; target: string; count: number }[] };
  method: { score_weights: Record<string, number>; description: string };
}

export interface TimelineEvent {
  timestamp: string;
  type: "fact" | "signal" | "inference";
  kind: string;
  service: string | null;
  title: string;
  detail: string;
  log_ids: number[];
  anomaly_id?: number;
  robust_z?: number;
  signature_key?: string;
}

export interface IncidentListItem {
  id: number;
  title: string;
  summary: string;
  status: IncidentStatus;
  severity: Level;
  confidence: number;
  started_at: string;
  ended_at: string;
  services: string[];
  categories: string[];
  anomaly_count: number;
  created_at: string;
  updated_at: string;
}

export interface IncidentDetail extends IncidentListItem {
  root_cause: RootCause;
  timeline: TimelineEvent[];
  narrative: string;
  narrative_source: string;
  notes: string;
  resolved_at: string | null;
  anomalies: Anomaly[];
  evidence_logs: LogEntry[];
}

export interface SimilarIncident {
  id: number;
  title: string;
  status: IncidentStatus;
  severity: Level;
  services: string[];
  started_at: string;
  similarity: number;
  root_cause_title: string | null;
  notes: string;
}

export interface Signature {
  id: number;
  key: string;
  name: string;
  category: string;
  pattern: string;
  severity: Level;
  min_count: number;
  description: string;
  enabled: boolean;
  builtin: boolean;
}

export interface Overview {
  total_logs: number;
  error_logs: number;
  error_rate: number;
  by_severity: { severity: Severity; count: number }[];
  by_service: { service: string; count: number; errors: number }[];
  incidents: { total: number; open: number; investigating: number; resolved: number; active_by_severity: Record<string, number> };
  anomalies: { total: number; by_service: { service: string; statistical: number; signature: number }[] };
  time_range: { start: string | null; end: string | null };
  last_run: (Record<string, unknown> & { run_id: number; incidents: number; anomalies_detected: number }) | null;
}

export interface TimeseriesPoint {
  t: string;
  DEBUG: number;
  INFO: number;
  WARNING: number;
  ERROR: number;
  CRITICAL: number;
  total: number;
}

export interface Timeseries {
  bucket_seconds: number;
  points: TimeseriesPoint[];
  anomalies: { id: number; t: string; end: string; service: string; severity: Level; kind: string; metric: string; incident_id: number | null }[];
}

export interface Health {
  status: string;
  database: { ok: boolean; dialect: string };
  cache: { backend: string; ok: boolean };
  vector_store: { backend: string; indexed_incidents: number };
  embeddings: { provider: string };
  llm: { provider: string; configured: boolean; model: string | null };
  live_subscribers: number;
  detection_defaults: { window_seconds: number; z_threshold: number; min_anomaly_count: number; correlation_gap_seconds: number };
}

export interface DependencyGraph {
  nodes: string[];
  edges: { source: string; target: string; count: number }[];
}
