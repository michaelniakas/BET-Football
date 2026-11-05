"""Schema migrations: plain SQL files in api/migrations, NNNN_name.up.sql + NNNN_name.down.sql.

    python -m api.migrate up      # apply every pending migration (Toft runs this before a deploy starts)
    python -m api.migrate down    # revert the latest applied migration

Each migration runs in its own transaction together with its row in schema_migrations, so a failed
one leaves nothing behind. Connection string from DATABASE_URL.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg

DIR = Path(__file__).resolve().parent / "migrations"
LOCK = 7_301_001        # advisory lock key: two deploys must not migrate at the same time


def versions() -> list[str]:
    ups = sorted(p.name.removesuffix(".up.sql") for p in DIR.glob("*.up.sql"))
    missing = [v for v in ups if not (DIR / f"{v}.down.sql").exists()]
    if missing:
        raise SystemExit(f"no down migration for {', '.join(missing)}")
    return ups


def _applied(conn: psycopg.Connection) -> list[str]:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                 "(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
    return [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]


def up(url: str) -> list[str]:
    done = []
    with psycopg.connect(url) as conn:
        for v in versions():
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
                if v in _applied(conn):
                    continue
                conn.execute((DIR / f"{v}.up.sql").read_text())
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (v,))
            done.append(v)
    return done


def down(url: str) -> str | None:
    with psycopg.connect(url) as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
        applied = _applied(conn)
        if not applied:
            return None
        v = applied[-1]
        conn.execute((DIR / f"{v}.down.sql").read_text())
        conn.execute("DELETE FROM schema_migrations WHERE version = %s", (v,))
        return v


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ("up", "down"):
        raise SystemExit(__doc__)
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set")
    if sys.argv[1] == "up":
        done = up(url)
        print(f"applied: {', '.join(done)}" if done else "up to date")
    else:
        v = down(url)
        print(f"reverted: {v}" if v else "nothing to revert")


if __name__ == "__main__":
    main()
