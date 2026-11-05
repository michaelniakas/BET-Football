"""Use cases: what the two screens show and compute, on top of `model/`.

Each function mirrors the Streamlit page it replaces (pages/1_Paper_Trading.py, pages/2_Optimizer.py)
so the numbers stay identical; tests/test_golden.py holds them to that. Results are plain Python
structures with the contract's field names; NaN means "no value" and becomes null at the edge.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

import numpy as np
import pandas as pd

from model import backtest, live_odds, optimizer as opt
from model.data import LEAGUES, MARKETS
from model.features import odds_cols
from model.paper import STRATEGIES


MATCH_KEY = ["date", "league", "home", "away"]
SWEEP_EDGES = inspect.signature(backtest.edge_sweep).parameters["edges"].default
SCENARIO_KIND = {"conservative": "singles", "balanced": "system", "aggressive": "accumulators"}
RISK_FIELDS = {          # model.optimizer.risk_summary keys -> contract fields
    "Ποντάρισμα": "staked", "Αναμ. κέρδος (μοντέλο)": "expected_profit_model",
    "Αναμ. κέρδος (αγορά)": "expected_profit_market", "P(κέρδος)": "p_profit",
    "P(χάνεις όλο το ποσό)": "p_lose_all", "Διάμεσος": "median_pnl",
    "Καλό σενάριο (95%)": "p95_pnl", "Μέγιστη απόδοση": "max_profit",
}
HISTOGRAM_BINS = 60
SIMULATIONS = 20_000


# --------------------------------------------------------------------------- ports

class StrategyNotFound(Exception):
    pass


class OddsApiNotConfigured(Exception):
    pass


class OddsApiFailed(Exception):
    def __init__(self, kind: Literal["quota_exhausted", "unauthorized", "unavailable"]):
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True)
class LeagueOdds:
    fetched_at: datetime
    rows: pd.DataFrame          # model.live_odds.LIVE_COLS


@dataclass(frozen=True)
class Quota:
    remaining: int
    used: int


class Ledgers(Protocol):
    def read(self, strategy: str) -> pd.DataFrame: ...


class LiveOdds(Protocol):
    ttl: timedelta
    quota: Quota | None

    @property
    def configured(self) -> bool: ...

    def get(self, leagues: list[str]) -> dict[str, LeagueOdds]: ...


# --------------------------------------------------------------------------- shared

def _strategy(strategy: str) -> tuple[dict, str, dict]:
    if strategy not in STRATEGIES:
        raise StrategyNotFound(strategy)
    cfg = STRATEGIES[strategy]
    return cfg, cfg["market"], MARKETS[cfg["market"]]


def _upcoming_unsettled(ledger: pd.DataFrame, spec: dict, today: pd.Timestamp) -> pd.DataFrame:
    if ledger.empty:
        return ledger
    return ledger[(ledger.date >= today) & ledger[spec["target"]].isna()]


def _settled(ledger: pd.DataFrame, spec: dict) -> pd.DataFrame:
    return ledger if ledger.empty else ledger[ledger[spec["target"]].notna()]


def paginate(df: pd.DataFrame, sort: list[str], limit: int, offset: int) -> tuple[pd.DataFrame, int]:
    """Sort by `sort` (`-field` = descending; the first mention of a field wins) then the match key,
    missing values last; slice one page."""
    order: dict[str, bool] = {}
    for s in sort + MATCH_KEY:
        order.setdefault(s.lstrip("-"), not s.startswith("-"))
    by, asc = list(order), list(order.values())
    if not df.empty:
        df = df.sort_values(by, ascending=asc, kind="stable", na_position="last")
    return df.iloc[offset:offset + limit], len(df)


def _metrics(bets: pd.DataFrame) -> dict:
    s = backtest.summarize(bets).iloc[0]
    return {"bets": int(s.bets), "staked": s.staked, "profit": s.profit, "roi": s["roi_%"] / 100,
            "roi_se": s["roi_se_%"] / 100, "avg_odds": s.avg_odds, "hit_rate": s["hit_%"] / 100,
            "clv": s["clv_%"] / 100, "beat_close_rate": s["beat_close_%"] / 100}


# --------------------------------------------------------------------------- catalog

def catalog(ledgers: Ledgers, odds: LiveOdds) -> dict:
    strategies = []
    for sid, cfg in STRATEGIES.items():
        ledger = ledgers.read(sid)
        books = [b for b in backtest.BOOKS
                 if not ledger.empty and ledger.reindex(columns=odds_cols(b, cfg["market"])).notna().any().any()]
        last = ledger.logged_at.max() if not ledger.empty else None
        strategies.append({
            "id": sid, "market": cfg["market"], "leagues": cfg["leagues"], "books": books,
            "default_min_edge": cfg["edge"],
            # The job runs on GitHub Actions, whose clock is UTC.
            "ledger": {"matches": len(ledger), "last_logged_at": last.tz_localize("UTC") if last is not None else None},
        })
    return {
        "strategies": strategies,
        "leagues": [{"id": k, "name": v} for k, v in LEAGUES.items()],
        "live_odds": {"configured": odds.configured, "ttl_seconds": int(odds.ttl.total_seconds())},
    }


# --------------------------------------------------------------------------- paper trading

PICK_COLS = ["date", "league", "home", "away", "pick", "odds", "p_model", "edge"]


def picks(ledgers: Ledgers, strategy: str, book: str, min_edge: float | None, today: pd.Timestamp) -> pd.DataFrame:
    cfg, market, spec = _strategy(strategy)
    upcoming = _upcoming_unsettled(ledgers.read(strategy), spec, today)
    edge = cfg["edge"] if min_edge is None else min_edge
    if upcoming.empty:
        return pd.DataFrame(columns=PICK_COLS)
    return backtest.find_bets(upcoming, book, edge, market)[PICK_COLS]


def predictions(ledgers: Ledgers, strategy: str, book: str, leagues: list[str] | None,
                today: pd.Timestamp) -> pd.DataFrame:
    """One row per upcoming fixture: model and market probability per outcome, and the outcome with the
    largest edge at `book` odds. Same computation as `all_predictions` in the Paper Trading page."""
    _, market, spec = _strategy(strategy)
    df = _upcoming_unsettled(ledgers.read(strategy), spec, today)
    if df.empty:
        return pd.DataFrame(columns=MATCH_KEY + ["outcomes", "best_outcome", "edge"])
    out = df[MATCH_KEY].copy()
    out["outcomes"] = [
        [{"outcome": o, "p_model": row[f"p_{o}"], "p_market": row[f"mkt_{s}"]}
         for o, s in zip(spec["outcomes"], spec["suffixes"])]
        for _, row in df.iterrows()
    ]
    probs = df[[f"p_{o}" for o in spec["outcomes"]]].to_numpy()
    edges = probs * df[odds_cols(book, market)].to_numpy(dtype=float) - 1
    priced = ~np.isnan(edges).all(axis=1)
    best = np.where(priced, np.nanargmax(np.where(np.isnan(edges), -np.inf, edges), axis=1), 0)
    out["best_outcome"] = np.where(priced, np.array(spec["outcomes"])[best], None)
    out["edge"] = np.where(priced, edges[np.arange(len(df)), best], np.nan)
    if leagues:
        out = out[out.league.isin(leagues)]
    return out


def _settled_bets(ledgers: Ledgers, strategy: str, book: str, min_edge: float | None) -> tuple[pd.DataFrame, int]:
    cfg, market, spec = _strategy(strategy)
    settled = _settled(ledgers.read(strategy), spec)
    edge = cfg["edge"] if min_edge is None else min_edge
    if settled.empty:
        return pd.DataFrame(), 0
    return backtest.find_bets(settled, book, edge, market), len(settled)


def results_summary(ledgers: Ledgers, strategy: str, book: str, min_edge: float | None) -> dict:
    bets, n_settled = _settled_bets(ledgers, strategy, book, min_edge)
    if bets.empty:
        return {"settled_matches": n_settled, "metrics": None, "series": []}
    b = bets.sort_values(MATCH_KEY, kind="stable")
    series = pd.DataFrame({"date": b.date, "cumulative_profit": b.profit.cumsum(),
                           "mean_clv": b.clv.expanding().mean()})
    return {"settled_matches": n_settled, "metrics": _metrics(bets), "series": series.to_dict("records")}


def settled_bets(ledgers: Ledgers, strategy: str, book: str, min_edge: float | None) -> pd.DataFrame:
    cols = PICK_COLS + ["result", "stake", "profit", "p_close", "clv"]
    bets, _ = _settled_bets(ledgers, strategy, book, min_edge)
    return bets[cols] if not bets.empty else pd.DataFrame(columns=cols)


def edge_sweep(ledgers: Ledgers, strategy: str, book: str) -> list[dict]:
    """`model.backtest.edge_sweep`, with numeric thresholds instead of its '>6%' labels."""
    _, market, spec = _strategy(strategy)
    settled = _settled(ledgers.read(strategy), spec)
    if settled.empty:
        return []
    rows = []
    for e in SWEEP_EDGES:
        bets = backtest.find_bets(settled, book, e, market)
        if not bets.empty:
            rows.append({"min_edge": e, **_metrics(bets)})
    return rows


def probability_report(ledgers: Ledgers, strategy: str) -> dict:
    _, market, spec = _strategy(strategy)
    settled = _settled(ledgers.read(strategy), spec)
    sources = {"model": "model", "market pre-match": "market_pre_match", "market closing": "market_closing"}
    if settled.empty:
        return {"n": 0, "rows": [{"source": s, "log_loss": None, "brier": None} for s in sources.values()]}
    cols = [f"{p}_{x}" for p, xs in (("p", spec["outcomes"]), ("mkt", spec["suffixes"]), ("close", spec["suffixes"]))
            for x in xs]
    if settled.dropna(subset=cols).empty:      # model.backtest.probability_report cannot score zero rows
        return {"n": 0, "rows": [{"source": s, "log_loss": None, "brier": None} for s in sources.values()]}
    rep = backtest.probability_report(settled, market)
    return {"n": int(rep.n.iloc[0]),
            "rows": [{"source": sources[i], "log_loss": r.log_loss, "brier": r.brier} for i, r in rep.iterrows()]}


# --------------------------------------------------------------------------- optimizer

def _live_info(entries: dict[str, LeagueOdds], fixtures: pd.DataFrame, quota: Quota | None) -> tuple[pd.DataFrame, dict]:
    """Attach live rows to our fixtures, league by league; report what matched and what did not."""
    event = ["kickoff", "api_home", "api_away"]
    mapped_parts, leagues = [], []
    for lg, entry in entries.items():
        events = entry.rows[event].drop_duplicates()     # one Odds API event = kickoff + teams
        # match_to_fixtures returns a frame with a duplicated `league` column when nothing matches.
        mapped = live_odds.match_to_fixtures(entry.rows, fixtures)[0] if not (events.empty or fixtures.empty) else None
        if mapped is None or mapped.empty:
            mapped, miss = None, events
        else:
            hit = events.merge(mapped[event].drop_duplicates(), how="left", on=event, indicator=True)
            miss = hit[hit._merge == "left_only"]
        leagues.append({
            "league": lg, "fetched_at": entry.fetched_at,
            "events": len(events), "matched": len(events) - len(miss),
            "unmatched": [{"kickoff": k, "home": h, "away": a} for k, h, a in miss[event].itertuples(index=False)],
        })
        if mapped is not None:
            mapped_parts.append(mapped)
    live = pd.concat(mapped_parts, ignore_index=True) if mapped_parts else pd.DataFrame(columns=live_odds.LIVE_COLS)
    info = {
        "quota": {"remaining": quota.remaining, "used": quota.used} if quota else None,
        "leagues": leagues,
        "bookmakers": sorted(live.bookmaker.unique()),
    }
    return live, info


def optimize(ledgers: Ledgers, odds: LiveOdds, budget: float, markets: list[str], min_prob: float,
             min_edge: float, source: str, book: str | None, leagues: list[str] | None,
             bookmakers: list[str] | None, today: pd.Timestamp) -> dict:
    """Same flow as the Optimizer page: selections -> candidate legs -> three scenarios -> simulation."""
    def upcoming(name: str) -> pd.DataFrame:
        df = ledgers.read(name)
        return df[df.date >= today] if not df.empty else df

    live, info = None, None
    if source == "live":
        covered = {lg for m in markets for lg in STRATEGIES[m]["leagues"]}
        entries = odds.get(sorted(set(leagues) & covered))      # no credits for leagues no market uses
        fixtures = pd.concat([df for df in map(upcoming, STRATEGIES) if not df.empty] or [pd.DataFrame()])
        live, info = _live_info(entries, fixtures, odds.quota)

    chosen = {m: upcoming(m) for m in STRATEGIES if m in markets}
    n_upcoming = sum(len(df) for df in chosen.values())
    result = {"upcoming_matches": n_upcoming, "simulations": SIMULATIONS, "live": info, "legs": [], "scenarios": []}
    chosen = {m: df for m, df in chosen.items() if not df.empty}
    if not chosen or (live is not None and live.empty):
        return result

    # The page falls back to snapshot odds when live odds match nothing; here live means live only (above).
    sel = opt.selections(chosen, live, book or "avg", sorted(set(bookmakers)) if bookmakers else None)
    legs = opt.candidate_legs(sel, min_prob, min_edge)
    if legs.empty:
        return result

    legs = legs.assign(min_odds=(1 + min_edge) / legs.p_model)
    result["legs"] = legs[MATCH_KEY + ["market", "outcome", "odds", "bookmaker", "p_model", "p_market", "edge",
                                       "min_odds"]].to_dict("records")
    for sid, sc in opt.scenarios(len(legs)).items():
        coupons = opt.build_coupons(legs, sc["combos"], budget)
        if coupons.empty:
            continue
        pnl = opt.simulate(legs, coupons, n=SIMULATIONS)
        counts, bin_edges = np.histogram(pnl, bins=HISTOGRAM_BINS)
        result["scenarios"].append({
            "id": sid, "kind": SCENARIO_KIND[sid],
            "system": {"pick": 2, "of": max(max(c) for c in sc["combos"]) + 1} if sid == "balanced" else None,
            "summary": {RISK_FIELDS[k]: v for k, v in opt.risk_summary(coupons, pnl).items()},
            "coupons": [{"legs": list(c.legs), "odds": c.odds, "p_model": c.p_model, "p_market": c.p_market,
                         "ev_model": c.ev_model, "ev_market": c.ev_market, "stake": c.stake, "payout": c.payout}
                        for c in coupons.itertuples()],
            "pnl_histogram": {"bin_edges": bin_edges.tolist(), "probabilities": (counts / len(pnl)).tolist()},
        })
    return result
