import { useId, useState, type PointerEvent } from "react";
import type { Schemas } from "./api";
import { useCtx } from "./ui";

/** A round step that splits `span` into about `parts` intervals (1, 2 or 5 × a power of ten). */
function niceStep(span: number, parts: number): number {
  const raw = span / parts || 1;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  return (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
}

/** SVG ids can't hold the characters React's useId uses. */
const useSvgId = () => `c${useId().replace(/[^\w-]/g, "")}`;

export function ProfitChart({ series }: { series: Schemas["ResultsSummary"]["series"] }) {
  const { t, f } = useCtx();
  const [hover, setHover] = useState<number | null>(null);
  const fill = useSvgId();
  const n = series.length;
  const last = series[n - 1];
  if (!last) return null;

  const W = 760, H = 240, L = 44, R = 14, T = 14, B = 28;
  const ys = series.map((p) => p.cumulative_profit);
  const lo = Math.min(0, ...ys), hi = Math.max(0, ...ys);
  const step = niceStep(hi - lo, 4);
  const y0 = Math.floor(lo / step) * step;
  const y1 = Math.max(Math.ceil(hi / step) * step, y0 + step);
  const x = (i: number) => (n === 1 ? (L + W - R) / 2 : L + (i / (n - 1)) * (W - L - R));
  const y = (v: number) => T + ((y1 - v) / (y1 - y0)) * (H - T - B);
  const line = ys.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
  const ticks: number[] = [];
  for (let v = y0; v <= y1 + step / 2; v += step) ticks.push(Math.round(v / step) * step);
  const digits = step < 1 ? 1 : 0;

  const onMove = (e: PointerEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * W;
    setHover(n === 1 ? 0 : Math.max(0, Math.min(n - 1, Math.round(((px - L) / (W - L - R)) * (n - 1)))));
  };
  const h = hover === null ? null : series[hover];

  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" onPointerMove={onMove} onPointerLeave={() => setHover(null)}
           aria-label={t("profitChartAria", { n, v: f.num(last.cumulative_profit, 2, true) })}>
        <defs>
          <linearGradient id={fill} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor="var(--accent)" stopOpacity=".28" />
            <stop offset="1" stopColor="var(--accent)" stopOpacity="0" />
          </linearGradient>
        </defs>
        {ticks.map((v) => (
          <g key={v}>
            <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke="var(--line)"
                  {...(v === 0 ? { strokeWidth: 1.5 } : { strokeDasharray: "3 4" })} />
            <text x={L - 8} y={y(v) + 4} textAnchor="end">{f.num(v, digits, true)}</text>
          </g>
        ))}
        <path d={`${line} L${x(n - 1)} ${y(y0)} L${x(0)} ${y(y0)} Z`} fill={`url(#${fill})`}
              style={{ animation: "rise 1s .6s both" }} />
        <path className="line-draw" pathLength={1} style={{ ["--len" as string]: 1 }} d={line} fill="none"
              stroke="var(--accent)" strokeWidth={3} strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={x(n - 1)} cy={y(last.cumulative_profit)} r={6} fill="var(--accent)" stroke="var(--surface)"
                strokeWidth={3} style={{ animation: "rise .4s 1.6s both" }} />
        {hover !== null && (
          <g aria-hidden="true">
            <line x1={x(hover)} x2={x(hover)} y1={T} y2={H - B} stroke="var(--muted)" strokeDasharray="2 3" />
            <circle cx={x(hover)} cy={y(ys[hover] ?? 0)} r={5} fill="var(--surface)" stroke="var(--accent)" strokeWidth={2.5} />
          </g>
        )}
        <text x={L} y={H - 6}>{f.date(series[0]!.date)}</text>
        <text x={W - R} y={H - 6} textAnchor="end">{f.date(last.date)}</text>
      </svg>
      {h && hover !== null && (
        <div className="tip" aria-hidden="true"
             style={{ left: `${(x(hover) / W) * 100}%`, top: `${(y(h.cumulative_profit) / H) * 100}%` }}>
          {t("tipBet", { i: hover + 1, date: f.date(h.date) })} · <b>{f.num(h.cumulative_profit, 2, true)}</b>
        </div>
      )}
    </div>
  );
}

/** Each bin as a dot at (what the model said, how often it happened); on the diagonal = taken at face value. */
export function Calibration({ bins }: { bins: Schemas["CalibrationBin"][] }) {
  const { t, f } = useCtx();
  const S = 300, P = 56;                              // P: room for the tick labels and the rotated title
  const sx = (v: number) => P + v * (S - P - 10);
  const sy = (v: number) => S - P - v * (S - P - 10);
  const maxN = Math.max(1, ...bins.map((b) => b.n));
  return (
    <div className="chart">
      <svg viewBox={`0 0 ${S} ${S}`} role="img" aria-label={t("calibAria")} style={{ maxWidth: 380, margin: "0 auto" }}>
        {[0, 0.25, 0.5, 0.75, 1].map((v) => (
          <g key={v}>
            <line x1={sx(v)} x2={sx(v)} y1={sy(0)} y2={sy(1)} stroke="var(--line)" strokeDasharray="3 4" />
            <line x1={sx(0)} x2={sx(1)} y1={sy(v)} y2={sy(v)} stroke="var(--line)" strokeDasharray="3 4" />
            <text x={sx(v)} y={S - P + 18} textAnchor="middle">{f.pct(v, 0)}</text>
            <text x={P - 8} y={sy(v) + 4} textAnchor="end">{f.pct(v, 0)}</text>
          </g>
        ))}
        <line className="line-draw" pathLength={1} style={{ ["--len" as string]: 1 }} x1={sx(0)} y1={sy(0)} x2={sx(1)}
              y2={sy(1)} stroke="var(--muted)" strokeWidth={1.5} />
        {bins.map((b, i) => (
          <circle key={b.min_p} cx={sx(b.mean_predicted)} cy={sy(b.observed)} r={4 + 10 * Math.sqrt(b.n / maxN)}
                  fill="var(--accent)" fillOpacity={0.8} stroke="var(--surface)" strokeWidth={2}
                  style={{ animation: `rise .5s ${0.4 + i * 0.08}s both` }}>
            <title>{t("calibDot", { said: f.pct(b.mean_predicted, 0), seen: f.pct(b.observed, 0), n: b.n })}</title>
          </circle>
        ))}
        <text x={sx(0.5)} y={S - 4} textAnchor="middle">{t("calibX")}</text>
        <text x={10} y={sy(0.5)} textAnchor="middle" transform={`rotate(-90 10 ${sy(0.5)})`}>{t("calibY")}</text>
      </svg>
    </div>
  );
}

/** The simulated profit/loss distribution of one scenario: losses red, profits green, zero dashed. */
export function PnlHistogram({ h }: { h: Schemas["Scenario"]["pnl_histogram"] }) {
  const { t, f } = useCtx();
  const e = h.bin_edges;
  const lo = e[0] ?? 0, hi = e[e.length - 1] ?? 1;
  const W = 760, H = 220, L = 44, R = 14, T = 10, B = 30;
  const top = Math.max(...h.probabilities, 1e-9);
  const yStep = niceStep(top, 4);
  const yTop = Math.ceil(top / yStep) * yStep;
  const x = (v: number) => L + ((v - lo) / (hi - lo || 1)) * (W - L - R);
  const y = (p: number) => T + (1 - p / yTop) * (H - T - B);
  const xStep = niceStep(hi - lo, 6);
  const xTicks: number[] = [];
  for (let v = Math.ceil(lo / xStep) * xStep; v <= hi; v += xStep) xTicks.push(v);
  const yTicks: number[] = [];
  for (let p = 0; p <= yTop + yStep / 2; p += yStep) yTicks.push(p);
  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t("histAria")}>
        {yTicks.map((p) => (
          <g key={p}>
            <line x1={L} x2={W - R} y1={y(p)} y2={y(p)} stroke="var(--line)" strokeDasharray={p ? "3 4" : undefined} />
            <text x={L - 8} y={y(p) + 4} textAnchor="end">{f.pct(p, 0)}</text>
          </g>
        ))}
        {h.probabilities.map((p, i) => {
          const a = e[i] ?? 0, b = e[i + 1] ?? 0;
          return (
            <rect key={i} x={x(a) + 0.5} width={Math.max(0, x(b) - x(a) - 1)} y={y(p)} height={y(0) - y(p)} rx={2}
                  fill={(a + b) / 2 < 0 ? "var(--loss)" : "var(--win)"} fillOpacity={0.85}
                  style={{ transformOrigin: `0 ${y(0)}px`, transformBox: "view-box", animation: `rise .5s ${i * 0.01}s both` }}>
              <title>{t("histBar", { from: f.eur(a), to: f.eur(b), p: f.pct(p, 1) })}</title>
            </rect>
          );
        })}
        {lo < 0 && hi > 0 && (
          <line x1={x(0)} x2={x(0)} y1={T} y2={H - B} stroke="var(--ink)" strokeDasharray="4 4" strokeWidth={1.5} />
        )}
        {xTicks.map((v) => <text key={v} x={x(v)} y={H - 10} textAnchor="middle">{f.num(v, 0, true)} €</text>)}
      </svg>
    </div>
  );
}
