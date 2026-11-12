"""Ingest use cases: fetched rows in, Postgres rows out, and a report of what was skipped or disagrees.

Each job is one transaction (its own, or a savepoint in the caller's) under the ledgers' advisory lock. A team name no source identifies is never
guessed: the fixture is skipped and the name queued for `python -m ingest alias`.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd
import psycopg

from model.data import LEAGUES, current_season_start

from . import espn, names as team_names, store
from .martj42 import Game

NATIONAL, CLUB = "national", "club"
LONDON = ZoneInfo("Europe/London")  # football-data's dates, which the ledger projection matches exactly
# Same teams within a day: sources disagree on the date (local vs UTC). Served by the fixtures_teams index.
NEAR = "f.home_id = s.home_id AND f.away_id = s.away_id AND f.date BETWEEN s.date - 1 AND s.date + 1"


class AliasError(Exception):
    pass


def map_names(conn: psycopg.Connection, source: str, kind: str, seen: dict[str, str]) -> dict[str, int]:
    """seen: name -> competition it was seen in. Returns name -> team id for the names a team is known for;
    exact matches after normalising become aliases, the rest are queued."""
    mapped = store.aliases(conn, source, kind)
    todo = set(seen) - set(mapped)
    if todo:
        found, unknown = team_names.match(todo, store.known_names(conn, kind))
        store.add_aliases(conn, source, found)
        store.queue(conn, source, kind, {n: (seen[n], s) for n, s in unknown.items()})
        mapped |= found
    return {n: mapped[n] for n in seen if n in mapped}


def import_martj42(conn: psycopg.Connection, games: list[Game], former: dict[str, str]) -> dict[str, int]:
    """martj42 is the identity source for national teams: every name in it is a team. A game already stored
    (same teams within a day, from ESPN or an earlier run) is kept; only a missing score is filled in."""
    with conn.transaction():
        store.lock(conn)
        teams = store.create_teams(conn, "martj42", NATIONAL, {g.home for g in games} | {g.away for g in games})
        store.add_aliases(conn, "martj42", {f: teams[c] for f, c in former.items() if c in teams and f not in teams})
        conn.execute("""
            INSERT INTO competitions (code, name, kind) SELECT DISTINCT t, t, 'international' FROM unnest(%s::text[]) t
            ON CONFLICT (code) DO NOTHING""", (sorted({g.tournament for g in games}),))
        conn.execute("""CREATE TEMP TABLE stage (date date, home_id int, away_id int, hg smallint, ag smallint,
                        tournament text, neutral bool, decided text) ON COMMIT DROP""")
        # The source lists a few games twice, sometimes with two scores: the first is staged, the rest reported.
        first = list({(g.date, g.home, g.away): g for g in reversed(games)}.values())
        with conn.cursor().copy("COPY stage FROM STDIN") as cp:
            for g in first:
                cp.write_row((g.date, teams[g.home], teams[g.away], g.hg, g.ag, g.tournament, g.neutral,
                              "penalties" if g.penalties else None))
        near = NEAR
        inserted = conn.execute(f"""
            INSERT INTO fixtures (competition_id, season, date, home_id, away_id, hg, ag, neutral, decided)
            SELECT c.id, extract(year FROM s.date)::smallint, s.date, s.home_id, s.away_id, s.hg, s.ag, s.neutral,
                   s.decided
            FROM stage s JOIN competitions c ON c.code = s.tournament
            WHERE NOT EXISTS (SELECT FROM fixtures f WHERE {near})
            ON CONFLICT DO NOTHING""").rowcount
        # ponytail: two martj42 games against one stored fixture (same teams on consecutive days) fill it from either.
        filled = conn.execute(f"""
            UPDATE fixtures f SET hg = s.hg, ag = s.ag, decided = coalesce(f.decided, s.decided),
                                  neutral = coalesce(f.neutral, s.neutral)
            FROM stage s WHERE {near} AND f.hg IS NULL AND s.hg IS NOT NULL""").rowcount
        missing, disagree = conn.execute(f"""
            SELECT count(*) FILTER (WHERE f.id IS NULL),
                   count(*) FILTER (WHERE (f.hg, f.ag) IS DISTINCT FROM (s.hg, s.ag) AND s.hg IS NOT NULL)
            FROM stage s LEFT JOIN LATERAL (SELECT f.id, f.hg, f.ag FROM fixtures f WHERE {near}
                                            ORDER BY abs(f.date - s.date) LIMIT 1) f ON true""").fetchone()
        return {"games": len(games), "duplicates": len(games) - len(first),
                "inserted": inserted, "filled": filled, "missing": missing,
                "score_disagrees": disagree, "teams": len(set(teams.values()))}



def import_espn(conn: psycopg.Connection, games: list[dict]) -> dict[str, int]:
    """ESPN games (ingest.espn.events: `league` is a football-data code or a national-competition slug) onto
    fixtures. An event's fixture is found by its ref, else by the same teams within a day, else inserted. Its
    kick-off follows ESPN; a missing score is filled once the game is finished, never rewritten. A game moved by
    more than a day is another fixture; its old one stays, unplayed."""
    with conn.transaction():
        store.lock(conn)
        comps = {}
        for g in games:
            if g["league"] in LEAGUES:
                comps[g["league"]] = (LEAGUES[g["league"]], "club_league")
            else:
                comps[espn.NATIONAL[g["league"]]] = (espn.NATIONAL[g["league"]], "international")
        conn.execute("""
            INSERT INTO competitions (code, name, kind) SELECT c, n, k FROM unnest(%s::text[], %s::text[], %s::text[]) u(c, n, k)
            ON CONFLICT (code) DO UPDATE SET name = coalesce(competitions.name, EXCLUDED.name)""",
            (list(comps), [n for n, _ in comps.values()], [k for _, k in comps.values()]))
        ids = {}
        for kind, part in ((CLUB, [g for g in games if g["league"] in LEAGUES]),
                           (NATIONAL, [g for g in games if g["league"] not in LEAGUES])):
            seen = {n: g["league"] for g in part for n in (g["home"], g["away"])}
            ids[kind] = map_names(conn, "espn", kind, seen)
        conn.execute("""CREATE TEMP TABLE stage (ref text, code text, season smallint, date date, kickoff timestamptz,
                        home_id int, away_id int, hg smallint, ag smallint, decided text, neutral bool)
                        ON COMMIT DROP""")
        staged = 0
        with conn.cursor().copy("COPY stage FROM STDIN") as cp:
            for g in games:
                kind = CLUB if g["league"] in LEAGUES else NATIONAL
                home, away = ids[kind].get(g["home"]), ids[kind].get(g["away"])
                if home is None or away is None:
                    continue
                if kind == CLUB:
                    date = g["kickoff"].astimezone(LONDON).date()
                    code, season = g["league"], current_season_start(pd.Timestamp(date))
                else:
                    date = g["kickoff"].astimezone(dt.UTC).date()
                    code, season = espn.NATIONAL[g["league"]], date.year
                cp.write_row((g["event_id"], code, season, date, g["kickoff"], home, away, g["hg"], g["ag"],
                              g["decided"], g["neutral"]))
                staged += 1
        ref = "r.source = 'espn' AND r.ref = s.ref"
        rescheduled = conn.execute(f"""
            DELETE FROM fixture_refs r USING stage s, fixtures f
            WHERE {ref} AND f.id = r.fixture_id AND abs(f.date - s.date) > 1""").rowcount
        inserted = conn.execute(f"""
            INSERT INTO fixtures (competition_id, season, date, home_id, away_id)
            SELECT c.id, s.season, s.date, s.home_id, s.away_id FROM stage s JOIN competitions c ON c.code = s.code
            WHERE NOT EXISTS (SELECT FROM fixture_refs r WHERE {ref}) AND NOT EXISTS (SELECT FROM fixtures f WHERE {NEAR})
            ON CONFLICT DO NOTHING""").rowcount
        # The nearest fixture no other ESPN event holds; two events claiming one fixture: the second stays unlinked.
        linked = conn.execute(f"""
            INSERT INTO fixture_refs (source, ref, fixture_id)
            SELECT 'espn', s.ref, f.id FROM stage s CROSS JOIN LATERAL (
                SELECT f.id FROM fixtures f WHERE {NEAR}
                  AND NOT EXISTS (SELECT FROM fixture_refs o WHERE o.source = 'espn' AND o.fixture_id = f.id)
                ORDER BY abs(f.date - s.date) LIMIT 1) f
            WHERE NOT EXISTS (SELECT FROM fixture_refs r WHERE {ref})
            ON CONFLICT DO NOTHING""").rowcount
        conn.execute(f"""
            UPDATE fixtures f SET kickoff = s.kickoff, neutral = coalesce(f.neutral, s.neutral)
            FROM stage s JOIN fixture_refs r ON {ref}
            WHERE f.id = r.fixture_id AND abs((s.kickoff AT TIME ZONE 'UTC')::date - f.date) <= 1
              AND (f.kickoff IS DISTINCT FROM s.kickoff OR f.neutral IS NULL AND s.neutral IS NOT NULL)""")
        # International scores only: football-data owns club scores (the ledgers settle on them).
        filled = conn.execute(f"""
            UPDATE fixtures f SET hg = s.hg, ag = s.ag, decided = s.decided
            FROM stage s JOIN fixture_refs r ON {ref} JOIN competitions c ON c.kind = 'international'
            WHERE f.id = r.fixture_id AND f.competition_id = c.id AND f.hg IS NULL AND s.hg IS NOT NULL
              AND (f.result IS NULL OR f.result = outcome_1x2(s.hg, s.ag))
              AND (f.ou25 IS NULL OR f.ou25 = outcome_ou25(s.hg, s.ag))""").rowcount
        disagree, unlinked = conn.execute(f"""
            SELECT count(*) FILTER (WHERE s.hg IS NOT NULL AND f.id IS NOT NULL
                                    AND ((f.hg, f.ag) <> (s.hg, s.ag) OR f.result <> outcome_1x2(s.hg, s.ag)
                                         OR f.ou25 <> outcome_ou25(s.hg, s.ag))),
                   count(*) FILTER (WHERE f.id IS NULL)
            FROM stage s LEFT JOIN fixture_refs r ON {ref} LEFT JOIN fixtures f ON f.id = r.fixture_id""").fetchone()
        return {"games": len(games), "unmapped": len(games) - staged, "inserted": inserted, "linked": linked,
                "rescheduled": rescheduled, "filled": filled, "score_disagrees": disagree, "unlinked": unlinked}


def import_odds(conn: psycopg.Connection, rows: list[dict]) -> dict[str, int]:
    """Odds API prices (ingest.oddsapi.parse) onto the stored fixture with the same teams within a day of the
    kick-off; the event id is kept as a ref. A game no source has stored yet gets no fixture here (ESPN and
    martj42 make fixtures): it is counted. Only prices quoted before kick-off are kept."""
    with conn.transaction():
        store.lock(conn)
        seen = {n: r["sport"] for r in rows for n in (r["home"], r["away"])}
        ids = map_names(conn, "the-odds-api", NATIONAL, seen)
        conn.execute("""CREATE TEMP TABLE stage (ref text, date date, kickoff timestamptz, home_id int, away_id int,
                        bookmaker text, captured_at timestamptz, prices float8[]) ON COMMIT DROP""")
        with conn.cursor().copy("COPY stage FROM STDIN") as cp:
            for r in rows:
                if r["home"] in ids and r["away"] in ids:
                    cp.write_row((r["ref"], r["kickoff"].astimezone(dt.UTC).date(), r["kickoff"], ids[r["home"]],
                                  ids[r["away"]], r["bookmaker"], r["captured_at"], r["prices"]))
        ref = "r.source = 'the-odds-api' AND r.ref = s.ref"
        conn.execute(f"""
            INSERT INTO fixture_refs (source, ref, fixture_id)
            SELECT DISTINCT ON (s.ref) 'the-odds-api', s.ref, f.id FROM stage s CROSS JOIN LATERAL (
                SELECT f.id FROM fixtures f WHERE {NEAR}
                  AND NOT EXISTS (SELECT FROM fixture_refs o WHERE o.source = 'the-odds-api' AND o.fixture_id = f.id)
                ORDER BY abs(f.date - s.date) LIMIT 1) f
            WHERE NOT EXISTS (SELECT FROM fixture_refs r WHERE {ref})
            ON CONFLICT DO NOTHING""")
        inserted = conn.execute(f"""
            INSERT INTO odds_snapshots (fixture_id, market, line, source, bookmaker, captured_at, prices)
            SELECT r.fixture_id, '1x2', NULL, 'the-odds-api', s.bookmaker, s.captured_at, s.prices
            FROM stage s JOIN fixture_refs r ON {ref} JOIN fixtures f ON f.id = r.fixture_id
            WHERE s.captured_at < coalesce(f.kickoff, s.kickoff)
            ON CONFLICT DO NOTHING""").rowcount
        no_fixture = conn.execute(f"SELECT count(DISTINCT s.ref) FROM stage s"
                                  f" WHERE NOT EXISTS (SELECT FROM fixture_refs r WHERE {ref})").fetchone()[0]
        return {"events": len({r["ref"] for r in rows}),
                "unmapped": len({r["ref"] for r in rows if r["home"] not in ids or r["away"] not in ids}),
                "no_fixture": no_fixture, "snapshots": inserted}


def seed(conn: psycopg.Connection, rows: list[dict[str, str]]) -> dict[str, int]:
    """The pilot's checked mapping (research/teams.csv: source, league, source_name, fd_name) as aliases.
    An alias that already points elsewhere is left alone and counted."""
    with conn.transaction():
        store.lock(conn)
        clubs = store.create_teams(conn, "football-data", CLUB, {r["fd_name"] for r in rows})
        conflicts = 0
        for source in sorted({r["source"] for r in rows}):
            have, new = store.aliases(conn, source, CLUB), {}
            for r in (r for r in rows if r["source"] == source):
                name, team = r["source_name"], clubs[r["fd_name"]]
                if name in have or name in new:
                    conflicts += have.get(name, new.get(name)) != team
                else:
                    new[name] = team
            store.add_aliases(conn, source, new)
        return {"rows": len(rows), "conflicts": conflicts}



def alias(conn: psycopg.Connection, source: str, name: str, target: str) -> int:
    """Confirm a queued name: `target` is a team id or a spelling exactly one team of the same kind has."""
    with conn.transaction():
        store.lock(conn)
        row = conn.execute("SELECT kind FROM unmapped_names WHERE (source, name) = (%s, %s)", (source, name)).fetchone()
        if row is None:
            raise AliasError(f"{source}:{name!r} is not in the queue (python -m ingest unmapped)")
        kind = row[0]
        if target.isdigit():
            teams = [t for (t,) in conn.execute("SELECT id FROM teams WHERE id = %s AND kind = %s", (int(target), kind))]
        else:
            teams = store.teams_named(conn, kind, target)
        if len(teams) != 1:
            raise AliasError(f"{target!r} names {len(teams)} {kind} teams{': ' + str(teams) if teams else ''}; "
                             "give the team id")
        store.add_aliases(conn, source, {name: teams[0]})
        return teams[0]


def check(conn: psycopg.Connection, since: dt.date) -> dict:
    """What the jobs left behind, for a human: fixtures per kind, refs per source, names still queued, how close
    to kick-off the last Odds API price of each finished game was, and fixtures from `since` on that share their
    teams with another fixture within two days (a two-legged tie, or one game stored twice)."""
    kinds = conn.execute("""
        SELECT c.kind, count(*), count(f.kickoff), count(f.hg) FROM fixtures f
        JOIN competitions c ON c.id = f.competition_id GROUP BY c.kind ORDER BY c.kind""").fetchall()
    refs = conn.execute("SELECT source, count(*) FROM fixture_refs GROUP BY source ORDER BY source").fetchall()
    unmapped = conn.execute("SELECT count(*) FROM unmapped_names").fetchone()[0]
    closing = conn.execute("""
        WITH last AS (
            SELECT f.kickoff - max(o.captured_at) AS gap FROM fixtures f
            JOIN odds_snapshots o ON o.fixture_id = f.id AND o.source = 'the-odds-api'
            WHERE f.hg IS NOT NULL GROUP BY f.id)
        SELECT count(*), percentile_cont(0.5) WITHIN GROUP (ORDER BY gap), max(gap) FROM last""").fetchone()
    near = conn.execute("""
        SELECT a.id, a.date, h.name, w.name, b.id, b.date, b.home_id = a.home_id FROM fixtures a
        JOIN fixtures b ON b.id > a.id AND b.date BETWEEN a.date - 2 AND a.date + 2
         AND ((b.home_id, b.away_id) = (a.home_id, a.away_id) OR (b.home_id, b.away_id) = (a.away_id, a.home_id))
        JOIN teams h ON h.id = a.home_id JOIN teams w ON w.id = a.away_id
        WHERE greatest(a.date, b.date) >= %s ORDER BY a.date, a.id""", (since,)).fetchall()
    return {"kinds": kinds, "refs": refs, "unmapped": unmapped, "closing": closing, "near": near}
