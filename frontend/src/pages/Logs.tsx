import { ChevronLeft, ChevronRight, RefreshCw, Search, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Severity } from "../api/types";
import { LogTable } from "../components/LogTable";
import { Card, Empty, ErrorBox, PageHeader, Spinner } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { fmtNum, fromLocalInput, toLocalInput } from "../lib/format";

const SEVERITIES: Severity[] = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];
const PAGE = 100;

export function Logs() {
  const [sp, setSp] = useSearchParams();
  const services = sp.getAll("service");
  const severities = sp.getAll("severity");
  const q = sp.get("q") ?? "";
  const start = sp.get("start") ?? "";
  const end = sp.get("end") ?? "";
  const traceId = sp.get("trace_id") ?? "";
  const offset = Number(sp.get("offset") ?? 0);
  const [qInput, setQInput] = useState(q);
  useEffect(() => setQInput(q), [q]);

  const facets = useApi(() => api.facets(), []);
  const logs = useApi(
    () => api.logs({ service: services, severity: severities, q, start, end, trace_id: traceId, limit: PAGE, offset }),
    [sp.toString()],
  );

  const update = (mut: (p: URLSearchParams) => void) => {
    const n = new URLSearchParams(sp);
    mut(n);
    n.delete("offset");
    setSp(n);
  };
  const toggleMulti = (key: string, v: string) =>
    update((p) => {
      const cur = p.getAll(key);
      p.delete(key);
      (cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v]).forEach((x) => p.append(key, x));
    });
  const setOne = (key: string, v: string | undefined) => update((p) => (v ? p.set(key, v) : p.delete(key)));
  const total = logs.data?.total ?? 0;
  const hasFilters = services.length || severities.length || q || start || end || traceId;

  return (
    <div>
      <PageHeader title="Log explorer" subtitle={`${fmtNum(total)} matching logs`}
        actions={<button className="btn-secondary" onClick={logs.reload}><RefreshCw className="h-4 w-4" /> Refresh</button>} />

      <Card className="mb-4" bodyClassName="space-y-3">
        <div className="grid gap-3 md:grid-cols-[1fr_auto_auto]">
          <form onSubmit={(e) => { e.preventDefault(); setOne("q", qInput.trim() || undefined); }} className="relative">
            <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-500" />
            <input className="input pl-9" placeholder="Keyword in message, service, host or exact trace ID…" value={qInput}
              onChange={(e) => setQInput(e.target.value)} />
          </form>
          <input type="datetime-local" className="input" value={toLocalInput(start)} aria-label="Start time"
            onChange={(e) => setOne("start", fromLocalInput(e.target.value))} />
          <input type="datetime-local" className="input" value={toLocalInput(end)} aria-label="End time"
            onChange={(e) => setOne("end", fromLocalInput(e.target.value))} />
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="mr-1 text-xs text-slate-500">Severity</span>
          {SEVERITIES.map((s) => (
            <Chip key={s} active={severities.includes(s)} onClick={() => toggleMulti("severity", s)}
              label={`${s} ${facets.data ? `(${fmtNum(facets.data.severities.find((x) => x.name === s)?.count ?? 0)})` : ""}`} />
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="mr-1 text-xs text-slate-500">Service</span>
          {facets.data?.services.map((s) => (
            <Chip key={s.name} active={services.includes(s.name)} onClick={() => toggleMulti("service", s.name)} label={`${s.name} (${fmtNum(s.count)})`} />
          ))}
        </div>
        {hasFilters ? (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            {traceId && <span className="rounded bg-sky-500/10 px-2 py-1 font-mono text-sky-300">trace_id = {traceId}</span>}
            <button className="btn-ghost py-1 text-xs" onClick={() => setSp(new URLSearchParams())}><X className="h-3.5 w-3.5" /> Clear filters</button>
          </div>
        ) : null}
      </Card>

      {logs.error && <ErrorBox error={logs.error} onRetry={logs.reload} />}
      <Card bodyClassName="p-0">
        {logs.loading && !logs.data ? <Spinner /> : !logs.data?.items.length ? <Empty title="No logs match these filters" /> : (
          <>
            <LogTable logs={logs.data.items} onTrace={(t) => update((p) => { p.set("trace_id", t); })} />
            <div className="flex items-center justify-between border-t border-slate-800 px-4 py-2 text-xs text-slate-400">
              <span>{fmtNum(offset + 1)}–{fmtNum(Math.min(offset + PAGE, total))} of {fmtNum(total)}</span>
              <div className="flex gap-1">
                <button className="btn-ghost p-1" disabled={offset === 0} aria-label="Previous page"
                  onClick={() => { const n = new URLSearchParams(sp); n.set("offset", String(Math.max(0, offset - PAGE))); setSp(n); }}>
                  <ChevronLeft className="h-4 w-4" /></button>
                <button className="btn-ghost p-1" disabled={offset + PAGE >= total} aria-label="Next page"
                  onClick={() => { const n = new URLSearchParams(sp); n.set("offset", String(offset + PAGE)); setSp(n); }}>
                  <ChevronRight className="h-4 w-4" /></button>
              </div>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}

function Chip({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button onClick={onClick}
      className={`rounded-md px-2 py-1 font-mono text-[11px] ring-1 transition ${active ? "bg-sky-500/15 text-sky-200 ring-sky-500/50" : "text-slate-400 ring-slate-700 hover:text-slate-200"}`}>
      {label}
    </button>
  );
}
