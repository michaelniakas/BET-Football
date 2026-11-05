"""Live odds from The Odds API, cached per league.

The HTTP call lives here rather than in `model.live_odds.fetch` because that function raises the
same error for a bad key and for exhausted credits; response parsing is reused from `model`.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pandas as pd
import requests

from model import live_odds

from .usecases import LeagueOdds, OddsApiFailed, OddsApiNotConfigured, Quota

# https://the-odds-api.com/liveapi/guides/v4/api-error-codes.html
UNAUTHORIZED = {"MISSING_KEY", "INVALID_KEY", "DEACTIVATED_KEY"}
QUOTA_EXHAUSTED = {"OUT_OF_USAGE_CREDITS"}
NOT_OFFERED = {"UNKNOWN_SPORT"}
# After a failed fetch, requests for that league fail fast for this long instead of queueing behind
# the lock and retrying one after another while the upstream is down.
FAILURE_TTL = timedelta(seconds=30)
TIMEOUT = (3.05, 10)            # connect, read (seconds)


class LiveOddsCache:
    def __init__(self, api_key: str, ttl: timedelta = timedelta(minutes=10),
                 get: Callable[..., requests.Response] = requests.get,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self._key, self.ttl, self._get, self._now = api_key, ttl, get, now
        self._entries: dict[str, LeagueOdds] = {}
        self._failures: dict[str, tuple[datetime, OddsApiFailed]] = {}
        # One lock per league, so concurrent requests for the same league share one fetch.
        self._locks = {lg: threading.Lock() for lg in live_odds.SPORT_KEYS}
        self.quota: Quota | None = None

    @property
    def configured(self) -> bool:
        return bool(self._key)

    def get(self, leagues: list[str]) -> dict[str, LeagueOdds]:
        """Odds per league, refetching only leagues whose copy is older than the TTL."""
        if not self.configured:
            raise OddsApiNotConfigured
        out = {}
        for lg in sorted(leagues):
            with self._locks[lg]:
                entry = self._entries.get(lg)
                if entry is None or self._now() - entry.fetched_at >= self.ttl:
                    failed = self._failures.get(lg)
                    if failed and self._now() - failed[0] < FAILURE_TTL:
                        raise failed[1]
                    try:
                        entry = self._entries[lg] = self._fetch(lg)
                    except OddsApiFailed as exc:
                        self._failures[lg] = (self._now(), exc)
                        raise
                    self._failures.pop(lg, None)
            out[lg] = entry
        return out

    def _fetch(self, league: str) -> LeagueOdds:
        try:
            r = self._get(live_odds.API_URL.format(sport=live_odds.SPORT_KEYS[league]), timeout=TIMEOUT, params={
                "apiKey": self._key, "regions": "eu", "markets": "h2h,totals", "oddsFormat": "decimal"})
        except requests.RequestException as exc:
            raise OddsApiFailed("unavailable") from exc
        self._record_quota(r)
        if r.ok:
            try:
                rows = pd.DataFrame(live_odds.parse(r.json(), league), columns=live_odds.LIVE_COLS)
            except (ValueError, KeyError, TypeError, AttributeError) as exc:      # not JSON, or not the documented shape
                raise OddsApiFailed("unavailable") from exc
            return LeagueOdds(self._now(), rows)
        code = _error_code(r)
        if code in QUOTA_EXHAUSTED:
            raise OddsApiFailed("quota_exhausted")
        if code in UNAUTHORIZED or (code is None and r.status_code == 401):
            raise OddsApiFailed("unauthorized")
        if code in NOT_OFFERED or r.status_code == 404:        # league not offered right now (off-season)
            return LeagueOdds(self._now(), pd.DataFrame(columns=live_odds.LIVE_COLS))
        raise OddsApiFailed("unavailable")

    def _record_quota(self, r: requests.Response) -> None:
        try:
            self.quota = Quota(int(float(r.headers["x-requests-remaining"])), int(float(r.headers["x-requests-used"])))
        except (KeyError, ValueError):
            pass


def _error_code(r: requests.Response) -> str | None:
    try:
        body = r.json()
    except ValueError:
        return None
    return body.get("error_code") if isinstance(body, dict) else None
