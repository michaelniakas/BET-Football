"""Match xG from Understat (top-5 leagues, 2014/15 onward), joined onto football-data matches."""
from __future__ import annotations

import json

import pandas as pd
import requests

from .data import ROOT, current_season_start

UNDERSTAT_LEAGUES = {"E0": "EPL", "SP1": "La_liga", "D1": "Bundesliga", "I1": "Serie_A", "F1": "Ligue_1"}
FIRST_SEASON = 2014
API_URL = "https://understat.com/getLeagueData/{league}/{season}"
XG_DIR = ROOT / "data" / "understat"


def download(seasons: list[int], refresh_current: bool = True) -> None:
    XG_DIR.mkdir(parents=True, exist_ok=True)
    current = current_season_start()
    for league, us_league in UNDERSTAT_LEAGUES.items():
        for season in seasons:
            if season < FIRST_SEASON:
                continue
            path = XG_DIR / f"{league}_{season}.json"
            if path.exists() and not (refresh_current and season == current):
                continue
            url = API_URL.format(league=us_league, season=season)
            headers = {"X-Requested-With": "XMLHttpRequest", "Referer": url.replace("getLeagueData", "league")}
            r = requests.get(url, headers=headers, timeout=30)
            if r.status_code != 200:
                print(f"  skip understat {league} {season} (HTTP {r.status_code})")
                continue
            path.write_text(json.dumps(r.json()["dates"]), encoding="utf-8")


def load(seasons: list[int]) -> pd.DataFrame:
    rows = []
    for league in UNDERSTAT_LEAGUES:
        for season in seasons:
            path = XG_DIR / f"{league}_{season}.json"
            if not path.exists():
                continue
            for m in json.loads(path.read_text(encoding="utf-8")):
                if not m["isResult"]:
                    continue
                rows.append({
                    "league": league, "season": season,
                    "us_date": pd.Timestamp(m["datetime"][:10]),
                    "us_home": m["h"]["title"], "us_away": m["a"]["title"],
                    "hg": int(m["goals"]["h"]), "ag": int(m["goals"]["a"]),
                    "hxg": float(m["xG"]["h"]), "axg": float(m["xG"]["a"]),
                })
    return pd.DataFrame(rows)


def _name_map(us: pd.DataFrame, matches: pd.DataFrame) -> dict[tuple[str, str], str]:
    """Learn Understat -> football-data team names from matches with the same league, score and date (±1 day)."""
    fd = matches[matches.league.isin(UNDERSTAT_LEAGUES)][["league", "season", "date", "home", "away", "hg", "ag"]]
    cand = us.merge(fd, on=["league", "season", "hg", "ag"])
    cand = cand[(cand.date - cand.us_date).abs() <= pd.Timedelta(days=1)]
    pairs = pd.concat([
        cand[["league", "us_home", "home"]].set_axis(["league", "us", "fd"], axis=1),
        cand[["league", "us_away", "away"]].set_axis(["league", "us", "fd"], axis=1),
    ])
    votes = pairs.value_counts().reset_index().drop_duplicates(["league", "us"])
    return {(r.league, r.us): r.fd for r in votes.itertuples()}


def attach_xg(matches: pd.DataFrame, download_first: bool = True) -> pd.DataFrame:
    seasons = sorted(matches.season.unique())
    if download_first:
        download(seasons)
    us = load(seasons)
    if us.empty:
        return matches.assign(hxg=float("nan"), axg=float("nan"))
    names = _name_map(us, matches)
    us["home"] = [names.get((lg, t)) for lg, t in zip(us.league, us.us_home)]
    us["away"] = [names.get((lg, t)) for lg, t in zip(us.league, us.us_away)]
    us = us.dropna(subset=["home", "away"]).drop_duplicates(["league", "season", "home", "away"])
    out = matches.merge(us[["league", "season", "home", "away", "hxg", "axg"]],
                        on=["league", "season", "home", "away"], how="left")
    top5 = out[out.league.isin(UNDERSTAT_LEAGUES) & (out.season >= FIRST_SEASON)]
    print(f"xG attached to {top5.hxg.notna().mean():.1%} of top-5 league matches since {FIRST_SEASON}")
    return out
