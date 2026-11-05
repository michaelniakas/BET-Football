"""Paper-trading ledgers as written by `python -m model.paper`: CSV files, or Postgres from phase 3 on."""
from __future__ import annotations

import io
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
import requests
from psycopg import sql

from model.data import MARKETS
from model.paper import KEY, STRATEGIES

log = logging.getLogger(__name__)
# Where the paper-trading workflow commits the ledgers (scheduled workflows run on `main`).
GITHUB_MAIN = "https://raw.githubusercontent.com/michaelniakas/BET-Football/main/model/paper"
TIMEOUT = (3.05, 5)             # connect, read (seconds)


def _with_result_columns(strategy: str, df: pd.DataFrame) -> pd.DataFrame:
    spec = MARKETS[STRATEGIES[strategy]["market"]]
    for c in [spec["target"]] + [f"close_{s}" for s in spec["suffixes"]]:
        if not df.empty and c not in df:
            df[c] = np.nan
    return df


class CsvLedgers:
    """Reads each ledger once and again only when the file changes on disk."""

    def __init__(self, directory: Path):
        self._dir = directory
        self._cache: dict[str, tuple[int, pd.DataFrame]] = {}

    def read(self, strategy: str) -> pd.DataFrame:
        """The ledger, with the result and closing columns present even before anything has settled.

        Callers must not mutate the returned frame: it is shared between requests.
        """
        path = self._dir / f"ledger_{strategy}.csv"
        try:
            mtime = path.stat().st_mtime_ns
        except FileNotFoundError:
            return pd.DataFrame()
        cached = self._cache.get(strategy)
        if cached and cached[0] == mtime:
            return cached[1]
        df = _with_result_columns(strategy, pd.read_csv(path, parse_dates=["date", "logged_at"]))
        self._cache[strategy] = (mtime, df)
        return df


class GithubLedgers:
    """The ledgers as last committed to GitHub `main`, so a Tue/Fri run shows without a redeploy.

    Refetched after `ttl`. If a fetch fails, the previous copy is served for another `ttl`, or the
    image's copy (`fallback`) before the first success.
    """
    # ponytail: stopgap until phase 3 (Postgres); the request after expiry waits on GitHub (<= ~8 s).

    def __init__(self, base_url: str, fallback: CsvLedgers, ttl: timedelta = timedelta(minutes=10),
                 get: Callable[..., requests.Response] = requests.get,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self._base, self._fallback, self._ttl, self._get, self._now = base_url, fallback, ttl, get, now
        self._cache: dict[str, tuple[datetime, pd.DataFrame]] = {}
        # One lock per strategy, so concurrent requests after expiry share one fetch.
        self._locks = {s: threading.Lock() for s in STRATEGIES}

    def read(self, strategy: str) -> pd.DataFrame:
        """Same contract as `CsvLedgers.read`: callers must not mutate the returned frame."""
        with self._locks[strategy]:
            cached = self._cache.get(strategy)
            if cached and self._now() - cached[0] < self._ttl:
                return cached[1]
            try:
                r = self._get(f"{self._base}/ledger_{strategy}.csv", timeout=TIMEOUT)
                r.raise_for_status()
                df = _with_result_columns(
                    strategy, pd.read_csv(io.BytesIO(r.content), parse_dates=["date", "logged_at"]))
            except (requests.RequestException, ValueError) as e:     # pandas parse errors are ValueErrors
                log.warning("ledger %s from GitHub failed, serving the previous copy: %s", strategy, e)
                df = cached[1] if cached else self._fallback.read(strategy)
            self._cache[strategy] = (self._now(), df)
            return df


class PostgresLedgers:
    """The ledgers in Postgres (api/migrations), one table per strategy.

    Reads return the same frame `CsvLedgers` does for the same rows, in the order they were logged.
    """
    # ponytail: a connection per call (2 users); a pool (psycopg_pool) once requests queue on connect.

    TEXT = ["league", "home", "away"]

    def __init__(self, url: str):
        self._url = url
        self._columns: dict[str, list[str]] = {}

    def _table(self, strategy: str) -> sql.Identifier:
        if strategy not in STRATEGIES:
            raise KeyError(strategy)
        return sql.Identifier(f"ledger_{strategy}")

    def columns(self, conn: psycopg.Connection, strategy: str) -> list[str]:
        """The table's columns in DDL order, without `seq`: the migration is the one list of them."""
        if strategy not in self._columns:
            rows = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'public'"
                                " AND table_name = %s AND column_name <> 'seq' ORDER BY ordinal_position",
                                (f"ledger_{strategy}",)).fetchall()
            if not rows:
                raise RuntimeError(f"no table ledger_{strategy}: run python -m api.migrate up")
            self._columns[strategy] = [r[0] for r in rows]
        return self._columns[strategy]

    def read(self, strategy: str) -> pd.DataFrame:
        """Callers must not mutate the returned frame (same contract as `CsvLedgers.read`)."""
        table = self._table(strategy)
        with psycopg.connect(self._url) as conn:
            cols = self.columns(conn, strategy)
            rows = conn.execute(sql.SQL("SELECT {} FROM {} ORDER BY seq").format(
                sql.SQL(", ").join(map(sql.Identifier, cols)), table)).fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=cols)
        target = MARKETS[STRATEGIES[strategy]["market"]]["target"]
        for c in cols:
            if c in ("date", "logged_at"):
                df[c] = pd.to_datetime(df[c]).astype("datetime64[us]")
            elif c in self.TEXT or c == target:
                df[c] = df[c].astype("str")
            elif c == "season":
                df[c] = df[c].astype("int64")
            else:
                df[c] = df[c].astype("float64")
        return df

    def save(self, strategy: str, ledger: pd.DataFrame) -> tuple[int, int]:
        """Write back a ledger the paper job has changed, in one transaction: (rows added, rows settled).

        New fixtures are inserted; an existing fixture only gets its result and closing columns, and
        only while it is unsettled. A snapshot (probabilities, odds, logged_at) is never overwritten.
        """
        table = self._table(strategy)
        spec = MARKETS[STRATEGIES[strategy]["market"]]
        settle_cols = [spec["target"]] + [f"close_{s}" for s in spec["suffixes"]]
        with psycopg.connect(self._url) as conn, conn.transaction():
            cols = self.columns(conn, strategy)
            unknown = set(ledger.columns) - set(cols)
            if unknown:     # the job writes a column the schema doesn't have: needs a migration, not a drop
                raise ValueError(f"ledger_{strategy} has no column(s) {sorted(unknown)}")
            cols = [c for c in cols if c in ledger.columns]
            values = ledger[cols].astype(object).where(ledger[cols].notna(), None)
            values["date"] = [v.date() for v in values["date"]]               # NOT NULL in both
            values["logged_at"] = [v.to_pydatetime() for v in values["logged_at"]]
            ident = lambda names: sql.SQL(", ").join(map(sql.Identifier, names))    # noqa: E731
            update = [c for c in settle_cols if c in cols]
            q = sql.SQL("INSERT INTO {t} AS l ({cols}) VALUES ({ph}) ON CONFLICT ({key}) DO ").format(
                t=table, cols=ident(cols), ph=sql.SQL(", ").join(sql.Placeholder() * len(cols)), key=ident(KEY))
            if update:
                q += sql.SQL("UPDATE SET {sets} WHERE l.{target} IS NULL AND EXCLUDED.{target} IS NOT NULL").format(
                    sets=sql.SQL(", ").join(sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(c)) for c in update),
                    target=sql.Identifier(spec["target"]))
            else:
                q += sql.SQL("NOTHING")
            q += sql.SQL(" RETURNING (xmax = 0)")         # true = inserted, false = updated
            added = settled = 0
            with conn.cursor() as cur:
                cur.executemany(q, values.itertuples(index=False, name=None), returning=True)
                while True:
                    row = cur.fetchone()
                    if row is not None:
                        added, settled = added + row[0], settled + (not row[0])
                    if not cur.nextset():
                        break
        return added, settled
