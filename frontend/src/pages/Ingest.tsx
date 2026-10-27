import clsx from "clsx";
import { CheckCircle2, FileUp, FlaskConical, Loader2, Play, Send, SlidersHorizontal, Upload, XCircle } from "lucide-react";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { AnalysisSummary, IngestResult } from "../api/types";
import { Card, ErrorBox, PageHeader, Toggle } from "../components/ui";
import { useAction } from "../hooks/useApi";
import { fmtDateTime, fmtNum, fromLocalInput } from "../lib/format";

const SCENARIOS = [
  { value: "mixed", label: "Mixed (4 incidents)", desc: "DB timeouts, auth attack, memory exhaustion and a bad deploy." },
  { value: "db_timeout", label: "Database timeouts", desc: "Connection pool saturation → query timeouts → HTTP 500s." },
  { value: "http_500", label: "HTTP 500 / bad deploy", desc: "NullPointerException in payments-service → 500s at the gateway." },
  { value: "memory_pressure", label: "Memory pressure → OOM", desc: "Heap pressure, OOMKill, restart, connection refused, 503s." },
  { value: "auth_failure", label: "Failed authentication", desc: "Credential-stuffing burst from a few IPs." },
  { value: "cascading_failure", label: "Cascading failure", desc: "Memory exhaustion overlapping with database timeouts." },
  { value: "normal", label: "Normal traffic", desc: "Healthy baseline — should produce no incidents." },
];

const EXAMPLE_LOG = JSON.stringify({
  timestamp: new Date().toISOString(), severity: "ERROR", service: "orders-service", host: "orders-1",
  trace_id: "a1b2c3d4", message: "Database query timed out after 5000ms (pool=orders-primary)", metadata: { latency_ms: 5000 },
}, null, 2);

export function Ingest() {
  const [result, setResult] = useState<{ title: string; r: IngestResult } | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisSummary | null>(null);
  const show = (title: string, r: IngestResult | undefined) => {
    if (!r) return;
    setResult({ title, r });
    if (r.analysis) setAnalysis(r.analysis);
  };

  return (
    <div className="space-y-6">
      <PageHeader title="Ingest & analyze" subtitle="Upload files, submit logs via the API, or generate realistic synthetic incidents." />
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <UploadCard onResult={show} />
        <GeneratorCard onResult={show} />
        <ManualCard onResult={show} />
        <AnalysisCard onDone={setAnalysis} />
      </div>
      {result && <ResultCard title={result.title} r={result.r} />}
      {analysis && <AnalysisResult a={analysis} />}
    </div>
  );
}

function UploadCard({ onResult }: { onResult: (t: string, r: IngestResult | undefined) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [format, setFormat] = useState("");
  const [analyze, setAnalyze] = useState(true);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const upload = useAction(api.upload);
  return (
    <Card title="Upload log file" icon={<FileUp className="h-4 w-4 text-sky-400" />}>
      <div onClick={() => input.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]); }}
        className={clsx("flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-4 py-8 text-center transition",
          drag ? "border-sky-500 bg-sky-500/5" : "border-slate-700 hover:border-slate-500")}>
        <Upload className="h-6 w-6 text-slate-500" />
        <div className="text-sm text-slate-300">{file ? <b>{file.name}</b> : "Drop a .jsonl, .json or .csv file, or click to browse"}</div>
        <div className="text-xs text-slate-500">{file ? `${(file.size / 1024).toFixed(1)} KB` : "Fields: timestamp, severity, service, message, host, trace_id, metadata (aliases accepted)"}</div>
        <input ref={input} type="file" accept=".json,.jsonl,.ndjson,.csv,.log,.txt" className="hidden" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-4">
        <select className="input w-40" value={format} onChange={(e) => setFormat(e.target.value)} aria-label="Format">
          <option value="">Auto-detect</option><option value="jsonl">JSONL</option><option value="json">JSON</option><option value="csv">CSV</option>
        </select>
        <Toggle checked={analyze} onChange={setAnalyze} label="Analyze after upload" />
        <button className="btn-primary ml-auto" disabled={!file || upload.pending}
          onClick={async () => onResult(`Upload: ${file!.name}`, await upload.run(file!, format || null, analyze))}>
          {upload.pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />} Upload
        </button>
      </div>
      {upload.error && <div className="mt-3"><ErrorBox error={upload.error} /></div>}
      <p className="mt-3 text-xs text-slate-500">Sample files are in <code className="font-mono">backend/sample_data/</code>.</p>
    </Card>
  );
}

function GeneratorCard({ onResult }: { onResult: (t: string, r: IngestResult | undefined) => void }) {
  const [scenario, setScenario] = useState("db_timeout");
  const [minutes, setMinutes] = useState(60);
  const [rpm, setRpm] = useState(40);
  const gen = useAction(api.generate);
  return (
    <Card title="Synthetic incident generator" icon={<FlaskConical className="h-4 w-4 text-violet-400" />}>
      <div className="grid gap-2 sm:grid-cols-2">
        {SCENARIOS.map((s) => (
          <button key={s.value} onClick={() => setScenario(s.value)}
            className={clsx("rounded-lg border p-3 text-left transition", scenario === s.value ? "border-violet-500/60 bg-violet-500/10" : "border-slate-800 hover:border-slate-600")}>
            <div className="text-sm font-medium text-slate-100">{s.label}</div>
            <div className="mt-0.5 text-xs text-slate-400">{s.desc}</div>
          </button>
        ))}
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
        <div><label className="label">Minutes</label><input type="number" className="input" min={5} max={1440} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} /></div>
        <div><label className="label">Requests / min</label><input type="number" className="input" min={1} max={2000} value={rpm} onChange={(e) => setRpm(Number(e.target.value))} /></div>
        <div className="col-span-2 flex items-end sm:col-span-1">
          <button className="btn-primary w-full" disabled={gen.pending}
            onClick={async () => onResult(`Generated: ${scenario}`, await gen.run({ scenario, minutes, requests_per_minute: rpm, analyze: true }))}>
            {gen.pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />} Generate & analyze
          </button>
        </div>
      </div>
      {gen.error && <div className="mt-3"><ErrorBox error={gen.error} /></div>}
      <p className="mt-3 text-xs text-slate-500">Logs end at the current time, so they appear at the right edge of the dashboard.</p>
    </Card>
  );
}

function ManualCard({ onResult }: { onResult: (t: string, r: IngestResult | undefined) => void }) {
  const [text, setText] = useState(EXAMPLE_LOG);
  const [parseError, setParseError] = useState<string | null>(null);
  const send = useAction(async (raw: string) => {
    const data = JSON.parse(raw);
    return Array.isArray(data) ? api.ingestBatch(data) : api.ingestOne(data);
  });
  return (
    <Card title="Submit logs (JSON object or array)" icon={<Send className="h-4 w-4 text-teal-400" />}>
      <textarea className="input h-56 font-mono text-xs" spellCheck={false} value={text} onChange={(e) => { setText(e.target.value); setParseError(null); }} />
      <div className="mt-3 flex items-center gap-2">
        <span className="text-xs text-slate-500">A single object → <code>POST /api/logs</code>; an array → <code>/api/logs/batch</code></span>
        <button className="btn-primary ml-auto" disabled={send.pending} onClick={async () => {
          try { JSON.parse(text); } catch (e) { setParseError(`Invalid JSON: ${(e as Error).message}`); return; }
          onResult("Manual submission", await send.run(text));
        }}>
          {send.pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />} Submit
        </button>
      </div>
      {(parseError || send.error) && <div className="mt-3"><ErrorBox error={(parseError || send.error)!} /></div>}
    </Card>
  );
}

function AnalysisCard({ onDone }: { onDone: (a: AnalysisSummary) => void }) {
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [window, setWindow] = useState(60);
  const [z, setZ] = useState(3.5);
  const [minCount, setMinCount] = useState(3);
  const [gap, setGap] = useState(300);
  const [useLlm, setUseLlm] = useState(false);
  const run = useAction(api.runAnalysis);
  return (
    <Card title="Run analysis" icon={<SlidersHorizontal className="h-4 w-4 text-amber-400" />}>
      <div className="grid grid-cols-2 gap-3">
        <div><label className="label">Start (optional)</label><input type="datetime-local" className="input" value={start} onChange={(e) => setStart(e.target.value)} /></div>
        <div><label className="label">End (optional)</label><input type="datetime-local" className="input" value={end} onChange={(e) => setEnd(e.target.value)} /></div>
        <div><label className="label">Window (seconds)</label><input type="number" className="input" min={5} value={window} onChange={(e) => setWindow(Number(e.target.value))} /></div>
        <div><label className="label">Robust z threshold</label><input type="number" step="0.5" min={1} className="input" value={z} onChange={(e) => setZ(Number(e.target.value))} /></div>
        <div><label className="label">Min errors / window</label><input type="number" min={1} className="input" value={minCount} onChange={(e) => setMinCount(Number(e.target.value))} /></div>
        <div><label className="label">Correlation gap (s)</label><input type="number" min={0} className="input" value={gap} onChange={(e) => setGap(Number(e.target.value))} /></div>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Toggle checked={useLlm} onChange={setUseLlm} label="LLM narratives (needs GROQ_API_KEY)" />
        <button className="btn-primary ml-auto" disabled={run.pending} onClick={async () => {
          const a = await run.run({ start: fromLocalInput(start), end: fromLocalInput(end), window_seconds: window, z_threshold: z,
            min_anomaly_count: minCount, correlation_gap_seconds: gap, use_llm: useLlm });
          if (a) onDone(a);
        }}>
          {run.pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />} Analyze
        </button>
      </div>
      {run.error && <div className="mt-3"><ErrorBox error={run.error} /></div>}
      <p className="mt-3 text-xs text-slate-500">
        Default range: the last 24h of logs. Re-running keeps the status and notes of incidents that are detected again.
      </p>
    </Card>
  );
}

function ResultCard({ title, r }: { title: string; r: IngestResult }) {
  return (
    <Card title={title} icon={r.rejected ? <XCircle className="h-4 w-4 text-amber-400" /> : <CheckCircle2 className="h-4 w-4 text-emerald-400" />}>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[["Received", r.received], ["Accepted", r.accepted], ["Duplicates", r.duplicates], ["Rejected", r.rejected]].map(([k, v]) => (
          <div key={k as string} className="rounded-lg bg-slate-950/60 p-3"><div className="text-xs text-slate-500">{k}</div><div className="text-lg font-semibold text-white">{fmtNum(v as number)}</div></div>
        ))}
      </div>
      {r.first_timestamp && <p className="mt-3 text-xs text-slate-400">Time range: {fmtDateTime(r.first_timestamp)} → {fmtDateTime(r.last_timestamp)}</p>}
      {r.errors.length > 0 && (
        <div className="mt-3">
          <div className="label">Validation errors (first {r.errors.length})</div>
          <ul className="scrollbar-thin max-h-56 space-y-1 overflow-y-auto font-mono text-xs">
            {r.errors.map((e, i) => (
              <li key={i} className="rounded bg-rose-500/5 px-2 py-1 text-rose-200">
                #{e.index}: {e.error}{e.record && <span className="block truncate text-slate-500">{e.record}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

function AnalysisResult({ a }: { a: AnalysisSummary }) {
  return (
    <Card title={`Analysis run #${a.run_id}`} icon={<CheckCircle2 className="h-4 w-4 text-emerald-400" />}
      actions={<Link to="/incidents" className="btn-secondary py-1 text-xs">View incidents</Link>}>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        {[["Logs analyzed", a.logs_analyzed], ["Statistical", a.statistical_anomalies], ["Signature", a.signature_anomalies], ["Incidents", a.incidents], ["Duration (ms)", a.duration_ms]].map(([k, v]) => (
          <div key={k as string} className="rounded-lg bg-slate-950/60 p-3"><div className="text-xs text-slate-500">{k}</div><div className="text-lg font-semibold text-white">{fmtNum(v as number)}</div></div>
        ))}
      </div>
      <p className="mt-3 text-xs text-slate-400">Range {fmtDateTime(a.range_start)} → {fmtDateTime(a.range_end)}</p>
    </Card>
  );
}
