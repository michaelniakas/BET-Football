"""Stake allocation and coupon scenarios (singles, system, accumulators) from model probabilities.

Every coupon is evaluated twice: with the model's probabilities and with the market's
no-vig probabilities. Accumulators multiply the bookmaker margin, so the market view
is the honest baseline when the model has no proven edge.
"""
from __future__ import annotations

from itertools import combinations

import numpy as np
import pandas as pd

from .data import MARKETS

MATCH_KEY = ["date", "league", "home", "away"]
SEL_KEY = MATCH_KEY + ["market", "outcome"]
PICK_LABELS = {"H": "1", "D": "X", "A": "2", "O": "Over 2.5", "U": "Under 2.5"}


def selections(ledgers: dict[str, pd.DataFrame], live: pd.DataFrame | None = None,
               fallback_book: str = "max", bookmakers: list[str] | None = None) -> pd.DataFrame:
    """One row per (match, market, outcome): model and market probability, best odds and where to get them.

    With live odds, only matches that have live prices are kept and the market probability is the
    no-vig consensus of the live bookmakers; otherwise the football-data snapshot is used.
    """
    parts = []
    for market, df in ledgers.items():
        spec = MARKETS[market]
        for o, s in zip(spec["outcomes"], spec["suffixes"]):
            part = df[MATCH_KEY].copy()
            part["market"], part["outcome"], part["pick"] = market, o, PICK_LABELS[o]
            part["p_model"] = df[f"p_{o}"].to_numpy()
            part["p_market"] = df[f"mkt_{s}"].to_numpy()
            part["odds"] = df[f"{fallback_book}_{s}"].to_numpy()
            part["bookmaker"] = f"football-data ({fallback_book})"
            parts.append(part)
    sel = pd.concat(parts, ignore_index=True)

    if live is not None and not live.empty:
        lv = live[live.bookmaker.isin(bookmakers)] if bookmakers else live
        best = lv.sort_values("odds").groupby(SEL_KEY).tail(1)[SEL_KEY + ["odds", "bookmaker"]]
        cons = lv.groupby(SEL_KEY).odds.mean().rename("avg_odds").reset_index()
        cons["inv"] = 1 / cons.avg_odds
        cons["p_market"] = cons.inv / cons.groupby(MATCH_KEY + ["market"]).inv.transform("sum")
        sel = (sel.drop(columns=["odds", "bookmaker", "p_market"])
                  .merge(best, on=SEL_KEY).merge(cons[SEL_KEY + ["p_market"]], on=SEL_KEY))

    sel["edge"] = sel.p_model * sel.odds - 1
    return sel.dropna(subset=["p_model", "p_market", "odds"]).reset_index(drop=True)


def candidate_legs(sel: pd.DataFrame, min_prob: float = 0.3, min_edge: float = 0.0, max_legs: int = 8) -> pd.DataFrame:
    """Best selection per match (1X2 and O/U of the same match are correlated, so never both), ranked by edge."""
    s = sel[(sel.p_model >= min_prob) & (sel.edge >= min_edge) & (sel.odds > 1.01)]
    s = s.sort_values("edge", ascending=False).drop_duplicates(MATCH_KEY)
    return s.head(max_legs).reset_index(drop=True)


def scenarios(n_legs: int) -> dict[str, dict]:
    """Three coupon structures over the top-ranked legs (indices into the legs table)."""
    n_sys = min(n_legs, 4)
    return {
        "conservative": {"title": "Συντηρητικό · Μονά",
                         "combos": [(i,) for i in range(min(n_legs, 6))]},
        "balanced": {"title": f"Ισορροπημένο · Σύστημα 2/{n_sys}",
                     "combos": list(combinations(range(n_sys), 2)) if n_sys >= 2 else []},
        "aggressive": {"title": "Επιθετικό · Παρολί 3 & 4",
                       "combos": [tuple(range(k)) for k in (3, 4) if n_legs >= k]},
    }


def build_coupons(legs: pd.DataFrame, combos: list[tuple[int, ...]], budget: float) -> pd.DataFrame:
    """Price each coupon and split the budget in proportion to its Kelly fraction (equal split if none is +EV)."""
    rows = []
    for c in combos:
        L = legs.iloc[list(c)]
        odds, p, pm = L.odds.prod(), L.p_model.prod(), L.p_market.prod()
        rows.append({
            "legs": c, "n": len(c),
            "coupon": " + ".join(f"{h}-{a}: {k}" for h, a, k in zip(L.home, L.away, L.pick)),
            "odds": odds, "p_model": p, "p_market": pm, "ev_model": p * odds - 1, "ev_market": pm * odds - 1,
        })
    cp = pd.DataFrame(rows)
    if cp.empty:
        return cp
    kelly = np.clip(cp.ev_model / (cp.odds - 1), 0, None)
    weights = kelly / kelly.sum() if kelly.sum() > 0 else np.full(len(cp), 1 / len(cp))
    cp["stake"] = np.floor(budget * weights * 10) / 10          # whole 10-cent amounts, never above budget
    cp["payout"] = cp.stake * cp.odds
    return cp[cp.stake > 0].reset_index(drop=True)


def simulate(legs: pd.DataFrame, coupons: pd.DataFrame, prob_col: str = "p_model",
             n: int = 20_000, seed: int = 0) -> np.ndarray:
    """Profit/loss of the whole coupon set over n simulated rounds (legs are independent matches)."""
    rng = np.random.default_rng(seed)
    wins = rng.random((n, len(legs))) < legs[prob_col].to_numpy()
    payout = np.zeros(n)
    for c in coupons.itertuples():
        payout += c.payout * wins[:, list(c.legs)].all(axis=1)
    return payout - coupons.stake.sum()


def risk_summary(coupons: pd.DataFrame, pnl: np.ndarray) -> dict:
    staked = coupons.stake.sum()
    return {
        "Ποντάρισμα": staked,
        "Αναμ. κέρδος (μοντέλο)": float((coupons.stake * coupons.ev_model).sum()),
        "Αναμ. κέρδος (αγορά)": float((coupons.stake * coupons.ev_market).sum()),
        "P(κέρδος)": float((pnl > 0).mean()),
        "P(χάνεις όλο το ποσό)": float((pnl <= -staked + 1e-9).mean()),
        "Διάμεσος": float(np.median(pnl)),
        "Καλό σενάριο (95%)": float(np.percentile(pnl, 95)),
        "Μέγιστη απόδοση": float(coupons.payout.sum() - staked),
    }
