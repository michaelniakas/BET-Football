import { useId, useState } from "react";
import { api, useApi, type ApiState, type Schemas } from "../api";
import {
  at, betText, CountUp, Info, initials, League, LeagueSelect, Loaded, MarketSwitch, matchKey, Note, ordered,
  OutcomeLegend, Pitch, Rich, RuleBar, Stat, Strip, useCtx, type LeagueId,
} from "../ui";

// ponytail: one page of 500 upcoming fixtures; the ledgers hold ~150 a week. Paginate if they grow.
const ALL = 500;

export function Picks() {
  const { t, s, rule } = useCtx();
  const path = { strategy: s.id };
  const summary = useApi((signal) => api.GET("/api/v1/strategies/{strategy}/results/summary", {
    params: { path, query: { book: rule.book, min_edge: rule.edge } }, signal,
  }), [s.id, rule.book, rule.edge]);
  const picks = useApi((signal) => api.GET("/api/v1/strategies/{strategy}/picks", {
    params: { path, query: { book: rule.book, min_edge: rule.edge, limit: ALL } }, signal,
  }), [s.id, rule.book, rule.edge]);
  const preds = useApi((signal) => api.GET("/api/v1/strategies/{strategy}/predictions", {
    params: { path, query: { book: rule.book, limit: ALL } }, signal,
  }), [s.id, rule.book]);

  return (
    <>
      <Hero summary={summary} preds={preds.data?.items} />
      {s.ledger.matches === 0 ? <Note>{t("ledgerEmpty")}</Note> : (
        <>
          <section className="section" style={at(1)}>
            <div className="section-head">
              <div>
                <span className="eyebrow">{t("betsToPlace")}</span>
                <h2 style={{ marginTop: 4 }}>
                  {!picks.data ? t("picksTitle")
                    : picks.data.total === 0 ? t("picksNone")
                    : t(picks.data.total === 1 ? "picksOne" : "picksMany", { n: picks.data.total })}
                </h2>
              </div>
              <RuleBar />
            </div>
            <Loaded state={picks} height={280}>
              {(page) => page.items.length === 0 ? <div className="card pad muted">{t("noPicks")}</div> : (
                <div className="grid-cards anim">
                  {page.items.map((p, i) => <PickCard key={matchKey(p)} p={p} i={i} />)}
                </div>
              )}
            </Loaded>
          </section>
          <Explainer />
          <Predictions preds={preds} />
        </>
      )}
    </>
  );
}

function Hero({ summary, preds }: {
  summary: ApiState<Schemas["ResultsSummary"]>;
  preds?: Schemas["Prediction"][];
}) {
  const { t, f, s } = useCtx();
  const dates = preds?.map((p) => p.date).sort() ?? [];
  const first = dates[0], last = dates[dates.length - 1];
  return (
    <header className="hero" style={at(0)}>
      <Pitch />
      <div className="hero-top">
        <div>
          {s.ledger.last_logged_at && (
            <span className="live"><i />{t("updated", { at: f.dateTime(s.ledger.last_logged_at), n: s.ledger.matches })}</span>
          )}
          <h1 style={{ marginTop: 10 }}>
            {!preds ? t("matchdayLoading")
              : !first || !last ? t("noMatchday")
              : t("matchday", { range: first === last ? f.date(first) : `${f.date(first)} – ${f.date(last)}` })}
          </h1>
          <p className="lede">{t("picksLede")}</p>
        </div>
        <MarketSwitch />
      </div>
      <Loaded state={summary} height={110}>{(sum) => <BetBoard sum={sum} />}</Loaded>
    </header>
  );
}

/** Paper-bet record at the current rule: the scoreboard and its verdict. Also the head of Results › Our bets. */
export function BetBoard({ sum }: { sum: Schemas["ResultsSummary"] }) {
  const { t, f } = useCtx();
  const m = sum.metrics;
  if (m === null) {
    return <p className="note">{sum.settled_matches === 0 ? t("noSettled") : t("settledNoPicks", { n: sum.settled_matches })}</p>;
  }
  const won = Math.round(m.hit_rate * m.bets);
  const se = m.roi_se;
  const clear = se !== null && Math.abs(m.roi) >= 2 * se;
  const beat = m.beat_close_rate;
  const clv = beat === null ? "" : ` ${t(beat > 0.5 ? "verdictClvGood" : "verdictClvNotYet", { rate: f.pct(beat, 0) })}`;
  return (
    <>
      <div className="board">
        <Stat label={t("sBets")} value={<CountUp value={m.bets} show={(v) => f.num(v, 0)} />}
              sub={t("wonLost", { won, lost: m.bets - won })} />
        <Stat label={t("sProfit")} info="units" tone={m.profit}
              value={<CountUp value={m.profit} show={(v) => f.num(v, 2, true)} />} sub={t("perBet")} />
        <Stat label={t("sReturn")} info="roi" tone={m.roi}
              value={<CountUp value={m.roi} show={(v) => f.pct(v, 1, true)} />}
              sub={se === null ? t("oneBet") : t("roiRange", { lo: f.pct(m.roi - 2 * se, 0, true), hi: f.pct(m.roi + 2 * se, 0, true) })} />
        <Stat label={t("sBeatClose")} info="clv"
              value={beat === null ? "–" : <CountUp value={beat} show={(v) => f.pct(v, 0)} />} sub={t("beatCloseSub")} />
      </div>
      <div className="verdict">
        {!clear ? (
          <>
            <span className="chip-state">{t("vEarly")}</span>
            <p>{t(m.profit >= 0 ? "verdictEarlyUp" : "verdictEarlyDown", { n: m.bets, v: f.num(Math.abs(m.profit), 2) })}{clv}</p>
          </>
        ) : m.roi > 0 ? (
          <><span className="chip-state good">{t("vAhead")}</span><p>{t("verdictAhead", { n: m.bets })}{clv}</p></>
        ) : (
          <><span className="chip-state bad">{t("vBehind")}</span><p>{t("verdictBehind", { n: m.bets })}{clv}</p></>
        )}
      </div>
    </>
  );
}

function PickCard({ p, i }: { p: Schemas["Pick"]; i: number }) {
  const { t, f } = useCtx();
  const implied = 1 / p.odds;
  return (
    <article className="card pick" style={at(i)}>
      <div className="meta"><League id={p.league} /><span>{f.date(p.date)}</span></div>
      <div className="fixture">
        <div><span className="crest h" aria-hidden="true">{initials(p.home)}</span><div className="team">{p.home}</div></div>
        <span className="vs">{t("vs")}</span>
        <div><span className="crest a" aria-hidden="true">{initials(p.away)}</span><div className="team">{p.away}</div></div>
      </div>
      <div className="bet">
        <div><div className="eyebrow">{t("betOn")}</div><div className="bet-what">{betText(t, p.pick, p.home, p.away)}</div></div>
        <div className="odds"><span aria-hidden="true">{f.num(p.odds)}</span><span className="visually-hidden">{t("oddsAria", { o: f.num(p.odds) })}</span></div>
      </div>
      <div className="vsbar">
        <span className="muted">{t("model")}</span>
        <span className="meter" aria-hidden="true"><i className="m-model" style={{ width: `${p.p_model * 100}%` }} /></span>
        <span className="val">{f.pct(p.p_model, 0)}</span>
        <span className="muted">{t("bookmaker")}</span>
        <span className="meter" aria-hidden="true"><i className="m-book" style={{ width: `${implied * 100}%` }} /></span>
        <span className="val">{f.pct(implied, 0)}</span>
      </div>
      <div className="edge-row">
        <span className="edge-badge">{f.pct(p.edge, 1, true)} <small>{t("edge")}</small></span>
        <Info id="edge" />
      </div>
      <p className="plain"><Rich k="worthDownTo" parts={{ odds: <b>{f.minOdds(p.min_odds)}</b> }} /></p>
    </article>
  );
}

function Explainer() {
  const { t, f, rule } = useCtx();
  const id = useId();
  const [pText, setP] = useState("53");
  const [oText, setO] = useState("2.10");
  const p = Number(pText) / 100, o = Number(oText);
  const pOk = pText.trim() !== "" && p > 0 && p < 1;
  const oOk = oText.trim() !== "" && o > 1;
  const e = p * o - 1;
  const margin = rule.edge;
  return (
    <div className="card explain" style={at(2)}>
      <div>
        <span className="eyebrow">{t("newHere")}</span>
        <h2 style={{ marginTop: 4 }}>{t("howToRead")}</h2>
        <ol className="steps">
          <li><p><b>{t("step1a")}</b> {t("step1b", { odds: f.num(2.1), p: f.pct(1 / 2.1, 0) })}</p></li>
          <li><p><b>{t("step2a")}</b> {t("step2b", { p: f.pct(0.53, 0) })}</p></li>
          <li><p><b>{t("step3a")}</b> {t("step3b", { calc: `${f.pct(0.53, 0)} × ${f.num(2.1)} − 1 = ${f.pct(0.53 * 2.1 - 1, 0, true)}` })}</p></li>
          <li><p><b>{t("step4a")}</b> {t("step4b")}</p></li>
        </ol>
      </div>
      <div>
        <span className="eyebrow">{t("tryIt")}</span>
        <h2 style={{ marginTop: 4 }}>{t("calcTitle")}</h2>
        <div className="calc">
          <label htmlFor={`${id}-p`}>{t("calcP")}
            <input id={`${id}-p`} type="number" min={1} max={99} step={1} inputMode="decimal" value={pText}
                   aria-invalid={!pOk} onChange={(ev) => setP(ev.target.value)} />
          </label>
          <label htmlFor={`${id}-o`}>{t("calcO")}
            <input id={`${id}-o`} type="number" min={1.01} step={0.01} inputMode="decimal" value={oText}
                   aria-invalid={!oOk} onChange={(ev) => setO(ev.target.value)} />
          </label>
        </div>
        <div className="calc-out" aria-live="polite">
          {!pOk || !oOk ? <span className="muted">{t("calcInvalid")}</span> : (
            <>
              <span className="eyebrow">{t("edge")}</span>
              <b className={e >= 0 ? "pos" : "neg"}>{f.pct(e, 1, true)}</b>
              <span className="muted">
                {t("calcImplied", { p: f.pct(1 / o, 0) })}{" "}
                {t(e >= margin ? "calcBet" : e >= 0 ? "calcSmall" : "calcSkip", { m: f.pct(margin, 0) })}
              </span>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Predictions({ preds }: { preds: ApiState<Schemas["PredictionPage"]> }) {
  const { t, f, rule } = useCtx();
  const [league, setLeague] = useState<LeagueId | "">("");
  return (
    <section className="section" style={at(3)}>
      <div className="section-head">
        <div>
          <span className="eyebrow">{t("allUpcoming")}</span>
          <h2 style={{ marginTop: 4 }}>{t("modelVsBooks")}</h2>
        </div>
        <OutcomeLegend />
      </div>
      <Loaded state={preds} height={360}>
        {(page) => {
          if (page.items.length === 0) return <Note>{t("noUpcoming")}</Note>;
          const present = [...new Set(page.items.map((p) => p.league))].sort();
          const lg = league && present.includes(league) ? league : "";    // a league no longer listed filters nothing
          const rows = lg ? page.items.filter((p) => p.league === lg) : page.items;
          return (
            <div className="section">
              <div className="row between">
                <LeagueSelect value={lg} options={present} onChange={setLeague} />
                {page.total > page.items.length && <span className="muted small">{t("firstOf", { n: page.items.length, total: page.total })}</span>}
              </div>
              <div className="card list">
                <div className="li li-head">
                  <span>{t("hDate")}</span><span>{t("hMatch")}</span><span>{t("hProbs")}</span>
                  <span className="right">{t("hBestEdge")}</span>
                </div>
                {rows.map((r) => {
                  const os = ordered(r.outcomes);
                  const model = os.map((o) => o.p_model), market = os.map((o) => o.p_market);
                  const list = (ps: number[]) => os.map((o, i) => `${t(`out_${o.outcome}`)} ${f.pct(ps[i], 0)}`).join(", ");
                  const hot = r.best !== null && r.best.edge >= rule.edge;
                  return (
                    <div key={matchKey(r)} className="li">
                      <span className="when"><span>{f.date(r.date)}</span><League id={r.league} /></span>
                      <span className="match">{r.home} – {r.away}</span>
                      <div className="probs" role="img" aria-label={t("probsAria", { model: list(model), market: list(market) })}>
                        <Strip ps={model} /><Strip ps={market} market />
                      </div>
                      <span className="right">
                        {r.best === null ? <span className="tag">{t("noOdds")}</span> : (
                          <span className={`tag${hot ? " win" : ""}`}>
                            {hot && <span aria-hidden="true">🔥</span>}
                            {t(`out_${r.best.outcome}`)} {f.pct(r.best.edge, 1, true)}
                          </span>
                        )}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        }}
      </Loaded>
    </section>
  );
}
