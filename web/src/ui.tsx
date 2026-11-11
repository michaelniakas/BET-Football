import {
  createContext, Fragment, useContext, useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties,
  type KeyboardEvent, type ReactNode,
} from "react";
import type { ApiError, Schemas } from "./api";
import type { Fmt } from "./format";
import type { Key, Lang, T } from "./i18n";

export type Strategy = Schemas["Strategy"];
export type Book = Schemas["Book"];
export type LeagueId = Schemas["League"];
export type Outcome = Schemas["Outcome"];

/** The bet rule of a strategy: a pick is the outcome with the largest edge at `book` odds, if it clears `edge`. */
export interface Rule {
  edge: number;
  book: Book;
}

export interface Ctx {
  t: T;
  f: Fmt;
  lang: Lang;
  dark: boolean;
  catalog: Schemas["Catalog"];
  s: Strategy;
  setStrategy: (id: Schemas["StrategyId"]) => void;
  rule: Rule;
  setRule: (r: Rule) => void;
}

export const AppCtx = createContext<Ctx | null>(null);

export function useCtx(): Ctx {
  const ctx = useContext(AppCtx);
  if (ctx === null) throw new Error("useCtx outside AppCtx");
  return ctx;
}

/** Stagger index for the entrance animation of `.anim` children. */
export const at = (i: number) => ({ "--i": i }) as CSSProperties;

export const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export function useMedia(query: string): boolean {
  const [on, setOn] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const change = () => setOn(mq.matches);
    mq.addEventListener("change", change);
    return () => mq.removeEventListener("change", change);
  }, [query]);
  return on;
}

export function useDebounced<V>(value: V, ms = 300): V {
  const [v, setV] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(timer);
  }, [value, ms]);
  return v;
}

/** A list offset that belongs to one filter combination: any other `key` reads as the first page. */
export function useOffset(key: string): [number, (offset: number) => void] {
  const [state, setState] = useState({ key, offset: 0 });
  return [state.key === key ? state.offset : 0, (offset) => setState({ key, offset })];
}

/** A translation whose `{name}` placeholders are filled with elements (t() leaves unknown placeholders as they are). */
export function Rich({ k, parts }: { k: Key; parts: Record<string, ReactNode> }) {
  const { t } = useCtx();
  return (
    <>
      {t(k).split(/\{(\w+)\}/).map((x, i) => <Fragment key={i}>{i % 2 ? parts[x] : x}</Fragment>)}
    </>
  );
}

// --------------------------------------------------------------------------- outcomes

const ORDER: Outcome[] = ["H", "D", "A", "O", "U"];

/** Outcomes in display order (home, draw, away / over, under), whatever order the API sent. */
export const ordered = <O extends { outcome: Outcome }>(xs: O[]) =>
  [...xs].sort((a, b) => ORDER.indexOf(a.outcome) - ORDER.indexOf(b.outcome));

/** What a bet on `o` is, in words: "Arsenal to win", "Draw", "Over 2.5 goals". */
export function betText(t: T, o: Outcome, home: string, away: string): string {
  if (o === "H" || o === "A") return t("betWin", { team: o === "H" ? home : away });
  return t(`bet_${o}`);
}

export function initials(team: string): string {
  const words = team.split(/[\s.-]+/).filter((w) => /\p{L}/u.test(w));
  const letters = (w: string) => [...w].filter((c) => /[\p{L}\p{N}]/u.test(c));
  const pick = words.length > 1 ? words.slice(0, 2).map((w) => letters(w)[0] ?? "") : letters(words[0] ?? team).slice(0, 2);
  return pick.join("").toUpperCase();
}

/** Model (thick, labelled) or market (thin) probabilities as one bar per outcome. `hit`: the outcome that happened. */
export function Strip({ ps, hit = -1, market = false }: { ps: number[]; hit?: number; market?: boolean }) {
  const { f } = useCtx();
  return (
    <div className={`strip${ps.length === 2 ? " two" : ""}${market ? " market" : ""}`} aria-hidden="true">
      {ps.map((p, i) => (
        <span key={i} className={`c${i}${i === hit ? " hit" : ""}`}
              style={{ width: `${p * 100}%`, animationDelay: `${i * 90}ms` }}>
          {!market && p >= 0.13 ? f.pct(p, 0) : ""}
        </span>
      ))}
    </div>
  );
}

export function OutcomeLegend() {
  const { t, s } = useCtx();
  const items: [Key, string][] = s.market === "1x2"
    ? [["out_H", "var(--home)"], ["out_D", "var(--draw)"], ["out_A", "var(--away)"]]
    : [["out_O", "var(--home)"], ["out_U", "var(--away)"]];
  return (
    <div className="legend">
      {items.map(([k, c]) => <span key={k}><i style={{ background: c }} />{t(k)}</span>)}
      <span>{t("legendMarket")}</span>
    </div>
  );
}

// --------------------------------------------------------------------------- leagues

// A league the contract adds without an entry here is a type error.
const FLAG: Record<LeagueId, string> = {
  E0: "\u{1F3F4}\u{E0067}\u{E0062}\u{E0065}\u{E006E}\u{E0067}\u{E007F}",       // England (emoji tag sequence)
  E1: "\u{1F3F4}\u{E0067}\u{E0062}\u{E0065}\u{E006E}\u{E0067}\u{E007F}",
  SC0: "\u{1F3F4}\u{E0067}\u{E0062}\u{E0073}\u{E0063}\u{E0074}\u{E007F}",      // Scotland
  D1: "🇩🇪", D2: "🇩🇪", I1: "🇮🇹", I2: "🇮🇹", SP1: "🇪🇸", SP2: "🇪🇸", F1: "🇫🇷", F2: "🇫🇷",
  N1: "🇳🇱", P1: "🇵🇹", B1: "🇧🇪", T1: "🇹🇷", G1: "🇬🇷",
};
// ESPN's league ids (the `logos` of site.api.espn.com/apis/site/v2/sports/soccer/<slug>/scoreboard).
// ponytail: hot-linked from ESPN's CDN, undocumented; a logo that fails to load is hidden. Bundle the
// images if they start moving.
const ESPN_LOGO: Record<LeagueId, number> = {
  E0: 23, E1: 24, D1: 10, D2: 97, I1: 12, I2: 99, SP1: 15, SP2: 107, F1: 9, F2: 96, N1: 11, P1: 14, B1: 6, T1: 18,
  G1: 98, SC0: 45,
};
const NO_DARK_LOGO: readonly LeagueId[] = ["D2", "I2", "G1"];
const logoUrl = (id: LeagueId, dir: string) =>
  `https://a.espncdn.com/combiner/i?img=/i/leaguelogos/soccer/${dir}/${ESPN_LOGO[id]}.png&w=40&h=40&transparent=true`;

export const leagueName = (catalog: Schemas["Catalog"], id: LeagueId) =>
  catalog.leagues.find((l) => l.id === id)?.name ?? id;

/** Flag, logo and name of a league. The flag and logo are decoration: the name carries the meaning. */
export function League({ id }: { id: LeagueId }) {
  const { catalog, dark } = useCtx();
  // The app's theme, not the system's: a <source media="(prefers-color-scheme)"> would ignore the toggle.
  const dir = dark && !NO_DARK_LOGO.includes(id) ? "500-dark" : "500";
  return (
    <span className="league">
      <span aria-hidden="true">{FLAG[id]}</span>
      <img key={dir} src={logoUrl(id, dir)} alt="" width={18} height={18} loading="lazy" referrerPolicy="no-referrer"
           onError={(e) => { e.currentTarget.hidden = true; }} />
      {leagueName(catalog, id)}
    </span>
  );
}

export function LeagueSelect({ value, options, onChange }: {
  value: LeagueId | "";
  options: LeagueId[];
  onChange: (v: LeagueId | "") => void;
}) {
  const { t, catalog } = useCtx();
  return (
    <select aria-label={t("league")} value={value} onChange={(e) => onChange(e.target.value as LeagueId | "")}>
      <option value="">{t("allLeagues", { n: options.length })}</option>
      {options.map((l) => <option key={l} value={l}>{leagueName(catalog, l)}</option>)}
    </select>
  );
}

// --------------------------------------------------------------------------- glossary

export type GlossId = "edge" | "odds" | "roi" | "clv" | "units" | "fav" | "logloss" | "closer" | "calib" | "scenarios";

export const GLOSS: { id: GlossId; title: Key; body: Key; formula?: Key }[] = [
  { id: "edge", title: "g_edge", body: "g_edge_body", formula: "g_edge_formula" },
  { id: "odds", title: "g_odds", body: "g_odds_body" },
  { id: "roi", title: "g_roi", body: "g_roi_body" },
  { id: "clv", title: "g_clv", body: "g_clv_body", formula: "g_clv_formula" },
  { id: "units", title: "g_units", body: "g_units_body" },
  { id: "fav", title: "g_fav", body: "g_fav_body" },
  { id: "logloss", title: "g_logloss", body: "g_logloss_body" },
  { id: "closer", title: "g_closer", body: "g_closer_body" },
  { id: "calib", title: "g_calib", body: "g_calib_body" },
  { id: "scenarios", title: "g_scenarios", body: "g_scenarios_body" },
];

export function GlossBody({ g }: { g: (typeof GLOSS)[number] }) {
  const { t } = useCtx();
  return (
    <>
      {t(g.body).split("\n\n").map((p, i) => <p key={i}>{p}</p>)}
      {g.formula && <div className="formula">{t(g.formula)}</div>}
    </>
  );
}

/** One popover per glossary entry; every `Info` button opens one of them. */
export function GlossPopovers() {
  const { t } = useCtx();
  return GLOSS.map((g) => (
    <div key={g.id} id={`g-${g.id}`} popover="auto" role="dialog" aria-labelledby={`g-${g.id}-h`}>
      <h3 id={`g-${g.id}-h`}>{t(g.title)}</h3>
      <GlossBody g={g} />
      <button type="button" className="pop-close" popoverTarget={`g-${g.id}`} popoverTargetAction="hide"
              aria-label={t("close")}>×</button>
    </div>
  ));
}

export function Info({ id }: { id: GlossId }) {
  const { t } = useCtx();
  const g = GLOSS.find((x) => x.id === id)!;
  return (
    <button type="button" className="i" popoverTarget={`g-${id}`} aria-label={t("whatIs", { x: t(g.title) })}>i</button>
  );
}

// --------------------------------------------------------------------------- controls

/** Single choice as pressed buttons: `seg` (pill group) or `chips`. */
export function Choice<V extends string>({ label, value, options, onChange, look = "seg" }: {
  label: string;
  value: V;
  options: { value: V; label: ReactNode; disabled?: boolean }[];
  onChange: (v: V) => void;
  look?: "seg" | "chips";
}) {
  return (
    <div className={look} role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" className={look === "chips" ? "chip" : undefined} disabled={o.disabled}
                aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Several choices as chips: the selected values, in the order of `options`. */
export function MultiChips<V extends string>({ label, options, value, onChange }: {
  label: string;
  options: { value: V; label: ReactNode }[];
  value: V[];
  onChange: (v: V[]) => void;
}) {
  const toggle = (v: V) =>
    onChange(options.map((o) => o.value).filter((o) => (o === v ? !value.includes(v) : value.includes(o))));
  return (
    <div className="chips" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" className="chip" aria-pressed={value.includes(o.value)}
                onClick={() => toggle(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function MarketSwitch() {
  const { t, catalog, s, setStrategy } = useCtx();
  return (
    <Choice label={t("market")} value={s.id} onChange={setStrategy}
            options={catalog.strategies.map((x) => ({ value: x.id, label: t(`market_${x.market}`) }))} />
  );
}

/** The bet rule's controls (edge threshold, odds source). Shared by every screen that shows bets. */
export function RuleBar() {
  const { t, f, s, rule, setRule } = useCtx();
  const id = useId();
  return (
    <div className="card toolbar">
      <span className="tool">
        <label htmlFor={`${id}-e`}>{t("edgeAtLeast")}</label>
        <input id={`${id}-e`} type="range" min={0} max={0.15} step={0.01} value={rule.edge}
               aria-valuetext={f.pct(rule.edge, 0)} onChange={(e) => setRule({ ...rule, edge: Number(e.target.value) })} />
        <output htmlFor={`${id}-e`}>{f.pct(rule.edge, 0)}</output>
        <Info id="edge" />
      </span>
      <span className="tool">
        <label htmlFor={`${id}-b`}>{t("oddsLabel")}</label>
        <select id={`${id}-b`} value={rule.book} onChange={(e) => setRule({ ...rule, book: e.target.value as Book })}>
          {s.books.map((b) => <option key={b} value={b}>{t(`book_${b}`)}</option>)}
        </select>
        <Info id="odds" />
      </span>
    </div>
  );
}

export function Search({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { t } = useCtx();
  return (
    <input className="search" type="search" placeholder={t("searchTeam")} aria-label={t("searchTeam")} maxLength={64}
           value={value} onChange={(e) => onChange(e.target.value)} />
  );
}

/** WAI-ARIA tabs with a sliding ink. A panel mounts on first visit and then stays mounted (hidden), keeping its state. */
export function Tabs<K extends string>({ tabs, label, i = 0 }: {
  tabs: { id: K; title: string; body: ReactNode }[];
  label: string;
  i?: number;
}) {
  const [active, setActive] = useState<K | undefined>(tabs[0]?.id);
  const [seen, setSeen] = useState<Set<K>>(() => new Set(active ? [active] : []));
  const [ink, setInk] = useState<{ left: number; width: number } | null>(null);
  const list = useRef<HTMLDivElement>(null);
  const id = useId();
  const current = tabs.find((x) => x.id === active)?.id ?? tabs[0]?.id;

  useLayoutEffect(() => {
    const measure = () => {
      const b = list.current?.querySelector<HTMLElement>('[aria-selected="true"]');
      if (b) setInk({ left: b.offsetLeft, width: b.offsetWidth });
    };
    measure();
    window.addEventListener("resize", measure);
    void document.fonts.ready.then(measure);          // the tab widths change when the web font arrives
    return () => window.removeEventListener("resize", measure);
  }, [current, label]);

  const select = (k: K) => {
    setActive(k);
    setSeen((s) => (s.has(k) ? s : new Set(s).add(k)));
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    const n = tabs.findIndex((x) => x.id === current);
    const next = tabs[(n + step + tabs.length) % tabs.length];
    if (!next) return;
    select(next.id);
    document.getElementById(`${id}-tab-${next.id}`)?.focus();
  };
  return (
    <>
      <div ref={list} role="tablist" aria-label={label} className="tabs" onKeyDown={onKey} style={at(i)}>
        {ink && <span className="ink" aria-hidden="true" style={ink} />}
        {tabs.map((x) => (
          <button key={x.id} type="button" role="tab" id={`${id}-tab-${x.id}`} aria-selected={x.id === current}
                  tabIndex={x.id === current ? 0 : -1} aria-controls={seen.has(x.id) || x.id === current ? `${id}-panel-${x.id}` : undefined} onClick={() => select(x.id)}>
            {x.title}
          </button>
        ))}
      </div>
      {tabs.map((x) => (seen.has(x.id) || x.id === current) && (
        <div key={x.id} role="tabpanel" id={`${id}-panel-${x.id}`} aria-labelledby={`${id}-tab-${x.id}`}
             hidden={x.id !== current} className="section anim" style={at(i + 1)}>
          {x.body}
        </div>
      ))}
    </>
  );
}

// --------------------------------------------------------------------------- numbers

/** Counts up from 0 to `value` once per value (instant under reduced motion). */
export function CountUp({ value, show }: { value: number; show: (v: number) => string }) {
  const [v, setV] = useState(() => (reducedMotion() ? value : 0));
  useEffect(() => {
    if (reducedMotion()) {
      setV(value);
      return;
    }
    const t0 = performance.now();
    let raf = 0;
    const step = (now: number) => {
      const k = Math.min(1, (now - t0) / 1100);
      setV(value * (1 - (1 - k) ** 3));
      if (k < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [value]);
  // Screen readers get the final number, not the animation.
  return (
    <>
      <span aria-hidden="true">{show(v)}</span>
      <span className="visually-hidden">{show(value)}</span>
    </>
  );
}

export function Stat({ label, info, value, sub, tone }: {
  label: string;
  info?: GlossId;
  value: ReactNode;
  sub?: ReactNode;
  tone?: number | null;
}) {
  const cls = tone == null || tone === 0 ? "" : tone > 0 ? " pos" : " neg";
  return (
    <div className="stat">
      <span className="stat-label">{label}{info && <Info id={info} />}</span>
      <span className={`stat-value${cls}`}>{value}</span>
      {sub && <span className="stat-sub">{sub}</span>}
    </div>
  );
}

export function Pitch() {
  return (
    <svg className="pitch" viewBox="0 0 1000 400" preserveAspectRatio="xMidYMid slice" aria-hidden="true" fill="none"
         stroke="#fff" strokeWidth="2">
      <rect x="20" y="20" width="960" height="360" /><line x1="500" y1="20" x2="500" y2="380" />
      <circle cx="500" cy="200" r="70" /><circle cx="500" cy="200" r="3" fill="#fff" />
      <rect x="20" y="110" width="120" height="180" /><rect x="20" y="160" width="40" height="80" />
      <rect x="860" y="110" width="120" height="180" /><rect x="940" y="160" width="40" height="80" />
      <path d="M140 160a60 60 0 0 1 0 80M860 160a60 60 0 0 0 0 80" />
    </svg>
  );
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
            {cols.map((c) => <th key={c.label} scope="col" className={c.num ? "num" : undefined}>{c.label}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={rowKey(r, i)}>
              {cols.map((c) => <td key={c.label} className={c.num ? "num" : undefined}>{c.cell(r)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export const matchKey = (r: Schemas["MatchRef"]) => `${r.date}|${r.league}|${r.home}|${r.away}`;

export function Pager({ page, loading, onPage }: {
  page: Schemas["Page"] & { items: unknown[] };
  loading: boolean;
  onPage: (offset: number) => void;
}) {
  const { t } = useCtx();
  return (
    <nav className="li-foot" aria-label={t("pages")}>
      <span className="muted small">
        {t("pageOf", { from: page.offset + 1, to: page.offset + page.items.length, total: page.total })}
      </span>
      <span className="row">
        <button type="button" className="chip" disabled={loading || page.offset === 0}
                onClick={() => onPage(Math.max(0, page.offset - page.limit))}>{t("prev")}</button>
        <button type="button" className="chip" disabled={loading || page.offset + page.items.length >= page.total}
                onClick={() => onPage(page.offset + page.limit)}>{t("next")}</button>
      </span>
    </nav>
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
export function Loaded<D>({ state, children, height = 120 }: {
  state: { data?: D; error?: ApiError; loading: boolean };
  children: (data: D) => ReactNode;
  height?: number;
}) {
  const { t } = useCtx();
  if (state.error) return <ErrorNote error={state.error} t={t} />;
  if (state.data === undefined) {
    return (
      <div className="skeleton" aria-busy="true" style={{ minHeight: height }}>
        <span className="visually-hidden">{t("loading")}</span>
      </div>
    );
  }
  return <div aria-busy={state.loading} className={state.loading ? "stale" : "loaded"}>{children(state.data)}</div>;
}
