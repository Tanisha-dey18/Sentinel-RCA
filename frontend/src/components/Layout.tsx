import clsx from "clsx";
import {
  Activity, AlertOctagon, Fingerprint, LayoutDashboard, Menu, ScrollText, Server, ShieldCheck, Upload, X, Zap,
} from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { api } from "../api/client";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/incidents", label: "Incidents", icon: AlertOctagon },
  { to: "/anomalies", label: "Anomalies", icon: Zap },
  { to: "/logs", label: "Log Explorer", icon: ScrollText },
  { to: "/ingest", label: "Ingest & Analyze", icon: Upload },
  { to: "/signatures", label: "Signatures", icon: Fingerprint },
  { to: "/system", label: "System", icon: Server },
];

export function Layout() {
  const [open, setOpen] = useState(false);
  const [live, setLive] = useState<{ connected: boolean; lastIngest: number | null }>({ connected: false, lastIngest: null });
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);

  useEffect(() => {
    const es = new EventSource(api.streamUrl());
    es.addEventListener("ready", () => setLive((l) => ({ ...l, connected: true })));
    es.addEventListener("ingest", (e) => {
      const d = JSON.parse((e as MessageEvent).data);
      setLive({ connected: true, lastIngest: d.accepted });
      window.dispatchEvent(new CustomEvent("sentinel:ingest", { detail: d }));
    });
    es.onerror = () => setLive((l) => ({ ...l, connected: false }));
    return () => es.close();
  }, []);

  const nav = (
    <nav className="flex flex-col gap-1">
      {NAV.map(({ to, label, icon: Icon, end }) => (
        <NavLink key={to} to={to} end={end}
          className={({ isActive }) => clsx("flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition",
            isActive ? "bg-sky-500/10 text-sky-300" : "text-slate-400 hover:bg-slate-800/60 hover:text-slate-200")}>
          <Icon className="h-4 w-4" /> {label}
        </NavLink>
      ))}
    </nav>
  );

  const brand = (
    <div className="flex items-center gap-2 px-2">
      <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-sky-500/15 ring-1 ring-sky-500/40">
        <ShieldCheck className="h-5 w-5 text-sky-400" />
      </div>
      <div>
        <div className="text-sm font-semibold text-white">Sentinel RCA</div>
        <div className="text-[11px] text-slate-500">Log anomaly & root cause</div>
      </div>
    </div>
  );

  const status = (
    <div className="flex items-center gap-2 rounded-lg border border-slate-800 px-3 py-2 text-xs text-slate-400">
      <Activity className={clsx("h-3.5 w-3.5", live.connected ? "text-emerald-400" : "text-slate-600")} />
      {live.connected ? "Live stream connected" : "Live stream offline"}
      {live.lastIngest !== null && <span className="ml-auto text-slate-500">+{live.lastIngest}</span>}
    </div>
  );

  return (
    <div className="min-h-screen lg:pl-60">
      <aside className="fixed inset-y-0 left-0 hidden w-60 flex-col justify-between border-r border-slate-800 bg-slate-950 p-4 lg:flex">
        <div className="space-y-6">{brand}{nav}</div>
        {status}
      </aside>

      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-slate-800 bg-slate-950/90 px-4 py-3 backdrop-blur lg:hidden">
        {brand}
        <button className="btn-ghost p-2" onClick={() => setOpen(true)} aria-label="Open menu"><Menu className="h-5 w-5" /></button>
      </header>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/60" onClick={() => setOpen(false)} />
          <div className="absolute inset-y-0 left-0 flex w-64 flex-col justify-between bg-slate-950 p-4">
            <div className="space-y-6">
              <div className="flex items-center justify-between">{brand}
                <button className="btn-ghost p-1" onClick={() => setOpen(false)} aria-label="Close menu"><X className="h-5 w-5" /></button>
              </div>
              {nav}
            </div>
            {status}
          </div>
        </div>
      )}

      <main className="mx-auto max-w-[1500px] px-4 py-6 sm:px-6 lg:px-8">
        <Outlet />
      </main>
    </div>
  );
}
