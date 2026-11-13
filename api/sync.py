"""Copy the paper ledgers between CSV files (as `python -m model.paper` reads and writes them) and Postgres.

    python -m api.sync import DIR    # CSV -> Postgres: adds new fixtures, fills results; never overwrites
    python -m api.sync export DIR    # Postgres -> CSV: the job's working copy, the backup, the rollback
    python -m api.sync project       # ledger tables -> normalized tables (the phase 1 backfill; idempotent), then check
    python -m api.sync check         # rows differing between each ledger table and its view; exit 1 if any
                                     # (rows logged after kick-off are counted apart, not as differences)

Connection string from DATABASE_URL.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from model.paper import STRATEGIES

from .ledgers import CsvLedgers, PostgresLedgers


def to_postgres(pg: PostgresLedgers, directory: Path) -> dict[str, tuple[int, int]]:
    """(rows added, rows settled) per strategy."""
    csv = CsvLedgers(directory)
    return {s: pg.save(s, df) for s in STRATEGIES if not (df := csv.read(s)).empty}


def to_csv(pg: PostgresLedgers, directory: Path) -> dict[str, int]:
    """Rows written per strategy. An empty ledger leaves no file, as before the job's first run.

    The whole ledger, rows logged after kick-off included: this is the backup, and the job's working copy.
    """
    directory.mkdir(parents=True, exist_ok=True)
    out = {}
    for s in STRATEGIES:
        path = directory / f"ledger_{s}.csv"
        df = pg.read(s, with_late=True)
        if df.empty:
            path.unlink(missing_ok=True)
        else:
            tmp = path.with_suffix(".csv.tmp")
            df.to_csv(tmp, index=False)
            tmp.replace(path)              # atomic: a reader never sees half a file
        out[s] = len(df)
    return out


def main() -> None:
    dirs = {"import": 3, "export": 3, "project": 2, "check": 2}
    if len(sys.argv) < 2 or dirs.get(sys.argv[1]) != len(sys.argv):
        raise SystemExit(__doc__)
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set")
    pg = PostgresLedgers(url)
    if sys.argv[1] in ("project", "check"):
        differ = 0
        for s in STRATEGIES:
            if sys.argv[1] == "project":
                pg.project(s)
            n = pg.diff(s)
            print(f"[{s}] {n} rows differ between ledger_{s} and v_ledger_{s}")
            print(f"[{s}] {pg.late(s)} logged after kick-off, left out of v_ledger_{s}")
            differ += n
        if differ:
            raise SystemExit(1)
        return
    directory = Path(sys.argv[2])
    if sys.argv[1] == "import":
        for s, (added, settled) in to_postgres(pg, directory).items():
            print(f"[{s}] added {added}, settled {settled}")
    else:
        for s, n in to_csv(pg, directory).items():
            print(f"[{s}] exported {n} rows")


if __name__ == "__main__":
    main()
