import { createContext, useContext, useId, useState, type KeyboardEvent, type ReactNode } from "react";
import type { ApiError, Schemas } from "./api";
import type { Fmt } from "./format";
import type { T } from "./i18n";

export interface Ctx {
  t: T;
  f: Fmt;
  catalog: Schemas["Catalog"];
}

export const AppCtx = createContext<Ctx | null>(null);

export function useCtx(): Ctx {
  const ctx = useContext(AppCtx);
  if (ctx === null) throw new Error("useCtx outside AppCtx");
  return ctx;
}

// --------------------------------------------------------------------------- tables

export interface Column<R> {
  label: string;
  cell: (row: R) => ReactNode;
  num?: boolean;
}

export function Table<R>({ rows, cols, rowKey, caption }: {
  rows: R[];
  cols: Column<R>[];
  rowKey: (row: R, i: number) => string;
  caption?: string;
}) {
  return (
    <div className="table-wrap">
      <table>
        {caption && <caption>{caption}</caption>}
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c.label} scope="col" className={c.num ? "num" : undefined}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={rowKey(r, i)}>
              {cols.map((c) => (
                <td key={c.label} className={c.num ? "num" : undefined}>
                  {c.cell(r)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The match columns every table starts with. */
export function matchCols<R extends Schemas["MatchRef"]>(t: T, f: Fmt): Column<R>[] {
  return [
    { label: t("colDate"), cell: (r) => f.date(r.date) },
    { label: t("colLeague"), cell: (r) => r.league },
    { label: t("colHome"), cell: (r) => r.home },
    { label: t("colAway"), cell: (r) => r.away },
  ];
}

export const matchKey = (r: Schemas["MatchRef"]) => `${r.date}|${r.league}|${r.home}|${r.away}`;

// --------------------------------------------------------------------------- controls

/** Every panel stays mounted (hidden when inactive), so each tab keeps its controls' state, as on Streamlit. */
export function Tabs<K extends string>({ tabs, label }: {
  tabs: { id: K; title: string; body: ReactNode }[];
  label: string;
}) {
  const [active, setActive] = useState<K | undefined>(tabs[0]?.id);
  const id = useId();
  const current = tabs.find((x) => x.id === active)?.id ?? tabs[0]?.id;
  // WAI-ARIA tabs: one tab stop, arrow keys move between tabs.
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    const i = tabs.findIndex((x) => x.id === current);
    const next = tabs[(i + step + tabs.length) % tabs.length];
    if (!next) return;
    setActive(next.id);
    document.getElementById(`${id}-tab-${next.id}`)?.focus();
  };
  return (
    <div>
      <div role="tablist" aria-label={label} className="tabs" onKeyDown={onKey}>
        {tabs.map((x) => (
          <button key={x.id} role="tab" id={`${id}-tab-${x.id}`} aria-selected={x.id === current}
                  tabIndex={x.id === current ? 0 : -1}
                  aria-controls={`${id}-panel-${x.id}`} onClick={() => setActive(x.id)}>
            {x.title}
          </button>
        ))}
      </div>
      {tabs.map((x) => (
        <div key={x.id} role="tabpanel" id={`${id}-panel-${x.id}`} aria-labelledby={`${id}-tab-${x.id}`}
             hidden={x.id !== current}>
          {x.body}
        </div>
      ))}
    </div>
  );
}

export function Slider({ label, value, min, max, step, onChange, show, help }: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  show: (v: number) => string;
  help?: string;
}) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>
        {label} <output htmlFor={id}>{show(value)}</output>
      </label>
      <input id={id} type="range" min={min} max={max} step={step} value={value} aria-valuetext={show(value)}
             aria-describedby={help ? `${id}-help` : undefined}
             onChange={(e) => onChange(Number(e.target.value))} />
      {help && <small id={`${id}-help`}>{help}</small>}
    </div>
  );
}

export function Select<V extends string>({ label, value, options, onChange, help }: {
  label: string;
  value: V;
  options: { value: V; label: string }[];
  onChange: (v: V) => void;
  help?: string;
}) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value as V)}
              aria-describedby={help ? `${id}-help` : undefined}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
      {help && <small id={`${id}-help`}>{help}</small>}
    </div>
  );
}

/** Checkbox group: the selected values, in the order of `options`. */
export function MultiCheck<V extends string>({ legend, options, value, onChange, help }: {
  legend: string;
  options: { value: V; label: string }[];
  value: V[];
  onChange: (v: V[]) => void;
  help?: string;
}) {
  const id = useId();
  const toggle = (v: V) =>
    onChange(options.map((o) => o.value).filter((o) => (o === v ? !value.includes(v) : value.includes(o))));
  return (
    <fieldset className="field checks" aria-describedby={help ? `${id}-help` : undefined}>
      <legend>{legend}</legend>
      {options.map((o) => (
        <label key={o.value}>
          <input type="checkbox" checked={value.includes(o.value)} onChange={() => toggle(o.value)} /> {o.label}
        </label>
      ))}
      {help && <small id={`${id}-help`}>{help}</small>}
    </fieldset>
  );
}

export function Metric({ label, value, help }: { label: string; value: string; help?: string }) {
  return (
    <div className="metric">
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
      {help && <small>{help}</small>}
    </div>
  );
}

// --------------------------------------------------------------------------- messages

export function Note({ kind = "info", children }: { kind?: "info" | "warn"; children: ReactNode }) {
  return <p className={`note note-${kind}`}>{children}</p>;
}

type OddsError = Extract<Schemas["ErrorCode"], `odds_api_${string}`>;
const ODDS_ERRORS: readonly string[] = [
  "odds_api_not_configured", "odds_api_quota_exhausted", "odds_api_unauthorized", "odds_api_unavailable",
] satisfies OddsError[];
const isOddsError = (code: string): code is OddsError => ODDS_ERRORS.includes(code);

export function ErrorNote({ error, t }: { error: ApiError; t: T }) {
  let text: string;
  if ("network" in error) text = t("errNetwork");
  else if (error.code === "validation_failed") text = t("errValidation");
  else if (isOddsError(error.code)) text = t(`err_${error.code}`);
  else text = t("errGeneric", { status: error.status });     // includes codes newer than this client
  return <p role="alert" className="note note-error">{text}</p>;
}

/** Renders the loading / error state of a request, or `children(data)`. */
export function Loaded<D>({ state, children }: {
  state: { data?: D; error?: ApiError; loading: boolean };
  children: (data: D) => ReactNode;
}) {
  const { t } = useCtx();
  if (state.error) return <ErrorNote error={state.error} t={t} />;
  if (state.data === undefined) return <p aria-busy="true">{t("loading")}</p>;
  return <div aria-busy={state.loading} className={state.loading ? "stale" : undefined}>{children(state.data)}</div>;
}
