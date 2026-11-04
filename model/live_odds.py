"""Live 1X2 and Over/Under 2.5 odds per bookmaker from The Odds API (the-odds-api.com).

Free tier: 500 credits/month. One call = one league x (markets x regions) credits, so
refreshing the top-5 leagues for h2h + totals in the EU region costs 10 credits.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

import numpy as np
import pandas as pd
import requests

API_URL = "https://api.the-odds-api.com/v4/sports/{sport}/odds"
SPORT_KEYS = {
    "E0": "soccer_epl", "E1": "soccer_efl_champ",
    "D1": "soccer_germany_bundesliga", "D2": "soccer_germany_bundesliga2",
    "I1": "soccer_italy_serie_a", "I2": "soccer_italy_serie_b",
    "SP1": "soccer_spain_la_liga", "SP2": "soccer_spain_segunda_division",
    "F1": "soccer_france_ligue_one", "F2": "soccer_france_ligue_two",
    "N1": "soccer_netherlands_eredivisie", "P1": "soccer_portugal_primeira_liga",
    "B1": "soccer_belgium_first_div", "T1": "soccer_turkey_super_league",
    "G1": "soccer_greece_super_league", "SC0": "soccer_spl",
}
MATCH_KEY = ["date", "league", "home", "away"]
LIVE_COLS = ["league", "kickoff", "api_home", "api_away", "bookmaker", "market", "outcome", "odds"]

# football-data abbreviations that string similarity alone gets wrong
ALIASES = {
    "man united": "manchester united", "man city": "manchester city", "wolves": "wolverhampton wanderers",
    "nott'm forest": "nottingham forest", "sheffield weds": "sheffield wednesday", "qpr": "queens park rangers",
    "m'gladbach": "borussia monchengladbach", "ein frankfurt": "eintracht frankfurt", "fc koln": "fc koln cologne",
    "paris sg": "paris saint germain", "ath madrid": "atletico madrid", "ath bilbao": "athletic bilbao",
    "sociedad": "real sociedad", "betis": "real betis", "celta": "celta vigo", "vallecano": "rayo vallecano",
    "espanol": "espanyol", "inter": "inter milan", "milan": "ac milan", "olympiakos": "olympiacos piraeus",
    "paok": "paok thessaloniki", "aek": "aek athens",
}


class OddsAPIError(RuntimeError):
    pass


def fetch(api_key: str, leagues: list[str], regions: str = "eu") -> tuple[pd.DataFrame, dict]:
    """One row per (event, bookmaker, market, outcome). Returns the rows and the remaining quota."""
    rows, quota = [], {}
    for league in leagues:
        r = requests.get(API_URL.format(sport=SPORT_KEYS[league]), timeout=20, params={
            "apiKey": api_key, "regions": regions, "markets": "h2h,totals", "oddsFormat": "decimal"})
        if r.status_code in (401, 429):
            raise OddsAPIError(r.json().get("message", r.text))
        if r.status_code == 404:          # league not offered right now (off-season)
            continue
        r.raise_for_status()
        quota = {"remaining": r.headers.get("x-requests-remaining"), "used": r.headers.get("x-requests-used")}
        rows += parse(r.json(), league)
    return pd.DataFrame(rows, columns=LIVE_COLS), quota


def parse(events: list[dict], league: str) -> list[dict]:
    rows = []
    for ev in events:
        home, away = ev["home_team"], ev["away_team"]
        base = {"league": league, "kickoff": ev["commence_time"], "api_home": home, "api_away": away}
        for bk in ev.get("bookmakers", []):
            for m in bk.get("markets", []):
                if m["key"] == "h2h":
                    names = {home: "H", "Draw": "D", away: "A"}
                    picks = [(names[o["name"]], o["price"]) for o in m["outcomes"] if o["name"] in names]
                    market = "1x2"
                elif m["key"] == "totals":
                    picks = [(o["name"][0], o["price"]) for o in m["outcomes"]
                             if o.get("point") == 2.5 and o["name"] in ("Over", "Under")]
                    market = "ou25"
                else:
                    continue
                rows += [{**base, "bookmaker": bk["title"], "market": market, "outcome": out, "odds": price}
                         for out, price in picks]
    return rows


def _norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower().strip()
    s = ALIASES.get(s, s)
    return re.sub(r"[^a-z0-9 ]", "", s.replace("-", " "))


def similarity(a: str, b: str) -> float:
    """Name similarity in [0, 1]; a shared distinctive word ('Leverkusen', 'Forest') counts as a strong match."""
    a, b = _norm(a), _norm(b)
    if a == b:
        return 1.0
    shared = {w for w in a.split() if len(w) >= 4} & {w for w in b.split() if len(w) >= 4}
    return max(SequenceMatcher(None, a, b).ratio(), 0.9 if shared else 0.0)


def match_to_fixtures(live: pd.DataFrame, fixtures: pd.DataFrame, min_score: float = 1.3) -> tuple[pd.DataFrame, list[str]]:
    """Attach our fixture key (date, league, home, away) to each live row; returns matched rows and unmatched events."""
    fx = fixtures[MATCH_KEY].drop_duplicates()
    events = live[["league", "kickoff", "api_home", "api_away"]].drop_duplicates()
    keys, unmatched = [], []
    for ev in events.itertuples(index=False):
        day = pd.Timestamp(ev.kickoff).tz_convert(None).normalize()
        cand = fx[(fx.league == ev.league) & ((fx.date - day).abs() <= pd.Timedelta(days=1))]
        scores = [similarity(ev.api_home, h) + similarity(ev.api_away, a) for h, a in zip(cand.home, cand.away)]
        if not scores or max(scores) < min_score:
            unmatched.append(f"{ev.league}: {ev.api_home} - {ev.api_away}")
            continue
        best = cand.iloc[int(np.argmax(scores))]
        keys.append({**ev._asdict(), **best[MATCH_KEY].to_dict()})
    if not keys:
        return pd.DataFrame(columns=MATCH_KEY + LIVE_COLS), unmatched
    mapped = live.merge(pd.DataFrame(keys), on=["league", "kickoff", "api_home", "api_away"])
    return mapped, unmatched
