"""Paper-trading ledgers as written by `python -m model.paper` (one CSV per strategy)."""
from __future__ import annotations

import io
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from model.data import MARKETS
from model.paper import STRATEGIES

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
