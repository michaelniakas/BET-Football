"""Paper trading: snapshot model probabilities + odds for upcoming fixtures, settle them later.

Every scored fixture is logged (not only picks), so CLV can be measured afterwards at any
edge threshold. The first snapshot of a fixture is kept: that is the price we would have taken.

    python -m model.paper log          # score this week's fixtures and append to the ledgers
    python -m model.paper settle       # attach results + closing odds to finished matches
    python -m model.paper report       # ROI / CLV of the settled picks
    python -m model.paper weekly       # settle + log + report (what the scheduled task runs)
"""
from __future__ import annotations

import argparse
import sys
import traceback

import pandas as pd

from . import backtest, data
from .features import FeatureBuilder, add_market
from .predict import score_fixtures

PAPER_DIR = data.ROOT / "paper"
TOP5 = ["E0", "SP1", "D1", "I1", "F1"]

# The O/U strategy is the hypothesis under test; 1X2 is the control (backtest CLV was negative).
STRATEGIES = {
    "ou25": {"market": "ou25", "features": "stats+market", "leagues": TOP5, "edge": 0.06},
    "1x2": {"market": "1x2", "features": "stats+market", "leagues": list(data.LEAGUES), "edge": 0.05},
}
KEY = ["date", "league", "home", "away"]
SCORE = ["hg", "ag"]       # final score (football-data FTHG, FTAG)
# Settle stops looking for a fixture's result and score this long after kick-off: a postponed match
# reappears under another date, and a result football-data changed after we settled never gets a score.
GIVE_UP = pd.Timedelta(days=30)
# The same teams in the same league this close are one game: fixtures.csv keeps a moved game's old date, the season
# files list the day it was played. Further apart, it is another game (ingest's NEAR rule).
NEAR = pd.Timedelta(days=1)


def ledger_path(name: str):
    return PAPER_DIR / f"ledger_{name}.csv"


def read_ledger(name: str) -> pd.DataFrame:
    path = ledger_path(name)
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, parse_dates=["date", "logged_at"])


def log(name: str) -> None:
    cfg = STRATEGIES[name]
    spec = data.MARKETS[cfg["market"]]
    fx = score_fixtures(cfg["leagues"], cfg["market"], cfg["features"])
    if fx.empty:
        print(f"[{name}] no fixtures with odds")
        return
    odds = [f"{b}_{s}" for b in ("avg", "max", "b365", "ps", "bfe") for s in spec["suffixes"]]
    cols = KEY + ["season"] + [f"p_{o}" for o in spec["outcomes"]] + [f"mkt_{s}" for s in spec["suffixes"]] + odds
    now = pd.Timestamp.now(tz="UTC")
    started = fx.kickoff <= now                     # NaT (no kick-off time) is False: the date alone decides
    snap = fx.loc[~started, cols].assign(logged_at=now.tz_localize(None).floor("s"))   # the database reads UTC
    snap = snap[snap.date >= now.tz_localize(None).normalize()]

    ledger = read_ledger(name)
    if not ledger.empty:
        snap = snap[_nearest(snap[KEY], ledger[KEY].assign(seen=True)).seen.isna()]
    PAPER_DIR.mkdir(exist_ok=True)
    pd.concat([ledger, snap], ignore_index=True).to_csv(ledger_path(name), index=False)

    picks = backtest.find_bets(snap.assign(**{spec["target"]: None}, **_no_close(spec)),
                               "avg", cfg["edge"], cfg["market"])
    print(f"[{name}] logged {len(snap)} new fixtures, {len(picks)} picks at edge > {cfg['edge']:.0%}")
    if not picks.empty:
        print(picks[["date", "league", "home", "away", "pick", "odds", "p_model", "edge"]]
              .sort_values("date").to_string(index=False))


def _nearest(rows: pd.DataFrame, other: pd.DataFrame) -> pd.DataFrame:
    """For each of `rows` (KEY columns), `other`'s row of the same game: the same date, else within NEAR. Indexed like
    `rows`, all NaN where there is none; `other`'s columns except its date."""
    m = rows[KEY].rename_axis("row").reset_index().merge(other, on=["league", "home", "away"], suffixes=("", "_other"))
    m = m.assign(off=(m.date_other - m.date).abs())
    m = m[m.off <= NEAR].sort_values("off", kind="stable").drop_duplicates("row")
    return m.set_index("row").drop(columns=["date_other", "off"]).reindex(rows.index)


def _no_close(spec: dict) -> dict:
    return {f"close_{s}": float("nan") for s in spec["suffixes"]}


def settle(name: str) -> None:
    """Fill result, final score + no-vig closing probabilities from football-data's season files.

    A fixture settled before the score was kept gets only the score; its result and closing odds stay.
    """
    ledger = read_ledger(name)
    if ledger.empty:
        print(f"[{name}] ledger is empty")
        return
    spec = data.MARKETS[STRATEGIES[name]["market"]]
    close_cols = [f"close_{s}" for s in spec["suffixes"]]
    for c in [spec["target"]] + close_cols + SCORE:
        if c not in ledger:
            ledger[c] = None if c == spec["target"] else float("nan")
    today = pd.Timestamp.today().normalize()
    past = (ledger.date < today) & (ledger.date >= today - GIVE_UP)
    open_rows = ledger[spec["target"]].isna() & past
    todo = open_rows | (ledger.hg.isna() & past)
    if not todo.any():
        print(f"[{name}] nothing to settle")
        return

    leagues = sorted(ledger.loc[todo, "league"].unique())
    seasons = sorted(ledger.loc[todo, "season"].unique())
    data.download(leagues, seasons)
    played = add_market(FeatureBuilder().build(data.load(leagues, seasons)))
    played = played[KEY + [spec["target"]] + close_cols + SCORE]

    merged = _nearest(ledger.loc[todo], played)
    for c in [spec["target"]] + close_cols:
        ledger.loc[open_rows, c] = merged[c]
    # Only a score that agrees with the stored result: the database refuses one that contradicts it.
    agrees = merged.index[merged[spec["target"]] == ledger.loc[todo, spec["target"]]]
    scored = agrees[ledger.loc[agrees, "hg"].isna()]
    for c in SCORE:
        ledger.loc[scored, c] = merged.loc[scored, c]
    done = ledger.loc[open_rows, spec["target"]].notna().sum()
    ledger.to_csv(ledger_path(name), index=False)
    print(f"[{name}] settled {done} of {open_rows.sum()} finished fixtures, {len(scored)} final scores added")


def report(name: str, book: str = "avg", edge: float | None = None) -> None:
    cfg = STRATEGIES[name]
    spec = data.MARKETS[cfg["market"]]
    ledger = read_ledger(name)
    if ledger.empty or spec["target"] not in ledger or ledger[spec["target"]].isna().all():
        print(f"[{name}] no settled fixtures yet")
        return
    settled = ledger[ledger[spec["target"]].notna()]
    edge = cfg["edge"] if edge is None else edge
    print(f"\n[{name}] {len(settled)} settled fixtures, "
          f"{settled.date.min():%Y-%m-%d} .. {settled.date.max():%Y-%m-%d}")
    print(backtest.probability_report(settled, cfg["market"]))
    bets = backtest.find_bets(settled, book, edge, cfg["market"])
    print(f"\nPicks at {backtest.BOOKS[book]} odds, edge > {edge:.0%}:")
    print(backtest.summarize(bets) if not bets.empty else "  none yet")
    print("\nAll thresholds (CLV is the number to watch; ROI needs hundreds of bets):")
    print(backtest.edge_sweep(settled, book, cfg["market"]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["log", "settle", "report", "weekly"])
    ap.add_argument("--strategy", choices=[*STRATEGIES, "all"], default="all")
    ap.add_argument("--book", choices=backtest.BOOKS, default="avg")
    ap.add_argument("--edge", type=float, default=None)
    args = ap.parse_args()

    pd.set_option("display.width", 160, "display.max_columns", 20, "display.float_format", "{:.3f}".format)
    names = list(STRATEGIES) if args.strategy == "all" else [args.strategy]
    failed = []
    for name in names:
        try:
            if args.command in ("settle", "weekly"):
                settle(name)
            if args.command in ("log", "weekly"):
                log(name)
            if args.command in ("report", "weekly"):
                report(name, args.book, args.edge)
        except Exception:  # one strategy's crash must not stop the others from settling/logging
            traceback.print_exc()
            failed.append(name)
    if failed:
        sys.exit(f"failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
