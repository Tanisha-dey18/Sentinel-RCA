import { CheckCircle2, Database, History, Loader2, Server, Trash2, XCircle } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { Card, ErrorBox, PageHeader, Spinner } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { fmtDateTime } from "../lib/format";

export function SystemPage() {
  const health = useApi(() => api.health(), []);
  const runs = useApi(() => api.runs(), []);
  const reset = useAction(api.reset);
  const [confirming, setConfirming] = useState(false);
  const h = health.data;

  const rows: [string, string, boolean | null][] = h ? [
    ["Database", h.database.dialect, h.database.ok],
    ["Cache / correlation state", h.cache.backend === "redis" ? "Redis" : "in-process (set REDIS_URL for Redis)", h.cache.ok],
    ["Vector store", `${h.vector_store.backend} · ${h.vector_store.indexed_incidents} incidents indexed`, h.vector_store.indexed_incidents >= 0],
    ["Embeddings", h.embeddings.provider, true],
    ["LLM narratives", h.llm.configured ? `Groq · ${h.llm.model}` : "not configured — deterministic fallback in use", h.llm.configured ? true : null],
  ] : [];

  return (
    <div className="space-y-6">
      <PageHeader title="System" subtitle="Backend components, detection defaults and analysis history." />
      {health.error && <ErrorBox error={health.error} onRetry={health.reload} />}
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Card title="Components" icon={<Server className="h-4 w-4 text-sky-400" />}>
          {!h ? <Spinner /> : (
            <ul className="divide-y divide-slate-800">
              {rows.map(([k, v, ok]) => (
                <li key={k} className="flex items-center gap-3 py-2.5 text-sm">
                  {ok === null ? <span className="h-4 w-4 rounded-full border border-slate-600" /> : ok ? <CheckCircle2 className="h-4 w-4 text-emerald-400" /> : <XCircle className="h-4 w-4 text-rose-400" />}
                  <span className="w-32 shrink-0 text-slate-300 sm:w-48">{k}</span><span className="min-w-0 break-all font-mono text-xs text-slate-400">{v}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Detection defaults" icon={<Database className="h-4 w-4 text-violet-400" />}>
          {!h ? <Spinner /> : (
            <dl className="grid grid-cols-2 gap-3 text-sm">
              {Object.entries(h.detection_defaults).map(([k, v]) => (
                <div key={k} className="rounded-lg bg-slate-950/60 p-3"><dt className="text-xs text-slate-500">{k}</dt><dd className="font-mono text-slate-100">{String(v)}</dd></div>
              ))}
            </dl>
          )}
          <p className="mt-3 text-xs text-slate-500">Configure via environment variables (see <code>.env.example</code>) or per run on the Ingest page.</p>
        </Card>
      </div>

      <Card title="Analysis runs" icon={<History className="h-4 w-4 text-amber-400" />} bodyClassName="p-0">
        {!runs.data ? <Spinner /> : (
          <div className="scrollbar-thin overflow-x-auto">
            <table className="w-full min-w-[800px]">
              <thead className="border-b border-slate-800"><tr><th className="th">Run</th><th className="th">Status</th><th className="th">Started</th><th className="th">Range</th><th className="th">Result</th></tr></thead>
              <tbody className="divide-y divide-slate-800/70">
                {runs.data.map((r) => {
                  const s = r.stats as Record<string, number>;
                  return (
                    <tr key={r.id}>
                      <td className="td font-mono text-xs">#{r.id}</td>
                      <td className="td text-xs">{String(r.status)}{r.error ? `: ${String(r.error)}` : ""}</td>
                      <td className="td text-xs text-slate-400">{fmtDateTime(r.started_at as string)}</td>
                      <td className="td text-xs text-slate-400">{fmtDateTime(r.range_start as string)} → {fmtDateTime(r.range_end as string)}</td>
                      <td className="td text-xs text-slate-300">{s?.logs_analyzed ?? 0} logs · {s?.anomalies_detected ?? 0} anomalies · {s?.incidents ?? 0} incidents</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Danger zone" icon={<Trash2 className="h-4 w-4 text-rose-400" />} className="border-rose-500/30">
        <div className="flex flex-wrap items-center gap-3">
          <p className="flex-1 text-sm text-slate-400">Delete all logs, anomalies, incidents and analysis runs. Signatures are kept.</p>
          {!confirming ? <button className="btn-secondary" onClick={() => setConfirming(true)}>Reset data…</button> : (
            <>
              <button className="btn-ghost" onClick={() => setConfirming(false)}>Cancel</button>
              <button className="btn-danger" disabled={reset.pending} onClick={async () => {
                if (await reset.run()) { setConfirming(false); health.reload(); runs.reload(); }
              }}>{reset.pending && <Loader2 className="h-4 w-4 animate-spin" />} Delete everything</button>
            </>
          )}
        </div>
        {reset.error && <div className="mt-3"><ErrorBox error={reset.error} /></div>}
      </Card>
    </div>
  );
}
