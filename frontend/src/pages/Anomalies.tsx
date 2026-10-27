import { Zap } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { api } from "../api/client";
import { Badge, Card, Empty, ErrorBox, PageHeader, ServiceTag, Spinner, Tabs } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { fmtNum, fmtTime } from "../lib/format";

type Kind = "all" | "statistical" | "signature";
const SEV_COLOR: Record<string, string> = { low: "#64748b", medium: "#fbbf24", high: "#fb923c", critical: "#f43f5e" };

export function Anomalies() {
  const [kind, setKind] = useState<Kind>("all");
  const [unassigned, setUnassigned] = useState(false);
  const list = useApi(() => api.anomalies({ kind: kind === "all" ? undefined : kind, unassigned, limit: 1000 }), [kind, unassigned]);
  const items = list.data?.items ?? [];
  const services = Array.from(new Set(items.map((a) => a.service))).sort();
  const points = items.map((a) => ({
    x: new Date(a.window_start).getTime(), y: services.indexOf(a.service), z: Math.max(40, a.score * 400),
    sev: a.severity, label: `${a.service} · ${a.signature_key ?? a.metric}`, desc: a.description,
  }));

  return (
    <div className="space-y-4">
      <PageHeader title="Anomalies" subtitle="Robust z-score (MAD) signals and signature bursts, before correlation."
        actions={<>
          <label className="flex items-center gap-2 text-sm text-slate-400">
            <input type="checkbox" checked={unassigned} onChange={(e) => setUnassigned(e.target.checked)} /> Uncorrelated only
          </label>
          <Tabs value={kind} onChange={setKind} tabs={[{ value: "all", label: "All" }, { value: "statistical", label: "Statistical" }, { value: "signature", label: "Signature" }]} />
        </>} />
      {list.error && <ErrorBox error={list.error} onRetry={list.reload} />}

      <Card title="Anomaly map" icon={<Zap className="h-4 w-4 text-amber-400" />}>
        {!items.length ? <Empty title="No anomalies" /> : (
          <div style={{ height: Math.max(180, services.length * 44 + 60) }}>
            <ResponsiveContainer>
              <ScatterChart margin={{ left: 40, right: 16, top: 10 }}>
                <CartesianGrid stroke="#1e293b" />
                <XAxis type="number" dataKey="x" domain={["dataMin", "dataMax"]} tickFormatter={(v) => fmtTime(new Date(v).toISOString())}
                  tick={{ fill: "#64748b", fontSize: 11 }} />
                <YAxis type="number" dataKey="y" ticks={services.map((_, i) => i)} tickFormatter={(i) => services[i] ?? ""}
                  domain={[-0.5, services.length - 0.5]} tick={{ fill: "#94a3b8", fontSize: 11 }} width={110} />
                <ZAxis dataKey="z" range={[40, 400]} />
                <Tooltip cursor={false} content={({ payload }) => {
                  const p = payload?.[0]?.payload;
                  return p ? <div className="max-w-xs rounded-lg border border-slate-700 bg-slate-900 p-2 text-xs"><b>{p.label}</b><div className="text-slate-400">{p.desc}</div></div> : null;
                }} />
                {Object.keys(SEV_COLOR).map((sev) => (
                  <Scatter key={sev} data={points.filter((p) => p.sev === sev)} fill={SEV_COLOR[sev]} fillOpacity={0.7} isAnimationActive={false} />
                ))}
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        )}
      </Card>

      <Card bodyClassName="p-0">
        {list.loading && !list.data ? <Spinner /> : !items.length ? <Empty title="No anomalies detected">Run analysis after ingesting logs.</Empty> : (
          <div className="scrollbar-thin overflow-x-auto">
            <table className="w-full min-w-[1000px]">
              <thead className="border-b border-slate-800"><tr>
                <th className="th">Window</th><th className="th">Service</th><th className="th">Type</th><th className="th">Detector</th>
                <th className="th">Description</th><th className="th">z</th><th className="th">Severity</th><th className="th">Incident</th>
              </tr></thead>
              <tbody className="divide-y divide-slate-800/70">
                {items.map((a) => (
                  <tr key={a.id} className="hover:bg-slate-800/40">
                    <td className="td whitespace-nowrap font-mono text-xs text-slate-400">{fmtTime(a.window_start)}–{fmtTime(a.window_end)}</td>
                    <td className="td"><ServiceTag name={a.service} /></td>
                    <td className="td"><Badge value={a.kind} /></td>
                    <td className="td font-mono text-xs">{a.signature_key ?? a.metric}</td>
                    <td className="td max-w-md text-xs text-slate-400">{a.description}</td>
                    <td className="td font-mono text-xs">{a.robust_z !== null ? fmtNum(a.robust_z, 1) : "—"}</td>
                    <td className="td"><Badge value={a.severity} /></td>
                    <td className="td text-xs">{a.incident_id ? <Link className="text-sky-400 hover:underline" to={`/incidents/${a.incident_id}`}>#{a.incident_id}</Link> : <span className="text-slate-600">—</span>}</td>
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
