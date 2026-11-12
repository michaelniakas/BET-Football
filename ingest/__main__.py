"""Fixtures, results and team identities from outside sources into Postgres (docs/phase2-plan.md).

    python -m ingest martj42                   # international results since 1872 (re-run: fills, never rewrites)
    python -m ingest espn [FIRST LAST]         # club + national fixtures, kick-offs, results (default today-2 .. +7)
    python -m ingest odds                      # national-team 1X2 prices, next 72h (ODDS_API_KEY; keeps a reserve)
    python -m ingest football-data [CODE [SEASON]]  # club results + 1X2/O-U 2.5 prices: this season, the extra
                                               # leagues, then missing seasons since 2000/01 (10-minute budget)
    python -m ingest merge NAME KEEP           # two football-data spellings of one club: NAME's team joins KEEP's
    python -m ingest seed research/teams.csv   # the pilot's ESPN -> football-data club names, as aliases
    python -m ingest unmapped                  # names a job could not map; their fixtures were skipped
    python -m ingest alias SOURCE NAME TEAM    # confirm one: TEAM is a team id or another spelling of it
    python -m ingest check                     # counts, queued names, closing prices, near-duplicates (30 days),
                                               # club seasons with scores, prices and closing prices

Connection string from DATABASE_URL. Exit 1 when something needs a human: unmapped names, disagreeing scores,
unknown price columns, failed files.
"""
from __future__ import annotations

import csv
import datetime as dt
import os
import sys
import time
from pathlib import Path

import pandas as pd
import psycopg
import requests

from model.data import current_season_start

from . import espn, footballdata, jobs, martj42, names, oddsapi, store

ARGS = {"martj42": [0], "espn": [0, 2], "odds": [0], "seed": [1], "unmapped": [0], "alias": [3], "check": [0],
        "football-data": [0, 1, 2], "merge": [2]}
WINDOW = (-2, 7)        # days around today: results of the last two days, fixtures of the next week
PAUSE = 0.5             # seconds between ESPN requests (~330 a run)
HORIZON = dt.timedelta(hours=72)
RECENT = dt.timedelta(days=30)  # how far back `check` looks for near-duplicates (older ones are martj42 history)
RESERVE = 50            # Odds API credits never spent by this job (free tier: 500 a month)
FD_PAUSE = 1.0          # seconds between football-data requests
FD_BUDGET = 10 * 60     # seconds: a run stops between files after this; the next run picks up what is left


def football_data(conn: psycopg.Connection, args: list[str]) -> int:
    """One file (`CODE SEASON` for a main division, `CODE` for an extra league), or a scheduled run."""
    current = current_season_start(pd.Timestamp(dt.datetime.now(dt.UTC).date()))
    if args:
        code, season = args[0], int(args[1]) if len(args) == 2 else None
        if code not in (footballdata.MAIN if season is not None else footballdata.EXTRA):
            raise SystemExit(f"unknown football-data code {code!r} (a main division needs a SEASON, an extra league none)")
        files = [(code, season)]
    else:
        done = jobs.footballdata_seasons(conn)
        files = ([(c, current) for c in footballdata.MAIN] + [(c, None) for c in footballdata.EXTRA]
                 + [(c, s) for s in range(current - 1, footballdata.FIRST_SEASON - 1, -1) for c in footballdata.MAIN
                    if s >= footballdata.FIRST.get(c, footballdata.FIRST_SEASON) and (c, s) not in done])
    session, start, failed, needs_look = requests.Session(), time.monotonic(), 0, 0
    for i, (code, season) in enumerate(files):
        if i and time.monotonic() - start > FD_BUDGET:
            print(f"budget spent: {len(files) - i} files left for the next run")
            break
        if i:
            time.sleep(FD_PAUSE)
        url = footballdata.main_url(code, season) if season is not None else footballdata.extra_url(code)
        try:
            games, parsed = footballdata.parse(footballdata.fetch(session, url), code, season)
            report = jobs.import_footballdata(conn, games)
        except requests.HTTPError as e:
            if e.response.status_code == 404 and season == current:
                print(f"{url}: no file yet")        # a new season's file appears with its first games
                continue
            failed += 1
            print(f"{url}: {e}")
            continue
        except (requests.RequestException, ValueError, csv.Error, psycopg.Error) as e:
            failed += 1
            print(f"{url}: {type(e).__name__}: {e}")
            continue
        needs_look += bool(parsed.unknown or report["score_disagrees"])
        print(f"{code} {season or ''}: " + ", ".join(f"{k} {v}" for k, v in report.items())
              + f", bad prices {parsed.bad_prices}, no score {parsed.no_score}"
              + (f", unknown columns {sorted(parsed.unknown)}" if parsed.unknown else ""))
    return 1 if failed or needs_look else 0


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
            found, failed = {}, 0
            for league, slug in (espn.SLUGS | {s: s for s in espn.NATIONAL}).items():
                try:        # one failing slug: the others still land; the next day's window catches it up
                    found |= {g["event_id"]: g for g in espn.events(first, last, {league: slug}, pause=PAUSE)}
                except requests.RequestException as e:
                    failed += 1
                    print(f"{slug}: {type(e).__name__}: {e}")
            report = jobs.import_espn(conn, list(found.values()))
            print(", ".join(f"{k} {v}" for k, v in report.items()))
            return 1 if failed or report["unmapped"] or report["score_disagrees"] or report["unlinked"] else 0
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
        if cmd == "football-data":
            return football_data(conn, args)
        if cmd == "merge":
            try:
                team = jobs.merge(conn, *args)
            except jobs.MergeError as e:
                raise SystemExit(str(e)) from None
            print(f"football-data:{args[0]!r} -> team {team} ({args[1]!r})")
            return 0
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
            for season, n, scored, priced, closing in r["seasons"]:
                print(f"club {season}: {n} fixtures, {scored} with a score, {priced} with football-data prices,"
                      f" {closing} with closing prices")
            return 1 if r["unmapped"] else 0
        try:
            team = jobs.alias(conn, *args)
        except jobs.AliasError as e:
            raise SystemExit(str(e)) from None
        print(f"{args[0]}:{args[1]!r} -> team {team}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
