import { Fragment, type ReactNode } from "react";

/** Minimal, safe markdown renderer for incident narratives (headings, lists, bold/italic/code, evidence refs). */
export function Markdown({ text, onLogRef }: { text: string; onLogRef?: (id: number) => void }) {
  const blocks: ReactNode[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  const flush = () => {
    if (!list) return;
    const Tag = list.ordered ? "ol" : "ul";
    blocks.push(
      <Tag key={blocks.length} className={list.ordered ? "ml-5 list-decimal space-y-1" : "ml-5 list-disc space-y-1"}>
        {list.items.map((it, i) => <li key={i}>{inline(it, onLogRef)}</li>)}
      </Tag>,
    );
    list = null;
  };

  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trimEnd();
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    const ul = line.match(/^\s*[-*]\s+(.*)$/);
    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (h) {
      flush();
      blocks.push(<h3 key={blocks.length} className="mt-4 text-sm font-semibold uppercase tracking-wide text-slate-300 first:mt-0">{inline(h[2], onLogRef)}</h3>);
    } else if (ul || ol) {
      const ordered = !!ol;
      if (!list || list.ordered !== ordered) {
        flush();
        list = { ordered, items: [] };
      }
      list.items.push((ul ?? ol)![1]);
    } else if (!line.trim()) {
      flush();
    } else {
      flush();
      blocks.push(<p key={blocks.length}>{inline(line, onLogRef)}</p>);
    }
  }
  flush();
  return <div className="space-y-2 text-sm leading-relaxed text-slate-300">{blocks}</div>;
}

const TOKEN = /(\*\*[^*]+\*\*|`[^`]+`|_[^_]+_|\[(?:log|anomaly):\d+\]|\blog:\d+\b|\[[FS]\d+\])/g;

function inline(s: string, onLogRef?: (id: number) => void): ReactNode {
  const parts = s.split(TOKEN);
  return parts.map((p, i) => {
    if (!p) return null;
    if (p.startsWith("**")) return <strong key={i} className="font-semibold text-slate-100">{inline(p.slice(2, -2), onLogRef)}</strong>;
    if (p.startsWith("`")) return <code key={i} className="rounded bg-slate-800 px-1 font-mono text-xs">{p.slice(1, -1)}</code>;
    if (/^_[^_]+_$/.test(p)) return <em key={i} className="text-slate-400">{inline(p.slice(1, -1), onLogRef)}</em>;
    const logRef = p.match(/^\[?log:(\d+)\]?$/);
    if (logRef) {
      const id = Number(logRef[1]);
      return (
        <button key={i} onClick={() => onLogRef?.(id)} className="mx-0.5 rounded bg-sky-500/10 px-1 font-mono text-[11px] text-sky-300 hover:bg-sky-500/20">
          log:{id}
        </button>
      );
    }
    if (/^\[(?:anomaly:\d+|[FS]\d+)\]$/.test(p))
      return <span key={i} className="mx-0.5 rounded bg-slate-800 px-1 font-mono text-[11px] text-slate-300">{p.slice(1, -1)}</span>;
    return <Fragment key={i}>{p}</Fragment>;
  });
}
