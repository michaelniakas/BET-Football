"""PostgresLedgers.save: what the paper job's write-back may and may not change."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from api import migrate
from api.ledgers import CsvLedgers, PostgresLedgers
from api.sync import to_csv, to_postgres


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
    assert pg.save(strategy, csv) == (0, 0)                  # idempotent
    assert len(pg.read(strategy)) == len(csv)


def test_settle_fills_results_only(pg: PostgresLedgers, ledger_dir):
    full = CsvLedgers(ledger_dir).read("1x2")
    settled = full.result.notna()
    unsettled = full.assign(result=np.nan, close_h=np.nan, close_d=np.nan, close_a=np.nan)
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
    assert pg.save("1x2", flipped) == (0, 0)
    same(pg.read("1x2"), full)


def test_first_log_without_result_columns(pg: PostgresLedgers, ledger_dir):
    """`model.paper log` on an empty ledger writes no result/close columns until the first settle."""
    snap = CsvLedgers(ledger_dir).read("ou25").drop(columns=["ou", "close_o", "close_u"])
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
