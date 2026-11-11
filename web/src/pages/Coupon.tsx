import { useId, useState } from "react";
import { api, useApi, type Schemas } from "../api";
import { PnlHistogram } from "../charts";
import { at, betText, Choice, Info, League, leagueName, Loaded, matchKey, MultiChips, Note, useCtx } from "../ui";

type Market = Schemas["Market"];
type LeagueId = Schemas["League"];
type OptReq = Schemas["OptimizeRequest"];
type OptRes = Schemas["OptimizeResponse"];
type Scenario = Schemas["Scenario"];

const TOP5: LeagueId[] = ["E0", "SP1", "D1", "I1", "F1"];
const SCEN_TAG: Record<Scenario["id"], string> = { conservative: "win", balanced: "", aggressive: "loss" };

const optimize = (body: OptReq) => (signal: AbortSignal) => api.POST("/api/v1/optimize", { body, signal });

export function Coupon() {
  const { t, f, catalog } = useCtx();
  const id = useId();
  const [budgetText, setBudgetText] = useState("50");
  const [markets, setMarkets] = useState<Market[]>(catalog.strategies.map((s) => s.market));
  const [minProb, setMinProb] = useState(0.35);
  const [minEdge, setMinEdge] = useState(0);
  const [source, setSource] = useState<"live" | "snapshot">(catalog.live_odds.configured ? "live" : "snapshot");
  const [book, setBook] = useState<Schemas["SnapshotBook"]>("avg");
  const [liveLeagues, setLiveLeagues] = useState<LeagueId[]>(TOP5);
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
  const allBookmakers = live.data?.live?.bookmakers;
  const quota = live.data?.live?.quota;

  return (
    <>
      <div className="section-head" style={at(0)}>
        <div>
          <span className="eyebrow">{t("couponEyebrow")}</span>
          <h1 style={{ marginTop: 6 }}>{t("couponTitle", { budget: budgetOk ? f.eur(budget) : "…" })}</h1>
          <p className="muted">{t("couponLede")}</p>
        </div>
      </div>
      <Note kind="warn">{t("optWarning")}</Note>

      <div className="coupon" style={at(2)}>
        <aside className="card pad">
          <div className="field">
            <label htmlFor={`${id}-b`}><b>{t("budget")}</b></label>
            <input id={`${id}-b`} type="number" inputMode="decimal" min={5} max={100_000} step={5} value={budgetText}
                   aria-invalid={!budgetOk} aria-describedby={budgetOk ? undefined : `${id}-berr`}
                   onChange={(e) => setBudgetText(e.target.value)} />
            {!budgetOk && <small id={`${id}-berr`} className="error">{t("budgetInvalid")}</small>}
          </div>
          <div className="field">
            <span>{t("markets")}</span>
            <MultiChips label={t("markets")} value={markets} onChange={setMarkets}
                        options={catalog.strategies.map((s) => ({ value: s.market, label: t(`market_${s.market}`) }))} />
          </div>
          <div className="field">
            <span><label htmlFor={`${id}-p`}>{t("minProb")}</label> · <output htmlFor={`${id}-p`}>{f.pct(minProb, 0)}</output></span>
            <input id={`${id}-p`} type="range" min={0.2} max={0.8} step={0.05} value={minProb}
                   aria-valuetext={f.pct(minProb, 0)} aria-describedby={`${id}-ph`}
                   onChange={(e) => setMinProb(Number(e.target.value))} />
            <small id={`${id}-ph`}>{t("minProbHelp")}</small>
          </div>
          <div className="field">
            <span>
              <label htmlFor={`${id}-e`}>{t("minEdge")}</label> · <output htmlFor={`${id}-e`}>{f.pct(minEdge, 0, true)}</output>
              <Info id="edge" />
            </span>
            <input id={`${id}-e`} type="range" min={-0.1} max={0.15} step={0.01} value={minEdge}
                   aria-valuetext={f.pct(minEdge, 0, true)} aria-describedby={`${id}-eh`}
                   onChange={(e) => setMinEdge(Number(e.target.value))} />
            <small id={`${id}-eh`}>{t("minEdgeHelp")}</small>
          </div>
          <div className="field">
            <span>{t("sourceLabel")}</span>
            <Choice label={t("sourceLabel")} value={source} onChange={setSource} options={[
              { value: "live", label: t("sourceLive"), disabled: !catalog.live_odds.configured },
              { value: "snapshot", label: t("sourceSnapshot") },
            ]} />
            <small>
              {!catalog.live_odds.configured ? t("liveOff") : source === "live"
                ? <>{t("liveCost")}{quota && <> · {t("creditsLeft", { n: quota.remaining })}</>}</>
                : t("snapshotBookHelp")}
            </small>
          </div>
          {source === "snapshot" ? (
            <div className="field">
              <label htmlFor={`${id}-k`}><b>{t("snapshotBook")}</b></label>
              <select id={`${id}-k`} value={book} onChange={(e) => setBook(e.target.value as Schemas["SnapshotBook"])}>
                {catalog.snapshot_books.map((b) => <option key={b} value={b}>{t(`book_${b}`)}</option>)}
              </select>
            </div>
          ) : (
            <>
              <div className="field">
                <span>{t("liveLeagues")}</span>
                <MultiChips label={t("liveLeagues")} value={leagues}
                            onChange={(v) => { setLiveLeagues(v); setBookmakers(null); }}
                            options={available.map((l) => ({ value: l, label: <League id={l} /> }))} />
                <small>{t("liveLeaguesHelp", { min: Math.round(catalog.live_odds.ttl_seconds / 60) })}</small>
              </div>
              {allBookmakers && (
                <div className="field">
                  <span>{t("bookmakers")}</span>
                  <MultiChips label={t("bookmakers")} value={bookmakers ?? allBookmakers}
                              onChange={(v) => setBookmakers(v.length === allBookmakers.length ? null : v)}
                              options={allBookmakers.map((b) => ({ value: b, label: b }))} />
                  <small>{t("bookmakersHelp")}</small>
                </div>
              )}
              <button type="button" className="cta" onClick={runLive}
                      disabled={!budgetOk || markets.length === 0 || leagues.length === 0 || bookmakers?.length === 0}>
                {t("fetchLive", { credits: 2 * leagues.length })}
              </button>
              {leagues.length === 0 && <small className="muted">{t("pickLeague")}</small>}
              {bookmakers?.length === 0 && <small className="muted">{t("pickBookmaker")}</small>}
            </>
          )}
        </aside>

        <div className="section">
          {markets.length === 0 ? <Note>{t("pickMarket")}</Note>
            : source === "live" && (!liveRun || liveStale) && <Note>{t("liveIdle")}</Note>}
          {markets.length > 0 && (source === "snapshot" ? snapBody : liveRun) && (
            <div className={`section${source === "live" && liveStale ? " stale" : ""}`}>
              <Loaded state={result} height={420}>{(r) => <Outcome r={r} />}</Loaded>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

function LiveInfo({ live }: { live: NonNullable<OptRes["live"]> }) {
  const { t, f, catalog } = useCtx();
  const unmatched = live.leagues.flatMap((l) => l.unmatched);
  return (
    <div className="muted small">
      {live.leagues.map((l) => (
        <p key={l.league}>
          {t("liveInfo", { league: leagueName(catalog, l.league), at: f.dateTime(l.fetched_at), events: l.events,
                           matched: l.matched })}
        </p>
      ))}
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
  const [picked, setPicked] = useState<Scenario["id"]>("balanced");
  const sel = r.scenarios.find((s) => s.id === picked) ?? r.scenarios[0];
  const kind = (s: Scenario) => t(`kind_${s.kind}`, { of: s.system?.of ?? "" });

  if (r.upcoming_matches === 0) return <><LiveInfoMaybe r={r} /><Note>{t("noUpcomingOpt")}</Note></>;
  if (r.live && r.live.leagues.every((l) => l.matched === 0)) return <><LiveInfoMaybe r={r} /><Note>{t("liveNoMatch")}</Note></>;
  if (r.legs.length === 0) return <><LiveInfoMaybe r={r} /><Note>{t("noLegs")}</Note></>;

  return (
    <>
      <LiveInfoMaybe r={r} />
      {r.scenarios.length === 0 ? <Note>{t("noScenarios")}</Note> : (
        <>
          <div className="row between">
            <span className="eyebrow">{t("scenEyebrow")} <Info id="scenarios" /></span>
            <span className="muted small">{t("scenariosHelp", { n: f.num(r.simulations, 0) })}</span>
          </div>
          <div className="scen" role="group" aria-label={t("scenEyebrow")}>
            {r.scenarios.map((s) => (
              <button key={s.id} type="button" className="card" aria-pressed={s.id === sel?.id} onClick={() => setPicked(s.id)}>
                <span className="row between" style={{ width: "100%" }}>
                  <span className="scen-name">{t(`scen_${s.id}`)}</span>
                  <span className={`tag ${SCEN_TAG[s.id]}`}>{kind(s)}</span>
                </span>
                <span>
                  <span className={`big ${s.summary.expected_profit_model >= 0 ? "pos" : "neg"}`}>
                    {f.eur(s.summary.expected_profit_model, true)}
                  </span>
                  <span className="muted small" style={{ display: "block", marginTop: 4 }}>{t("expectedIfRight")}</span>
                </span>
                <span className="kv"><span>{t("r_p_profit")}</span><b>{f.pct(s.summary.p_profit, 0)}</b></span>
                <span className="kv"><span>{t("r_p_lose_all")}</span><b>{f.pct(s.summary.p_lose_all, 0)}</b></span>
                <span className="risk" aria-hidden="true"><i style={{ width: `${s.summary.p_lose_all * 100}%` }} /></span>
                <span className="kv">
                  <span>{t("r_expected_profit_market")}</span>
                  <b className={s.summary.expected_profit_market >= 0 ? "pos" : "neg"}>{f.eur(s.summary.expected_profit_market, true)}</b>
                </span>
              </button>
            ))}
          </div>
          {sel && <ScenarioDetail r={r} s={sel} kind={kind(sel)} />}
        </>
      )}

      <div className="card list">
        <div className="li li-head legs">
          <span>{t("hSelection")}</span><span>{t("hWhere")}</span>
          <span className="right">{t("hChance")}</span><span className="right">{t("colOdds")}</span>
        </div>
        {r.legs.map((l) => (
          <div key={`${matchKey(l)}|${l.outcome}`} className="li legs">
            <span className="match">
              {betText(t, l.outcome, l.home, l.away)}
              <small>{l.home} – {l.away} · {f.date(l.date)} · <League id={l.league} /></small>
            </span>
            <span className="muted small">
              {l.bookmaker}<br />{t("takeAtLeast", { odds: f.minOdds(l.min_odds) })}
            </span>
            <span className="right num stack">
              {f.pct(l.p_model, 0)}
              <small className={l.edge >= 0 ? "pos" : "neg"}>{t("edgeShort", { v: f.pct(l.edge, 1, true) })}</small>
            </span>
            <span className="right odds">{f.num(l.odds)}</span>
          </div>
        ))}
        <p className="li-foot muted small">{t("legsHelp")}</p>
      </div>
    </>
  );
}

const LiveInfoMaybe = ({ r }: { r: OptRes }) => (r.live ? <LiveInfo live={r.live} /> : null);

function ScenarioDetail({ r, s, kind }: { r: OptRes; s: Scenario; kind: string }) {
  const { t, f } = useCtx();
  const legText = (i: number) => {
    const l = r.legs[i];
    return l ? `${l.home} – ${l.away}: ${betText(t, l.outcome, l.home, l.away)}` : "?";
  };
  return (
    <div className="card pad section">
      <div className="row between" style={{ alignItems: "flex-start" }}>
        <div>
          <span className="eyebrow">{t("scenDetail")}</span>
          <h3 style={{ margin: "4px 0 0" }}>{t(`scen_${s.id}`)} · {kind}</h3>
        </div>
        <div style={{ minWidth: 220 }}>
          {(["staked", "median_pnl", "p95_pnl", "max_profit"] as const).map((k) => (
            <div key={k} className="kv"><span>{t(`r_${k}`)}</span><b>{f.eur(s.summary[k], k !== "staked")}</b></div>
          ))}
        </div>
      </div>
      <div className="list">
        <div className="li li-head legs">
          <span>{t("colCoupon")}</span><span>{t("hStakePayout")}</span>
          <span className="right">{t("hChance")}</span><span className="right">{t("colOdds")}</span>
        </div>
        {s.coupons.map((c) => (
          <div key={c.legs.join(",")} className="li legs">
            <span className="match">
              <span className="coupon-line">{c.legs.map((i) => <span key={i} className="tag">{legText(i)}</span>)}</span>
              <small>
                {t("evLine", { model: f.pct(c.ev_model, 1, true), market: f.pct(c.ev_market, 1, true) })}
              </small>
            </span>
            <span className="num">{f.eur(c.stake)} → <b>{f.eur(c.payout)}</b></span>
            <span className="right num stack">
              {f.pct(c.p_model, 1)}
              <small className="muted">{t("bookiesPct", { p: f.pct(c.p_market, 1) })}</small>
            </span>
            <span className="right odds">{f.num(c.odds)}</span>
          </div>
        ))}
      </div>
      <div>
        <span className="eyebrow">{t("histTitle")}</span>
        <p className="muted small" style={{ margin: "4px 0 10px" }}>{t("histHelp")}</p>
        <PnlHistogram h={s.pnl_histogram} />
      </div>
    </div>
  );
}
