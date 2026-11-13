"""International games: odds snapshots, results from ESPN, and paper bets on the news research's probabilities.

    python -m research.intl odds 2026-11-12 2026-11-18     # ODDS_API_KEY; prints the ESPN ids that have odds
    python -m research.news 2026-11-12 2026-11-18 --intl --cap 110 --events <those ids>
    python -m research.intl report                         # after the games

Paper only, nothing is placed. A bet is the outcome with the largest EV of Claude's p_* at the market average
price of the last snapshot before the research finished, when that EV is above the 1X2 control's edge.
"""
import argparse
import datetime as dt
import os
import pathlib
import sys

import numpy as np
import pandas as pd
import requests

from model import backtest
from model.live_odds import API_URL, LIVE_COLS, parse, similarity
from model.paper import STRATEGIES
from ingest.espn import INTL, URL, fixtures

HERE = pathlib.Path(__file__).parent
ODDS = HERE / "odds_intl.csv"
NEWS = HERE / "news_intl.csv"
SPORTS_URL = "https://api.the-odds-api.com/v4/sports"
WANTED = ("nations league", "friendl", "qualif")  # Odds API soccer titles of national-team competitions
EDGE = STRATEGIES["1x2"]["edge"]
PROBS, SUFFIXES = ["p_H", "p_D", "p_A"], ["h", "d", "a"]


def _get(url: str, **params) -> requests.Response:
    """The URL carries the API key: never print a response's or an exception's text."""
    try:
        r = requests.get(url, params=params, timeout=20)
    except requests.RequestException as e:
        raise SystemExit(f"Odds API request failed: {type(e).__name__}") from None
    if r.status_code not in (200, 404):
        raise SystemExit(f"Odds API answered HTTP {r.status_code}")
    return r


def match(events: pd.DataFrame, fx: list[dict], min_score: float = 1.3) -> dict[tuple, str]:
    """(kickoff, api_home, api_away) -> ESPN event id: same kick-off within 3 hours, best summed name similarity.
    An ESPN id claimed by two events is dropped: averaging two matches' prices is worse than no price."""
    found = {}
    for ev in events.itertuples(index=False):
        kickoff = pd.Timestamp(ev.kickoff)
        cand = [f for f in fx if abs(pd.Timestamp(f["kickoff"]) - kickoff) <= pd.Timedelta(hours=3)]
        scores = [similarity(ev.api_home, f["home"]) + similarity(ev.api_away, f["away"]) for f in cand]
        if scores and max(scores) >= min_score:
            found[ev.kickoff, ev.api_home, ev.api_away] = cand[int(np.argmax(scores))]["event_id"]
    claims = pd.Series(list(found.values())).value_counts()
    return {k: v for k, v in found.items() if claims[v] == 1}


def snapshot(first: dt.date, last: dt.date) -> int:
    key = os.environ["ODDS_API_KEY"]
    sports = [s["key"] for s in _get(SPORTS_URL, apiKey=key).json()  # this listing costs no credits
              if s["group"] == "Soccer" and not s["has_outrights"]
              and any(w in f"{s['title']} {s['description']}".lower() for w in WANTED)]
    print(f"Odds API competitions: {', '.join(sports) or 'none'}")
    rows, quota = [], None
    for sport in sports:
        r = _get(API_URL.format(sport=sport), apiKey=key, regions="eu", markets="h2h", oddsFormat="decimal")
        quota = r.headers.get("x-requests-remaining", quota)
        rows += parse(r.json(), sport) if r.status_code == 200 else []
    odds = pd.DataFrame(rows, columns=LIVE_COLS)
    odds = odds[odds.market == "1x2"]
    fx = fixtures(first, last, INTL)
    ids = match(odds[["kickoff", "api_home", "api_away"]].drop_duplicates(), fx)
    odds["event_id"] = [ids.get(k) for k in zip(odds.kickoff, odds.api_home, odds.api_away)]
    odds = odds.dropna(subset=["event_id"]).assign(fetched_at=dt.datetime.now(dt.timezone.utc).isoformat())
    odds.to_csv(ODDS, mode="a", header=not ODDS.exists(), index=False)
    with_odds = sorted(set(odds.event_id))
    print(f"{len(fx)} ESPN games, {len(with_odds)} with odds ({odds.bookmaker.nunique()} bookmakers), "
          f"credits left: {quota}")
    if not with_odds:  # an empty --events would make research.news research every game
        return 1
    print("--events " + " ".join(with_odds))
    return 0


def finals(news: pd.DataFrame) -> dict[str, str]:
    """ESPN event id -> H/D/A for researched games that have finished."""
    kickoffs = pd.to_datetime(news.kickoff, utc=True)
    wanted, found = set(news.event_id.astype(str)), {}
    days = {(league, d) for league, k in zip(news.league, kickoffs) if k < pd.Timestamp.now(tz="UTC")
            for d in (k.date(), k.date() - dt.timedelta(1))}  # ESPN files late UTC kick-offs under the US date
    for league, day in sorted(days):
        r = requests.get(URL.format(slug=INTL[league]), params={"dates": day.strftime("%Y%m%d")}, timeout=30)
        r.raise_for_status()
        for event in r.json().get("events", []):
            if event["id"] not in wanted or not event["status"]["type"]["completed"]:
                continue
            goals = {c["homeAway"]: int(c["score"]) for c in event["competitions"][0]["competitors"]}
            found[event["id"]] = "H" if goals["home"] > goals["away"] else "A" if goals["home"] < goals["away"] else "D"
    return found


def priced(news: pd.DataFrame, odds: pd.DataFrame, results: dict[str, str]) -> pd.DataFrame:
    """One row per researched game with odds: Claude's p_*, average/best price of the last snapshot taken before
    the research finished (a later price may know the line-ups), the no-vig market probabilities, and the result
    (NaN while open). Empty when no game has such a snapshot."""
    o = odds.merge(news[["event_id", "researched_at"]], on="event_id")
    fetched = pd.to_datetime(o.fetched_at, utc=True)
    o = o[fetched <= pd.to_datetime(o.researched_at, utc=True)].assign(fetched=fetched)
    o = o[o.fetched == o.groupby("event_id").fetched.transform("max")]
    if o.empty:
        return pd.DataFrame()
    prices = o.pivot_table(index="event_id", columns="outcome", values="odds", aggfunc=["mean", "max"])
    df = news.set_index("event_id").join(prices.set_axis(
        [f"{'avg' if a == 'mean' else 'max'}_{s.lower()}" for a, s in prices.columns], axis=1), how="inner")
    inv = 1 / df[[f"avg_{s}" for s in SUFFIXES]].to_numpy()
    df[[f"mkt_{s}" for s in SUFFIXES]] = inv / inv.sum(axis=1, keepdims=True)
    df[[f"close_{s}" for s in SUFFIXES]] = np.nan  # no closing snapshot: CLV unknown
    return df.assign(result=df.index.map(results), season="").reset_index()


def log_loss(df: pd.DataFrame, cols: list[str]) -> float:
    y = df.result.map({"H": 0, "D": 1, "A": 2}).to_numpy()
    return float(-np.mean(np.log(df[cols].to_numpy()[np.arange(len(y)), y])))


def report() -> int:
    news, odds = pd.read_csv(NEWS, dtype={"event_id": str}), pd.read_csv(ODDS, dtype={"event_id": str})
    df = priced(news, odds, finals(news))
    if df.empty:
        print(f"{len(news)} researched, none with a price saved before its research")
        return 1
    settled = df[df.result.notna()]
    print(f"{len(news)} researched, {len(df)} with odds before kick-off, {len(settled)} finished")
    if not settled.empty:
        print(f"log loss on the same {len(settled)} games: Claude {log_loss(settled, PROBS):.3f}, "
              f"market average no-vig {log_loss(settled, [f'mkt_{s}' for s in SUFFIXES]):.3f}")
    bets = backtest.find_bets(df, "avg", EDGE).drop(columns=["season", "p_close", "clv"])
    if bets.empty:
        print(f"no paper bets at edge > {EDGE:.0%}")
        return 0
    bets.loc[bets.result.isna(), "profit"] = np.nan
    pd.set_option("display.width", 160, "display.float_format", "{:.2f}".format)
    print(f"\nPaper bets, 1 unit at the average price, edge > {EDGE:.0%}:\n{bets.to_string(index=False)}")
    done = bets[bets.result.notna()]
    if not done.empty:
        print(f"\nsettled: {len(done)} bets, profit {done.profit.sum():+.2f} units, "
              f"ROI {100 * done.profit.sum() / done.stake.sum():+.1f}%, hit {100 * (done.pick == done.result).mean():.0f}%")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("odds")
    o.add_argument("first", type=dt.date.fromisoformat)
    o.add_argument("last", type=dt.date.fromisoformat)
    sub.add_parser("report")
    args = ap.parse_args()
    return snapshot(args.first, args.last) if args.cmd == "odds" else report()


if __name__ == "__main__":
    sys.exit(main())
