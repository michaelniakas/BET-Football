"""Migrations go up, down and up again; the ledger tables reject what the paper job must never write."""
from __future__ import annotations

import pandas as pd
import psycopg
import pytest
from psycopg import sql

from api import migrate
from tests.conftest import FIXTURES

TABLES = {"ledger_1x2", "ledger_ou25", "paper_runs", "competitions", "teams", "team_aliases", "fixtures", "models",
          "predictions", "odds_snapshots", "market_probs", "v_ledger_1x2", "v_ledger_ou25", "unmapped_names", "fixture_refs"}


def tables(url: str) -> set[str]:
    """Tables and views, without the runner's own."""
    with psycopg.connect(url) as conn:
        return {r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            " AND table_name <> 'schema_migrations'")}


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
    "score contradicts result": lambda r: {"hg": r["ag"], "ag": r["hg"]} if r["hg"] != r["ag"] else {"hg": r["hg"] + 1},
    "half a score": lambda r: {"ag": None},
    "negative goals": lambda r: {"hg": -1, "ag": -1},
    "score without result": lambda r: {"result": None, "close_h": None, "close_d": None, "close_a": None},
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


def test_ledger_ou25_rejects_score_contradicting_ou(pg_url: str):
    migrate.up(pg_url)
    row = _row("ou25")
    with psycopg.connect(pg_url) as conn, pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, "ou25", {**row, "ou": "U" if row["ou"] == "O" else "O"})


# --------------------------------------------------------------------------- normalized tables (0004)

@pytest.fixture
def fixture_row(pg_url: str):
    """A connection holding one valid fixture, prediction, snapshot and market probability (1x2 and O/U 2.5)."""
    migrate.up(pg_url)
    with psycopg.connect(pg_url) as conn:
        conn.execute("""
            INSERT INTO competitions (code) VALUES ('E0');
            INSERT INTO teams (name) VALUES ('Arsenal'), ('Chelsea');
            INSERT INTO team_aliases VALUES ('football-data', 'Arsenal', 1), ('football-data', 'Chelsea', 2);
            INSERT INTO fixtures (competition_id, season, date, home_id, away_id, hg, ag, result, ou25)
                VALUES (1, 2026, '2026-11-08', 1, 2, 2, 1, 'H', 'O');
            INSERT INTO models (name, version) VALUES ('michalis', 'stats+market');
            INSERT INTO predictions (model_id, fixture_id, fixture_date, market, line, predicted_at, p)
                VALUES (1, 1, '2026-11-08', '1x2', NULL, '2026-11-06 14:00Z', '{0.5,0.3,0.2}'),
                       (1, 1, '2026-11-08', 'ou', 2.5, '2026-11-06 14:00Z', '{0.6,0.4}');
            INSERT INTO odds_snapshots VALUES (1, '1x2', NULL, 'football-data', 'avg', '2026-11-06 14:00Z', '{2,3.5,4}');
            INSERT INTO market_probs VALUES (1, '1x2', NULL, 'closing', 'model.paper', NULL, '{0.5,0.3,0.2}');
        """)
        conn.commit()
        yield conn


BAD_NORMALIZED = {
    "probabilities don't sum to 1": "INSERT INTO predictions (model_id, fixture_id, fixture_date, market, line, "
        "predicted_at, p) VALUES (1, 1, '2026-11-08', 'ou', 3.5, '2026-11-06Z', '{0.6,0.5}')",
    "a probability missing": "INSERT INTO predictions (model_id, fixture_id, fixture_date, market, line, "
        "predicted_at, p) VALUES (1, 1, '2026-11-08', 'ou', 3.5, '2026-11-06Z', '{1,NULL}')",
    "three outcomes for O/U": "INSERT INTO predictions (model_id, fixture_id, fixture_date, market, line, "
        "predicted_at, p) VALUES (1, 1, '2026-11-08', 'ou', 3.5, '2026-11-06Z', '{0.5,0.3,0.2}')",
    "O/U without a line": "INSERT INTO market_probs VALUES (1, 'ou', NULL, 'closing', 'x', NULL, '{0.6,0.4}')",
    "1x2 with a line": "INSERT INTO market_probs VALUES (1, '1x2', 1, 'closing', 'x', NULL, '{0.5,0.3,0.2}')",
    "unknown market": "INSERT INTO market_probs VALUES (1, 'btts', NULL, 'closing', 'x', NULL, '{0.6,0.4}')",
    "predicted after the match day": "INSERT INTO predictions (model_id, fixture_id, fixture_date, market, line, "
        "predicted_at, p) VALUES (1, 1, '2026-11-08', 'ou', 3.5, '2026-11-09 00:30Z', '{0.6,0.4}')",
    "fixture date not the fixture's": "INSERT INTO predictions (model_id, fixture_id, fixture_date, market, line, "
        "predicted_at, p) VALUES (1, 1, '2026-11-09', 'ou', 3.5, '2026-11-06Z', '{0.6,0.4}')",
    "second prediction of a market": "INSERT INTO predictions (model_id, fixture_id, fixture_date, market, line, "
        "predicted_at, p) VALUES (1, 1, '2026-11-08', '1x2', NULL, '2026-11-07Z', '{0.4,0.3,0.3}')",
    "match moved before its prediction": "UPDATE fixtures SET date = '2026-11-05'",
    "odds of 1": "INSERT INTO odds_snapshots VALUES (1, '1x2', NULL, 'football-data', 'max', '2026-11-06Z', '{1,3.5,4}')",
    "second closing line": "INSERT INTO market_probs VALUES (1, '1x2', NULL, 'closing', 'model.paper', NULL, "
        "'{0.4,0.3,0.3}')",
    "pre-match without a time": "INSERT INTO market_probs VALUES (1, '1x2', NULL, 'pre_match', 'x', NULL, "
        "'{0.4,0.3,0.3}')",
    "score contradicts result": "UPDATE fixtures SET hg = 1, ag = 1, ou25 = NULL",
    "score contradicts O/U": "UPDATE fixtures SET hg = 1, ag = 0",
    "half a score": "UPDATE fixtures SET ag = NULL",
    "team plays itself": "INSERT INTO fixtures (competition_id, season, date, home_id, away_id) "
        "VALUES (1, 2026, '2026-11-09', 1, 1)",
    "fixture twice": "INSERT INTO fixtures (competition_id, season, date, home_id, away_id) "
        "VALUES (1, 2026, '2026-11-08', 1, 2)",
    "one name, two teams": "INSERT INTO team_aliases VALUES ('football-data', 'Arsenal', 2)",
    "settled result changed": "UPDATE fixtures SET result = 'A', hg = NULL, ag = NULL",
    "settled O/U changed": "UPDATE fixtures SET ou25 = 'U', hg = NULL, ag = NULL",
    "score changed": "UPDATE fixtures SET hg = 3, ag = 1",
    "prediction rewritten": "UPDATE predictions SET p = '{0.4,0.3,0.3}' WHERE market = '1x2'",
    "odds rewritten": "UPDATE odds_snapshots SET prices = '{2,3.4,4}'",
    "market probabilities rewritten": "UPDATE market_probs SET p = '{0.4,0.3,0.3}'",
    # 0005
    "kick-off two days off the date": "UPDATE fixtures SET kickoff = '2026-11-10 15:00Z'",
    "unknown way of deciding": "UPDATE fixtures SET decided = 'golden_goal'",
    "unknown team kind": "UPDATE teams SET kind = 'women' WHERE id = 1",
    "unknown competition kind": "UPDATE competitions SET kind = 'cup'",
    "paper slot twice": "INSERT INTO paper_runs (slot) VALUES ('2026-11-06 07:00Z'), ('2026-11-06 07:00Z')",
    "one ESPN event, two fixtures": "INSERT INTO fixtures (competition_id, season, date, home_id, away_id) "
        "VALUES (1, 2026, '2026-11-20', 2, 1); INSERT INTO fixture_refs VALUES ('espn', '1', 1), ('espn', '1', 2)",
    "two ESPN events, one fixture": "INSERT INTO fixture_refs VALUES ('espn', '1', 1), ('espn', '2', 1)",
    "queued name never seen": "INSERT INTO unmapped_names (source, name, kind, competition, seen) "
        "VALUES ('espn', 'Arsenal FC', 'club', 'E0', 0)",
}

GOOD_NORMALIZED = {
    "score withdrawn": "UPDATE fixtures SET hg = NULL, ag = NULL",
    "match moved, predictions follow": "UPDATE fixtures SET date = '2026-11-09'",
    # 0005
    "kick-off the evening before (UTC)": "UPDATE fixtures SET kickoff = '2026-11-07 23:30Z', decided = 'regular'",
    "another job's slot": "INSERT INTO paper_runs (job, slot) VALUES ('espn', '2026-11-06 06:00Z')",
}


def test_old_worker_insert_still_works(fixture_row: psycopg.Connection):
    """The 0004 worker keeps running beside 0005 during a deploy: its insert and lookup have no `job`."""
    for _ in range(2):
        fixture_row.execute("INSERT INTO paper_runs (slot) VALUES ('2026-11-06 07:00Z') ON CONFLICT DO NOTHING")
    assert fixture_row.execute("SELECT job FROM paper_runs WHERE slot = '2026-11-06 07:00Z'").fetchall() == [("paper",)]


def test_alias_clears_the_queue(fixture_row: psycopg.Connection):
    fixture_row.execute("INSERT INTO unmapped_names (source, name, kind, competition) VALUES "
                        "('espn', 'Arsenal FC', 'club', 'E0'), ('espn', 'Chelsea FC', 'club', 'E0')")
    fixture_row.execute("INSERT INTO team_aliases VALUES ('espn', 'Arsenal FC', 1)")
    assert fixture_row.execute("SELECT name FROM unmapped_names").fetchall() == [("Chelsea FC",)]


def test_down_removes_international_data_only(fixture_row: psycopg.Connection, pg_url: str):
    fixture_row.execute("""
        INSERT INTO competitions (code, name, kind) VALUES ('Friendly', 'Friendly', 'international');
        INSERT INTO teams (name, kind) VALUES ('Greece', 'national'), ('Cyprus', 'national');
        INSERT INTO team_aliases VALUES ('martj42', 'Greece', 3), ('martj42', 'Cyprus', 4);
        INSERT INTO fixtures (competition_id, season, date, home_id, away_id, hg, ag)
            VALUES (2, 2026, '2026-11-14', 3, 4, 1, 0);
        INSERT INTO odds_snapshots VALUES (2, '1x2', NULL, 'the-odds-api', 'avg', '2026-11-13Z', '{1.5,4,6}');
        INSERT INTO paper_runs (job, slot) VALUES ('espn', '2026-11-12 06:00Z'), ('paper', '2026-11-10 07:00Z');
    """)
    fixture_row.commit()
    assert migrate.down(pg_url) == "0005_ingestion"
    with psycopg.connect(pg_url) as conn:
        counts = conn.execute("SELECT (SELECT count(*) FROM fixtures), (SELECT count(*) FROM teams),"
                              " (SELECT count(*) FROM team_aliases), (SELECT count(*) FROM odds_snapshots),"
                              " (SELECT count(*) FROM competitions), (SELECT array_agg(slot) FROM paper_runs)").fetchone()
    assert counts[:5] == (1, 2, 2, 1, 1)
    assert len(counts[5]) == 1


@pytest.mark.parametrize("stmt", BAD_NORMALIZED.values(), ids=BAD_NORMALIZED.keys())
def test_normalized_rejects(fixture_row: psycopg.Connection, stmt: str):
    with pytest.raises(psycopg.errors.IntegrityError):
        fixture_row.execute(stmt)


@pytest.mark.parametrize("stmt", GOOD_NORMALIZED.values(), ids=GOOD_NORMALIZED.keys())
def test_normalized_allows(fixture_row: psycopg.Connection, stmt: str):
    assert fixture_row.execute(stmt).rowcount == 1
