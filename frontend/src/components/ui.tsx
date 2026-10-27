import clsx from "clsx";
import { AlertTriangle, Inbox, Loader2 } from "lucide-react";
import type { ReactNode } from "react";

export function Card({ title, icon, actions, children, className, bodyClassName }: {
  title?: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={clsx("card", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-slate-800 px-4 py-3">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-100">
            {icon}
            {title}
          </h2>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={clsx("p-4", bodyClassName)}>{children}</div>
    </section>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 className="text-xl font-semibold text-white">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-slate-400">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

const LEVEL_STYLES: Record<string, string> = {
  low: "bg-slate-700/40 text-slate-300 ring-slate-600",
  medium: "bg-amber-500/10 text-amber-300 ring-amber-500/40",
  high: "bg-orange-500/10 text-orange-300 ring-orange-500/40",
  critical: "bg-rose-500/15 text-rose-300 ring-rose-500/50",
  DEBUG: "bg-slate-700/40 text-slate-400 ring-slate-600",
  INFO: "bg-sky-500/10 text-sky-300 ring-sky-500/30",
  WARNING: "bg-amber-500/10 text-amber-300 ring-amber-500/40",
  ERROR: "bg-rose-500/10 text-rose-300 ring-rose-500/40",
  CRITICAL: "bg-rose-600/25 text-rose-200 ring-rose-500/60",
  open: "bg-rose-500/10 text-rose-300 ring-rose-500/40",
  investigating: "bg-amber-500/10 text-amber-300 ring-amber-500/40",
  resolved: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/40",
  fact: "bg-sky-500/10 text-sky-300 ring-sky-500/40",
  signal: "bg-violet-500/10 text-violet-300 ring-violet-500/40",
  inference: "bg-amber-500/10 text-amber-200 ring-amber-500/40",
  statistical: "bg-violet-500/10 text-violet-300 ring-violet-500/40",
  signature: "bg-teal-500/10 text-teal-300 ring-teal-500/40",
};

export function Badge({ value, label, className }: { value: string; label?: string; className?: string }) {
  return (
    <span className={clsx("inline-flex items-center whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wide ring-1 ring-inset",
      LEVEL_STYLES[value] ?? "bg-slate-700/40 text-slate-300 ring-slate-600", className)}>
      {label ?? value}
    </span>
  );
}

export function ServiceTag({ name }: { name: string }) {
  return <span className="inline-flex rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[11px] text-slate-300">{name}</span>;
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-sm text-slate-400">
      <Loader2 className="h-4 w-4 animate-spin" /> {label}
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-200">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="flex-1 break-words">{error}</div>
      {onRetry && <button className="btn-ghost -my-1 py-1" onClick={onRetry}>Retry</button>}
    </div>
  );
}

export function Empty({ title, children, icon }: { title: string; children?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-12 text-center">
      <div className="text-slate-600">{icon ?? <Inbox className="h-8 w-8" />}</div>
      <div className="text-sm font-medium text-slate-300">{title}</div>
      {children && <div className="max-w-md text-sm text-slate-500">{children}</div>}
    </div>
  );
}

export function Stat({ label, value, hint, icon, tone = "default" }: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  icon?: ReactNode;
  tone?: "default" | "danger" | "warn" | "ok";
}) {
  const toneCls = { default: "text-sky-400", danger: "text-rose-400", warn: "text-amber-400", ok: "text-emerald-400" }[tone];
  return (
    <div className="card p-4">
      <div className="flex items-center justify-between text-xs font-medium uppercase tracking-wide text-slate-400">
        {label}
        <span className={toneCls}>{icon}</span>
      </div>
      <div className="mt-2 text-2xl font-semibold text-white">{value}</div>
      {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}

export function ConfidenceBar({ value, label }: { value: number; label?: string }) {
  const color = value >= 0.7 ? "bg-emerald-500" : value >= 0.45 ? "bg-amber-500" : "bg-rose-500";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-full min-w-16 overflow-hidden rounded-full bg-slate-800">
        <div className={clsx("h-full rounded-full", color)} style={{ width: `${Math.round(value * 100)}%` }} />
      </div>
      <span className="w-10 shrink-0 text-right font-mono text-xs text-slate-300">{label ?? `${Math.round(value * 100)}%`}</span>
    </div>
  );
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: ReactNode }) {
  return (
    <label className="inline-flex cursor-pointer select-none items-center gap-2 text-sm text-slate-300">
      <button type="button" role="switch" aria-checked={checked} onClick={() => onChange(!checked)}
        className={clsx("relative h-5 w-9 rounded-full transition", checked ? "bg-sky-600" : "bg-slate-700")}>
        <span className={clsx("absolute top-0.5 h-4 w-4 rounded-full bg-white transition", checked ? "left-4" : "left-0.5")} />
      </button>
      {label}
    </label>
  );
}

export function Tabs<T extends string>({ value, onChange, tabs }: {
  value: T;
  onChange: (v: T) => void;
  tabs: { value: T; label: ReactNode }[];
}) {
  return (
    <div className="flex flex-wrap gap-1 rounded-lg border border-slate-800 bg-slate-900/60 p-1">
      {tabs.map((t) => (
        <button key={t.value} onClick={() => onChange(t.value)}
          className={clsx("rounded-md px-3 py-1.5 text-sm font-medium transition",
            value === t.value ? "bg-slate-700 text-white" : "text-slate-400 hover:text-slate-200")}>
          {t.label}
        </button>
      ))}
    </div>
  );
}
