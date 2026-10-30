"""Value picks for the upcoming fixtures (football-data.co.uk/fixtures.csv).

    python -m model.predict --features stats+market --edge 0.04
    python -m model.predict --market ou25 --features stats+xg+market --leagues E0 SP1 D1 I1 F1
"""
from __future__ import annotations

import argparse

import pandas as pd

from . import backtest, data
from .features import FEATURE_SETS, add_market, features_for
from .train import eligible, fit_final, predict_proba
from .run import build_dataset

OUT_DIR = data.ROOT / "output"


def score_fixtures(leagues: list[str], market: str, feature_set: str, kind: str = "logreg",
                   first_season: int = 2012) -> pd.DataFrame:
    """Model probabilities for upcoming fixtures, from a model fitted on all played matches."""
    history, builder = build_dataset(leagues, first_season)
    features = features_for(feature_set, market)
    model = fit_final(history, features, kind, market)

    # The builder now holds every team's state after its last played match.
    fixtures = data.load_fixtures(leagues)
    if fixtures.empty:
        return fixtures
    feats = pd.DataFrame([builder.pre_match(f.league, f.season, f.home, f.away) for f in fixtures.itertuples()])
    fx = add_market(pd.concat([fixtures, feats], axis=1))
    fx = fx[eligible(fx, features)]
    if fx.empty:
        return fx
    return fx.join(predict_proba(model, fx[features], market))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--leagues", nargs="+", default=list(data.LEAGUES))
    ap.add_argument("--market", choices=data.MARKETS, default="1x2")
    ap.add_argument("--first-season", type=int, default=2012)
    ap.add_argument("--features", choices=FEATURE_SETS, default="stats")
    ap.add_argument("--kind", choices=["logreg", "hgb"], default="logreg")
    ap.add_argument("--book", choices=backtest.BOOKS, default="avg")
    ap.add_argument("--edge", type=float, default=0.05)
    args = ap.parse_args()

    fx = score_fixtures(args.leagues, args.market, args.features, args.kind, args.first_season)
    if fx.empty:
        print("No upcoming fixtures with odds for these leagues.")
        return

    picks = backtest.find_bets(fx, args.book, args.edge, args.market)
    cols = ["date", "league", "home", "away", "pick", "odds", "p_model", "edge"]
    pd.set_option("display.width", 160, "display.float_format", "{:.3f}".format)
    print(f"{len(fx)} fixtures scored, {len(picks)} with edge > {args.edge:.0%} at {backtest.BOOKS[args.book]} odds:\n")
    print(picks[cols].sort_values("edge", ascending=False).to_string(index=False))
    OUT_DIR.mkdir(exist_ok=True)
    fx.to_csv(OUT_DIR / f"fixtures_scored_{args.market}.csv", index=False)


if __name__ == "__main__":
    main()
