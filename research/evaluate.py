"""Score the news pilot on settled fixtures: news vs model vs market, and the CLV of each one's picks.

    python -m research.evaluate [--news research/news_1x2.csv]

Numbers, not claims: on this few fixtures a difference is mostly noise. Rows researched long before
kick-off face a market that had days more information, so they are reported apart.
"""
import argparse
import pathlib

import pandas as pd

from api.ledgers import GITHUB_MAIN
from model import backtest
from model.paper import KEY, STRATEGIES

PROBS = ["p_H", "p_D", "p_A"]
CLOSE = ["close_h", "close_d", "close_a"]
EARLY_DAYS = 7          # 13-16 Nov games were researched 1-4 days ahead, 20-23 Nov games 8-11
BOOK = "avg"            # what model.paper reports by default


def join_settled(news: pd.DataFrame, ledger: pd.DataFrame) -> pd.DataFrame:
    """Settled fixtures in both files: p_* stay the model's, the research's become news_p_*."""
    late = pd.to_datetime(news.researched_at, utc=True) >= pd.to_datetime(news.kickoff, utc=True)
    if late.any():
        raise ValueError(f"researched at or after kick-off, the comparison would be look-ahead: "
                         f"{news.loc[late, 'event_id'].tolist()}")
    ours = news[KEY + ["event_id", "days_to_kickoff"] + PROBS].rename(columns={p: f"news_{p}" for p in PROBS})
    ours = ours.assign(date=pd.to_datetime(ours.date))
    return ours.merge(ledger[ledger.result.notna()], on=KEY, validate="one_to_one")


def unjoined(news: pd.DataFrame, joined: pd.DataFrame, now: pd.Timestamp) -> pd.DataFrame:
    """Researched games past kick-off with no settled ledger row: not settled yet, or moved to another date."""
    return news[(pd.to_datetime(news.kickoff, utc=True) < now) & ~news.event_id.isin(joined.event_id)]


def compare(joined: pd.DataFrame, edge: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(log loss and Brier per source on the same fixtures, flat-stake picks and CLV per source)."""
    as_news = joined.assign(**{p: joined[f"news_{p}"] for p in PROBS})
    scores = pd.DataFrame()
    if not joined.dropna(subset=PROBS + CLOSE).empty:       # probability_report cannot score zero rows
        scores = backtest.probability_report(joined)
        scores.loc["news"] = backtest.probability_report(as_news).loc["model"]
    picks = [backtest.summarize(backtest.find_bets(d, BOOK, edge)).rename(index={"all": who})
             for who, d in (("news", as_news), ("model", joined))]
    picks = [p for p in picks if not p.empty]
    return scores, pd.concat(picks) if picks else pd.DataFrame()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--news", type=pathlib.Path, default=pathlib.Path(__file__).parent / "news_1x2.csv")
    args = ap.parse_args()
    news = pd.read_csv(args.news)
    ledger = pd.read_csv(f"{GITHUB_MAIN}/ledger_1x2.csv", parse_dates=["date", "logged_at"])
    joined = join_settled(news, ledger)
    print(f"{len(news)} researched, {len(joined)} settled and found in ledger_1x2")
    missing = unjoined(news, joined, pd.Timestamp.now(tz="UTC"))
    if not missing.empty:
        print(f"{len(missing)} past kick-off but not joined (unsettled, or the date moved):\n"
              f"{missing[KEY].to_string(index=False)}")
    pd.set_option("display.width", 160, "display.max_columns", 20, "display.float_format", "{:.3f}".format)
    edge = STRATEGIES["1x2"]["edge"]
    near = joined.days_to_kickoff < EARLY_DAYS
    for label, part in ((f"researched under {EARLY_DAYS} days ahead", joined[near]),
                        (f"researched {EARLY_DAYS}+ days ahead", joined[~near])):
        if part.empty:
            continue
        scores, picks = compare(part, edge)
        print(f"\n[{label}] n={len(part)}\n{scores}\n\nPicks at {backtest.BOOKS[BOOK]} odds, edge > {edge:.0%}:\n{picks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
