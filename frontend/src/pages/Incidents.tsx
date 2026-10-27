import { AlertOctagon, Search } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { IncidentStatus, Level } from "../api/types";
import { Badge, Card, ConfidenceBar, Empty, ErrorBox, PageHeader, ServiceTag, Spinner } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { fmtDateTime, fmtDuration } from "../lib/format";

const STATUSES: IncidentStatus[] = ["open", "investigating", "resolved"];
const LEVELS: Level[] = ["critical", "high", "medium", "low"];

export function Incidents() {
  const [status, setStatus] = useState<IncidentStatus[]>([]);
  const [severity, setSeverity] = useState<Level[]>([]);
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const list = useApi(() => api.incidents({ status, severity, q: query, limit: 200 }), [status.join(), severity.join(), query]);

  const toggle = <T,>(arr: T[], v: T, set: (x: T[]) => void) => set(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);

  return (
    <div>
      <PageHeader title="Incidents" subtitle="Anomalies correlated by time, service, shared traces and dependencies." />
      <Card className="mb-4" bodyClassName="flex flex-col gap-3 p-3 lg:flex-row lg:items-center">
        <form className="relative flex-1" onSubmit={(e) => { e.preventDefault(); setQuery(q); }}>
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-500" />
          <input className="input pl-9" placeholder="Search incident titles…" value={q} onChange={(e) => setQ(e.target.value)} />
        </form>
        <div className="flex flex-wrap gap-1.5">
          {STATUSES.map((s) => (
            <button key={s} onClick={() => toggle(status, s, setStatus)}
              className={`rounded-md px-2 py-1 text-xs ring-1 ${status.includes(s) ? "bg-sky-500/15 text-sky-300 ring-sky-500/50" : "text-slate-400 ring-slate-700 hover:text-slate-200"}`}>
              {s}
            </button>
          ))}
          <span className="mx-1 w-px bg-slate-800" />
          {LEVELS.map((s) => (
            <button key={s} onClick={() => toggle(severity, s, setSeverity)}
              className={`rounded-md px-2 py-1 text-xs ring-1 ${severity.includes(s) ? "bg-sky-500/15 text-sky-300 ring-sky-500/50" : "text-slate-400 ring-slate-700 hover:text-slate-200"}`}>
              {s}
            </button>
          ))}
        </div>
      </Card>

      {list.error && <ErrorBox error={list.error} onRetry={list.reload} />}
      <Card bodyClassName="p-0">
        {list.loading && !list.data ? <Spinner /> : !list.data?.length ? (
          <Empty title="No incidents match" icon={<AlertOctagon className="h-8 w-8" />}>
            Try clearing filters, or ingest logs and run analysis from the Ingest page.
          </Empty>
        ) : (
          <div className="scrollbar-thin overflow-x-auto">
            <table className="w-full min-w-[900px]">
              <thead className="border-b border-slate-800">
                <tr>
                  <th className="th">Incident</th>
                  <th className="th w-28">Severity</th>
                  <th className="th w-32">Status</th>
                  <th className="th w-44">Started</th>
                  <th className="th w-24">Duration</th>
                  <th className="th w-20">Signals</th>
                  <th className="th w-40">RCA confidence</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {list.data.map((i) => (
                  <tr key={i.id} className="hover:bg-slate-800/40">
                    <td className="td">
                      <Link to={`/incidents/${i.id}`} className="font-medium text-slate-100 hover:text-sky-300">
                        <span className="mr-2 text-slate-500">#{i.id}</span>{i.title}
                      </Link>
                      <div className="mt-1 flex flex-wrap gap-1">{i.services.map((s) => <ServiceTag key={s} name={s} />)}</div>
                    </td>
                    <td className="td"><Badge value={i.severity} /></td>
                    <td className="td"><Badge value={i.status} /></td>
                    <td className="td whitespace-nowrap text-xs text-slate-400">{fmtDateTime(i.started_at)}</td>
                    <td className="td text-xs text-slate-400">{fmtDuration(i.started_at, i.ended_at)}</td>
                    <td className="td text-xs text-slate-400">{i.anomaly_count}</td>
                    <td className="td"><ConfidenceBar value={i.confidence} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
