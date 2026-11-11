"""The Toft worker: which slot is due, one run around the unchanged job, and no slot run twice."""
from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import psycopg
import pytest
from fastapi.testclient import TestClient

from api import migrate, worker
from api.ledgers import CsvLedgers, PostgresLedgers
from api.main import from_env


def utc(day: int, hour: int, minute: int = 0) -> datetime:
    # 2026-02-10 is a Tuesday. In the past: paper_runs refuses a slot that finished before it began.
    return datetime(2026, 2, day, hour, minute, tzinfo=UTC)


@pytest.mark.parametrize("now, due, nxt", [
    (utc(9, 0), utc(6, 7), utc(10, 7)),             # Monday: last Friday's slot, next is Tuesday
    (utc(10, 6, 59), utc(6, 7), utc(10, 7)),        # Tuesday before 07:00
    (utc(10, 7), utc(10, 7), utc(13, 7)),           # exactly on the slot
    (utc(11, 12), utc(10, 7), utc(13, 7)),          # Wednesday
    (utc(13, 23, 59), utc(13, 7), utc(17, 7)),      # Friday night: next is Tuesday
    (utc(15, 7), utc(13, 7), utc(17, 7)),           # Sunday
])
def test_slots(now: datetime, due: datetime, nxt: datetime):
    assert worker.due_slot(now) == due
    assert worker.next_slot(now) == nxt


@pytest.fixture
def pg(pg_url: str) -> PostgresLedgers:
    migrate.up(pg_url)
    return PostgresLedgers(pg_url)


def fake_job(ledger_dir: Path, exit_code: int = 0) -> list[str]:
    """Stands in for `python -m model.paper weekly`: notes how many 1X2 rows the export gave it, then leaves
    the full fixture ledgers in model/paper, as the real job leaves its rewritten CSVs."""
    script = (
        "import pathlib, shutil, sys\n"
        "paper = pathlib.Path('model/paper')\n"
        "old = paper / 'ledger_1x2.csv'            # absent while the database's ledger is empty\n"
        "pathlib.Path('seen').write_text(str(len(old.read_text().splitlines()) - 1 if old.exists() else 0))\n"
        f"for f in pathlib.Path({str(ledger_dir)!r}).glob('ledger_*.csv'):\n"
        "    shutil.copy(f, paper / f.name)\n"
        f"sys.exit({exit_code})\n"
    )
    return [sys.executable, "-c", script]


def seed_unsettled(pg: PostgresLedgers, ledger_dir: Path, keep: int) -> None:
    """The first `keep` 1X2 fixtures, none settled yet."""
    df = CsvLedgers(ledger_dir).read("1x2").head(keep)
    df = df.assign(result=np.nan, close_h=np.nan, close_d=np.nan, close_a=np.nan, hg=np.nan, ag=np.nan)
    df["result"] = df.result.astype("str")
    pg.save("1x2", df)


@pytest.mark.parametrize("exit_code", [0, 1])
def test_run_once_exports_runs_and_writes_back(pg: PostgresLedgers, ledger_dir, tmp_path, exit_code: int):
    full = {s: CsvLedgers(ledger_dir).read(s) for s in ("1x2", "ou25")}
    seed_unsettled(pg, ledger_dir, keep=10)

    assert worker.run_once(pg, tmp_path, fake_job(ledger_dir, exit_code)) is (exit_code == 0)

    assert (tmp_path / "seen").read_text() == "10"                  # the job started from the database's rows
    assert (tmp_path / "model" / "paper.py").exists()               # ... in a copy of the image's model/
    backup = CsvLedgers(tmp_path / "backup")
    for s, want in full.items():                                    # saved even when the job failed
        got = pg.read(s)
        assert len(got) == len(want)
        assert got[want.columns[-1]].notna().sum() == want[want.columns[-1]].notna().sum()
        assert len(backup.read(s)) == len(want)


def runs(url: str) -> list[datetime]:
    with psycopg.connect(url) as conn:
        return [r[0] for r in conn.execute("SELECT slot FROM paper_runs ORDER BY slot")]


def test_tick_runs_a_slot_once(pg: PostgresLedgers, pg_url: str, ledger_dir, tmp_path):
    now = utc(11, 12)
    worker.tick(pg_url, tmp_path, now, fake_job(ledger_dir))
    assert runs(pg_url) == [utc(10, 7)]
    assert len(pg.read("1x2")) == len(CsvLedgers(ledger_dir).read("1x2"))

    # A restart inside the same slot: the job must not start again.
    (tmp_path / "seen").unlink()
    worker.tick(pg_url, tmp_path, now + timedelta(hours=5), fake_job(ledger_dir))
    assert not (tmp_path / "seen").exists()

    # The next slot runs, also when the worker wakes late.
    worker.tick(pg_url, tmp_path, utc(14, 3), fake_job(ledger_dir))
    assert runs(pg_url) == [utc(10, 7), utc(13, 7)]


def test_a_job_that_exits_non_zero_is_saved_and_not_run_again(pg: PostgresLedgers, pg_url: str, ledger_dir, tmp_path):
    now = utc(11, 12)
    worker.tick(pg_url, tmp_path, now, fake_job(ledger_dir, exit_code=1))
    assert runs(pg_url) == [utc(10, 7)]
    assert len(pg.read("1x2")) == len(CsvLedgers(ledger_dir).read("1x2"))
    (tmp_path / "seen").unlink()
    worker.tick(pg_url, tmp_path, now + worker.RETRY, fake_job(ledger_dir))
    assert not (tmp_path / "seen").exists()


def test_a_job_killed_by_the_timeout_writes_nothing_back_and_stays_due(pg: PostgresLedgers, pg_url: str, ledger_dir,
                                                                       tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "JOB_TIMEOUT", 1)
    hangs = fake_job(ledger_dir)
    hangs[-1] = hangs[-1].replace("sys.exit(0)", "import time; time.sleep(60)")
    now = utc(11, 12)
    with pytest.raises(subprocess.TimeoutExpired):
        worker.tick(pg_url, tmp_path, now, hangs)
    assert (tmp_path / "seen").exists()             # it had left its CSVs in model/paper before it hung
    assert pg.read("1x2").empty
    assert runs(pg_url) == []
    worker.tick(pg_url, tmp_path, now + worker.RETRY, fake_job(ledger_dir))
    assert runs(pg_url) == [utc(10, 7)]


def test_app_reads_postgres_when_database_url_is_set(pg_ledger_url: str, ledger_dir, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg_ledger_url)
    monkeypatch.setattr("api.main.GithubLedgers", lambda *a, **k: pytest.fail("read the ledgers from GitHub"))
    catalog = TestClient(from_env()).get("/api/v1/catalog").json()
    assert {s["id"]: s["ledger"]["matches"] for s in catalog["strategies"]} == {
        s: len(CsvLedgers(ledger_dir).read(s)) for s in ("ou25", "1x2")}
