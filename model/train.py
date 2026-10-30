"""Walk-forward training: for each test season, fit only on earlier seasons."""
from __future__ import annotations

import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .data import MARKETS

MIN_TEAM_MATCHES = 5


def make_model(kind: str):
    if kind == "logreg":
        return make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=2000))
    if kind == "hgb":
        return HistGradientBoostingClassifier(
            max_depth=3, learning_rate=0.04, max_iter=400, l2_regularization=1.0,
            early_stopping=True, validation_fraction=0.15, random_state=0)
    raise ValueError(f"unknown model kind: {kind}")


def eligible(df: pd.DataFrame, features: list[str]) -> pd.Series:
    """Enough history for both teams and every feature present."""
    return (df.h_n >= MIN_TEAM_MATCHES) & (df.a_n >= MIN_TEAM_MATCHES) & df[features].notna().all(axis=1)


def predict_proba(model, X: pd.DataFrame, market: str) -> pd.DataFrame:
    proba = model.predict_proba(X)
    cols = {c: i for i, c in enumerate(model.classes_)}
    return pd.DataFrame({f"p_{o}": proba[:, cols[o]] for o in MARKETS[market]["outcomes"]}, index=X.index)


def walk_forward(df: pd.DataFrame, features: list[str], test_seasons: list[int], kind: str,
                 market: str = "1x2") -> pd.DataFrame:
    """Out-of-sample probabilities for every match in test_seasons."""
    target = MARKETS[market]["target"]
    data = df[eligible(df, features) & df[target].notna()]
    preds = []
    for season in test_seasons:
        train, test = data[data.season < season], data[data.season == season]
        if test.empty or train.empty:
            continue
        model = make_model(kind).fit(train[features], train[target])
        preds.append(test.join(predict_proba(model, test[features], market)))
        print(f"  {season}/{(season + 1) % 100:02d}: train {len(train):>6}  test {len(test):>5}")
    return pd.concat(preds)


def fit_final(df: pd.DataFrame, features: list[str], kind: str, market: str = "1x2"):
    target = MARKETS[market]["target"]
    data = df[eligible(df, features) & df[target].notna()]
    return make_model(kind).fit(data[features], data[target])
