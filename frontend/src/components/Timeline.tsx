import clsx from "clsx";
import { Activity, Eye, Lightbulb } from "lucide-react";
import type { TimelineEvent } from "../api/types";
import { fmtTime } from "../lib/format";
import { Badge, ServiceTag } from "./ui";

const ICON = { fact: Eye, signal: Activity, inference: Lightbulb };
const DOT = { fact: "bg-sky-500", signal: "bg-violet-500", inference: "bg-amber-500" };

export function Timeline({ events, onLogs }: { events: TimelineEvent[]; onLogs?: (ids: number[]) => void }) {
  return (
    <ol className="relative ml-3 border-l border-slate-800">
      {events.map((e, i) => {
        const Icon = ICON[e.type] ?? Eye;
        return (
          <li key={i} className="mb-4 ml-6 last:mb-0">
            <span className={clsx("absolute -left-[13px] flex h-6 w-6 items-center justify-center rounded-full ring-4 ring-slate-950", DOT[e.type])}>
              <Icon className="h-3.5 w-3.5 text-white" />
            </span>
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs text-slate-500">{fmtTime(e.timestamp)}</span>
              <Badge value={e.type} />
              {e.service && <ServiceTag name={e.service} />}
              {e.robust_z !== undefined && e.robust_z !== null && (
                <span className="font-mono text-[11px] text-violet-300">z={e.robust_z.toFixed(1)}</span>
              )}
            </div>
            <div className="mt-1 text-sm font-medium text-slate-100">{e.title}</div>
            <div className="text-xs text-slate-400">{e.detail}</div>
            {e.log_ids?.length > 0 && onLogs && (
              <button className="mt-1 text-xs text-sky-400 hover:underline" onClick={() => onLogs(e.log_ids)}>
                View {e.log_ids.length} evidence log{e.log_ids.length > 1 ? "s" : ""}
              </button>
            )}
          </li>
        );
      })}
    </ol>
  );
}
