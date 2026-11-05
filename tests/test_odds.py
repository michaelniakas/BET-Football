"""The live-odds cache: TTL, one fetch per league under concurrency, and Odds API error mapping."""
from __future__ import annotations

import threading
import time
from datetime import timedelta

import pytest
import requests

from api.odds import LiveOddsCache
from api.usecases import OddsApiFailed, OddsApiNotConfigured
from tests.conftest import FakeOddsHttp, FakeResponse

EVENT = {"home_team": "Arsenal", "away_team": "Chelsea", "commence_time": "2030-01-05T15:00:00Z", "bookmakers": [
    {"title": "BookA", "markets": [{"key": "h2h", "outcomes": [
        {"name": "Arsenal", "price": 2.0}, {"name": "Draw", "price": 3.5}, {"name": "Chelsea", "price": 3.8}]}]}]}


def test_not_configured():
    with pytest.raises(OddsApiNotConfigured):
        LiveOddsCache("").get(["E0"])


def test_parses_rows_and_quota(fake_now):
    cache = LiveOddsCache("k", get=FakeOddsHttp({"soccer_epl": [EVENT]}), now=fake_now)
    rows = cache.get(["E0"])["E0"].rows
    assert sorted(rows.outcome) == ["A", "D", "H"] and set(rows.bookmaker) == {"BookA"}
    assert (cache.quota.remaining, cache.quota.used) == (480, 20)


def test_ttl(fake_now):
    http = FakeOddsHttp({"soccer_epl": [EVENT]})
    cache = LiveOddsCache("k", ttl=timedelta(minutes=10), get=http, now=fake_now)
    cache.get(["E0"])
    fake_now.advance(minutes=9, seconds=59)
    cache.get(["E0"])
    assert len(http.calls) == 1
    fake_now.advance(seconds=1)
    cache.get(["E0"])
    assert len(http.calls) == 2


def test_concurrent_requests_share_one_fetch(fake_now):
    class Slow(FakeOddsHttp):
        def __call__(self, *a, **kw):
            time.sleep(0.05)
            return super().__call__(*a, **kw)
    http = Slow({"soccer_epl": [EVENT]})
    cache = LiveOddsCache("k", get=http, now=fake_now)
    threads = [threading.Thread(target=cache.get, args=(["E0"],)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(http.calls) == 1


def test_off_season_league_is_empty(fake_now):
    for resp in (FakeResponse(404, {"message": "x"}), FakeResponse(422, {"error_code": "UNKNOWN_SPORT"})):
        assert LiveOddsCache("k", get=FakeOddsHttp(response=resp), now=fake_now).get(["E0"])["E0"].rows.empty


def test_missing_quota_headers_are_ignored(fake_now):
    cache = LiveOddsCache("k", get=FakeOddsHttp(response=FakeResponse(200, [], headers={})), now=fake_now)
    cache.get(["E0"])
    assert cache.quota is None


@pytest.mark.parametrize("response, kind", [
    (FakeResponse(401, {"error_code": "OUT_OF_USAGE_CREDITS"}), "quota_exhausted"),
    (FakeResponse(401, {"error_code": "INVALID_KEY"}), "unauthorized"),
    (FakeResponse(401, {"error_code": "DEACTIVATED_KEY"}), "unauthorized"),
    (FakeResponse(401, raw="not json"), "unauthorized"),
    (FakeResponse(429, {"error_code": "EXCEEDED_FREQ_LIMIT"}), "unavailable"),
    (FakeResponse(500, raw="boom"), "unavailable"),
    (FakeResponse(200, raw="<html>"), "unavailable"),
    (requests.Timeout("slow"), "unavailable"),
])
def test_error_mapping(fake_now, response, kind):
    with pytest.raises(OddsApiFailed) as exc:
        LiveOddsCache("k", get=FakeOddsHttp(response=response), now=fake_now).get(["E0"])
    assert exc.value.kind == kind


def test_failures_are_remembered_briefly(fake_now):
    http = FakeOddsHttp(response=requests.ConnectionError())
    cache = LiveOddsCache("k", get=http, now=fake_now)
    for _ in range(3):
        with pytest.raises(OddsApiFailed):
            cache.get(["E0"])
    assert len(http.calls) == 1                                 # no retry storm while the upstream is down
    fake_now.advance(seconds=30)
    http.response = FakeResponse(200, [EVENT])
    assert not cache.get(["E0"])["E0"].rows.empty
    assert len(http.calls) == 2


@pytest.mark.parametrize("body", [[{"home_team": "A"}], {"not": "a list"}, ["x"]])
def test_unexpected_shape_is_unavailable(fake_now, body):
    with pytest.raises(OddsApiFailed) as exc:
        LiveOddsCache("k", get=FakeOddsHttp(response=FakeResponse(200, body)), now=fake_now).get(["E0"])
    assert exc.value.kind == "unavailable"


def test_failed_fetch_keeps_earlier_leagues(fake_now):
    class FailSpain(FakeOddsHttp):
        def __call__(self, url, **kw):
            if "spain" in url:
                raise requests.ConnectionError
            return super().__call__(url, **kw)
    http = FailSpain({"soccer_epl": [EVENT]})
    cache = LiveOddsCache("k", get=http, now=fake_now)
    with pytest.raises(OddsApiFailed):
        cache.get(["E0", "SP1"])
    cache.get(["E0"])
    assert http.calls.count(http.calls[0]) == 1                  # E0 was cached before SP1 failed
