"""National-team 1X2 prices from The Odds API (free tier: 500 credits a month).

Listing sports and events costs nothing; `/odds` costs 1 credit per sport here (one market, one region). Every
response carries `x-requests-remaining`: the run stops before a paid call would leave less than the reserve.
The URL carries the API key: never print a response's or an exception's text.
"""
from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable

import requests

BASE = "https://api.the-odds-api.com/v4/sports"
WANTED = ("nations league", "friendl", "qualif")  # soccer titles of national-team competitions
REGION, MARKET = "eu", "h2h"


class OddsApiError(Exception):
    pass


def _get(session: requests.Session, url: str, **params) -> tuple[list, int | None]:
    try:
        r = session.get(url, params=params, timeout=20)
    except requests.RequestException as e:
        raise OddsApiError(f"request failed: {type(e).__name__}") from None
    if r.status_code != 200:
        try:                        # only the machine-readable code, never the body's text
            code = str(r.json().get("error_code", ""))
        except (ValueError, AttributeError):
            code = ""
        code = f" {code}" if re.fullmatch(r"[A-Z_]{1,40}", code) else ""
        raise OddsApiError(f"answered HTTP {r.status_code}{code}")
    left = r.headers.get("x-requests-remaining")
    return r.json(), (int(float(left)) if left is not None else None)


def parse(events: list[dict]) -> list[dict]:
    """One row per event and bookmaker: prices in outcome order (home, draw, away), as of the market's update."""
    rows = []
    for ev in events:
        for bk in ev.get("bookmakers", []):
            for m in bk.get("markets", []):
                prices = {o["name"]: o["price"] for o in m["outcomes"]}
                if m["key"] != MARKET or not {ev["home_team"], "Draw", ev["away_team"]} <= set(prices):
                    continue
                rows.append({"ref": ev["id"], "kickoff": _time(ev["commence_time"]), "home": ev["home_team"],
                             "away": ev["away_team"], "bookmaker": bk["key"], "captured_at": _time(m["last_update"]),
                             "prices": [prices[ev["home_team"]], prices["Draw"], prices[ev["away_team"]]],
                             "sport": ev["sport_key"]})
    return rows


def _time(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def snapshot(key: str, now: dt.datetime, horizon: dt.timedelta, reserve: int,
             national: Callable[[str], bool]) -> tuple[list[dict], int | None, list[str]]:
    """Prices of every national-team game kicking off within `horizon`, the credits left, and the sports skipped
    to keep the reserve. A title match is not enough to pay for a sport (club qualifiers say "qualification"):
    one of its games must be between two teams `national` knows."""
    session = requests.Session()
    sports, left = _get(session, BASE, apiKey=key)
    wanted = [s["key"] for s in sports if s["group"] == "Soccer" and not s["has_outrights"]
              and any(w in f"{s['title']} {s['description']}".lower() for w in WANTED)]
    window = {"commenceTimeFrom": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "commenceTimeTo": (now + horizon).strftime("%Y-%m-%dT%H:%M:%SZ")}
    rows, skipped = [], []
    for sport in wanted:
        events, left = _get(session, f"{BASE}/{sport}/events", apiKey=key, **window)
        if not any(national(e["home_team"]) and national(e["away_team"]) for e in events):
            continue
        if left is not None and left - 1 < reserve:
            skipped.append(sport)
            continue
        priced, left = _get(session, f"{BASE}/{sport}/odds", apiKey=key, regions=REGION, markets=MARKET,
                            oddsFormat="decimal", **window)
        rows += parse(priced)
    return rows, left, skipped
