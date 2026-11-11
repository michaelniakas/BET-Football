import { useState } from "react";
import { api, useApi, type Schemas } from "../api";
import { Calibration, ProfitChart } from "../charts";
import {
  at, betText, Choice, CountUp, League, LeagueSelect, Loaded, MarketSwitch, matchKey, Note, ordered, OutcomeLegend,
  Pager, RuleBar, Search, Stat, Strip, Table, Tabs, useCtx, useDebounced, useOffset, type Column, type LeagueId,
} from "../ui";
import { BetBoard } from "./Picks";

type Fixture = Schemas["SettledFixture"];
const PAGE = 25;

export function Results() {
  const { t } = useCtx();
  return (
    <>
      <div className="section-head" style={at(0)}>
        <div>
          <span className="live"><i />{t("settlesItself")}</span>
          <h1 style={{ marginTop: 8 }}>{t("navResults")}</h1>
          <p className="muted">{t("resultsLede")}</p>
        </div>
        <MarketSwitch />
      </div>
      <Tabs label={t("navResults")} i={1} tabs={[
        { id: "bets", title: t("tabBets"), body: <BetsTab /> },
        { id: "matches", title: t("tabMatches"), body: <MatchesTab /> },
        { id: "accuracy", title: t("tabAccuracy"), body: <AccuracyTab /> },
      ]} />
    </>
  );
}

const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString().slice(0, 10);

/** The final score, or the result's symbol for matches settled before scores were kept. */
function Score({ r }: { r: Fixture }) {
  const { t } = useCtx();
  return r.score
    ? (
      <span className="score">
        <span aria-hidden="true">{r.score.home}–{r.score.away}</span>
        <span className="visually-hidden">{t("scoreAria", { h: r.score.home, a: r.score.away })}</span>
      </span>
    )
    : <span className="score none" title={t("noScore")}>{t(`res_${r.result}`)}</span>;
}

function BetsTab() {
  const { t, f, s, rule } = useCtx();
  const [period, setPeriod] = useState<"4w" | "all">("all");
  const [status, setStatus] = useState<"all" | "won" | "lost">("all");
  const [typed, setTyped] = useState("");
  const team = useDebounced(typed.trim()) || undefined;
  const since = period === "4w" ? daysAgo(28) : undefined;
  const where = { book: rule.book, min_edge: rule.edge, team, date_from: since };
  const [offset, setOffset] = useOffset(JSON.stringify([s.id, where, status]));
  const path = { strategy: s.id };

  const summary = useApi((signal) => api.GET("/api/v1/strategies/{strategy}/results/summary", {
    params: { path, query: where }, signal,
  }), [s.id, JSON.stringify(where)]);
  const fixtures = useApi((signal) => api.GET("/api/v1/strategies/{strategy}/results/fixtures", {
    params: { path, query: { ...where, bet_status: status === "all" ? ["won", "lost"] : [status], limit: PAGE, offset } },
    signal,
  }), [s.id, JSON.stringify(where), status, offset]);

  const noBets = summary.data?.metrics === null;    // the board says why, once: no chart, no empty list
  return (
    <>
      <RuleBar />
      <Loaded state={summary} height={110}>{(sum) => <div className="hero"><BetBoard sum={sum} /></div>}</Loaded>
      <div className="card pad">
        <div className="row between" style={{ marginBottom: noBets ? 0 : 14, alignItems: "flex-start" }}>
          <div>
            <span className="eyebrow">{t("profitOverTime")}</span>
            {summary.data?.metrics && (
              <div className={`big ${summary.data.metrics.profit >= 0 ? "pos" : "neg"}`} style={{ marginTop: 6 }}>
                <CountUp value={summary.data.metrics.profit} show={(v) => t("units", { v: f.num(v, 2, true) })} />
              </div>
            )}
          </div>
          <Choice look="chips" label={t("period")} value={period} onChange={setPeriod}
                  options={[{ value: "4w", label: t("last4w") }, { value: "all", label: t("allTime") }]} />
        </div>
        {!noBets && (
          <Loaded state={summary} height={240}>
            {(sum) => sum.metrics !== null && <ProfitChart series={sum.series} />}
          </Loaded>
        )}
      </div>

      <div className="row between">
        <Choice look="chips" label={t("outcomeFilter")} value={status} onChange={setStatus} options={[
          { value: "all", label: t("allBets") }, { value: "won", label: t("won") }, { value: "lost", label: t("lost") },
        ]} />
        <Search value={typed} onChange={setTyped} />
      </div>
      <Loaded state={fixtures} height={300}>
        {(page) => page.total === 0 ? !noBets && <Note>{t("noBetsFound")}</Note> : (
          <div className="card list">
            <div className="li li-head">
              <span>{t("hDate")}</span><span>{t("hMatchScore")}</span><span>{t("hOurBet")}</span>
              <span className="right">{t("hProfit")}</span>
            </div>
            {page.items.map((r) => r.bet && (
              <div key={matchKey(r)} className="li">
                <span className="when"><span>{f.date(r.date)}</span><League id={r.league} /></span>
                <span className="match">
                  {r.home}<Score r={r} />{r.away}
                  <small>
                    {r.bet.clv === null ? t("noClose") : t(r.bet.clv > 0 ? "closeBeat" : "closeMissed", {
                      odds: f.num(r.bet.odds / (1 + r.bet.clv)),
                    })}
                  </small>
                </span>
                <span className="wide">
                  {betText(t, r.bet.pick, r.home, r.away)} @ <b className="num">{f.num(r.bet.odds)}</b>{" "}
                  <span className={`tag ${r.bet_status === "won" ? "win" : "loss"}`}>{t(r.bet_status === "won" ? "won" : "lost")}</span>
                </span>
                <span className={`right profit ${r.bet.profit >= 0 ? "pos" : "neg"}`}>{f.num(r.bet.profit, 2, true)}</span>
              </div>
            ))}
            <Pager page={page} loading={fixtures.loading} onPage={setOffset} />
          </div>
        )}
      </Loaded>

      {summary.data?.metrics && <Detailed sum={summary.data} />}
    </>
  );
}

/** The analyst's tables, collapsed: per league, per edge bucket, and every fixed threshold. */
function Detailed({ sum }: { sum: Schemas["ResultsSummary"] }) {
  const { t, f, s, rule } = useCtx();
  const [open, setOpen] = useState(false);
  const sweep = useApi(open ? (signal) => api.GET("/api/v1/strategies/{strategy}/results/edge-sweep", {
    params: { path: { strategy: s.id }, query: { book: rule.book } }, signal,
  }) : null, [open, s.id, rule.book]);
  const cols = <R extends Schemas["Metrics"]>(): Column<R>[] => [
    { label: t("colBets"), cell: (x) => x.bets, num: true },
    { label: t("colProfit"), cell: (x) => f.num(x.profit, 2, true), num: true },
    { label: t("colRoi"), cell: (x) => f.pct(x.roi, 1, true), num: true },
    { label: t("colRoiSe"), cell: (x) => f.pct(x.roi_se, 1), num: true },
    { label: t("colAvgOdds"), cell: (x) => f.num(x.avg_odds), num: true },
    { label: t("colHit"), cell: (x) => f.pct(x.hit_rate, 1), num: true },
    { label: t("colClv"), cell: (x) => f.pct(x.clv, 2, true), num: true },
    { label: t("colBeatClose"), cell: (x) => f.pct(x.beat_close_rate, 0), num: true },
  ];
  return (
    <details className="card more" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>{t("detailed")}</summary>
      {open && (
        <div>
          <Table caption={t("byLeague")} rows={sum.by_league} rowKey={(x) => x.league} cols={[
            { label: t("league"), cell: (x) => <League id={x.league} /> }, ...cols<Schemas["LeagueMetrics"]>(),
          ]} />
          <Table caption={t("byEdge")} rows={sum.by_edge} rowKey={(x) => String(x.min_edge)} cols={[
            { label: t("colEdge"), cell: (x) => x.max_edge === null
                ? `> ${f.pct(x.min_edge, 0)}` : `${f.pct(x.min_edge, 0)} – ${f.pct(x.max_edge, 0)}` },
            ...cols<Schemas["EdgeBucketMetrics"]>(),
          ]} />
          <Loaded state={sweep}>
            {(sw) => sw.rows.length === 0 ? null : (
              <Table caption={t("edgeSweep")} rows={sw.rows} rowKey={(x) => String(x.min_edge)} cols={[
                { label: t("colThreshold"), cell: (x) => `> ${f.pct(x.min_edge, 0)}` },
                { label: t("colStaked"), cell: (x) => f.num(x.staked, 0), num: true },
                ...cols<Schemas["EdgeSweepRow"]>(),
              ]} />
            )}
          </Loaded>
          <p className="muted small">{t("detailedHelp")}</p>
        </div>
      )}
    </details>
  );
}

function MatchesTab() {
  const { t, f, s, rule } = useCtx();
  const [status, setStatus] = useState<"all" | "bet" | "none">("all");
  const [league, setLeague] = useState<LeagueId | "">("");
  const [typed, setTyped] = useState("");
  const team = useDebounced(typed.trim()) || undefined;
  const lg = league && s.leagues.includes(league) ? league : "";    // a league of the other market filters nothing
  const query = {
    book: rule.book, min_edge: rule.edge, team, league: lg ? [lg] : undefined,
    bet_status: status === "all" ? undefined : status === "bet" ? ["won" as const, "lost" as const] : ["no_bet" as const],
  };
  const [offset, setOffset] = useOffset(JSON.stringify([s.id, query]));
  const fixtures = useApi((signal) => api.GET("/api/v1/strategies/{strategy}/results/fixtures", {
    params: { path: { strategy: s.id }, query: { ...query, limit: PAGE, offset } }, signal,
  }), [s.id, JSON.stringify(query), offset]);

  return (
    <>
      <p className="muted" style={{ maxWidth: "75ch" }}>{t("matchesLede")}</p>
      <RuleBar />
      <div className="row between">
        <Choice look="chips" label={t("betFilter")} value={status} onChange={setStatus} options={[
          { value: "all", label: t("allMatches") }, { value: "bet", label: t("weBetFilter") }, { value: "none", label: t("noBet") },
        ]} />
        <div className="row">
          <Search value={typed} onChange={setTyped} />
          <LeagueSelect value={lg} options={s.leagues} onChange={setLeague} />
        </div>
      </div>
      <OutcomeLegend />
      <Loaded state={fixtures} height={360}>
        {(page) => page.total === 0 ? <Note>{t("noMatchesFound")}</Note> : (
          <div className="card list">
            <div className="li li-head">
              <span>{t("hDate")}</span><span>{t("hFinalScore")}</span><span>{t("hModelSaid")}</span>
              <span className="right">{t("hModelCall")}</span>
            </div>
            {page.items.map((r) => {
              const os = ordered(r.outcomes);
              const model = os.map((o) => o.p_model), market = os.map((o) => o.p_market);
              const hit = os.findIndex((o) => o.outcome === r.result);
              const pHit = model[hit] ?? 0;
              const top = Math.max(...model);
              // As the API's favourite hit rate: a tie for favourite that includes the result is a partial hit.
              const fav = pHit !== top ? "favLost" : model.filter((p) => p === top).length > 1 ? "favShared" : "favWon";
              const closer = pHit > (market[hit] ?? 0);
              return (
                <div key={matchKey(r)} className="li">
                  <span className="when"><span>{f.date(r.date)}</span><League id={r.league} /></span>
                  <span className="match">
                    {r.home}<Score r={r} />{r.away}
                    <small>
                      {r.bet === null ? t("noBet") : (
                        <>
                          {t("weBet", { pick: betText(t, r.bet.pick, r.home, r.away), odds: f.num(r.bet.odds) })} ·{" "}
                          <b className={r.bet.profit >= 0 ? "pos" : "neg"}>
                            {t(r.bet_status === "won" ? "wonV" : "lostV", { v: f.num(r.bet.profit, 2, true) })}
                          </b>
                        </>
                      )}
                    </small>
                  </span>
                  <div className="probs" role="img" aria-label={t("probsHitAria", {
                    model: os.map((o, i) => `${t(`out_${o.outcome}`)} ${f.pct(model[i], 0)}`).join(", "),
                    result: t(`out_${r.result}`),
                  })}>
                    <Strip ps={model} hit={hit} /><Strip ps={market} market />
                  </div>
                  <span className="stack">
                    <span className={`tag ${fav === "favWon" ? "win" : fav === "favLost" ? "loss" : ""}`}>{t(fav)}</span>
                    <small className="muted">
                      {t("onResult", { p: f.pct(pHit, 0), o: t(`out_${r.result}`) })} · {t(closer ? "beatBookies" : "bookiesCloser")}
                    </small>
                  </span>
                </div>
              );
            })}
            <Pager page={page} loading={fixtures.loading} onPage={setOffset} />
          </div>
        )}
      </Loaded>
    </>
  );
}

function AccuracyTab() {
  const { t, f, s } = useCtx();
  const report = useApi((signal) => api.GET("/api/v1/strategies/{strategy}/results/probability-report", {
    params: { path: { strategy: s.id } }, signal,
  }), [s.id]);
  return (
    <Loaded state={report} height={300}>
      {(r) => {
        if (r.n === 0) return <Note>{t("noReport")}</Note>;
        const row = (src: string) => r.rows.find((x) => x.source === src);
        const model = row("model"), pre = row("market_pre_match"), close = row("market_closing");
        const ahead = model?.log_loss != null && pre?.log_loss != null && model.log_loss <= pre.log_loss;
        return (
          <>
            <div className="hero" style={{ gap: 22 }}>
              <div className="board">
                <Stat label={t("sChecked")} value={<CountUp value={r.n} show={(v) => f.num(v, 0)} />} sub={t("sCheckedSub")} />
                <Stat label={t("sFav")} info="fav"
                      value={model?.favourite_hit_rate == null ? "–" : <CountUp value={model.favourite_hit_rate} show={(v) => f.pct(v, 0)} />}
                      sub={t("sFavSub", { p: f.pct(pre?.favourite_hit_rate, 0) })} />
                <Stat label={t("sLogLoss")} info="logloss" value={f.num(model?.log_loss, 3)}
                      sub={t("sLogLossSub", { pre: f.num(pre?.log_loss, 3), close: f.num(close?.log_loss, 3) })} />
                <Stat label={t("sCloser")} info="closer"
                      value={r.model_closer_rate === null ? "–" : <CountUp value={r.model_closer_rate} show={(v) => f.pct(v, 0)} />}
                      sub={t("sCloserSub")} />
              </div>
              <div className="verdict">
                <span className={`chip-state${ahead ? " good" : ""}`}>{t(ahead ? "accAhead" : "accBehind")}</span>
                <p>{t(ahead ? "accAheadText" : "accBehindText")}</p>
              </div>
            </div>
            <div className="two">
              <div className="card pad" style={{ alignSelf: "start" }}>
                <span className="eyebrow">{t("calibEyebrow")}</span>
                <h3 style={{ margin: "4px 0 6px" }}>{t("calibTitle")}</h3>
                <p className="muted small" style={{ marginBottom: 12 }}>{t("calibHelp")}</p>
                <Calibration bins={r.calibration} />
              </div>
              <div className="card pad">
                <span className="eyebrow">{t("byLeague")}</span>
                <h3 style={{ margin: "4px 0 6px" }}>{t("leagueErrTitle")}</h3>
                <p className="muted small" style={{ marginBottom: 6 }}>{t("leagueErrHelp")}</p>
                {r.by_league.map((l) => {
                  const m = l.rows.find((x) => x.source === "model")?.log_loss;
                  const k = l.rows.find((x) => x.source === "market_pre_match")?.log_loss;
                  const better = m != null && k != null && m < k;
                  return (
                    <div key={l.league} className="row between" style={{ padding: "12px 0", borderTop: "1px solid var(--line)" }}>
                      <span style={{ fontWeight: 600, minWidth: 0 }}>
                        <League id={l.league} /> <span className="muted small">· {t("nMatches", { n: l.n })}</span>
                      </span>
                      <span className="row" style={{ flexWrap: "nowrap", flex: "none" }}>
                        <span className="num muted small">{t("bookiesLL", { v: f.num(k, 3) })}</span>
                        <span className={`tag num${better ? " win" : ""}`}>{t("modelLL", { v: f.num(m, 3) })}</span>
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          </>
        );
      }}
    </Loaded>
  );
}
