"""Men's international results since 1872 from github.com/martj42/international_results (CC0).

Dates are local, scores include extra time, `shootouts.csv` lists the games decided on penalties, and results
carry each team's current name (`former_names.csv` gives the older ones, for other sources).
"""
from __future__ import annotations

import csv
import datetime as dt
import io
from dataclasses import dataclass

import requests

BASE = "https://raw.githubusercontent.com/martj42/international_results/master/{}.csv"


@dataclass(frozen=True)
class Game:
    date: dt.date
    home: str
    away: str
    hg: int | None      # None: scheduled, not played yet
    ag: int | None
    tournament: str
    neutral: bool
    penalties: bool


def _rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def parse(results: str, shootouts: str) -> list[Game]:
    pens = {(r["date"], r["home_team"], r["away_team"]) for r in _rows(shootouts)}
    neutral = {"TRUE": True, "FALSE": False}
    score = lambda v: None if v == "NA" else int(v)  # noqa: E731
    return [Game(dt.date.fromisoformat(r["date"]), r["home_team"], r["away_team"], score(r["home_score"]),
                 score(r["away_score"]), r["tournament"], neutral[r["neutral"]],
                 (r["date"], r["home_team"], r["away_team"]) in pens)
            for r in _rows(results)]


FORMER_SINCE = "1990-01-01"   # an older name no current source uses, and one is now another team's: Ireland (to 1956)


def parse_former(former: str) -> dict[str, str]:
    """former name -> current name, for names dropped since FORMER_SINCE."""
    return {r["former"]: r["current"] for r in _rows(former) if r["end_date"] >= FORMER_SINCE}


def fetch() -> tuple[list[Game], dict[str, str]]:
    def get(name: str) -> str:
        r = requests.get(BASE.format(name), timeout=60)
        r.raise_for_status()
        return r.text
    return parse(get("results"), get("shootouts")), parse_former(get("former_names"))
