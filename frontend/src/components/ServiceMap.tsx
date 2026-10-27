import type { DependencyGraph } from "../api/types";

/** Layered SVG of the trace-inferred call graph. Affected services are highlighted. */
export function ServiceMap({ graph, highlight = [], origin }: { graph: DependencyGraph; highlight?: string[]; origin?: string }) {
  const nodes = graph.nodes.length ? graph.nodes : Array.from(new Set(graph.edges.flatMap((e) => [e.source, e.target])));
  if (!nodes.length) return <div className="py-8 text-center text-sm text-slate-500">No trace data to infer dependencies.</div>;

  // BFS layering from roots (services nobody calls). The inferred graph can contain cycles
  // (e.g. a service and its database both logging first in different traces), so BFS, not longest path.
  const callers = new Set(graph.edges.map((e) => e.target));
  let frontier = nodes.filter((n) => !callers.has(n));
  if (!frontier.length) frontier = [nodes[0]];
  const depth: Record<string, number> = {};
  frontier.forEach((n) => (depth[n] = 0));
  while (frontier.length) {
    const next: string[] = [];
    for (const n of frontier)
      for (const e of graph.edges)
        if (e.source === n && depth[e.target] === undefined) {
          depth[e.target] = depth[n] + 1;
          next.push(e.target);
        }
    frontier = next;
  }
  nodes.forEach((n) => (depth[n] ??= 0));
  const layers: string[][] = [];
  nodes.forEach((n) => (layers[depth[n]] ??= []).push(n));
  for (let i = 0; i < layers.length; i++) layers[i] ??= [];

  const W = 640, colW = W / Math.max(1, layers.length), rowH = 56;
  const H = Math.max(...layers.map((l) => l.length)) * rowH + 20;
  const pos: Record<string, { x: number; y: number }> = {};
  layers.forEach((layer, li) =>
    layer.sort().forEach((n, ni) => {
      pos[n] = { x: colW * li + colW / 2, y: (H - layer.length * rowH) / 2 + ni * rowH + rowH / 2 };
    }));
  const hl = new Set(highlight);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label="Service dependency map">
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="#475569" />
        </marker>
      </defs>
      {graph.edges.map((e) => {
        const a = pos[e.source], b = pos[e.target];
        if (!a || !b) return null;
        const hot = hl.has(e.source) && hl.has(e.target);
        return (
          <line key={`${e.source}-${e.target}`} x1={a.x + 58} y1={a.y} x2={b.x - 60} y2={b.y}
            stroke={hot ? "#f87171" : "#475569"} strokeWidth={hot ? 2 : 1.2} markerEnd="url(#arrow)" opacity={0.9}>
            <title>{`${e.source} → ${e.target} (${e.count} traces)`}</title>
          </line>
        );
      })}
      {nodes.map((n) => {
        const p = pos[n];
        const isOrigin = n === origin, isHot = hl.has(n);
        return (
          <g key={n} transform={`translate(${p.x - 58}, ${p.y - 16})`}>
            <rect width="116" height="32" rx="8"
              fill={isOrigin ? "rgba(244,63,94,0.18)" : isHot ? "rgba(251,191,36,0.12)" : "rgba(30,41,59,0.9)"}
              stroke={isOrigin ? "#f43f5e" : isHot ? "#f59e0b" : "#334155"} strokeWidth={isOrigin ? 2 : 1} />
            <text x="58" y="20" textAnchor="middle" className="fill-slate-200" style={{ fontSize: 11, fontFamily: "ui-monospace, monospace" }}>
              {n.length > 17 ? n.slice(0, 16) + "…" : n}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
