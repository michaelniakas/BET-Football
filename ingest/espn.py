"""Games of the model's 16 leagues and of the national-team competitions, from ESPN's public scoreboard.
Unofficial: no published terms (not verified). An unknown league slug answers 400."""
import datetime as dt
import time

import requests

from model.data import LEAGUES

SLUGS = {
    "E0": "eng.1", "E1": "eng.2", "D1": "ger.1", "D2": "ger.2", "I1": "ita.1", "I2": "ita.2",
    "SP1": "esp.1", "SP2": "esp.2", "F1": "fra.1", "F2": "fra.2", "N1": "ned.1", "P1": "por.1",
    "B1": "bel.1", "T1": "tur.1", "G1": "gre.1", "SC0": "sco.1",
}
URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"
assert set(SLUGS) == set(LEAGUES)
# The news pilot's national-team codes (research/news.py, research/intl.py).
INTL = {"UNL": "uefa.nations", "FRI": "fifa.friendly"}
INTL_NAMES = {"UNL": "UEFA Nations League", "FRI": "International friendly"}
# National-team competitions -> martj42's tournament name, the competition's code (checked 2026-11-12).
NATIONAL = {
    "uefa.nations": "UEFA Nations League", "fifa.friendly": "Friendly",
    "fifa.worldq.uefa": "FIFA World Cup qualification", "fifa.worldq.conmebol": "FIFA World Cup qualification",
    "fifa.worldq.concacaf": "FIFA World Cup qualification", "fifa.worldq.afc": "FIFA World Cup qualification",
    "fifa.worldq.caf": "FIFA World Cup qualification", "fifa.world": "FIFA World Cup",
    "uefa.euroq": "UEFA Euro qualification", "uefa.euro": "UEFA Euro", "conmebol.america": "Copa América",
    "concacaf.gold": "Gold Cup", "concacaf.nations.league": "CONCACAF Nations League",
    "caf.nations": "African Cup of Nations", "caf.nations_qual": "African Cup of Nations qualification",
    "afc.asian.cup": "AFC Asian Cup", "afc.cupq": "AFC Asian Cup qualification",
}
# How a finished game was decided; any other status (postponed, abandoned, ...) carries no result.
DECIDED = {"STATUS_FULL_TIME": "regular", "STATUS_FINAL_AET": "extra_time", "STATUS_FINAL_PEN": "penalties"}


def parse(league: str, event: dict) -> dict:
    """One scoreboard event. Score at the end of play (extra time included), only for a finished game."""
    comp = event["competitions"][0]
    sides = {c["homeAway"]: c for c in comp["competitors"]}
    decided = DECIDED.get(event["status"]["type"]["name"])
    score = lambda side: int(sides[side]["score"]) if decided else None  # noqa: E731
    return {
        "event_id": event["id"], "league": league,
        "kickoff": dt.datetime.fromisoformat(event["date"].replace("Z", "+00:00")),
        "home": sides["home"]["team"]["displayName"], "away": sides["away"]["team"]["displayName"],
        "state": event["status"]["type"]["state"], "decided": decided, "hg": score("home"), "ag": score("away"),
        "neutral": comp.get("neutralSite"),
    }


def events(first: dt.date, last: dt.date, slugs: dict[str, str], pause: float = 0.0) -> list[dict]:
    """Every game with first <= kick-off date (UTC) <= last, one per ESPN event, in any state."""
    session = requests.Session()
    found = {}
    days = [first + dt.timedelta(i) for i in range((last - first).days + 1)]
    for league, slug in slugs.items():
        for day in days:  # ESPN answers 400 to a date range on this endpoint
            r = session.get(URL.format(slug=slug), params={"dates": day.strftime("%Y%m%d")}, timeout=30)
            r.raise_for_status()
            for event in r.json().get("events", []):
                game = parse(league, event)
                if first <= game["kickoff"].date() <= last:
                    found[game["event_id"]] = game
            time.sleep(pause)
    return sorted(found.values(), key=lambda f: (f["kickoff"], f["league"], f["home"]))


def fixtures(first: dt.date, last: dt.date, slugs: dict[str, str] = SLUGS) -> list[dict]:
    """Not-yet-started games with first <= kick-off date (UTC) <= last (the news pilot's view)."""
    keys = ("event_id", "league", "kickoff", "home", "away")
    return [{k: g[k] for k in keys} for g in events(first, last, slugs) if g["state"] == "pre"]
