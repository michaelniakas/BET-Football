"""Migrations go up, down and up again; the ledger tables reject what the paper job must never write."""
from __future__ import annotations

import pandas as pd
import psycopg
import pytest
from psycopg import sql

from api import migrate
from tests.conftest import FIXTURES

TABLES = {"ledger_1x2", "ledger_ou25"}


def tables(url: str) -> set[str]:
    with psycopg.connect(url) as conn:
        return {r[0] for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'ledger_%'")}


def test_up_down_up(pg_url: str):
    assert migrate.up(pg_url) == migrate.versions()
    assert tables(pg_url) == TABLES
    assert migrate.up(pg_url) == []                     # idempotent
    for v in reversed(migrate.versions()):
        assert migrate.down(pg_url) == v
    assert tables(pg_url) == set()
    assert migrate.down(pg_url) is None
    assert migrate.up(pg_url) == migrate.versions()
    assert tables(pg_url) == TABLES


def _row(strategy: str) -> dict:
    """A settled row from the fixture ledger, as the job writes it."""
    df = pd.read_csv(FIXTURES / f"ledger_{strategy}.csv", parse_dates=["date", "logged_at"])
    target = "result" if strategy == "1x2" else "ou"
    r = df[df[target].notna()].iloc[0]
    return {k: (None if pd.isna(v) else v.to_pydatetime() if isinstance(v, pd.Timestamp) else
                v.item() if hasattr(v, "item") else v) for k, v in r.items()}


def _insert(conn: psycopg.Connection, strategy: str, row: dict) -> None:
    conn.execute(sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(f"ledger_{strategy}"), sql.SQL(", ").join(map(sql.Identifier, row)),
        sql.SQL(", ").join(sql.Placeholder() * len(row))), list(row.values()))


BAD_1X2 = {
    "unknown result": lambda r: {"result": "X"},
    "probabilities don't sum to 1": lambda r: {"p_H": 0.9},
    "odds of 1 or less": lambda r: {"avg_h": 1.0},
    "logged after the match": lambda r: {"logged_at": r["date"] + pd.Timedelta(days=2)},
    "closing odds partly missing": lambda r: {"close_d": None},
    "no model probability": lambda r: {"p_D": None},
    "team plays itself": lambda r: {"away": r["home"]},
}


@pytest.mark.parametrize("change", BAD_1X2.values(), ids=BAD_1X2.keys())
def test_ledger_1x2_rejects(pg_url: str, change):
    migrate.up(pg_url)
    row = _row("1x2")
    with psycopg.connect(pg_url) as conn:
        _insert(conn, "1x2", row)                       # the unchanged row is valid
        conn.commit()
        with pytest.raises(psycopg.errors.IntegrityError):
            _insert(conn, "1x2", {**row, "date": row["date"] + pd.Timedelta(days=1), **change(row)})


@pytest.mark.parametrize("strategy", ["1x2", "ou25"])
def test_ledger_rejects_second_snapshot_of_a_fixture(pg_url: str, strategy: str):
    migrate.up(pg_url)
    row = _row(strategy)
    with psycopg.connect(pg_url) as conn:
        _insert(conn, strategy, row)
        conn.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            _insert(conn, strategy, {**row, "logged_at": row["logged_at"] - pd.Timedelta(hours=1)})


def test_ledger_ou25_rejects_unknown_outcome(pg_url: str):
    migrate.up(pg_url)
    with psycopg.connect(pg_url) as conn, pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, "ou25", {**_row("ou25"), "ou": "H"})
