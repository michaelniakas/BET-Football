"""PostgresLedgers.save: what the paper job's write-back may and may not change."""
from __future__ import annotations

import sys
import threading

import numpy as np
import pandas as pd
import psycopg
import pytest

from api import migrate
from api.ledgers import CsvLedgers, PostgresLedgers
from api.sync import main, to_csv, to_postgres
from model.paper import KEY, STRATEGIES


def same(got: pd.DataFrame, want: pd.DataFrame) -> None:
    """Equal values, dtypes and row order; column order follows the DDL, not the CSV."""
    pd.testing.assert_frame_equal(got[list(want.columns)], want)


@pytest.fixture
def pg(pg_url: str) -> PostgresLedgers:
    migrate.up(pg_url)
    return PostgresLedgers(pg_url)


@pytest.mark.parametrize("strategy", ["1x2", "ou25"])
def test_round_trip_equals_csv(pg: PostgresLedgers, ledger_dir, strategy: str):
    csv = CsvLedgers(ledger_dir).read(strategy)
    assert pg.save(strategy, csv) == (len(csv), 0)
    same(pg.read(strategy), csv)
    assert pg.diff(strategy) == 0
    assert pg.save(strategy, csv) == (0, 0)                  # idempotent
    assert len(pg.read(strategy)) == len(csv)


def test_backfill_from_ledger_tables(pg: PostgresLedgers, pg_url: str, ledger_dir):
    """Ledgers saved before migration 0004: `project` builds the normalized tables, and again is a no-op."""
    csv = CsvLedgers(ledger_dir)
    for s in ("1x2", "ou25"):
        pg.save(s, csv.read(s))
    saved = {s: pg.read(s) for s in ("1x2", "ou25")}
    with psycopg.connect(pg_url) as conn:
        conn.execute("TRUNCATE competitions, teams, team_aliases, fixtures, models, predictions, odds_snapshots,"
                     " market_probs RESTART IDENTITY")
    assert pg.read("1x2").empty
    for _ in range(2):
        for s in ("1x2", "ou25"):
            pg.project(s)
            assert pg.diff(s) == 0
            same(pg.read(s), saved[s])


def test_settle_fills_results_only(pg: PostgresLedgers, ledger_dir):
    full = CsvLedgers(ledger_dir).read("1x2")
    settled = full.result.notna()
    unsettled = full.assign(result=np.nan, close_h=np.nan, close_d=np.nan, close_a=np.nan, hg=np.nan, ag=np.nan)
    unsettled["result"] = unsettled.result.astype("str")
    assert pg.save("1x2", unsettled) == (len(full), 0)

    # The job's rewrite also changes a snapshot price: that must not reach the database.
    rewritten = full.copy()
    rewritten.loc[settled.idxmax(), "avg_h"] = 99.0
    assert pg.save("1x2", rewritten) == (0, settled.sum())
    same(pg.read("1x2"), full)

    # A settled result is final, too.
    flipped = full.copy()
    flipped.loc[settled, "result"] = flipped.loc[settled, "result"].map({"H": "A", "D": "H", "A": "D"})
    flipped.loc[settled, ["hg", "ag"]] = np.nan          # a score must agree with its row's result
    assert pg.save("1x2", flipped) == (0, 0)
    same(pg.read("1x2"), full)
    assert pg.diff("1x2") == 0


def test_score_backfilled_on_settled_rows_only(pg: PostgresLedgers, ledger_dir):
    """Rows settled before the score was kept get it from the next save; nothing else on them changes."""
    full = CsvLedgers(ledger_dir).read("1x2")
    scored = full.hg.notna()
    assert 0 < scored.sum() < full.result.notna().sum()       # the fixture has settled rows without a score
    assert pg.save("1x2", full.assign(hg=np.nan, ag=np.nan)) == (len(full), 0)

    rewritten = full.copy()
    rewritten.loc[scored.idxmax(), ["close_h", "close_d", "close_a"]] = [0.2, 0.3, 0.5]
    assert pg.save("1x2", rewritten) == (0, scored.sum())
    same(pg.read("1x2"), full)

    # A score is final once stored.
    other = full.copy()
    other.loc[scored, ["hg", "ag"]] = other.loc[scored, ["hg", "ag"]] + 1
    assert pg.save("1x2", other) == (0, 0)
    same(pg.read("1x2"), full)
    assert pg.diff("1x2") == 0


def test_score_never_rewrites_a_settled_result(pg: PostgresLedgers, ledger_dir):
    """A settled row without a score, sent with another result and a score matching that one: the stored
    result stays and that row gets no score; the rest of the save goes through."""
    full = CsvLedgers(ledger_dir).read("1x2")
    pg.save("1x2", full.assign(hg=np.nan, ag=np.nan))
    scored = full.hg.notna()
    i = scored.idxmax()
    other = full.copy()
    other.loc[i, ["result", "hg", "ag"]] = ["A", 0, 1] if full.loc[i, "result"] != "A" else ["H", 1, 0]
    assert pg.save("1x2", other) == (0, scored.sum() - 1)
    same(pg.read("1x2"), full.assign(hg=full.hg.mask(full.index == i), ag=full.ag.mask(full.index == i)))


def test_score_is_the_fixtures(pg: PostgresLedgers, ledger_dir):
    """A row settled without a score shows its fixture's score once the other market's ledger has it."""
    csv = CsvLedgers(ledger_dir)
    ou = csv.read("ou25")
    scored_1x2 = csv.read("1x2").loc[lambda d: d.hg.notna()].set_index(KEY).index
    i = (ou.hg.notna() & ou.set_index(KEY).index.isin(scored_1x2)).idxmax()
    pg.save("ou25", ou.assign(hg=ou.hg.mask(ou.index == i), ag=ou.ag.mask(ou.index == i)))
    assert pd.isna(pg.read("ou25").loc[i, "hg"])
    pg.save("1x2", csv.read("1x2"))
    same(pg.read("ou25"), ou)
    assert pg.diff("ou25") == 0                                 # a score the table lacks is not a difference


def test_features_change_keeps_one_prediction(pg: PostgresLedgers, ledger_dir, monkeypatch):
    """A new model version in `model.paper` doesn't log the existing ledger a second time."""
    ou = CsvLedgers(ledger_dir).read("ou25")
    pg.save("ou25", ou)
    monkeypatch.setitem(STRATEGIES["ou25"], "features", "stats")
    pg.save("ou25", ou)
    same(pg.read("ou25"), ou)
    assert pg.diff("ou25") == 0


def test_corrected_score_settles_the_other_market(pg: PostgresLedgers, ledger_dir):
    """ou25 settles a fixture after football-data corrected the score 1x2 was settled with: the save goes
    through, the outcome is stored, the fixture takes the corrected score and `diff` shows the 1x2 row."""
    csv = CsvLedgers(ledger_dir)
    x1, ou = csv.read("1x2"), csv.read("ou25")
    k1 = x1.set_index(KEY).index
    i = (ou.hg.notna() & ou.set_index(KEY).index.isin(k1[x1.hg.notna().values])).idxmax()
    j = k1.get_loc(tuple(ou.loc[i, KEY]))
    hg, ag, result = x1.loc[j, ["hg", "ag", "result"]]
    unsettled = ou.copy()
    unsettled.loc[i, ["ou", "close_o", "close_u", "hg", "ag"]] = np.nan
    unsettled["ou"] = unsettled.ou.astype("str")
    pg.save("ou25", unsettled)
    pg.save("1x2", x1)
    # Same 1x2 result, the other side of 2.5.
    nh, na = (hg + 2, ag + 2) if hg + ag <= 2 else {"H": (1, 0), "D": (0, 0), "A": (0, 1)}[result]
    corrected = ou.copy()
    corrected.loc[i, ["ou", "hg", "ag"]] = ["O" if nh + na > 2 else "U", nh, na]
    assert pg.save("ou25", corrected) == (0, 1)
    got_ou, got_1x2 = pg.read("ou25"), pg.read("1x2")
    assert got_ou.loc[i, "ou"] == corrected.loc[i, "ou"] and got_1x2.loc[j, "result"] == result
    assert list(got_ou.loc[i, ["hg", "ag"]]) == list(got_1x2.loc[j, ["hg", "ag"]]) == [nh, na]
    assert (pg.diff("ou25"), pg.diff("1x2")) == (0, 1)


def test_overlapping_saves_wait_for_each_other(pg: PostgresLedgers, ledger_dir, monkeypatch):
    """A deploy's two workers both saving a new team: the second waits for the first instead of failing."""
    x1 = CsvLedgers(ledger_dir).read("1x2")
    a, b = x1.iloc[[0]].assign(home="New Team"), x1.iloc[[1]].assign(home="New Team")
    inside, release, errors = threading.Event(), threading.Event(), []
    project = PostgresLedgers._project

    def first_one_pauses(self, conn, strategy):
        project(self, conn, strategy)
        if not inside.is_set():
            inside.set()
            release.wait(10)
    monkeypatch.setattr(PostgresLedgers, "_project", first_one_pauses)

    def save(df):
        try:
            pg.save("1x2", df)
        except Exception as e:                                  # noqa: BLE001 - the test reports it
            errors.append(e)
    first = threading.Thread(target=save, args=(a,))
    first.start()
    assert inside.wait(10)
    second = threading.Thread(target=save, args=(b,))
    second.start()
    second.join(1)
    release.set()
    first.join(10)
    second.join(10)
    assert errors == []
    assert len(pg.read("1x2")) == 2 and pg.diff("1x2") == 0


def test_sync_project_and_check_exit_codes(pg: PostgresLedgers, pg_url: str, ledger_dir, monkeypatch):
    to_postgres(pg, ledger_dir)
    monkeypatch.setenv("DATABASE_URL", pg_url)
    run = lambda cmd: (monkeypatch.setattr(sys, "argv", ["api.sync", cmd]), main())   # noqa: E731
    run("check")
    with psycopg.connect(pg_url) as conn:
        conn.execute("DELETE FROM market_probs WHERE kind = 'closing'")
    with pytest.raises(SystemExit) as failed:
        run("check")
    assert failed.value.code == 1
    run("project")                                              # the backfill puts them back
    run("check")


def test_score_contradicting_result_refused(pg: PostgresLedgers, ledger_dir):
    full = CsvLedgers(ledger_dir).read("ou25")
    pg.save("ou25", full.assign(hg=np.nan, ag=np.nan))
    i = full.hg.notna().idxmax()
    bad = full.copy()
    bad.loc[i, ["hg", "ag"]] = [0, 0] if bad.loc[i, "ou"] == "O" else [2, 1]
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.save("ou25", bad)
    assert pg.read("ou25").hg.isna().all()                     # the whole save rolled back


def test_first_log_without_result_columns(pg: PostgresLedgers, ledger_dir):
    """`model.paper log` on an empty ledger writes no result/close columns until the first settle."""
    snap = CsvLedgers(ledger_dir).read("ou25").drop(columns=["ou", "close_o", "close_u", "hg", "ag"])
    assert pg.save("ou25", snap) == (len(snap), 0)
    assert pg.read("ou25").ou.isna().all()


def test_unknown_column_refused_and_nothing_written(pg: PostgresLedgers, ledger_dir):
    df = CsvLedgers(ledger_dir).read("1x2").assign(p_new=0.5)
    with pytest.raises(ValueError, match="p_new"):
        pg.save("1x2", df)
    assert pg.read("1x2").empty


def test_read_empty_ledger(pg: PostgresLedgers):
    assert pg.read("1x2").empty


def test_export_import_round_trip(pg: PostgresLedgers, ledger_dir, tmp_path):
    assert to_postgres(pg, ledger_dir) == {s: (len(CsvLedgers(ledger_dir).read(s)), 0) for s in ("ou25", "1x2")}
    assert to_csv(pg, tmp_path / "out") == {s: len(CsvLedgers(ledger_dir).read(s)) for s in ("ou25", "1x2")}
    exported = CsvLedgers(tmp_path / "out")
    for s in ("ou25", "1x2"):
        same(exported.read(s), pg.read(s))
    assert to_postgres(pg, tmp_path / "out") == {"ou25": (0, 0), "1x2": (0, 0)}    # re-import is a no-op


def test_export_of_empty_ledger_removes_stale_file(pg: PostgresLedgers, tmp_path):
    (tmp_path / "ledger_1x2.csv").write_text("stale")
    assert to_csv(pg, tmp_path) == {"ou25": 0, "1x2": 0}
    assert not (tmp_path / "ledger_1x2.csv").exists()
