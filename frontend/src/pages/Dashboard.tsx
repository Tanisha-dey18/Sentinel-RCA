import { AlertOctagon, Database, Gauge, Loader2, Network, Play, ScrollText, Zap } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api } from "../api/client";
import { ServiceMap } from "../components/ServiceMap";
import { Badge, Card, ConfidenceBar, Empty, ErrorBox, PageHeader, ServiceTag, Spinner, Stat, Tabs } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { fmtDateTime, fmtNum, fmtPct, fmtTime, SEVERITY_COLORS } from "../lib/format";

const tooltipStyle = { backgroundColor: "#0f172a", border: "1px solid #334155", borderRadius: 8, fontSize: 12 };

export function Dashboard() {
  const [bucket, setBucket] = useState<"60" | "300" | "900">("60");
  const overview = useApi(() => api.overview(), []);
  const series = useApi(() => api.timeseries({ bucket_seconds: Number(bucket), hours: 24 }), [bucket]);
  const incidents = useApi(() => api.incidents({ status: ["open", "investigating"], limit: 8 }), []);
  const deps = useApi(() => api.dependencies(), []);
  const analysis = useAction(api.runAnalysis);

  const reloadAll = () => { overview.reload(); series.reload(); incidents.reload(); deps.reload(); };
  const { reload: reloadOverview } = overview;
  const { reload: reloadSeries } = series;
  useEffect(() => {
    const h = () => { reloadOverview(); reloadSeries(); };
    window.addEventListener("sentinel:ingest", h);
    return () => window.removeEventListener("sentinel:ingest", h);
  }, [reloadOverview, reloadSeries]);

  const chart = useMemo(() => (series.data?.points ?? []).map((p) => ({ ...p, label: fmtTime(p.t) })), [series.data]);
  const anomalyBands = useMemo(() => {
    // Merge anomaly windows into bands that line up with chart buckets.
    if (!series.data || !chart.length) return [];
    const toLabel = (iso: string) => {
      const t = new Date(iso).getTime();
      let best = chart[0];
      for (const p of chart) if (new Date(p.t).getTime() <= t) best = p;
      return best.label;
    };
    const seen = new Set<string>();
    return series.data.anomalies
      .filter((a) => a.severity === "high" || a.severity === "critical")
      .map((a) => ({ x1: toLabel(a.t), x2: toLabel(a.end), sev: a.severity }))
      .filter((b) => { const k = `${b.x1}-${b.x2}`; if (seen.has(k)) return false; seen.add(k); return true; });
  }, [series.data, chart]);

  const ov = overview.data;
  const serviceChart = useMemo(() => {
    if (!ov) return [];
    const anomalies = Object.fromEntries(ov.anomalies.by_service.map((a) => [a.service, a]));
    return ov.by_service.map((s) => ({
      service: s.service, errors: s.errors,
      statistical: anomalies[s.service]?.statistical ?? 0, signature: anomalies[s.service]?.signature ?? 0,
    }));
  }, [ov]);

  return (
    <div className="space-y-6">
      <PageHeader title="Operations overview"
        subtitle={ov?.time_range.start ? `Logs from ${fmtDateTime(ov.time_range.start)} to ${fmtDateTime(ov.time_range.end)}` : "No logs yet"}
        actions={
          <button className="btn-primary" disabled={analysis.pending} onClick={async () => { if (await analysis.run({})) reloadAll(); }}>
            {analysis.pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />} Run analysis
          </button>
        } />
      {analysis.error && <ErrorBox error={analysis.error} />}
      {overview.error && <ErrorBox error={`Backend unreachable: ${overview.error}`} onRetry={reloadAll} />}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Logs ingested" icon={<ScrollText className="h-4 w-4" />} value={fmtNum(ov?.total_logs)}
          hint={ov ? `${ov.by_service.length} services` : undefined} />
        <Stat label="Error rate" tone={ov && ov.error_rate > 0.05 ? "danger" : "default"} icon={<Gauge className="h-4 w-4" />}
          value={ov ? fmtPct(ov.error_rate, 2) : "—"} hint={ov ? `${fmtNum(ov.error_logs)} ERROR/CRITICAL logs` : undefined} />
        <Stat label="Active incidents" tone={ov && ov.incidents.open + ov.incidents.investigating > 0 ? "danger" : "ok"}
          icon={<AlertOctagon className="h-4 w-4" />} value={ov ? ov.incidents.open + ov.incidents.investigating : "—"}
          hint={ov ? `${ov.incidents.investigating} investigating · ${ov.incidents.resolved} resolved` : undefined} />
        <Stat label="Anomalies" tone="warn" icon={<Zap className="h-4 w-4" />} value={fmtNum(ov?.anomalies.total)}
          hint={ov?.last_run ? `last run #${ov.last_run.run_id}` : "no analysis yet"} />
      </div>

      <Card title="Log volume by severity" icon={<Database className="h-4 w-4 text-sky-400" />}
        actions={<Tabs value={bucket} onChange={setBucket} tabs={[{ value: "60", label: "1m" }, { value: "300", label: "5m" }, { value: "900", label: "15m" }]} />}>
        {series.loading && !series.data ? <Spinner /> : chart.length === 0 ? <Empty title="No log data" /> : (
          <>
            <div className="h-72">
              <ResponsiveContainer>
                <AreaChart data={chart} margin={{ left: -10, right: 8, top: 8 }}>
                  <CartesianGrid stroke="#1e293b" vertical={false} />
                  <XAxis dataKey="label" tick={{ fill: "#64748b", fontSize: 11 }} minTickGap={40} />
                  <YAxis tick={{ fill: "#64748b", fontSize: 11 }} />
                  <Tooltip contentStyle={tooltipStyle} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  {anomalyBands.map((b, i) => (
                    <ReferenceArea key={i} x1={b.x1} x2={b.x2} fill={b.sev === "critical" ? "#f43f5e" : "#f59e0b"} fillOpacity={0.08} />
                  ))}
                  {(["INFO", "WARNING", "ERROR", "CRITICAL"] as const).map((s) => (
                    <Area key={s} type="monotone" dataKey={s} stackId="1" stroke={SEVERITY_COLORS[s]} strokeWidth={s === "INFO" ? 1 : 0} fill={SEVERITY_COLORS[s]}
                      fillOpacity={s === "INFO" ? 0.15 : 0.5} isAnimationActive={false} />
                  ))}
                </AreaChart>
              </ResponsiveContainer>
            </div>
            <p className="mt-2 text-xs text-slate-500">Shaded bands mark high/critical anomaly windows. DEBUG logs are excluded from the chart.</p>
          </>
        )}
      </Card>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-5">
        <Card className="xl:col-span-3" title="Active incidents" icon={<AlertOctagon className="h-4 w-4 text-rose-400" />}
          actions={<Link to="/incidents" className="text-xs text-sky-400 hover:underline">View all</Link>} bodyClassName="p-0">
          {incidents.loading && !incidents.data ? <Spinner /> : !incidents.data?.length ? (
            <Empty title="No active incidents">Ingest logs and run analysis to detect incidents.</Empty>
          ) : (
            <ul className="divide-y divide-slate-800">
              {incidents.data.map((i) => (
                <li key={i.id}>
                  <Link to={`/incidents/${i.id}`} className="block px-4 py-3 hover:bg-slate-800/40">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge value={i.severity} /><Badge value={i.status} />
                          <span className="text-xs text-slate-500">#{i.id} · {fmtDateTime(i.started_at)}</span>
                        </div>
                        <div className="mt-1 truncate text-sm font-medium text-slate-100">{i.title}</div>
                        <div className="mt-1 flex flex-wrap gap-1">{i.services.map((s) => <ServiceTag key={s} name={s} />)}</div>
                      </div>
                      <div className="w-28 shrink-0 pt-1">
                        <div className="mb-1 text-right text-[10px] uppercase text-slate-500">confidence</div>
                        <ConfidenceBar value={i.confidence} />
                      </div>
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card className="xl:col-span-2" title="Service dependency map" icon={<Network className="h-4 w-4 text-violet-400" />}>
          {deps.data ? <ServiceMap graph={deps.data} /> : <Spinner />}
          <p className="mt-2 text-xs text-slate-500">Inferred from shared trace IDs: an arrow A → B means A logs before B within the same trace.</p>
        </Card>
      </div>

      <Card title="Errors and anomalies by service" icon={<Zap className="h-4 w-4 text-amber-400" />}>
        {serviceChart.length === 0 ? <Empty title="No data" /> : (
          <div className="h-64">
            <ResponsiveContainer>
              <BarChart data={serviceChart} margin={{ left: -10, right: 8, top: 8 }}>
                <CartesianGrid stroke="#1e293b" vertical={false} />
                <XAxis dataKey="service" tick={{ fill: "#94a3b8", fontSize: 11 }} />
                <YAxis yAxisId="l" tick={{ fill: "#64748b", fontSize: 11 }} />
                <YAxis yAxisId="r" orientation="right" tick={{ fill: "#64748b", fontSize: 11 }} />
                <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "rgba(148,163,184,0.06)" }} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar yAxisId="l" dataKey="errors" name="error logs" fill="#f87171" radius={[4, 4, 0, 0]} />
                <Bar yAxisId="r" dataKey="statistical" name="statistical anomalies" fill="#a78bfa" radius={[4, 4, 0, 0]} />
                <Bar yAxisId="r" dataKey="signature" name="signature anomalies" fill="#2dd4bf" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </Card>
    </div>
  );
}
