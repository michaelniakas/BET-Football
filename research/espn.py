"""Scheduled games of the model's 16 leagues, from ESPN's public scoreboard."""
import datetime as dt

import requests

from model.data import LEAGUES

SLUGS = {
    "E0": "eng.1", "E1": "eng.2", "D1": "ger.1", "D2": "ger.2", "I1": "ita.1", "I2": "ita.2",
    "SP1": "esp.1", "SP2": "esp.2", "F1": "fra.1", "F2": "fra.2", "N1": "ned.1", "P1": "por.1",
    "B1": "bel.1", "T1": "tur.1", "G1": "gre.1", "SC0": "sco.1",
}
URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"
assert set(SLUGS) == set(LEAGUES)
# National teams: no football-data files, so no model and no teams.csv mapping.
INTL = {"UNL": "uefa.nations", "FRI": "fifa.friendly"}
INTL_NAMES = {"UNL": "UEFA Nations League", "FRI": "International friendly"}


def fixtures(first: dt.date, last: dt.date, slugs: dict[str, str] = SLUGS) -> list[dict]:
    """Not-yet-started games with first <= kick-off date (UTC) <= last, one per ESPN event."""
    session = requests.Session()
    found = {}
    days = [first + dt.timedelta(i) for i in range((last - first).days + 1)]
    for league, slug in slugs.items():
        for day in days:  # ESPN answers 400 to a date range on this endpoint
            r = session.get(URL.format(slug=slug), params={"dates": day.strftime("%Y%m%d")}, timeout=30)
            r.raise_for_status()
            for event in r.json().get("events", []):
                kickoff = dt.datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
                if event["status"]["type"]["state"] != "pre" or not first <= kickoff.date() <= last:
                    continue
                teams = {c["homeAway"]: c["team"]["displayName"] for c in event["competitions"][0]["competitors"]}
                found[event["id"]] = {
                    "event_id": event["id"], "league": league, "kickoff": kickoff,
                    "home": teams["home"], "away": teams["away"],
                }
    return sorted(found.values(), key=lambda f: (f["kickoff"], f["league"], f["home"]))
