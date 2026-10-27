import clsx from "clsx";
import {
  ArrowLeft, ArrowRight, Bot, Brain, CheckCircle2, Clock, Eye, FileSearch, GitBranch, History, Lightbulb, Loader2,
  Network, RefreshCw, Save, Sparkles, Activity,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api/client";
import type { Hypothesis, IncidentDetail, IncidentStatus } from "../api/types";
import { LogTable } from "../components/LogTable";
import { Markdown } from "../components/Markdown";
import { ServiceMap } from "../components/ServiceMap";
import { Timeline } from "../components/Timeline";
import { Badge, Card, ConfidenceBar, Empty, ErrorBox, ServiceTag, Spinner, Tabs } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { fmtDateTime, fmtDuration, fmtNum, fmtTime } from "../lib/format";

type Tab = "analysis" | "timeline" | "evidence" | "anomalies";

const FACTOR_LABELS: Record<string, string> = {
  precedence: "Temporal precedence",
  propagation: "Trace propagation origin",
  dependency_depth: "Dependency depth",
  cause_category: "Cause-category prior",
  signal_strength: "Signal strength",
};

export function IncidentDetailPage() {
  const id = Number(useParams().id);
  const inc = useApi(() => api.incident(id), [id]);
  const similar = useApi(() => api.similar(id), [id]);
  const [tab, setTab] = useState<Tab>("analysis");
  const [logFilter, setLogFilter] = useState<number[] | null>(null);

  const showLogs = (ids: number[]) => {
    setLogFilter(ids);
    setTab("evidence");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  if (inc.loading && !inc.data) return <Spinner label="Loading incident…" />;
  if (inc.error) return <ErrorBox error={inc.error} onRetry={inc.reload} />;
  if (!inc.data) return null;
  const d = inc.data;
  const rc = d.root_cause;

  return (
    <div className="space-y-6">
      <div>
        <Link to="/incidents" className="mb-3 inline-flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200">
          <ArrowLeft className="h-3.5 w-3.5" /> Incidents
        </Link>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <Badge value={d.severity} /><Badge value={d.status} />
              <span className="text-xs text-slate-500">Incident #{d.id}</span>
            </div>
            <h1 className="mt-2 text-xl font-semibold text-white">{d.title}</h1>
            <p className="mt-1 max-w-4xl text-sm text-slate-400">{d.summary}</p>
            <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-slate-400">
              <span className="inline-flex items-center gap-1"><Clock className="h-3.5 w-3.5" />{fmtDateTime(d.started_at)} → {fmtTime(d.ended_at)} ({fmtDuration(d.started_at, d.ended_at)})</span>
              <span className="inline-flex flex-wrap items-center gap-1">{d.services.map((s) => <ServiceTag key={s} name={s} />)}</span>
              <span>{d.anomaly_count} anomalies</span>
            </div>
          </div>
          <StatusControl incident={d} onSaved={inc.reload} />
        </div>
      </div>

      <EvidenceLegend />

      <Tabs value={tab} onChange={(t) => { setTab(t); if (t !== "evidence") setLogFilter(null); }} tabs={[
        { value: "analysis", label: "Root cause analysis" },
        { value: "timeline", label: `Timeline (${d.timeline.length})` },
        { value: "evidence", label: `Evidence logs (${d.evidence_logs.length})` },
        { value: "anomalies", label: `Anomalies (${d.anomalies.length})` },
      ]} />

      {tab === "analysis" && (
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
          <div className="space-y-6 xl:col-span-2">
            {rc.primary ? <PrimaryHypothesis h={rc.primary} onLogs={showLogs} /> : <Empty title="No root-cause hypothesis" />}
            <CausalChain incident={d} onLogs={showLogs} />
            <FactsAndSignals incident={d} onLogs={showLogs} />
            <Narrative incident={d} onSaved={inc.reload} onLog={(id) => showLogs([id])} />
          </div>
          <div className="space-y-6">
            <AlternativeHypotheses hs={rc.hypotheses} />
            <Card title="Impact map" icon={<Network className="h-4 w-4 text-violet-400" />}>
              <ServiceMap graph={{ nodes: [], edges: rc.dependency_graph.edges }} highlight={d.services} origin={rc.primary?.service} />
              <p className="mt-2 text-xs text-slate-500"><span className="text-rose-400">Red</span>: inferred origin · <span className="text-amber-400">amber</span>: affected.</p>
            </Card>
            <ServiceImpact incident={d} />
            <Card title="Similar past incidents" icon={<History className="h-4 w-4 text-sky-400" />} bodyClassName="p-0">
              {similar.loading ? <Spinner /> : !similar.data?.length ? <Empty title="No similar incidents yet" /> : (
                <ul className="divide-y divide-slate-800">
                  {similar.data.map((s) => (
                    <li key={s.id} className="px-4 py-3">
                      <Link to={`/incidents/${s.id}`} className="text-sm font-medium text-slate-100 hover:text-sky-300">#{s.id} {s.title}</Link>
                      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                        <Badge value={s.status} /> {fmtDateTime(s.started_at)} · similarity {(s.similarity * 100).toFixed(0)}%
                      </div>
                      {s.notes && <div className="mt-1 rounded bg-emerald-500/5 p-2 text-xs text-emerald-200/90">Resolution notes: {s.notes}</div>}
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        </div>
      )}

      {tab === "timeline" && (
        <Card title="Causal timeline" icon={<Clock className="h-4 w-4 text-sky-400" />}>
          <Timeline events={d.timeline} onLogs={showLogs} />
        </Card>
      )}

      {tab === "evidence" && (
        <Card title={logFilter ? `Selected evidence (${logFilter.length})` : "All evidence logs"} icon={<FileSearch className="h-4 w-4 text-sky-400" />}
          actions={logFilter && <button className="btn-ghost py-1 text-xs" onClick={() => setLogFilter(null)}>Show all</button>}
          bodyClassName="p-0">
          <EvidenceLogs incident={d} ids={logFilter} />
        </Card>
      )}

      {tab === "anomalies" && (
        <Card bodyClassName="p-0">
          <div className="scrollbar-thin overflow-x-auto">
            <table className="w-full min-w-[900px]">
              <thead className="border-b border-slate-800"><tr>
                <th className="th">Window</th><th className="th">Service</th><th className="th">Type</th><th className="th">Detector</th>
                <th className="th">Observed / baseline</th><th className="th">Robust z</th><th className="th">Severity</th><th className="th">Evidence</th>
              </tr></thead>
              <tbody className="divide-y divide-slate-800/70">
                {d.anomalies.map((a) => (
                  <tr key={a.id} className="hover:bg-slate-800/40" title={a.description}>
                    <td className="td whitespace-nowrap font-mono text-xs text-slate-400">{fmtTime(a.window_start)}–{fmtTime(a.window_end)}</td>
                    <td className="td"><ServiceTag name={a.service} /></td>
                    <td className="td"><Badge value={a.kind} /></td>
                    <td className="td font-mono text-xs">{a.signature_key ?? a.metric}</td>
                    <td className="td font-mono text-xs">{fmtNum(a.observed_value, 1)} / {fmtNum(a.baseline_median, 1)}</td>
                    <td className="td font-mono text-xs">{a.robust_z?.toFixed(1) ?? "—"}</td>
                    <td className="td"><Badge value={a.severity} /></td>
                    <td className="td"><button className="text-xs text-sky-400 hover:underline" onClick={() => showLogs(a.evidence_log_ids)}>{a.evidence_count} logs</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

function EvidenceLegend() {
  const items = [
    { icon: Eye, cls: "text-sky-400", label: "Fact", text: "directly observed in logs" },
    { icon: Activity, cls: "text-violet-400", label: "Signal", text: "statistical deviation from baseline (robust z / MAD)" },
    { icon: Lightbulb, cls: "text-amber-400", label: "Inference", text: "probable cause derived from evidence + rules" },
  ];
  return (
    <div className="flex flex-wrap gap-x-6 gap-y-2 rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-2 text-xs text-slate-400">
      {items.map(({ icon: I, cls, label, text }) => (
        <span key={label} className="inline-flex items-center gap-1.5"><I className={clsx("h-3.5 w-3.5", cls)} /><b className="text-slate-200">{label}</b> {text}</span>
      ))}
    </div>
  );
}

function PrimaryHypothesis({ h, onLogs }: { h: Hypothesis; onLogs: (ids: number[]) => void }) {
  return (
    <Card title="Probable root cause" icon={<Lightbulb className="h-4 w-4 text-amber-400" />}
      actions={<Badge value="inference" />} className="border-amber-500/30">
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <div className="text-lg font-semibold text-white">{h.title}</div>
          <p className="mt-1 text-sm text-slate-300">{h.explanation}</p>
        </div>
        <div className="w-48 shrink-0">
          <div className="mb-1 text-xs text-slate-400">Confidence: <b className="text-slate-200">{h.confidence_label}</b></div>
          <ConfidenceBar value={h.confidence} />
          <div className="mt-1 text-[11px] text-slate-500">score {h.score.toFixed(3)}</div>
        </div>
      </div>

      <div className="mt-5 grid gap-6 lg:grid-cols-2">
        <div>
          <h3 className="label">Why this ranks first</h3>
          <ul className="space-y-1.5 text-sm text-slate-300">
            {h.reasoning.map((r, i) => <li key={i} className="flex gap-2"><ArrowRight className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-400" />{r}</li>)}
          </ul>
        </div>
        <div>
          <h3 className="label">Score factors</h3>
          <div className="space-y-2">
            {Object.entries(h.factors).map(([k, v]) => (
              <div key={k}>
                <div className="flex justify-between text-xs text-slate-400"><span>{FACTOR_LABELS[k] ?? k}</span></div>
                <ConfidenceBar value={v} label={v.toFixed(2)} />
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="mt-5">
        <h3 className="label">Recommended next steps</h3>
        <ol className="ml-5 list-decimal space-y-1 text-sm text-slate-300">{h.recommendations.map((r, i) => <li key={i}>{r}</li>)}</ol>
      </div>

      <div className="mt-4 flex flex-wrap gap-2 border-t border-slate-800 pt-3 text-xs">
        <button className="btn-secondary py-1 text-xs" onClick={() => onLogs(h.evidence.log_ids)} disabled={!h.evidence.log_ids.length}>
          <FileSearch className="h-3.5 w-3.5" /> {h.evidence.log_ids.length} supporting logs
        </button>
        <span className="self-center text-slate-500">anomalies: {h.evidence.anomaly_ids.map((a) => `#${a}`).join(", ") || "—"}</span>
      </div>
    </Card>
  );
}

function CausalChain({ incident, onLogs }: { incident: IncidentDetail; onLogs: (ids: number[]) => void }) {
  const chain = incident.root_cause.causal_chain;
  if (!chain.length) return null;
  return (
    <Card title="Inferred causal chain" icon={<GitBranch className="h-4 w-4 text-amber-400" />} actions={<Badge value="inference" />}>
      <ul className="space-y-3">
        {chain.map((c, i) => (
          <li key={i} className="rounded-lg border border-slate-800 bg-slate-950/50 p-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="rounded bg-rose-500/10 px-2 py-0.5 font-medium text-rose-200">{c.cause_name}</span>
              <span className="text-xs text-slate-500">{c.cause_services.join(", ")}</span>
              <ArrowRight className="h-4 w-4 text-slate-500" />
              <span className="rounded bg-amber-500/10 px-2 py-0.5 font-medium text-amber-200">{c.effect_name}</span>
              <span className="text-xs text-slate-500">{c.effect_services.join(", ")}</span>
              <span className="ml-auto font-mono text-xs text-slate-400">+{c.lag_seconds.toFixed(0)}s</span>
              {c.trace_linked && <Badge value="fact" label="trace-linked" />}
            </div>
            <p className="mt-1 text-xs text-slate-400">{c.explanation}</p>
            <div className="mt-1 flex gap-3 text-xs">
              <button className="text-sky-400 hover:underline" onClick={() => onLogs([...c.evidence.cause_log_ids, ...c.evidence.effect_log_ids])}>evidence logs</button>
              <span className="text-slate-500">based on {c.evidence.fact_ids.join(", ")}</span>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function FactsAndSignals({ incident, onLogs }: { incident: IncidentDetail; onLogs: (ids: number[]) => void }) {
  const { facts, signals } = incident.root_cause;
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <Card title="Detected facts" icon={<Eye className="h-4 w-4 text-sky-400" />} actions={<Badge value="fact" />}>
        {!facts.length ? <Empty title="No signature facts" /> : (
          <ul className="space-y-3 text-sm">
            {facts.map((f) => (
              <li key={f.id} className="flex gap-2">
                <span className="mt-0.5 shrink-0 rounded bg-sky-500/10 px-1 font-mono text-[11px] text-sky-300">{f.id}</span>
                <div className="min-w-0 text-slate-300">
                  <span className="break-words">{f.statement}</span>
                  {!!f.evidence.log_ids?.length && (
                    <button className="ml-1 text-xs text-sky-400 hover:underline" onClick={() => onLogs(f.evidence.log_ids!)}>[{f.evidence.log_ids.length} logs]</button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card title="Statistical signals" icon={<Activity className="h-4 w-4 text-violet-400" />} actions={<Badge value="signal" />}>
        {!signals.length ? <Empty title="No statistical signals">Detection is based on signatures only.</Empty> : (
          <ul className="space-y-3 text-sm">
            {signals.map((s) => (
              <li key={s.id} className="flex gap-2">
                <span className="mt-0.5 shrink-0 rounded bg-violet-500/10 px-1 font-mono text-[11px] text-violet-300">{s.id}</span>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2"><ServiceTag name={s.service} />
                    <span className="font-mono text-xs text-slate-400">{s.metric}</span>
                    <span className="font-mono text-xs text-violet-300">z={s.robust_z?.toFixed(1)}</span></div>
                  <div className="mt-0.5 text-xs text-slate-400">
                    observed <b className="text-slate-200">{fmtNum(s.observed, 1)}</b> vs median {fmtNum(s.baseline_median, 1)} (MAD {fmtNum(s.mad, 2)}) · {fmtTime(s.window_start)}–{fmtTime(s.window_end)}
                  </div>
                  {s.log_ids.length > 0 && <button className="text-xs text-sky-400 hover:underline" onClick={() => onLogs(s.log_ids)}>evidence</button>}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

function AlternativeHypotheses({ hs }: { hs: Hypothesis[] }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <Card title="Ranked hypotheses" icon={<Brain className="h-4 w-4 text-amber-400" />} bodyClassName="p-0">
      <ul className="divide-y divide-slate-800">
        {hs.map((h) => (
          <li key={h.rank} className="px-4 py-3">
            <button className="w-full text-left" onClick={() => setOpen(open === h.rank ? null : h.rank)}>
              <div className="flex items-center gap-2">
                <span className={clsx("flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold",
                  h.rank === 1 ? "bg-amber-500 text-slate-950" : "bg-slate-800 text-slate-300")}>{h.rank}</span>
                <span className="truncate text-sm text-slate-100">{h.title}</span>
              </div>
              <div className="mt-2 pl-7"><ConfidenceBar value={h.score} label={h.score.toFixed(2)} /></div>
            </button>
            {open === h.rank && (
              <ul className="mt-2 space-y-1 pl-7 text-xs text-slate-400">{h.reasoning.map((r, i) => <li key={i}>• {r}</li>)}</ul>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function ServiceImpact({ incident }: { incident: IncidentDetail }) {
  const data = incident.root_cause.affected_services.map((s) => ({ service: s.service, errors: s.error_logs, first: fmtTime(s.first_seen) }));
  return (
    <Card title="Service impact" icon={<Activity className="h-4 w-4 text-rose-400" />}>
      <div className="h-48">
        <ResponsiveContainer>
          <BarChart data={data} layout="vertical" margin={{ left: 20, right: 10 }}>
            <CartesianGrid stroke="#1e293b" horizontal={false} />
            <XAxis type="number" tick={{ fill: "#64748b", fontSize: 11 }} />
            <YAxis type="category" dataKey="service" tick={{ fill: "#94a3b8", fontSize: 11 }} width={110} />
            <Tooltip contentStyle={{ backgroundColor: "#0f172a", border: "1px solid #334155", borderRadius: 8, fontSize: 12 }} />
            <Bar dataKey="errors" name="error logs in anomaly windows" fill="#f87171" radius={[0, 4, 4, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <ul className="mt-3 space-y-1 text-xs text-slate-400">
        {incident.root_cause.affected_services.map((s) => (
          <li key={s.service} className="flex justify-between gap-2"><span className="font-mono">{s.service}</span><span>onset {fmtTime(s.first_seen)}</span></li>
        ))}
      </ul>
    </Card>
  );
}

function Narrative({ incident, onSaved, onLog }: { incident: IncidentDetail; onSaved: () => void; onLog: (id: number) => void }) {
  const explain = useAction(api.explain);
  const [warning, setWarning] = useState<string | null>(null);
  const isLlm = incident.narrative_source.startsWith("llm");
  const regenerate = async (useLlm: boolean) => {
    const r = await explain.run(incident.id, useLlm);
    if (r) { setWarning(r.warning); onSaved(); }
  };
  return (
    <Card title="Root-cause narrative" icon={isLlm ? <Sparkles className="h-4 w-4 text-fuchsia-400" /> : <Bot className="h-4 w-4 text-slate-400" />}
      actions={<>
        <span className="hidden text-xs text-slate-500 sm:inline">{isLlm ? incident.narrative_source.replace("llm:", "LLM · ") : "deterministic"}</span>
        <button className="btn-secondary py-1 text-xs" disabled={explain.pending} onClick={() => regenerate(true)}>
          {explain.pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />} Explain with LLM
        </button>
        <button className="btn-ghost py-1 text-xs" disabled={explain.pending} onClick={() => regenerate(false)} title="Regenerate deterministic narrative">
          <RefreshCw className="h-3.5 w-3.5" />
        </button>
      </>}>
      {warning && <div className="mb-3 rounded-md bg-amber-500/10 px-3 py-2 text-xs text-amber-200">{warning} — showing the deterministic, evidence-only narrative.</div>}
      {explain.error && <div className="mb-3"><ErrorBox error={explain.error} /></div>}
      <Markdown text={incident.narrative} onLogRef={onLog} />
    </Card>
  );
}

function StatusControl({ incident, onSaved }: { incident: IncidentDetail; onSaved: () => void }) {
  const [status, setStatus] = useState<IncidentStatus>(incident.status);
  const [notes, setNotes] = useState(incident.notes);
  const save = useAction(api.updateIncident);
  useEffect(() => { setStatus(incident.status); setNotes(incident.notes); }, [incident.status, incident.notes]);
  const dirty = status !== incident.status || notes !== incident.notes;
  return (
    <div className="card w-full shrink-0 space-y-2 p-3 lg:w-80">
      <label className="label">Status</label>
      <div className="flex gap-1">
        {(["open", "investigating", "resolved"] as IncidentStatus[]).map((s) => (
          <button key={s} onClick={() => setStatus(s)}
            className={clsx("flex-1 rounded-md px-2 py-1 text-xs capitalize ring-1",
              status === s ? "bg-sky-500/15 text-sky-200 ring-sky-500/50" : "text-slate-400 ring-slate-700 hover:text-slate-200")}>{s}</button>
        ))}
      </div>
      <label className="label pt-1">Resolution notes</label>
      <textarea className="input h-20 resize-y" value={notes} placeholder="What fixed it? Shown on similar future incidents."
        onChange={(e) => setNotes(e.target.value)} />
      {save.error && <ErrorBox error={save.error} />}
      <button className="btn-primary w-full py-1.5" disabled={!dirty || save.pending}
        onClick={async () => { if (await save.run(incident.id, { status, notes })) onSaved(); }}>
        {save.pending ? <Loader2 className="h-4 w-4 animate-spin" /> : status === "resolved" ? <CheckCircle2 className="h-4 w-4" /> : <Save className="h-4 w-4" />} Save
      </button>
    </div>
  );
}

function EvidenceLogs({ incident, ids }: { incident: IncidentDetail; ids: number[] | null }) {
  const known = useMemo(() => new Set(incident.evidence_logs.map((l) => l.id)), [incident.evidence_logs]);
  const missing = useMemo(() => (ids ?? []).filter((i) => !known.has(i)), [ids, known]);
  const extra = useApi(() => (missing.length ? api.logs({ ids: missing.join(","), limit: 500, order: "asc" }) : Promise.resolve(null)),
    [missing.join(",")]);
  const all = [...incident.evidence_logs, ...(extra.data?.items ?? [])];
  const shown = ids ? all.filter((l) => ids.includes(l.id)) : all;
  shown.sort((a, b) => a.timestamp.localeCompare(b.timestamp));
  if (extra.loading && missing.length) return <Spinner />;
  if (!shown.length) return <Empty title="No evidence logs" />;
  return <LogTable logs={shown} onTrace={(t) => window.open(`/logs?trace_id=${encodeURIComponent(t)}`, "_self")} />;
}
