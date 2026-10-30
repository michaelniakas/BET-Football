"""Probability quality vs the market, value-bet selection, ROI and closing-line value."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .data import MARKETS
from .features import odds_cols

BOOKS = {"avg": "market average", "max": "best price (line shopping)", "b365": "Bet365", "ps": "Pinnacle"}


def _scores(p: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    onehot = np.eye(p.shape[1])[y]
    logloss = -np.mean(np.log(np.clip(p[np.arange(len(y)), y], 1e-12, 1)))
    brier = np.mean(np.sum((p - onehot) ** 2, axis=1))
    return logloss, brier


def probability_report(pred: pd.DataFrame, market: str = "1x2") -> pd.DataFrame:
    """Log loss / Brier of the model vs pre-match and closing market, on the same matches."""
    spec = MARKETS[market]
    model_cols = [f"p_{o}" for o in spec["outcomes"]]
    mkt_cols = [f"mkt_{s}" for s in spec["suffixes"]]
    close_cols = [f"close_{s}" for s in spec["suffixes"]]
    d = pred.dropna(subset=model_cols + mkt_cols + close_cols)
    y = d[spec["target"]].map({o: i for i, o in enumerate(spec["outcomes"])}).to_numpy()
    rows = {
        "model": _scores(d[model_cols].to_numpy(), y),
        "market pre-match": _scores(d[mkt_cols].to_numpy(), y),
        "market closing": _scores(d[close_cols].to_numpy(), y),
    }
    out = pd.DataFrame(rows, index=["log_loss", "brier"]).T
    out["n"] = len(d)
    return out


def find_bets(pred: pd.DataFrame, book: str = "avg", min_edge: float = 0.03, market: str = "1x2",
              max_odds: float = 10.0, kelly: float = 0.0) -> pd.DataFrame:
    """At most one bet per match: the outcome with the largest positive expected value.

    Stakes are 1 unit flat, or a fraction of Kelly (capped at 5%) when kelly > 0.
    """
    spec = MARKETS[market]
    odds = pred[odds_cols(book, market)].to_numpy(dtype=float)
    probs = pred[[f"p_{o}" for o in spec["outcomes"]]].to_numpy()
    ev = probs * odds - 1
    ev = np.where((odds <= max_odds) & ~np.isnan(odds), ev, -np.inf)
    pick = ev.argmax(axis=1)
    idx = np.arange(len(pred))
    best_ev = ev[idx, pick]
    keep = best_ev > min_edge

    bets = pred.loc[keep, ["date", "league", "season", "home", "away"]].copy()
    bets["result"] = pred.loc[keep, spec["target"]]
    k = pick[keep]
    bets["pick"] = np.array(spec["outcomes"])[k]
    bets["odds"] = odds[keep, k]
    bets["p_model"] = probs[keep, k]
    bets["edge"] = best_ev[keep]
    if kelly > 0:
        bets["stake"] = np.clip(kelly * bets.edge / (bets.odds - 1), 0, 0.05) * 100
    else:
        bets["stake"] = 1.0
    won = bets.pick == bets.result
    bets["profit"] = np.where(won, bets.stake * (bets.odds - 1), -bets.stake)

    # Closing-line value: EV of the price we took, measured with no-vig closing probabilities.
    close = pred.loc[keep, [f"close_{s}" for s in spec["suffixes"]]].to_numpy()
    bets["p_close"] = close[np.arange(len(k)), k]
    bets["clv"] = bets.odds * bets.p_close - 1
    return bets


def summarize(bets: pd.DataFrame, by: str | list[str] | None = None) -> pd.DataFrame:
    def agg(g: pd.DataFrame) -> pd.Series:
        roi = g.profit.sum() / g.stake.sum()
        se = (g.profit / g.stake).std(ddof=1) / np.sqrt(len(g)) if len(g) > 1 else np.nan
        return pd.Series({
            "bets": len(g), "staked": g.stake.sum(), "profit": g.profit.sum(),
            "roi_%": 100 * roi, "roi_se_%": 100 * se,
            "avg_odds": g.odds.mean(), "hit_%": 100 * (g.pick == g.result).mean(),
            "clv_%": 100 * g.clv.mean(), "beat_close_%": 100 * (g.clv.dropna() > 0).mean(),
        })
    if bets.empty:
        return pd.DataFrame()
    if by is None:
        return agg(bets).to_frame("all").T
    return bets.groupby(by).apply(agg, include_groups=False)


def edge_sweep(pred: pd.DataFrame, book: str, market: str = "1x2",
               edges=(0.0, 0.02, 0.04, 0.06, 0.08, 0.10, 0.15)) -> pd.DataFrame:
    rows = {f">{e:.0%}": summarize(find_bets(pred, book, e, market)) for e in edges}
    return pd.DataFrame({k: v.iloc[0] for k, v in rows.items() if not v.empty}).T
