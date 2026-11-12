"""The paper-trading job on Toft, in place of .github/workflows/paper-trading.yml, and the ingest jobs.

    python -m api.worker         # run every slot of every job, forever (the worker's start command)
    python -m api.worker once    # one run now, outside the schedule

`model/` runs unchanged, as it did on a GitHub runner: in a writable copy under WORKER_DIR (the worker's
volume, which also keeps the downloaded seasons and a CSV backup of the ledgers), with the ledgers
exported from Postgres before the run and written back after it. Connection string from DATABASE_URL.

The ingest jobs (docs/phase2-plan.md) run `python -m ingest <job>` at their own daily slots, never on a paper
slot: the worker before this one looks a paper slot up without its job.
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
ROOT = MODEL.parent
DAILY = range(7)
# Ingest job -> its UTC times of day, in run order: results and fixtures before the prices that need them.
INGEST = {
    "football-data": (timedelta(hours=5),),
    "martj42": (timedelta(hours=6),),
    "espn": (timedelta(hours=6),),
    "odds": (timedelta(hours=6, minutes=30), timedelta(hours=11), timedelta(hours=15),
             timedelta(hours=18, minutes=30)),
}
INGEST_TIMEOUT = 15 * 60            # seconds: an ESPN run takes ~4 minutes; football-data stops at 10 (FD_BUDGET)


def _slots(now: datetime, days, times, span: range):
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return [midnight + timedelta(days=d) + t for d in span for t in times
            if (midnight + timedelta(days=d)).weekday() in days]


def due_slot(now: datetime, days=DAYS, times=(timedelta(hours=HOUR),)) -> datetime:
    """The latest slot at or before `now` (UTC)."""
    return max(s for s in _slots(now, days, times, range(-7, 1)) if s <= now)


def next_slot(now: datetime, days=DAYS, times=(timedelta(hours=HOUR),)) -> datetime:
    """The first slot after `now` (UTC)."""
    return min(s for s in _slots(now, days, times, range(0, 8)) if s > now)


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
        if conn.execute("SELECT 1 FROM paper_runs WHERE job = 'paper' AND slot = %s", (slot,)).fetchone():
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


def ingest_tick(url: str, job: str, now: datetime, cmd: list[str] | None = None) -> None:
    """Run the ingest job's due slot unless it already ran.

    Exit 1 (names to confirm, a disagreeing score, or a failed source) and a timeout are logged and not run
    again in this slot: every run re-reads a window of days, so the next slot catches up what this one missed.
    A job is one transaction per import, so a killed one leaves only whole imports behind.
    """
    slot = due_slot(now, DAILY, INGEST[job])
    with psycopg.connect(url) as conn:
        if conn.execute("SELECT 1 FROM paper_runs WHERE job = %s AND slot = %s", (job, slot)).fetchone():
            return
    log.info("%s slot %s: running", job, slot.isoformat())
    try:
        code = subprocess.run(cmd or [sys.executable, "-m", "ingest", job], cwd=ROOT, timeout=INGEST_TIMEOUT,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}).returncode
        outcome = f"exited {code}"
    except subprocess.TimeoutExpired:
        code, outcome = None, f"killed after {INGEST_TIMEOUT} s"
    with psycopg.connect(url) as conn:
        conn.execute("INSERT INTO paper_runs (job, slot) VALUES (%s, %s) ON CONFLICT DO NOTHING", (job, slot))
    if code != 0:
        log.error("%s slot %s: %s (see its output above; `python -m ingest unmapped`)", job,
                  slot.isoformat(), outcome)


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
        wake = next_slot(now)
        try:                        # first: a hanging source must not delay the paper run
            tick(url, Path(workdir), now)
        except Exception:           # database, disk or a hung download must not end the schedule
            log.exception("the run did not finish, trying again")
            wake = min(wake, now + RETRY)
        for job, times in INGEST.items():
            wake = min(wake, next_slot(now, DAILY, times))
            try:
                ingest_tick(url, job, now)
            except Exception:       # one job's failure must not stop the others or the schedule
                log.exception("%s did not finish, trying again", job)
                wake = min(wake, now + RETRY)
        log.info("next look at %s", wake.isoformat(timespec="seconds"))
        time.sleep(max(0.0, (wake - datetime.now(UTC)).total_seconds()))


if __name__ == "__main__":
    main()
