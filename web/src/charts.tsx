import {
  Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { Schemas } from "./api";
import { useCtx } from "./ui";

// Mid-tone colours that keep contrast on both the light and the dark background.
const C1 = "#2f6fdf";
const C2 = "#d9822b";

export function CumulativeChart({ series }: { series: Schemas["ResultsSummary"]["series"] }) {
  const { t, f } = useCtx();
  const data = series.map((p) => ({
    date: p.date,
    profit: p.cumulative_profit,
    clv: p.mean_clv === null ? null : 100 * p.mean_clv,
  }));
  return (
    <figure className="chart" aria-label={`${t("chartProfit")} / ${t("chartClv")}`}>
      <ResponsiveContainer width="100%" height={340}>
        <LineChart data={data} margin={{ top: 10, right: 10, bottom: 10, left: 0 }}>
          <CartesianGrid strokeOpacity={0.2} />
          <XAxis dataKey="date" tickFormatter={f.date} minTickGap={24} />
          <YAxis yAxisId="profit" tickFormatter={(v: number) => f.num(v, 1)} />
          <YAxis yAxisId="clv" orientation="right" tickFormatter={(v: number) => f.num(v, 1)} />
          <Tooltip labelFormatter={(d) => f.date(String(d))} formatter={(v) => f.num(Number(v), 2)} />
          <Legend verticalAlign="top" />
          <Line yAxisId="profit" dataKey="profit" name={t("chartProfit")} stroke={C1} dot={false} strokeWidth={2} />
          <Line yAxisId="clv" dataKey="clv" name={t("chartClv")} stroke={C2} dot={false} strokeWidth={2}
                connectNulls />
        </LineChart>
      </ResponsiveContainer>
    </figure>
  );
}

export function PnlHistogram({ h }: { h: Schemas["Scenario"]["pnl_histogram"] }) {
  const { t, f } = useCtx();
  const e = h.bin_edges;
  const data = h.probabilities.map((p, i) => ({ mid: ((e[i] ?? 0) + (e[i + 1] ?? 0)) / 2, p }));
  return (
    <figure className="chart" aria-label={`${t("histX")} / ${t("histY")}`}>
      <ResponsiveContainer width="100%" height={280}>
        <BarChart data={data} margin={{ top: 10, right: 10, bottom: 20, left: 0 }} barCategoryGap="5%">
          <CartesianGrid strokeOpacity={0.2} vertical={false} />
          <XAxis dataKey="mid" type="number" domain={[e[0] ?? 0, e[e.length - 1] ?? 0]}
                 tickFormatter={(v: number) => f.num(v, 0)}
                 label={{ value: t("histX"), position: "insideBottom", offset: -10 }} />
          <YAxis tickFormatter={(v: number) => f.pct(v, 0)} />
          <Tooltip labelFormatter={(v) => f.eur(Number(v))} formatter={(v) => [f.pct(Number(v), 1), t("histY")]} />
          <Bar dataKey="p" fill={C1} />
          <ReferenceLine x={0} stroke={C2} strokeDasharray="4 4" />
        </BarChart>
      </ResponsiveContainer>
    </figure>
  );
}
