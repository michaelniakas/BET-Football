"""Leak-free pre-match features: Elo + exponentially weighted team form (goals, shots on target, xG).

Every feature for a match is computed from the state *before* that match,
and the state is updated only after the row is emitted.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .data import MARKETS

STATS_FEATURES = ["elo_diff", "h_gf", "h_ga", "a_gf", "a_ga", "h_sotf", "h_sota", "a_sotf", "a_sota"]
XG_FEATURES = ["h_xgf", "h_xga", "a_xgf", "a_xga"]
MARKET_FEATURES = {"1x2": ["mkt_lh", "mkt_la"], "ou25": ["mkt_lo"]}
FEATURE_SETS = ["stats", "stats+xg", "market", "stats+market", "stats+xg+market"]

START_FORM = {"gf": 1.35, "ga": 1.35, "sotf": 4.3, "sota": 4.3}
START_XG = 1.35


def features_for(feature_set: str, market: str) -> list[str]:
    parts = {"stats": STATS_FEATURES, "xg": XG_FEATURES, "market": MARKET_FEATURES[market]}
    return [f for part in feature_set.split("+") for f in parts[part]]


def country(league: str) -> str:
    return league.rstrip("0123456789")


class FeatureBuilder:
    def __init__(self, k: float = 20.0, hfa: float = 60.0, alpha: float = 0.85, season_regress: float = 0.2):
        self.k, self.hfa, self.alpha, self.season_regress = k, hfa, alpha, season_regress
        self.elo: dict[str, float] = {}
        self.form: dict[str, dict[str, float]] = {}
        self.n: dict[str, int] = {}
        self.team_league: dict[str, str] = {}
        self.team_season: dict[str, int] = {}

    def _team(self, league: str, season: int, team: str) -> str:
        # Key by country so promoted/relegated sides keep their rating across divisions.
        key = f"{country(league)}:{team}"
        if key not in self.elo:
            # Newcomers (promoted from a league we don't load) start near the bottom of the table.
            peers = [self.elo[t] for t, lg in self.team_league.items() if lg == league]
            self.elo[key] = float(np.percentile(peers, 20)) if len(peers) >= 5 else 1500.0
            # xG form stays NaN until the team plays its first match with xG.
            self.form[key] = dict(START_FORM, xgf=np.nan, xga=np.nan)
            self.n[key] = 0
        elif self.team_season.get(key) != season:
            self.elo[key] = (1 - self.season_regress) * self.elo[key] + self.season_regress * 1500.0
        self.team_league[key] = league
        self.team_season[key] = season
        return key

    def pre_match(self, league: str, season: int, home: str, away: str) -> dict:
        h, a = self._team(league, season, home), self._team(league, season, away)
        fh, fa = self.form[h], self.form[a]
        return {
            "elo_h": self.elo[h], "elo_a": self.elo[a], "elo_diff": self.elo[h] - self.elo[a],
            "h_gf": fh["gf"], "h_ga": fh["ga"], "a_gf": fa["gf"], "a_ga": fa["ga"],
            "h_sotf": fh["sotf"], "h_sota": fh["sota"], "a_sotf": fa["sotf"], "a_sota": fa["sota"],
            "h_xgf": fh["xgf"], "h_xga": fh["xga"], "a_xgf": fa["xgf"], "a_xga": fa["xga"],
            "h_n": self.n[h], "a_n": self.n[a],
        }

    def update(self, league: str, home: str, away: str, hg: float, ag: float, hst: float, ast: float,
               hxg: float = np.nan, axg: float = np.nan) -> None:
        h, a = f"{country(league)}:{home}", f"{country(league)}:{away}"
        exp_h = 1 / (1 + 10 ** (-(self.elo[h] + self.hfa - self.elo[a]) / 400))
        score = 1.0 if hg > ag else 0.5 if hg == ag else 0.0
        gd = abs(hg - ag)
        mult = 1.0 if gd <= 1 else 1.5 if gd == 2 else (11 + gd) / 8
        delta = self.k * mult * (score - exp_h)
        self.elo[h] += delta
        self.elo[a] -= delta

        for team, gf, ga, sf, sa, xf, xa in ((h, hg, ag, hst, ast, hxg, axg), (a, ag, hg, ast, hst, axg, hxg)):
            f = self.form[team]
            for name, x in (("gf", gf), ("ga", ga), ("sotf", sf), ("sota", sa), ("xgf", xf), ("xga", xa)):
                if np.isnan(x):
                    continue
                prev = START_XG if np.isnan(f[name]) else f[name]
                f[name] = self.alpha * prev + (1 - self.alpha) * x
            self.n[team] += 1

    def build(self, matches: pd.DataFrame) -> pd.DataFrame:
        """Emit pre-match features for played matches in date order, updating state as we go."""
        rows = []
        has_xg = "hxg" in matches.columns
        for m in matches.itertuples(index=False):
            rows.append(self.pre_match(m.league, m.season, m.home, m.away))
            self.update(m.league, m.home, m.away, m.hg, m.ag, m.hst, m.ast,
                        m.hxg if has_xg else np.nan, m.axg if has_xg else np.nan)
        return pd.concat([matches.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def devig(odds: np.ndarray) -> np.ndarray:
    """Fair probabilities from decimal odds (proportional margin removal)."""
    inv = 1.0 / odds
    return inv / inv.sum(axis=1, keepdims=True)


def odds_cols(prefix: str, market: str = "1x2") -> list[str]:
    return [f"{prefix}_{s}" for s in MARKETS[market]["suffixes"]]


def _first_complete(df: pd.DataFrame, prefixes: list[str], market: str) -> np.ndarray:
    """Per row, the first odds source with a plausible book (sharpest first).

    Illiquid exchange markets show prices like 1.14/1.13/1.13, so books whose
    overround is outside [0.97, 1.15] are skipped.
    """
    out = np.full((len(df), len(MARKETS[market]["suffixes"])), np.nan)
    for prefix in prefixes:
        cols = odds_cols(prefix, market)
        if not set(cols) <= set(df.columns):
            continue
        odds = df[cols].to_numpy(dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            overround = (1.0 / odds).sum(axis=1)
        fill = np.isnan(out).any(axis=1) & (overround >= 0.97) & (overround <= 1.15)
        out[fill] = odds[fill]
    return out


def add_market(df: pd.DataFrame) -> pd.DataFrame:
    """Market probabilities known before kick-off, plus no-vig closing probabilities for CLV.

    football-data stopped publishing Pinnacle odds in late 2025, so we fall back to
    Betfair Exchange and then the market average.
    """
    for market, spec in MARKETS.items():
        mkt = [f"mkt_{s}" for s in spec["suffixes"]]
        df[mkt] = devig(_first_complete(df, ["ps", "bfe", "avg"], market))
        df[[f"close_{s}" for s in spec["suffixes"]]] = devig(_first_complete(df, ["psc", "bfec", "avgc"], market))
    df["mkt_lh"] = np.log(df.mkt_h / df.mkt_d)
    df["mkt_la"] = np.log(df.mkt_a / df.mkt_d)
    df["mkt_lo"] = np.log(df.mkt_o / df.mkt_u)
    return df
