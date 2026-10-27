import { Beaker, Fingerprint, Loader2, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { Level, Signature } from "../api/types";
import { Badge, Card, ErrorBox, PageHeader, Spinner, Toggle } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";

const EMPTY = { key: "", name: "", category: "application", pattern: "", severity: "medium" as Level, min_count: 1, description: "" };

export function Signatures() {
  const list = useApi(() => api.signatures(), []);
  const [form, setForm] = useState(EMPTY);
  const create = useAction(api.createSignature);
  const update = useAction(api.updateSignature);
  const remove = useAction(api.deleteSignature);
  const [testPattern, setTestPattern] = useState("(timeout|timed out)");
  const [testText, setTestText] = useState("Database query timed out after 5000ms\nGET /api/orders -> 200 OK in 45ms\nupstream request timeout");
  const tester = useAction(api.testSignature);
  const [results, setResults] = useState<{ message: string; matched: boolean; match: string | null }[]>([]);

  const patch = async (s: Signature, body: Partial<Signature>) => {
    if (await update.run(s.key, body)) list.reload();
  };

  return (
    <div className="space-y-6">
      <PageHeader title="Log signatures" subtitle="Configurable regex rules. A signature fires when matches in one window reach its minimum count." />
      {(update.error || remove.error) && <ErrorBox error={(update.error || remove.error)!} />}

      <Card bodyClassName="p-0">
        {list.loading && !list.data ? <Spinner /> : (
          <div className="scrollbar-thin overflow-x-auto">
            <table className="w-full min-w-[1000px]">
              <thead className="border-b border-slate-800"><tr>
                <th className="th">Signature</th><th className="th">Category</th><th className="th">Pattern</th>
                <th className="th w-32">Severity</th><th className="th w-24">Min / window</th><th className="th w-24">Enabled</th><th className="th w-12" />
              </tr></thead>
              <tbody className="divide-y divide-slate-800/70">
                {list.data?.map((s) => (
                  <tr key={s.key} className={s.enabled ? "" : "opacity-50"}>
                    <td className="td"><div className="font-medium text-slate-100">{s.name}</div><div className="font-mono text-xs text-slate-500">{s.key}{s.builtin && " · built-in"}</div>
                      <div className="mt-1 max-w-xs text-xs text-slate-400">{s.description}</div></td>
                    <td className="td"><Badge value={s.category} /></td>
                    <td className="td max-w-md"><code className="break-all font-mono text-[11px] text-teal-200">{s.pattern}</code></td>
                    <td className="td">
                      <select className="input py-1 text-xs" value={s.severity} onChange={(e) => patch(s, { severity: e.target.value as Level })}>
                        {["low", "medium", "high", "critical"].map((l) => <option key={l}>{l}</option>)}
                      </select>
                    </td>
                    <td className="td"><input type="number" min={1} className="input py-1 text-xs" defaultValue={s.min_count}
                      onBlur={(e) => Number(e.target.value) !== s.min_count && patch(s, { min_count: Number(e.target.value) })} /></td>
                    <td className="td"><Toggle checked={s.enabled} onChange={(v) => patch(s, { enabled: v })} label="" /></td>
                    <td className="td">{!s.builtin && (
                      <button className="btn-ghost p-1 text-rose-400" aria-label="Delete" onClick={async () => { await remove.run(s.key); list.reload(); }}>
                        <Trash2 className="h-4 w-4" /></button>)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Card title="Add signature" icon={<Plus className="h-4 w-4 text-sky-400" />}>
          <div className="grid grid-cols-2 gap-3">
            <div><label className="label">Key</label><input className="input font-mono" placeholder="kafka_lag" value={form.key} onChange={(e) => setForm({ ...form, key: e.target.value })} /></div>
            <div><label className="label">Name</label><input className="input" placeholder="Kafka consumer lag" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
            <div><label className="label">Category</label>
              <select className="input" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
                {["application", "database", "resource", "storage", "config", "security", "dependency", "availability", "network", "traffic", "resilience", "http"].map((c) => <option key={c}>{c}</option>)}
              </select></div>
            <div className="grid grid-cols-2 gap-2">
              <div><label className="label">Severity</label>
                <select className="input" value={form.severity} onChange={(e) => setForm({ ...form, severity: e.target.value as Level })}>
                  {["low", "medium", "high", "critical"].map((l) => <option key={l}>{l}</option>)}
                </select></div>
              <div><label className="label">Min / window</label><input type="number" min={1} className="input" value={form.min_count} onChange={(e) => setForm({ ...form, min_count: Number(e.target.value) })} /></div>
            </div>
            <div className="col-span-2"><label className="label">Regex pattern (case-insensitive)</label><input className="input font-mono" placeholder="consumer lag \d+" value={form.pattern} onChange={(e) => setForm({ ...form, pattern: e.target.value })} /></div>
            <div className="col-span-2"><label className="label">Description</label><input className="input" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></div>
          </div>
          {create.error && <div className="mt-3"><ErrorBox error={create.error} /></div>}
          <div className="mt-3 flex justify-end">
            <button className="btn-primary" disabled={create.pending || !form.key || !form.name || !form.pattern}
              onClick={async () => { if (await create.run(form)) { setForm(EMPTY); list.reload(); } }}>
              {create.pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Fingerprint className="h-4 w-4" />} Create
            </button>
          </div>
          <p className="mt-2 text-xs text-slate-500">Changes apply on the next analysis run.</p>
        </Card>

        <Card title="Pattern tester" icon={<Beaker className="h-4 w-4 text-violet-400" />}>
          <label className="label">Pattern</label>
          <input className="input font-mono" value={testPattern} onChange={(e) => setTestPattern(e.target.value)} />
          <label className="label mt-3">Sample messages (one per line)</label>
          <textarea className="input h-28 font-mono text-xs" value={testText} onChange={(e) => setTestText(e.target.value)} />
          <div className="mt-3 flex justify-end">
            <button className="btn-secondary" disabled={tester.pending} onClick={async () => {
              const r = await tester.run(testPattern, testText.split("\n").filter((l) => l.trim()));
              if (r) setResults(r.results);
            }}>Test</button>
          </div>
          {tester.error && <div className="mt-3"><ErrorBox error={tester.error} /></div>}
          <ul className="mt-3 space-y-1 font-mono text-xs">
            {results.map((r, i) => (
              <li key={i} className={r.matched ? "text-emerald-300" : "text-slate-500"}>
                {r.matched ? "✓" : "✗"} {r.message}{r.match && <span className="ml-2 rounded bg-emerald-500/10 px-1">{r.match}</span>}
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  );
}
