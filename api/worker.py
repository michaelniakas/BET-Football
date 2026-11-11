"""The paper-trading job on Toft, in place of .github/workflows/paper-trading.yml.

    python -m api.worker         # run every Tue + Fri 07:00 UTC slot, forever (the worker's start command)
    python -m api.worker once    # one run now, outside the schedule

`model/` runs unchanged, as it did on a GitHub runner: in a writable copy under WORKER_DIR (the worker's
volume, which also keeps the downloaded seasons and a CSV backup of the ledgers), with the ledgers
exported from Postgres before the run and written back after it. Connection string from DATABASE_URL.
"""
from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg

from . import sync
from .ledgers import PostgresLedgers

log = logging.getLogger(__name__)
MODEL = Path(__file__).resolve().parent.parent / "model"
DAYS = (1, 4)                       # Tue, Fri (Monday = 0): the workflow's cron "0 7 * * 2,5"
HOUR = 7                            # UTC
JOB = [sys.executable, "-m", "model.paper", "weekly"]
JOB_TIMEOUT = 30 * 60               # seconds: the workflow's timeout-minutes
RETRY = timedelta(hours=1)          # after a run that could not finish


def due_slot(now: datetime) -> datetime:
    """The latest slot at or before `now` (UTC)."""
    slot = now.replace(hour=HOUR, minute=0, second=0, microsecond=0)
    if slot > now:
        slot -= timedelta(days=1)
    while slot.weekday() not in DAYS:
        slot -= timedelta(days=1)
    return slot


def next_slot(now: datetime) -> datetime:
    """The first slot after `now` (UTC)."""
    slot = due_slot(now) + timedelta(days=1)
    while slot.weekday() not in DAYS:
        slot += timedelta(days=1)
    return slot


def run_once(pg: PostgresLedgers, workdir: Path, job: list[str] = JOB) -> bool:
    """One run of the job; False when it ran to the end and exited non-zero (one strategy crashed).

    What the other strategy settled or logged is saved either way, as the workflow committed the ledgers
    after a failed run. A job killed by the timeout raises instead and nothing is written back: it may
    have died halfway through writing a CSV.
    """
    # The image's code over the previous copy; the downloaded seasons under model/data stay.
    shutil.copytree(MODEL, workdir / "model", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "data", "output"))
    paper = workdir / "model" / "paper"
    sync.to_csv(pg, paper)
    # No bytecode on the volume: copytree keeps the image's mtimes, so a stale .pyc could outlive a deploy.
    ok = subprocess.run(job, cwd=workdir, timeout=JOB_TIMEOUT,
                        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}).returncode == 0
    for strategy, (added, settled) in sync.to_postgres(pg, paper).items():
        log.info("[%s] added %d, settled %d", strategy, added, settled)
    sync.to_csv(pg, workdir / "backup")
    return ok


def tick(url: str, workdir: Path, now: datetime, job: list[str] = JOB) -> None:
    """Run the due slot unless it already ran. Raises when the run could not finish: the slot stays due.

    A job that finished with a non-zero exit is not run again, as a failed workflow run was not: an
    hourly repeat would log mid-week fixtures at odd hours, and their first snapshot is kept for good.
    """
    slot = due_slot(now)
    with psycopg.connect(url) as conn:
        if conn.execute("SELECT 1 FROM paper_runs WHERE slot = %s", (slot,)).fetchone():
            return
    log.info("slot %s: running", slot.isoformat())
    ok = run_once(PostgresLedgers(url), workdir, job)
    with psycopg.connect(url) as conn:
        # Two workers overlap during a deploy: both may run the slot (harmless), one row.
        conn.execute("INSERT INTO paper_runs (slot) VALUES (%s) ON CONFLICT DO NOTHING", (slot,))
    if ok:
        log.info("slot %s: done", slot.isoformat())
    else:
        log.error("slot %s: the job exited non-zero (see its output above); not run again", slot.isoformat())


def main() -> None:
    if sys.argv[1:] not in ([], ["once"]):
        raise SystemExit(__doc__)
    url, workdir = os.environ.get("DATABASE_URL"), os.environ.get("WORKER_DIR")
    if not url or not workdir:
        raise SystemExit("DATABASE_URL and WORKER_DIR must be set")
    logging.basicConfig(level=logging.INFO)
    # PID 1 gets no default SIGTERM action: without this a redeploy waits out the stop timeout.
    signal.signal(signal.SIGTERM, lambda signum, _: sys.exit(128 + signum))
    if sys.argv[1:] == ["once"]:
        sys.exit(0 if run_once(PostgresLedgers(url), Path(workdir)) else 1)
    while True:
        now = datetime.now(UTC)
        try:
            tick(url, Path(workdir), now)
            wake = next_slot(now)
        except Exception:           # database, disk or a hung download must not end the schedule
            log.exception("the run did not finish, trying again")
            wake = min(now + RETRY, next_slot(now))
        log.info("next look at %s", wake.isoformat(timespec="seconds"))
        time.sleep(max(0.0, (wake - datetime.now(UTC)).total_seconds()))


if __name__ == "__main__":
    main()
