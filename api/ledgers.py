"""Paper-trading ledgers as written by `python -m model.paper` (one CSV per strategy)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from model.data import MARKETS
from model.paper import STRATEGIES


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
        df = pd.read_csv(path, parse_dates=["date", "logged_at"])
        spec = MARKETS[STRATEGIES[strategy]["market"]]
        for c in [spec["target"]] + [f"close_{s}" for s in spec["suffixes"]]:
            if not df.empty and c not in df:
                df[c] = np.nan
        self._cache[strategy] = (mtime, df)
        return df
