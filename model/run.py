"""Walk-forward backtest.

    python -m model.run                                          # 1X2, all leagues, stats-only
    python -m model.run --features stats+market --kind hgb
    python -m model.run --market ou25 --features stats+xg+market --leagues E0 SP1 D1 I1 F1
"""
from __future__ import annotations

import argparse

import pandas as pd

from . import backtest, data, xg
from .features import FEATURE_SETS, FeatureBuilder, add_market, features_for
from .train import walk_forward

OUT_DIR = data.ROOT / "output"


def build_dataset(leagues: list[str], first_season: int, download: bool = True) -> tuple[pd.DataFrame, FeatureBuilder]:
    seasons = list(range(first_season, data.current_season_start() + 1))
    if download:
        print(f"Downloading {len(leagues)} leagues x {len(seasons)} seasons (cached)...")
        data.download(leagues, seasons)
    matches = data.load(leagues, seasons)
    matches = xg.attach_xg(matches, download)
    builder = FeatureBuilder()
    return add_market(builder.build(matches)), builder


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--leagues", nargs="+", default=list(data.LEAGUES))
    ap.add_argument("--market", choices=data.MARKETS, default="1x2")
    ap.add_argument("--first-season", type=int, default=2012, help="first season used (Elo warm-up)")
    ap.add_argument("--test-from", type=int, default=2016, help="first out-of-sample season")
    ap.add_argument("--features", choices=FEATURE_SETS, default="stats",
                    help="xg features exist only for E0 SP1 D1 I1 F1 (Understat)")
    ap.add_argument("--kind", choices=["logreg", "hgb"], default="logreg")
    ap.add_argument("--book", choices=backtest.BOOKS, default="avg", help="odds you would actually bet at")
    ap.add_argument("--edge", type=float, default=0.05, help="minimum expected value to place a bet")
    ap.add_argument("--no-download", action="store_true")
    args = ap.parse_args()

    pd.set_option("display.width", 160, "display.max_columns", 20, "display.float_format", "{:.3f}".format)
    df, _ = build_dataset(args.leagues, args.first_season, not args.no_download)
    print(f"{len(df):,} matches, {df.date.min():%Y-%m-%d} .. {df.date.max():%Y-%m-%d}")

    features = features_for(args.features, args.market)
    test_seasons = list(range(args.test_from, data.current_season_start() + 1))
    print(f"\nWalk-forward ({args.market}, {args.kind}, features={args.features}):")
    pred = walk_forward(df, features, test_seasons, args.kind, args.market)

    print("\n== Probability quality (lower is better) ==")
    print(backtest.probability_report(pred, args.market))

    bets = backtest.find_bets(pred, args.book, args.edge, args.market)
    print(f"\n== Value bets at {backtest.BOOKS[args.book]} odds, edge > {args.edge:.0%} ==")
    print(backtest.summarize(bets))
    print("\nBy season:")
    print(backtest.summarize(bets, "season"))
    print("\nBy league:")
    print(backtest.summarize(bets, "league").sort_values("clv_%", ascending=False))
    print("\nEdge threshold sweep:")
    print(backtest.edge_sweep(pred, args.book, args.market))

    OUT_DIR.mkdir(exist_ok=True)
    pred.to_csv(OUT_DIR / f"predictions_{args.market}.csv", index=False)
    bets.to_csv(OUT_DIR / f"bets_{args.market}.csv", index=False)
    print(f"\nSaved predictions_{args.market}.csv and bets_{args.market}.csv in {OUT_DIR}")


if __name__ == "__main__":
    main()
