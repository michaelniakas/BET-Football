"""Download and normalise match results + odds from football-data.co.uk."""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pandas as pd
import requests

BASE_URL = "https://www.football-data.co.uk/mmz4281/{season}/{league}.csv"
FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"

LEAGUES = {
    "E0": "England Premier League", "E1": "England Championship",
    "D1": "Germany Bundesliga", "D2": "Germany 2. Bundesliga",
    "I1": "Italy Serie A", "I2": "Italy Serie B",
    "SP1": "Spain La Liga", "SP2": "Spain Segunda",
    "F1": "France Ligue 1", "F2": "France Ligue 2",
    "N1": "Netherlands Eredivisie", "P1": "Portugal Primeira Liga",
    "B1": "Belgium Pro League", "T1": "Turkey Super Lig",
    "G1": "Greece Super League", "SC0": "Scotland Premiership",
}

ROOT = Path(__file__).parent
RAW_DIR = ROOT / "data" / "raw"
OUTCOMES = ["H", "D", "A"]
# target column, outcome labels and the odds-column suffix for each outcome
MARKETS = {
    "1x2": {"target": "result", "outcomes": ["H", "D", "A"], "suffixes": ["h", "d", "a"]},
    "ou25": {"target": "ou", "outcomes": ["O", "U"], "suffixes": ["o", "u"]},
}


def season_code(start_year: int) -> str:
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"


def current_season_start(today: pd.Timestamp | None = None) -> int:
    today = today or pd.Timestamp.today()
    return today.year if today.month >= 7 else today.year - 1


def download(leagues: list[str], seasons: list[int], refresh_current: bool = True) -> None:
    """Cache raw CSVs locally; only the running season is re-downloaded."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    current = current_season_start()
    for league in leagues:
        for season in seasons:
            path = RAW_DIR / f"{league}_{season_code(season)}.csv"
            if path.exists() and not (refresh_current and season == current):
                continue
            url = BASE_URL.format(season=season_code(season), league=league)
            r = requests.get(url, timeout=30)
            if r.status_code != 200 or len(r.content) < 200:
                print(f"  skip {league} {season_code(season)} (HTTP {r.status_code})")
                continue
            path.write_bytes(r.content)


def _read_csv(content: bytes) -> pd.DataFrame:
    text = content.decode("utf-8-sig", errors="replace")
    df = pd.read_csv(io.StringIO(text), on_bad_lines="skip", low_memory=False)
    df.columns = [c.strip() for c in df.columns]
    return df.dropna(subset=["HomeTeam", "AwayTeam"])


def _col(df: pd.DataFrame, *names: str) -> pd.Series:
    for name in names:
        if name in df.columns:
            return pd.to_numeric(df[name], errors="coerce")
    return pd.Series(np.nan, index=df.index)


def normalise(df: pd.DataFrame, league: str | None = None, season: int | None = None) -> pd.DataFrame:
    """Map football-data columns to one schema (pre-match odds, closing odds, stats)."""
    out = pd.DataFrame({
        "league": league if league else df["Div"],
        "season": season,
        "date": pd.to_datetime(df["Date"], dayfirst=True, errors="coerce", format="mixed"),
        "home": df["HomeTeam"].astype(str).str.strip(),
        "away": df["AwayTeam"].astype(str).str.strip(),
        "hg": _col(df, "FTHG", "HG"), "ag": _col(df, "FTAG", "AG"),
        "hs": _col(df, "HS"), "as": _col(df, "AS"),
        "hst": _col(df, "HST"), "ast": _col(df, "AST"),
    })
    for side in OUTCOMES:
        s = side.lower()
        out[f"ps_{s}"] = _col(df, f"PS{side}")            # Pinnacle, pre-match
        out[f"psc_{s}"] = _col(df, f"PSC{side}")          # Pinnacle, closing
        out[f"bfe_{s}"] = _col(df, f"BFE{side}")          # Betfair Exchange, pre-match
        out[f"bfec_{s}"] = _col(df, f"BFEC{side}")        # Betfair Exchange, closing
        out[f"avg_{s}"] = _col(df, f"Avg{side}", f"BbAv{side}")
        out[f"avgc_{s}"] = _col(df, f"AvgC{side}")
        out[f"max_{s}"] = _col(df, f"Max{side}", f"BbMx{side}")
        out[f"b365_{s}"] = _col(df, f"B365{side}")
    for side, s in ((">", "o"), ("<", "u")):              # Over / Under 2.5 goals
        out[f"ps_{s}"] = _col(df, f"P{side}2.5")
        out[f"psc_{s}"] = _col(df, f"PC{side}2.5")
        out[f"bfe_{s}"] = _col(df, f"BFE{side}2.5")
        out[f"bfec_{s}"] = _col(df, f"BFEC{side}2.5")
        out[f"avg_{s}"] = _col(df, f"Avg{side}2.5", f"BbAv{side}2.5")
        out[f"avgc_{s}"] = _col(df, f"AvgC{side}2.5")
        out[f"max_{s}"] = _col(df, f"Max{side}2.5", f"BbMx{side}2.5")
        out[f"b365_{s}"] = _col(df, f"B365{side}2.5")
    out["result"] = np.select([out.hg > out.ag, out.hg == out.ag, out.hg < out.ag], OUTCOMES, None)
    out["ou"] = np.where(out.hg.isna(), None, np.where(out.hg + out.ag > 2.5, "O", "U"))
    return out.dropna(subset=["date"])


def load(leagues: list[str], seasons: list[int]) -> pd.DataFrame:
    frames = []
    for league in leagues:
        for season in seasons:
            path = RAW_DIR / f"{league}_{season_code(season)}.csv"
            if path.exists():
                frames.append(normalise(_read_csv(path.read_bytes()), league, season))
    df = pd.concat(frames, ignore_index=True)
    df = df.dropna(subset=["result"])
    return df.sort_values(["date", "league", "home"], kind="stable").reset_index(drop=True)


def load_fixtures(leagues: list[str]) -> pd.DataFrame:
    """Upcoming matches with pre-match odds (football-data refreshes this weekly)."""
    r = requests.get(FIXTURES_URL, timeout=30)
    r.raise_for_status()
    raw = _read_csv(r.content)
    fx = normalise(raw)
    fx["season"] = current_season_start()
    # UK local time; NaT when football-data gives none (or the hour doesn't exist / repeats at a clock change).
    fx["kickoff"] = (pd.to_datetime(raw["Date"] + " " + raw.get("Time", pd.Series(index=raw.index)).astype("str"),
                                    dayfirst=True, errors="coerce", format="mixed")
                     .dt.tz_localize("Europe/London", ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC"))
    return fx[fx.league.isin(leagues)].reset_index(drop=True)
