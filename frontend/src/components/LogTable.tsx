import clsx from "clsx";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Fragment, useState } from "react";
import type { LogEntry } from "../api/types";
import { fmtDateTime } from "../lib/format";
import { Badge } from "./ui";

export function LogTable({ logs, highlight, onTrace, compact = false }: {
  logs: LogEntry[];
  highlight?: Set<number>;
  onTrace?: (traceId: string) => void;
  compact?: boolean;
}) {
  const [open, setOpen] = useState<Set<number>>(new Set());
  const toggle = (id: number) =>
    setOpen((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  return (
    <div className="scrollbar-thin overflow-x-auto">
      <table className="w-full min-w-[720px] border-collapse">
        <thead className="border-b border-slate-800">
          <tr>
            <th className="th w-6" />
            <th className="th w-44">Timestamp</th>
            <th className="th w-24">Severity</th>
            <th className="th w-40">Service</th>
            <th className="th">Message</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800/70">
          {logs.map((l) => {
            const isOpen = open.has(l.id);
            return (
              <Fragment key={l.id}>
                <tr id={`log-${l.id}`} onClick={() => toggle(l.id)}
                  className={clsx("cursor-pointer hover:bg-slate-800/40", highlight?.has(l.id) && "bg-sky-500/10")}>
                  <td className="td text-slate-500">{isOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}</td>
                  <td className="td whitespace-nowrap font-mono text-xs text-slate-400">{fmtDateTime(l.timestamp)}</td>
                  <td className="td"><Badge value={l.severity} /></td>
                  <td className="td font-mono text-xs text-slate-300">{l.service}</td>
                  <td className={clsx("td font-mono text-xs text-slate-200", compact ? "line-clamp-2" : "break-all")}>{l.message}</td>
                </tr>
                {isOpen && (
                  <tr className="bg-slate-950/60">
                    <td />
                    <td colSpan={4} className="px-3 py-3">
                      <dl className="grid grid-cols-1 gap-x-6 gap-y-1 font-mono text-xs sm:grid-cols-2">
                        <Field k="log id" v={String(l.id)} />
                        <Field k="host" v={l.host ?? "—"} />
                        <div className="flex gap-2">
                          <dt className="w-24 shrink-0 text-slate-500">trace_id</dt>
                          <dd>{l.trace_id ? (
                            <button className="text-sky-300 hover:underline" onClick={(e) => { e.stopPropagation(); onTrace?.(l.trace_id!); }}>
                              {l.trace_id}
                            </button>) : "—"}</dd>
                        </div>
                        <Field k="source" v={l.source} />
                        <Field k="template" v={l.template} wide />
                      </dl>
                      {Object.keys(l.metadata ?? {}).length > 0 && (
                        <pre className="mt-2 overflow-x-auto rounded-lg bg-slate-900 p-2 font-mono text-xs text-slate-300">
                          {JSON.stringify(l.metadata, null, 2)}
                        </pre>
                      )}
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Field({ k, v, wide }: { k: string; v: string; wide?: boolean }) {
  return (
    <div className={clsx("flex gap-2", wide && "sm:col-span-2")}>
      <dt className="w-24 shrink-0 text-slate-500">{k}</dt>
      <dd className="break-all text-slate-300">{v}</dd>
    </div>
  );
}
