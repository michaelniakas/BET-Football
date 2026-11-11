import { useState } from "react";
import { api, useApi, type Schemas } from "../api";
import { CumulativeChart } from "../charts";
import { PICK_LABELS } from "../format";
import { League, Loaded, matchCols, matchKey, Metric, MultiCheck, Note, Select, Slider, Table, Tabs,
         useCtx } from "../ui";

type Strategy = Schemas["Strategy"];
type Book = Schemas["Book"];
// ponytail: one page of 500 upcoming fixtures / settled bets; the ledgers hold ~150 a week. Paginate if they grow.
const ALL = 500;

export function PaperTrading() {
  const { t, catalog } = useCtx();
  return (
    <>
      <h1>{t("paperTitle")}</h1>
      <p className="lead">{t("paperIntro")}</p>
      <Tabs label={t("paperTitle")}
            tabs={catalog.strategies.map((s) => ({
              id: s.id, title: t(`strategy_${s.id}`), body: <StrategyTab s={s} />,
            }))} />
    </>
  );
}

function StrategyTab({ s }: { s: Strategy }) {
  const { t, f } = useCtx();
  const [edge, setEdge] = useState(s.default_min_edge);
  const [book, setBook] = useState<Book>(s.books.includes("avg") ? "avg" : (s.books[0] ?? "avg"));

  if (s.ledger.matches === 0) return <Note>{t("ledgerEmpty")}</Note>;

  return (
    <section>
      <div className="controls">
        <Slider label={t("minEdge")} value={edge} min={0} max={0.15} step={0.01} onChange={setEdge}
                show={(v) => f.pct(v, 0)} />
        <Select label={t("oddsSource")} value={book} onChange={setBook}
                options={s.books.map((b) => ({ value: b, label: t(`book_${b}`) }))} />
      </div>
      {s.ledger.last_logged_at && (
        <p className="caption">{t("lastUpdate", { at: f.dateTime(s.ledger.last_logged_at), n: s.ledger.matches })}</p>
      )}
      <Picks s={s} book={book} edge={edge} />
      <Predictions s={s} book={book} />
      <Results s={s} book={book} edge={edge} />
    </section>
  );
}

const path = (s: Strategy) => ({ strategy: s.id });

function Picks({ s, book, edge }: { s: Strategy; book: Book; edge: number }) {
  const { t, f } = useCtx();
  const picks = useApi((signal) => api.GET("/api/v1/strategies/{strategy}/picks", {
    params: { path: path(s), query: { book, min_edge: edge, limit: ALL } }, signal,
  }), [s.id, book, edge]);
  return (
    <>
      <h2>{t("upcomingPicks")}</h2>
      <Loaded state={picks}>
        {(page) => page.items.length === 0 ? <Note>{t("noPicks")}</Note> : (
          <Table rows={page.items} rowKey={matchKey} cols={[
            ...matchCols<Schemas["Pick"]>(t, f),
            { label: t("colPick"), cell: (r) => PICK_LABELS[r.pick] },
            { label: t("colOdds"), cell: (r) => f.num(r.odds), num: true },
            { label: t("colPModel"), cell: (r) => f.pct(r.p_model), num: true },
            { label: t("colEdge"), cell: (r) => f.pct(r.edge, 1, true), num: true },
          ]} />
        )}
      </Loaded>
    </>
  );
}

function Predictions({ s, book }: { s: Strategy; book: Book }) {
  const { t, f } = useCtx();
  const [leagues, setLeagues] = useState<Schemas["League"][]>([]);
  const preds = useApi((signal) => api.GET("/api/v1/strategies/{strategy}/predictions", {
    params: { path: path(s), query: { book, limit: ALL } }, signal,
  }), [s.id, book]);
  return (
    <>
      <h2>{t("allPredictions")}</h2>
      <Loaded state={preds}>
        {(page) => {
          if (page.items.length === 0) return <Note>{t("noUpcoming")}</Note>;
          const present = [...new Set(page.items.map((p) => p.league))].sort();
          const rows = leagues.length ? page.items.filter((p) => leagues.includes(p.league)) : page.items;
          const outcomes = page.items[0]?.outcomes.map((o) => o.outcome) ?? [];
          const prob = (r: Schemas["Prediction"], o: string, k: "p_model" | "p_market") =>
            f.pct(r.outcomes.find((x) => x.outcome === o)?.[k]);
          return (
            <>
              <MultiCheck legend={`${t("leagues")} (${leagues.length ? leagues.length : t("all")})`}
                          options={present.map((l) => ({ value: l, label: <League id={l} /> }))} value={leagues}
                          onChange={setLeagues} />
              <p className="caption">{t("predictionsHelp")}</p>
              {page.total > page.items.length && (
                <p className="caption">{t("firstOf", { n: page.items.length, total: page.total })}</p>
              )}
              <Table rows={rows} rowKey={matchKey} cols={[
                ...matchCols<Schemas["Prediction"]>(t, f),
                ...outcomes.flatMap((o) => [
                  { label: t("colModel", { o: PICK_LABELS[o] ?? o }), cell: (r: Schemas["Prediction"]) => prob(r, o, "p_model"), num: true },
                  { label: t("colMarket", { o: PICK_LABELS[o] ?? o }), cell: (r: Schemas["Prediction"]) => prob(r, o, "p_market"), num: true },
                ]),
                { label: t("colBest"), cell: (r) => (r.best ? PICK_LABELS[r.best.outcome] : "–") },
                { label: t("colEdge"), cell: (r) => f.pct(r.best?.edge, 1, true), num: true },
              ]} />
            </>
          );
        }}
      </Loaded>
    </>
  );
}

function Results({ s, book, edge }: { s: Strategy; book: Book; edge: number }) {
  const { t, f } = useCtx();
  const summary = useApi((signal) => api.GET("/api/v1/strategies/{strategy}/results/summary", {
    params: { path: path(s), query: { book, min_edge: edge } }, signal,
  }), [s.id, book, edge]);
  return (
    <>
      <h2>{t("results")}</h2>
      <Loaded state={summary}>
        {(sum) => {
          if (sum.settled_matches === 0) return <Note>{t("noSettled")}</Note>;
          const m = sum.metrics;
          return (
            <>
              {m === null ? <Note>{t("settledNoPicks", { n: sum.settled_matches })}</Note> : (
                <>
                  <div className="metrics">
                    <Metric label={t("mBets")} value={String(m.bets)} />
                    <Metric label={t("mProfit")} value={t("units", { v: f.num(m.profit, 2, true) })} />
                    <Metric label={t("mRoi")} value={f.pct(m.roi, 1, true)}
                            help={m.roi_se === null ? undefined : t("mRoiHelp", { se: f.pct(m.roi_se, 1) })} />
                    <Metric label={t("mClv")} value={f.pct(m.clv, 2, true)} />
                    <Metric label={t("mBeatClose")} value={f.pct(m.beat_close_rate, 0)} />
                  </div>
                  <CumulativeChart series={sum.series} />
                  <details>
                    <summary>{t("allSettled")}</summary>
                    <SettledBets s={s} book={book} edge={edge} />
                  </details>
                </>
              )}
              <details>
                <summary>{t("qualityAndSweep")}</summary>
                <Quality s={s} book={book} />
              </details>
            </>
          );
        }}
      </Loaded>
    </>
  );
}

function SettledBets({ s, book, edge }: { s: Strategy; book: Book; edge: number }) {
  const { t, f } = useCtx();
  const bets = useApi((signal) => api.GET("/api/v1/strategies/{strategy}/results/bets", {
    params: { path: path(s), query: { book, min_edge: edge, limit: ALL } }, signal,
  }), [s.id, book, edge]);
  return (
    <Loaded state={bets}>
      {(page) => (
        <>
        {page.total > page.items.length && <p className="caption">{t("firstOf", { n: page.items.length, total: page.total })}</p>}
        <Table rows={page.items} rowKey={matchKey} cols={[
          ...matchCols<Schemas["SettledBet"]>(t, f),
          { label: t("colPick"), cell: (r) => PICK_LABELS[r.pick] },
          { label: t("colOdds"), cell: (r) => f.num(r.odds), num: true },
          { label: t("colPModel"), cell: (r) => f.pct(r.p_model), num: true },
          { label: t("colEdge"), cell: (r) => f.pct(r.edge, 1, true), num: true },
          { label: t("colResult"), cell: (r) => PICK_LABELS[r.result] },
          { label: t("colProfit"), cell: (r) => f.num(r.profit, 2, true), num: true },
          { label: t("colClv"), cell: (r) => f.pct(r.clv, 1, true), num: true },
        ]} />
        </>
      )}
    </Loaded>
  );
}

function Quality({ s, book }: { s: Strategy; book: Book }) {
  const { t, f } = useCtx();
  const report = useApi((signal) => api.GET("/api/v1/strategies/{strategy}/results/probability-report", {
    params: { path: path(s) }, signal,
  }), [s.id]);
  const sweep = useApi((signal) => api.GET("/api/v1/strategies/{strategy}/results/edge-sweep", {
    params: { path: path(s), query: { book } }, signal,
  }), [s.id, book]);
  type Row = Schemas["EdgeSweepRow"];
  return (
    <>
      <Loaded state={report}>
        {(r) => (
          <Table caption={t("probabilityReport", { n: r.n })} rows={r.rows} rowKey={(x) => x.source} cols={[
            { label: t("colSource"), cell: (x) => t(`source_${x.source}`) },
            { label: t("colLogLoss"), cell: (x) => f.num(x.log_loss, 4), num: true },
            { label: t("colBrier"), cell: (x) => f.num(x.brier, 4), num: true },
          ]} />
        )}
      </Loaded>
      <Loaded state={sweep}>
        {(sw) => sw.rows.length === 0 ? null : (
          <Table caption={t("edgeSweep")} rows={sw.rows} rowKey={(x) => String(x.min_edge)} cols={[
            { label: t("colThreshold"), cell: (x: Row) => f.pct(x.min_edge, 0), num: true },
            { label: t("mBets"), cell: (x: Row) => x.bets, num: true },
            { label: t("colStaked"), cell: (x: Row) => f.num(x.staked, 0), num: true },
            { label: t("colProfit"), cell: (x: Row) => f.num(x.profit, 2, true), num: true },
            { label: t("mRoi"), cell: (x: Row) => f.pct(x.roi, 1, true), num: true },
            { label: t("colRoiSe"), cell: (x: Row) => f.pct(x.roi_se, 1), num: true },
            { label: t("colAvgOdds"), cell: (x: Row) => f.num(x.avg_odds), num: true },
            { label: t("colHit"), cell: (x: Row) => f.pct(x.hit_rate, 1), num: true },
            { label: t("mClv"), cell: (x: Row) => f.pct(x.clv, 2, true), num: true },
            { label: t("mBeatClose"), cell: (x: Row) => f.pct(x.beat_close_rate, 0), num: true },
          ]} />
        )}
      </Loaded>
    </>
  );
}
