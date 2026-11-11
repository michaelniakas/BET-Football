import { useId, useState } from "react";
import { api, useApi, type Schemas } from "../api";
import { PnlHistogram } from "../charts";
import { PICK_LABELS } from "../format";
import { League as LeagueLabel, leagueName, Loaded, matchCols, matchKey, Metric, MultiCheck, Note, Select, Slider,
         Table, Tabs, useCtx } from "../ui";

type Market = Schemas["Market"];
type League = Schemas["League"];
type OptReq = Schemas["OptimizeRequest"];
type OptRes = Schemas["OptimizeResponse"];
type Leg = Schemas["Leg"];
type Scenario = Schemas["Scenario"];

const TOP5: League[] = ["E0", "SP1", "D1", "I1", "F1"];
const RISK: (keyof Scenario["summary"])[] = [
  "staked", "expected_profit_model", "expected_profit_market", "p_profit", "p_lose_all", "median_pnl", "p95_pnl",
  "max_profit",
];

const optimize = (body: OptReq) => (signal: AbortSignal) => api.POST("/api/v1/optimize", { body, signal });

export function Optimizer() {
  const { t, f, catalog } = useCtx();
  const budgetId = useId();
  const [budgetText, setBudgetText] = useState("50");
  const [markets, setMarkets] = useState<Market[]>(catalog.strategies.map((s) => s.market));
  const [minProb, setMinProb] = useState(0.35);
  const [minEdge, setMinEdge] = useState(0);
  const [source, setSource] = useState<"live" | "snapshot">(catalog.live_odds.configured ? "live" : "snapshot");
  const [book, setBook] = useState<Schemas["SnapshotBook"]>("avg");
  const [liveLeagues, setLiveLeagues] = useState<League[]>(TOP5);
  const [bookmakers, setBookmakers] = useState<string[] | null>(null);     // null: all of them
  const [liveRun, setLiveRun] = useState<{ body: OptReq; n: number } | null>(null);

  const budget = Number(budgetText);
  const budgetOk = budgetText.trim() !== "" && budget >= 5 && budget <= 100_000;
  const available = [...new Set(catalog.strategies.filter((s) => markets.includes(s.market)).flatMap((s) => s.leagues))]
    .sort();
  const leagues = liveLeagues.filter((l) => available.includes(l));
  const base = { budget, markets, min_prob: minProb, min_edge: minEdge };

  const snapBody: OptReq | null =
    source === "snapshot" && budgetOk && markets.length ? { ...base, odds: { source: "snapshot", book } } : null;
  const snap = useApi(snapBody && optimize(snapBody), [JSON.stringify(snapBody)]);
  const live = useApi(liveRun && optimize(liveRun.body), [liveRun?.n]);
  const result = source === "snapshot" ? snap : live;

  const liveBody: OptReq = { ...base, odds: { source: "live", leagues, ...(bookmakers ? { bookmakers } : {}) } };
  const liveStale = liveRun !== null && JSON.stringify(liveBody) !== JSON.stringify(liveRun.body);
  const runLive = () => setLiveRun((prev) => ({ n: (prev?.n ?? 0) + 1, body: liveBody }));

  return (
    <>
      <h1>{t("optTitle")}</h1>
      <Note kind="warn">{t("optWarning")}</Note>

      <div className="controls">
        <div className="field">
          <label htmlFor={budgetId}>{t("budget")}</label>
          <input id={budgetId} type="number" inputMode="decimal" min={5} max={100_000} step={5} value={budgetText}
                 aria-invalid={!budgetOk} aria-describedby={budgetOk ? undefined : `${budgetId}-err`}
                 onChange={(e) => setBudgetText(e.target.value)} />
          {!budgetOk && <small id={`${budgetId}-err`} className="error">{t("budgetInvalid")}</small>}
        </div>
        <MultiCheck legend={t("markets")} value={markets} onChange={setMarkets}
                    options={catalog.strategies.map((s) => ({ value: s.market, label: t(`market_${s.market}`) }))} />
        <Slider label={t("minProb")} value={minProb} min={0.2} max={0.8} step={0.05} onChange={setMinProb}
                show={(v) => f.pct(v, 0)} help={t("minProbHelp")} />
        <Slider label={t("minEdge")} value={minEdge} min={-0.1} max={0.15} step={0.01} onChange={setMinEdge}
                show={(v) => f.pct(v, 0, true)} help={t("minEdgeHelp")} />
      </div>
      {markets.length === 0 ? <Note>{t("pickMarket")}</Note> : (
        <>
          <h2>{t("stepOdds")}</h2>
          <fieldset className="field radios">
            <legend>{t("sourceLabel")}</legend>
            {(["live", "snapshot"] as const).map((s) => (
              <label key={s}>
                <input type="radio" name="source" value={s} checked={source === s} onChange={() => setSource(s)}
                       disabled={s === "live" && !catalog.live_odds.configured} />{" "}
                {t(s === "live" ? "sourceLive" : "sourceSnapshot")}
              </label>
            ))}
            {!catalog.live_odds.configured && <small>{t("liveOff")}</small>}
          </fieldset>

          {source === "snapshot" ? (
            <Select label={t("snapshotBook")} value={book} onChange={setBook} help={t("snapshotBookHelp")}
                    options={catalog.snapshot_books.map((b) => ({ value: b, label: t(`book_${b}`) }))} />
          ) : (
            <>
              <MultiCheck legend={t("liveLeagues")} value={leagues}
                          onChange={(v) => { setLiveLeagues(v); setBookmakers(null); }}
                          options={available.map((l) => ({ value: l, label: <LeagueLabel id={l} /> }))}
                          help={t("liveLeaguesHelp", { min: Math.round(catalog.live_odds.ttl_seconds / 60) })} />
              {live.data?.live && (
                <MultiCheck legend={t("bookmakers")} help={t("bookmakersHelp")}
                            options={live.data.live.bookmakers.map((b) => ({ value: b, label: b }))}
                            value={bookmakers ?? live.data.live.bookmakers}
                            onChange={(v) => setBookmakers(v.length === live.data?.live?.bookmakers.length ? null : v)} />
              )}
              <button type="button" onClick={runLive} disabled={!budgetOk || leagues.length === 0 || bookmakers?.length === 0}>
                {t("fetchLive", { credits: 2 * leagues.length })}
              </button>
              {leagues.length === 0 && <Note>{t("pickLeague")}</Note>}
              {bookmakers?.length === 0 && <Note>{t("pickBookmaker")}</Note>}
              {(!liveRun || liveStale) && <Note>{t("liveIdle")}</Note>}
            </>
          )}

          {(source === "snapshot" ? snapBody : liveRun) && (
            <div className={source === "live" && liveStale ? "stale" : undefined}>
              <Loaded state={result}>{(r) => <Outcome r={r} />}</Loaded>
            </div>
          )}
        </>
      )}
    </>
  );
}

function LiveInfo({ live }: { live: NonNullable<OptRes["live"]> }) {
  const { t, f, catalog } = useCtx();
  const unmatched = live.leagues.flatMap((l) => l.unmatched);
  return (
    <div className="caption">
      {live.leagues.map((l) => (
        <p key={l.league}>
          {t("liveInfo", { league: leagueName(catalog, l.league), at: f.dateTime(l.fetched_at), events: l.events,
                           matched: l.matched })}
        </p>
      ))}
      {live.quota && <p>{t("creditsLeft", { n: live.quota.remaining })}</p>}
      {unmatched.length > 0 && (
        <details>
          <summary>{t("unmatched", { n: unmatched.length })}</summary>
          <p>{unmatched.map((u) => `${u.home} – ${u.away}`).join(", ")}</p>
        </details>
      )}
    </div>
  );
}

function Outcome({ r }: { r: OptRes }) {
  const { t, f } = useCtx();
  const title = (s: Scenario) => t(`scenario_${s.id}`, { of: s.system?.of ?? "" });
  const couponText = (legs: number[]) =>
    legs.map((i) => r.legs[i]).map((l) => (l ? `${l.home}-${l.away}: ${PICK_LABELS[l.outcome]}` : "?")).join(" + ");
  type Coupon = Scenario["coupons"][number];

  return (
    <>
      {r.live && <LiveInfo live={r.live} />}
      <h2>{t("stepLegs")}</h2>
      {r.upcoming_matches === 0 ? <Note>{t("noUpcomingOpt")}</Note>
        : r.live && r.live.leagues.every((l) => l.matched === 0) ? <Note>{t("liveNoMatch")}</Note>
        : r.legs.length === 0 ? <Note>{t("noLegs")}</Note> : (
        <>
          <p className="caption">{t("legsHelp")}</p>
          <Table rows={r.legs} rowKey={matchKey} cols={[
            ...matchCols<Leg>(t, f),
            { label: t("colPick"), cell: (l) => PICK_LABELS[l.outcome] },
            { label: t("colOdds"), cell: (l) => f.num(l.odds), num: true },
            { label: t("colMinOdds"), cell: (l) => f.minOdds(l.min_odds), num: true },
            { label: t("colBookmaker"), cell: (l) => l.bookmaker },
            { label: t("colPModel"), cell: (l) => f.pct(l.p_model), num: true },
            { label: t("colPMarket"), cell: (l) => f.pct(l.p_market), num: true },
            { label: t("colEdge"), cell: (l) => f.pct(l.edge, 1, true), num: true },
          ]} />

          <h2>{t("stepScenarios")}</h2>
          {r.scenarios.length === 0 ? <Note>{t("noScenarios")}</Note> : (
            <>
              <p className="caption">{t("scenariosHelp", { n: f.num(r.simulations, 0) })}</p>
              <Table rows={r.scenarios} rowKey={(s) => s.id} cols={[
                { label: t("colScenario"), cell: title },
                ...RISK.map((k) => ({
                  label: t(`r_${k}`), num: true,
                  cell: (s: Scenario) => k.startsWith("p_") ? f.pct(s.summary[k], 0)
                    : f.eur(s.summary[k], k !== "staked"),
                })),
              ]} />
              <Tabs label={t("stepScenarios")} tabs={r.scenarios.map((s) => ({
                id: s.id, title: title(s), body: (
                  <>
                    <div className="metrics">
                      <Metric label={t("r_expected_profit_model")} value={f.eur(s.summary.expected_profit_model, true)} />
                      <Metric label={t("r_expected_profit_market")} value={f.eur(s.summary.expected_profit_market, true)} />
                      <Metric label={t("r_p_profit")} value={f.pct(s.summary.p_profit, 0)} />
                      <Metric label={t("r_p_lose_all")} value={f.pct(s.summary.p_lose_all, 0)} />
                    </div>
                    <Table rows={s.coupons} rowKey={(c) => c.legs.join(",")} cols={[
                      { label: t("colCoupon"), cell: (c: Coupon) => couponText(c.legs) },
                      { label: t("colOdds"), cell: (c: Coupon) => f.num(c.odds), num: true },
                      { label: t("colPModel"), cell: (c: Coupon) => f.pct(c.p_model), num: true },
                      { label: t("colPMarket"), cell: (c: Coupon) => f.pct(c.p_market), num: true },
                      { label: t("colEvModel"), cell: (c: Coupon) => f.pct(c.ev_model, 1, true), num: true },
                      { label: t("colEvMarket"), cell: (c: Coupon) => f.pct(c.ev_market, 1, true), num: true },
                      { label: t("colStake"), cell: (c: Coupon) => f.eur(c.stake), num: true },
                      { label: t("colPayout"), cell: (c: Coupon) => f.eur(c.payout), num: true },
                    ]} />
                    <PnlHistogram h={s.pnl_histogram} />
                  </>
                ),
              }))} />
            </>
          )}
        </>
      )}
    </>
  );
}
