"""Postgres for the ingest jobs: team identities, the unmapped-name queue, fixtures. Every write runs inside the
caller's transaction, after `lock`: the ledger projection creates teams and fixtures too."""
from __future__ import annotations

import psycopg

from api.ledgers import LOCK


def lock(conn: psycopg.Connection) -> None:
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))


def aliases(conn: psycopg.Connection, source: str, kind: str) -> dict[str, int]:
    return dict(conn.execute("SELECT a.name, a.team_id FROM team_aliases a JOIN teams t ON t.id = a.team_id"
                             " WHERE a.source = %s AND t.kind = %s", (source, kind)).fetchall())


def known_names(conn: psycopg.Connection, kind: str) -> list[tuple[str, int]]:
    """Every spelling of every team of this kind, from any source."""
    return conn.execute("SELECT DISTINCT a.name, a.team_id FROM team_aliases a JOIN teams t ON t.id = a.team_id"
                        " WHERE t.kind = %s", (kind,)).fetchall()


def add_aliases(conn: psycopg.Connection, source: str, mapping: dict[str, int]) -> None:
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO team_aliases (source, name, team_id) VALUES (%s, %s, %s)",
                        [(source, n, t) for n, t in mapping.items()])


def create_teams(conn: psycopg.Connection, source: str, kind: str, names: set[str]) -> dict[str, int]:
    """The identity source's names (football-data for clubs, martj42 for national teams): a team for each new one."""
    known = aliases(conn, source, kind)
    rows = conn.execute("""
        WITH new AS (INSERT INTO teams (name, kind) SELECT n, %s FROM unnest(%s::text[]) n RETURNING id, name)
        INSERT INTO team_aliases (source, name, team_id) SELECT %s, name, id FROM new RETURNING name, team_id""",
        (kind, sorted(names - set(known)), source)).fetchall()
    return known | dict(rows)


def queue(conn: psycopg.Connection, source: str, kind: str, items: dict[str, tuple[str, str | None]]) -> None:
    """name -> (competition, suggestion). A name seen again counts once more and keeps its first sighting."""
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO unmapped_names (source, name, kind, competition, suggestion) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (source, name) DO UPDATE SET seen = unmapped_names.seen + 1, last_seen = now(),
                competition = EXCLUDED.competition, suggestion = EXCLUDED.suggestion""",
            [(source, n, kind, c, s) for n, (c, s) in items.items()])


def unmapped(conn: psycopg.Connection) -> list[tuple]:
    return conn.execute("SELECT source, name, kind, competition, suggestion, seen, last_seen FROM unmapped_names"
                        " ORDER BY source, competition, name").fetchall()


def teams_named(conn: psycopg.Connection, kind: str, name: str) -> list[int]:
    """Teams of this kind with a spelling exactly `name`, in any source."""
    return [r[0] for r in conn.execute("SELECT DISTINCT a.team_id FROM team_aliases a JOIN teams t ON t.id = a.team_id"
                                       " WHERE t.kind = %s AND a.name = %s ORDER BY 1", (kind, name))]
