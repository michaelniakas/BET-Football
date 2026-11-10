"""Build research/teams.csv: ESPN team name -> football-data name, per league.

    python -m research.teams 2026-11-10 2026-11-23

Exits non-zero, listing the names, while any fixture team is unmapped or two names share a target.
"""
import collections
import csv
import datetime as dt
import difflib
import pathlib
import sys
import unicodedata

import requests

from model.data import BASE_URL, _read_csv, current_season_start, season_code
from research.espn import fixtures

OUT = pathlib.Path(__file__).parent / "teams.csv"
NOISE = {"fc", "cf", "afc", "sc", "ac", "as", "ssc", "sv", "vfb", "vfl", "tsv", "fsv", "kv", "krc", "rc", "cd", "ud", "sd", "1", "the"}
# Names difflib can't resolve safely, checked by hand: (league, ESPN name) -> football-data name.
OVERRIDES: dict[tuple[str, str], str] = {
    ("B1", "OH Leuven"): "Oud-Heverlee Leuven", ("B1", "Sint-Truidense"): "St Truiden",
    ("D1", "Borussia Mönchengladbach"): "M'gladbach", ("D1", "FC Cologne"): "FC Koln",
    ("E0", "Manchester City"): "Man City", ("E0", "Manchester United"): "Man United",
    ("E1", "Queens Park Rangers"): "QPR", ("E1", "West Bromwich Albion"): "West Brom",
    ("E1", "Wolverhampton Wanderers"): "Wolves",
    ("F1", "Paris Saint-Germain"): "Paris SG", ("F1", "Stade Rennais"): "Rennes",
    ("F2", "Saint-Étienne"): "St Etienne",
    ("I1", "Internazionale"): "Inter",
    ("SC0", "Heart of Midlothian"): "Hearts",
    ("SP1", "Athletic Club"): "Ath Bilbao", ("SP1", "Atlético Madrid"): "Ath Madrid", ("SP1", "Deportivo"): "La Coruna",
    ("SP2", "RC Celta Fortuna"): "Celta B", ("SP2", "Real Sociedad II"): "Sociedad B", ("SP2", "Sporting Gijón"): "Sp Gijon",
    ("T1", "Amed SFK"): "Amedspor", ("T1", "Erzurum BB"): "Erzurumspor", ("T1", "Istanbul Basaksehir"): "Buyuksehyr",
}


def norm(name: str) -> str:
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return " ".join(t for t in plain.replace(".", " ").replace("-", " ").split() if t not in NOISE)


def fd_teams(league: str) -> list[str]:
    url = BASE_URL.format(season=season_code(current_season_start()), league=league)
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    df = _read_csv(r.content)
    return sorted(set(df["HomeTeam"].str.strip()) | set(df["AwayTeam"].str.strip()))


def resolve(league: str, names: set[str], candidates: list[str]) -> tuple[dict[str, str], list[str]]:
    """Map names onto candidates; a name stays unresolved if it is unclear or its target is contested."""
    by_norm = {norm(c): c for c in candidates}
    guess = {}
    for name in names:
        if (league, name) in OVERRIDES:
            guess[name] = OVERRIDES[league, name]
        elif hit := difflib.get_close_matches(norm(name), by_norm, n=1, cutoff=0.8):
            guess[name] = by_norm[hit[0]]
        elif len(hits := [c for n, c in by_norm.items() if set(n.split()) <= set(norm(name).split())]) == 1:
            guess[name] = hits[0]  # "Hull" inside "Hull City"
    taken = collections.Counter(guess.values())
    mapped = {n: c for n, c in guess.items() if taken[c] == 1 and c in candidates}
    return mapped, sorted(names - set(mapped))


def main(first: dt.date, last: dt.date) -> int:
    by_league = collections.defaultdict(set)
    games = fixtures(first, last)
    for f in games:
        by_league[f["league"]] |= {f["home"], f["away"]}
    rows, failed = [], []
    for league, names in sorted(by_league.items()):
        candidates = fd_teams(league)
        mapped, missing = resolve(league, names, candidates)
        rows += [("espn", league, n, mapped[n]) for n in sorted(mapped)]
        failed += [(league, n, candidates) for n in missing]
    with OUT.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["source", "league", "source_name", "fd_name"])
        writer.writerows(rows)
    print(f"{len(games)} fixtures, {len(rows)} teams mapped, {len(failed)} unresolved")
    for league, name, candidates in failed:
        print(f"  {league}: {name!r} (norm {norm(name)!r}) candidates: {candidates}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(dt.date.fromisoformat(sys.argv[1]), dt.date.fromisoformat(sys.argv[2])))
