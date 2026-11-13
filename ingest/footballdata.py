"""Club results and bookmakers' prices from football-data.co.uk: the main divisions' season files
(`mmz4281/{season}/{code}.csv`, 2000/01 on) and the extra leagues' one file per country (`new/{code}.csv`, 2012 on).

Dates are UK dates and `Time` UK time. Pre-match prices were "collected Friday afternoons, Tuesday afternoons for
midweek games" and closing prices carry a `C` after the bookmaker: neither has a capture time. Only 1X2 and O/U 2.5
are read (`market_ok`); Asian handicap is not wanted. A column that looks like a price but names no known bookmaker
is reported, never guessed.
"""
from __future__ import annotations

import collections
import csv
import datetime as dt
import io
import re
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import requests

from model.data import LEAGUES, season_code

BASE = "https://football-data.co.uk"
LONDON = ZoneInfo("Europe/London")
MAIN = LEAGUES | {"E2": "England League One", "E3": "England League Two", "EC": "England National League",
                  "SC1": "Scotland Championship", "SC2": "Scotland League One", "SC3": "Scotland League Two"}
FIRST = {"EC": 2005}            # first season with a file; every other main code has 2000/01
FIRST_SEASON = 2000
EXTRA = {"ARG": "Argentina", "AUT": "Austria", "BRA": "Brazil", "CHN": "China", "DNK": "Denmark", "FIN": "Finland",
         "IRL": "Ireland", "JPN": "Japan", "MEX": "Mexico", "NOR": "Norway", "POL": "Poland", "ROU": "Romania",
         "RUS": "Russia", "SWE": "Sweden", "SWZ": "Switzerland", "USA": "USA"}
# football-data's bookmaker codes (notes.txt, plus SKB from 2026/27) -> ours; `P` and `PS` are both Pinnacle.
BOOKS = {b: b.lower() for b in ("1XB", "B365", "BF", "BFD", "BFE", "BMGM", "BS", "BV", "BW", "CL", "GB", "IW", "LB",
                                "PP", "SB", "SJ", "SK", "SKB", "SO", "SY", "VC", "WH", "Max", "Avg", "BbMx", "BbAv")}
BOOKS |= {"P": "ps", "PS": "ps"}
SIDES = {"1x2": ("H", "D", "A"), "ou": (">", "<")}
LINES = {"1x2": None, "ou": 2.5}
# Columns that end like a price but are not one, or that are read and set aside: D2 2004/05 heads three columns
# "LB" (Ladbrokes 1X2, which outcome where unstated); HT/AT are G1 2000/01-2004/05's team columns.
NOT_PRICES = {"LB", "HT", "AT", "Bb1X2", "BbOU", "BbAH", "BbAHh", "AHh", "AHCh", "FTHG", "FTAG", "HTHG", "HTAG", "HxG",
              "AxG", "HTR", "FTR"}
LOOKS_LIKE_PRICE = re.compile(r"[HDA]$|[<>]\d")


@dataclass(frozen=True)
class Price:
    market: str             # '1x2' | 'ou'
    line: float | None
    bookmaker: str
    kind: str               # 'pre_match' | 'closing'
    prices: tuple[float, ...]


@dataclass(frozen=True)
class Game:
    code: str
    season: int             # the season's first year
    date: dt.date           # UK date
    kickoff: dt.datetime | None
    home: str
    away: str
    hg: int
    ag: int
    prices: tuple[Price, ...]


@dataclass
class Report:
    unknown: set[str] = field(default_factory=set)      # price-like columns no bookmaker code explains
    no_score: int = 0
    bad_prices: int = 0                                 # a missing outcome, a zero, a price of 1 or less


def decode(raw: bytes) -> str:
    """Most files are UTF-8; a few older ones are Windows-1252 (King’s Lynn, referees' umlauts)."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


def _column(name: str) -> tuple[str, str, str, str] | None:
    """(market, bookmaker, kind, outcome) for a price column, else None."""
    for market, sides in SIDES.items():
        for side in sides:
            suffix = side if market == "1x2" else f"{side}2.5"
            if not name.endswith(suffix):
                continue
            stem = name.removesuffix(suffix)
            if stem in BOOKS:
                return market, BOOKS[stem], "pre_match", side
            if stem.endswith("C") and stem[:-1] in BOOKS:
                return market, BOOKS[stem[:-1]], "closing", side
    return None


def _date(value: str) -> dt.date:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"date {value!r}")


def _price(value: str) -> float | None:
    try:
        p = float(value)
    except ValueError:
        return None
    return p if p > 1 else None


def parse(text: str, code: str, season: int | None = None) -> tuple[list[Game], Report]:
    """One file. `season` for a main division's file; an extra league's rows carry theirs (`2012/2013` or `2012`)."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise ValueError("empty file")
    header = [h.strip() for h in rows[0]]
    report = Report()
    groups: dict[tuple[str, str, str], dict[str, int]] = collections.defaultdict(dict)
    for i, name in enumerate(header):
        if not name or name in NOT_PRICES or "AH" in name:
            continue
        col = _column(name)
        if col is None:
            if LOOKS_LIKE_PRICE.search(name):
                report.unknown.add(name)
            continue
        market, book, kind, side = col
        if side in groups[(market, book, kind)]:            # one column twice: which one is meant is unknown
            report.unknown.add(name)
        groups[(market, book, kind)].setdefault(side, i)
    idx = {name: i for i, name in reversed(list(enumerate(header)))}    # first column of a name

    games = []
    for r in rows[1:]:
        r = [v.strip() for v in r] + [""] * (len(header) - len(r))
        get = lambda *names: next((r[idx[n]] for n in names if n in idx and r[idx[n]]), "")  # noqa: E731
        home, away = get("HomeTeam", "Home", "HT"), get("AwayTeam", "Away", "AT")
        if not home and not away:
            continue                                        # blank line
        hg, ag = get("FTHG", "HG"), get("FTAG", "AG")
        if not hg or not ag:
            report.no_score += 1
            continue
        date = _date(get("Date"))
        time = get("Time")
        kickoff = (dt.datetime.combine(date, dt.time.fromisoformat(time), LONDON).astimezone(dt.UTC)
                   if time else None)
        prices = []
        for (market, book, kind), sides in groups.items():
            values = [r[sides[s]] if s in sides else "" for s in SIDES[market]]
            if not any(values):
                continue
            ps = [_price(v) for v in values]
            if None in ps:
                report.bad_prices += 1
                continue
            prices.append(Price(market, LINES[market], book, kind, tuple(ps)))
        games.append(Game(code, season if season is not None else int(get("Season")[:4]), date, kickoff, home, away,
                          int(hg), int(ag), tuple(prices)))
    return games, report


def main_url(code: str, season: int) -> str:
    return f"{BASE}/mmz4281/{season_code(season)}/{code}.csv"


def extra_url(code: str) -> str:
    return f"{BASE}/new/{code}.csv"


def fetch(session: requests.Session, url: str) -> str:
    r = session.get(url, timeout=60)
    r.raise_for_status()
    return decode(r.content)
