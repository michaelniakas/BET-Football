import { useEffect, useId, useState } from "react";
import { api, useApi, type Schemas } from "../api";
import { PICK_LABELS } from "../format";
import { Loaded, matchCols, matchKey, Metric, MultiCheck, Note, Select, Slider, Table, Tabs, useCtx,
         type Column } from "../ui";

type Strategy = Schemas["Strategy"];
type Book = Schemas["Book"];
type Fixture = Schemas["SettledFixture"];
type Status = Schemas["BetStatus"];
const STATUSES = ["won", "lost", "no_bet"] as const satisfies readonly Status[];
const PAGE = 50;
const SEARCH_DELAY_MS = 300;

export function History() {
  const { t, catalog } = useCtx();
  return (
    <>
      <h1>{t("historyTitle")}</h1>
      <p className="lead">{t("historyIntro")}</p>
      <Tabs label={t("historyTitle")}
            tabs={catalog.strategies.map((s) => ({
              id: s.id, title: t(`strategy_${s.id}`), body: <StrategyHistory s={s} />,
            }))} />
    </>
  );
}

function Input({ label, type, value, onChange, help, maxLength }: {
  label: string;
  type: "search" | "date";
  value: string;
  onChange: (v: string) => void;
  help?: string;
  maxLength?: number;
}) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input id={id} type={type} value={value} maxLength={maxLength}
             aria-describedby={help ? `${id}-help` : undefined} onChange={(e) => onChange(e.target.value)} />
      {help && <small id={`${id}-help`}>{help}</small>}
    </div>
  );
}

function StrategyHistory({ s }: { s: Strategy }) {
  const { t, f } = useCtx();
  const [edge, setEdge] = useState(s.default_min_edge);
  const [book, setBook] = useState<Book>(s.books.includes("avg") ? "avg" : (s.books[0] ?? "avg"));
  const [leagues, setLeagues] = useState<Schemas["League"][]>([]);
  const [typed, setTyped] = useState("");
  const [team, setTeam] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [status, setStatus] = useState<Status | "all">("all");

  // The search waits for a pause in typing; every other filter applies at once.
  useEffect(() => {
    const timer = window.setTimeout(() => setTeam(typed.trim()), SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [typed]);

  const where = {
    book, min_edge: edge,
    league: leagues.length ? leagues : undefined,
    team: team || undefined, date_from: from || undefined, date_to: to || undefined,
  };
  const whereKey = JSON.stringify(where);
  // The offset belongs to one list: with any other filter the table is back on its first page.
  const listKey = `${whereKey}|${status}`;
  const [at, setAt] = useState({ list: listKey, offset: 0 });
  if (at.list !== listKey) setAt({ list: listKey, offset: 0 });      // and stays there when the filter comes back
  const offset = at.list === listKey ? at.offset : 0;

  const path = { strategy: s.id };
  const summary = useApi((signal) => api.GET("/api/v1/strategies/{strategy}/results/summary", {
    params: { path, query: where }, signal,
  }), [s.id, whereKey]);
  const fixtures = useApi((signal) => api.GET("/api/v1/strategies/{strategy}/results/fixtures", {
    params: { path, query: { ...where, bet_status: status === "all" ? undefined : [status], limit: PAGE, offset } },
    signal,
  }), [s.id, listKey, offset]);

  if (s.ledger.matches === 0) return <Note>{t("ledgerEmpty")}</Note>;

  type Metrics = Schemas["Metrics"];
  const metricCols = <R extends Metrics>(): Column<R>[] => [
    { label: t("mBets"), cell: (x) => x.bets, num: true },
    { label: t("colProfit"), cell: (x) => f.num(x.profit, 2, true), num: true },
    { label: t("mRoi"), cell: (x) => f.pct(x.roi, 1, true), num: true },
    { label: t("colRoiSe"), cell: (x) => f.pct(x.roi_se, 1), num: true },
    { label: t("colHit"), cell: (x) => f.pct(x.hit_rate, 1), num: true },
    { label: t("mClv"), cell: (x) => f.pct(x.clv, 2, true), num: true },
  ];
  // What each source gave the result that happened.
  const prob = (r: Fixture, k: "p_model" | "p_market" | "p_close") =>
    f.pct(r.outcomes.find((o) => o.outcome === r.result)?.[k]);

  return (
    <section>
      <div className="controls">
        <Slider label={t("minEdge")} value={edge} min={0} max={0.15} step={0.01} onChange={setEdge}
                show={(v) => f.pct(v, 0)} />
        <Select label={t("oddsSource")} value={book} onChange={setBook}
                options={s.books.map((b) => ({ value: b, label: t(`book_${b}`) }))} />
        <Input label={t("teamSearch")} type="search" value={typed} onChange={setTyped} maxLength={64}
               help={t("teamSearchHelp")} />
        <Input label={t("dateFrom")} type="date" value={from} onChange={setFrom} />
        <Input label={t("dateTo")} type="date" value={to} onChange={setTo} />
      </div>
      <MultiCheck legend={`${t("leagues")} (${leagues.length ? leagues.length : t("all")})`}
                  options={s.leagues.map((l) => ({ value: l, label: l }))} value={leagues} onChange={setLeagues} />

      <Loaded state={summary}>
        {(sum) => {
          if (sum.settled_matches === 0) return <Note>{t("historyNone")}</Note>;
          const m = sum.metrics;
          return (
            <>
              {m === null ? <Note>{t("settledNoPicks", { n: sum.settled_matches })}</Note> : (
                <>
                  <div className="metrics">
                    <Metric label={t("mMatches")} value={String(sum.settled_matches)} />
                    <Metric label={t("mBets")} value={String(m.bets)} />
                    <Metric label={t("mProfit")} value={t("units", { v: f.num(m.profit, 2, true) })} />
                    <Metric label={t("mRoi")} value={f.pct(m.roi, 1, true)}
                            help={m.roi_se === null ? undefined : t("mRoiHelp", { se: f.pct(m.roi_se, 1) })} />
                    <Metric label={t("mClv")} value={f.pct(m.clv, 2, true)} />
                    <Metric label={t("mBeatClose")} value={f.pct(m.beat_close_rate, 0)} />
                  </div>
                  <Table caption={t("byLeague")} rows={sum.by_league} rowKey={(x) => x.league} cols={[
                    { label: t("colLeague"), cell: (x) => x.league },
                    ...metricCols<Schemas["LeagueMetrics"]>(),
                  ]} />
                  <Table caption={t("byEdge")} rows={sum.by_edge} rowKey={(x) => String(x.min_edge)} cols={[
                    { label: t("colEdge"),
                      cell: (x) => x.max_edge === null
                        ? `> ${f.pct(x.min_edge, 0)}` : `${f.pct(x.min_edge, 0)} – ${f.pct(x.max_edge, 0)}` },
                    ...metricCols<Schemas["EdgeBucketMetrics"]>(),
                  ]} />
                </>
              )}

              <h2>{t("settledFixtures")}</h2>
              <div className="narrow">
                <Select label={t("betStatus")} value={status} onChange={setStatus}
                        options={[{ value: "all", label: t("status_all") },
                                  ...STATUSES.map((v) => ({ value: v, label: t(`status_${v}`) }))]} />
              </div>
              <p className="caption">{t("resultProbHelp")}</p>
              <Loaded state={fixtures}>
                {(page) => page.total === 0 ? <Note>{t("historyNone")}</Note> : (
                  <>
                    <Table rows={page.items} rowKey={matchKey} cols={[
                      ...matchCols<Fixture>(t, f),
                      { label: t("colResult"), cell: (r) => PICK_LABELS[r.result] },
                      { label: t("colPModel"), cell: (r) => prob(r, "p_model"), num: true },
                      { label: t("colPMarket"), cell: (r) => prob(r, "p_market"), num: true },
                      { label: t("colPClose"), cell: (r) => prob(r, "p_close"), num: true },
                      { label: t("colPick"), cell: (r) => (r.bet ? PICK_LABELS[r.bet.pick] : "–") },
                      { label: t("colOdds"), cell: (r) => f.num(r.bet?.odds), num: true },
                      { label: t("colEdge"), cell: (r) => f.pct(r.bet?.edge, 1, true), num: true },
                      { label: t("betStatus"), cell: (r) => t(`status_${r.bet_status}`) },
                      { label: t("colProfit"), cell: (r) => f.num(r.bet?.profit, 2, true), num: true },
                      { label: t("colClv"), cell: (r) => f.pct(r.bet?.clv, 1, true), num: true },
                    ]} />
                    <nav className="pager" aria-label={t("pages")}>
                      <button type="button" disabled={fixtures.loading || page.offset === 0}
                              onClick={() => setAt({ list: listKey, offset: Math.max(0, offset - PAGE) })}>
                        {t("prev")}
                      </button>
                      <span>
                        {t("pageOf", { from: page.offset + 1, to: page.offset + page.items.length, total: page.total })}
                      </span>
                      <button type="button"
                              disabled={fixtures.loading || page.offset + page.items.length >= page.total}
                              onClick={() => setAt({ list: listKey, offset: offset + PAGE })}>
                        {t("next")}
                      </button>
                    </nav>
                  </>
                )}
              </Loaded>
            </>
          );
        }}
      </Loaded>
    </section>
  );
}
