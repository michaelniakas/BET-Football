"""Fixtures, results and team identities from outside sources into Postgres (docs/phase2-plan.md).

    python -m ingest martj42                   # international results since 1872 (re-run: fills, never rewrites)
    python -m ingest espn [FIRST LAST]         # club + national fixtures, kick-offs, results (default today-2 .. +7)
    python -m ingest odds                      # national-team 1X2 prices, next 72h (ODDS_API_KEY; keeps a reserve)
    python -m ingest seed research/teams.csv   # the pilot's ESPN -> football-data club names, as aliases
    python -m ingest unmapped                  # names a job could not map; their fixtures were skipped
    python -m ingest alias SOURCE NAME TEAM    # confirm one: TEAM is a team id or another spelling of it
    python -m ingest check                     # counts, queued names, closing prices, near-duplicates (30 days)

Connection string from DATABASE_URL. Exit 1 when something needs a human: unmapped names, disagreeing scores.
"""
from __future__ import annotations

import csv
import datetime as dt
import os
import sys
from pathlib import Path

import psycopg

from . import espn, jobs, martj42, names, oddsapi, store

ARGS = {"martj42": [0], "espn": [0, 2], "odds": [0], "seed": [1], "unmapped": [0], "alias": [3], "check": [0]}
WINDOW = (-2, 7)        # days around today: results of the last two days, fixtures of the next week
PAUSE = 0.5             # seconds between ESPN requests (~330 a run)
HORIZON = dt.timedelta(hours=72)
RECENT = dt.timedelta(days=30)  # how far back `check` looks for near-duplicates (older ones are martj42 history)
RESERVE = 50            # Odds API credits never spent by this job (free tier: 500 a month)


def main() -> int:
    cmd, args = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("", [])
    if len(args) not in ARGS.get(cmd, []):
        raise SystemExit(__doc__)
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set")
    with psycopg.connect(url, autocommit=True) as conn:   # each job is its own transaction
        if cmd == "martj42":
            games, former = martj42.fetch()
            report = jobs.import_martj42(conn, games, former)
            print(", ".join(f"{k} {v}" for k, v in report.items()))
            return 1 if report["missing"] or report["score_disagrees"] else 0
        if cmd == "espn":
            today = dt.datetime.now(dt.UTC).date()
            first, last = (map(dt.date.fromisoformat, args) if args else
                           (today + dt.timedelta(WINDOW[0]), today + dt.timedelta(WINDOW[1])))
            games = espn.events(first, last, espn.SLUGS | {s: s for s in espn.NATIONAL}, pause=PAUSE)
            report = jobs.import_espn(conn, games)
            print(", ".join(f"{k} {v}" for k, v in report.items()))
            return 1 if report["unmapped"] or report["score_disagrees"] or report["unlinked"] else 0
        if cmd == "odds":
            key = os.environ.get("ODDS_API_KEY")
            if not key:
                raise SystemExit("ODDS_API_KEY is not set")
            try:
                known = {names.norm(n) for n, _ in store.known_names(conn, jobs.NATIONAL)}
                rows, left, skipped = oddsapi.snapshot(key, dt.datetime.now(dt.UTC), HORIZON, RESERVE,
                                                       lambda n: names.norm(n) in known)
            except oddsapi.OddsApiError as e:
                raise SystemExit(f"Odds API {e}") from None
            report = jobs.import_odds(conn, rows)
            print(", ".join(f"{k} {v}" for k, v in report.items()) + f", credits left {left}, skipped {skipped}")
            return 1 if report["unmapped"] or skipped else 0
        if cmd == "seed":
            with Path(args[0]).open(newline="") as fh:
                report = jobs.seed(conn, list(csv.DictReader(fh)))
            print(f"{report['rows']} rows, {report['conflicts']} already mapped to another team")
            return 1 if report["conflicts"] else 0
        if cmd == "unmapped":
            rows = store.unmapped(conn)
            for source, name, kind, competition, suggestion, seen, last in rows:
                print(f"{source}\t{kind}\t{competition}\t{name!r}\tseen {seen}, last {last:%Y-%m-%d}"
                      f"\tsuggestion: {suggestion!r}")
            print(f"{len(rows)} unmapped")
            return 1 if rows else 0
        if cmd == "check":
            r = jobs.check(conn, dt.datetime.now(dt.UTC).date() - RECENT)
            for kind, n, kicked, scored in r["kinds"]:
                print(f"{kind}: {n} fixtures, {kicked} with a kick-off time, {scored} with a score")
            print("refs: " + ", ".join(f"{source} {n}" for source, n in r["refs"]))
            print(f"unmapped names: {r['unmapped']}")
            n, median, worst = r["closing"]
            print(f"finished games with Odds API prices: {n}; last price before kick-off, median {median}, worst {worst}")
            for a, a_date, home, away, b, b_date, same in r["near"]:
                print(f"near: {a} {a_date} {home} v {away} ~ {b} {b_date}{'' if same else ' (reversed)'}")
            print(f"{len(r['near'])} near-duplicate pairs since {dt.datetime.now(dt.UTC).date() - RECENT}")
            return 1 if r["unmapped"] else 0
        try:
            team = jobs.alias(conn, *args)
        except jobs.AliasError as e:
            raise SystemExit(str(e)) from None
        print(f"{args[0]}:{args[1]!r} -> team {team}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
