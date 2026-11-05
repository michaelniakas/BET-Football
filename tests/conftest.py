"""Shared fixtures: date-shifted fixture ledgers, a fake Odds API, and a test client that checks every
request/response pair against openapi.yaml."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from openapi_core import Config, OpenAPI
from openapi_core.datatypes import RequestParameters
from openapi_core.templating.paths.exceptions import OperationNotFound, PathNotFound
from werkzeug.datastructures import Headers, ImmutableMultiDict

from api.ledgers import CsvLedgers
from api.main import create_app
from api.odds import LiveOddsCache

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
# The fixture ledgers are written as if today were ANCHOR: settled matches before it, upcoming after.
ANCHOR = pd.Timestamp("2026-11-05")
CONTRACT = OpenAPI.from_path(ROOT / "openapi.yaml",
                             config=Config(extra_media_type_deserializers={
                                 "application/problem+json": json.loads, "application/yaml": bytes.decode}))


# How far fixture dates move so that ANCHOR becomes today. Fixed once per session, so a run across midnight
# shifts the ledgers and the recorded golden outputs alike.
SHIFT = pd.Timestamp.today().normalize() - ANCHOR


@pytest.fixture(scope="session")
def ledger_dir(tmp_path_factory) -> Path:
    """The fixture ledgers with every date moved so that ANCHOR becomes today."""
    shift = SHIFT
    out = tmp_path_factory.mktemp("ledgers")
    for f in FIXTURES.glob("ledger_*.csv"):
        df = pd.read_csv(f, parse_dates=["date", "logged_at"])
        df["date"] += shift
        df["logged_at"] += shift
        df.to_csv(out / f.name, index=False)
    return out


# --------------------------------------------------------------------------- fake Odds API

class FakeResponse:
    def __init__(self, status: int, body=None, headers: dict | None = None, raw: str | None = None):
        self.status_code, self.ok = status, 200 <= status < 300
        self.headers = headers if headers is not None else {"x-requests-remaining": "480", "x-requests-used": "20"}
        self._text = raw if raw is not None else json.dumps(body)

    def json(self):
        return json.loads(self._text)


class FakeOddsHttp:
    """Stands in for `requests.get`: serves canned events per sport key, or a canned response for all."""

    def __init__(self, events: dict[str, list] | None = None, response: FakeResponse | Exception | None = None):
        self.events, self.response, self.calls = events or {}, response, []

    def __call__(self, url: str, timeout: float, params: dict):
        self.calls.append(url)
        if isinstance(self.response, Exception):
            raise self.response
        if self.response is not None:
            return self.response
        return FakeResponse(200, self.events.get(url.split("/sports/")[1].split("/")[0], []))


def odds_events(ledger_dir: Path, league: str) -> list[dict]:
    """Odds API events for the upcoming fixture matches of `league`, two bookmakers, plus one unknown match."""
    up = []
    for name in ("ou25", "1x2"):
        df = pd.read_csv(ledger_dir / f"ledger_{name}.csv", parse_dates=["date"])
        up.append(df[(df.league == league) & (df.date >= pd.Timestamp.today().normalize())][["date", "home", "away"]])
    matches = pd.concat(up).drop_duplicates()
    events = []
    for i, m in enumerate(matches.itertuples(index=False)):
        books = [{"title": title, "markets": [
            {"key": "h2h", "outcomes": [{"name": m.home, "price": 2.1 * f}, {"name": "Draw", "price": 3.4 * f},
                                         {"name": m.away, "price": 3.6 * f}]},
            {"key": "totals", "outcomes": [{"name": "Over", "price": 1.9 * f, "point": 2.5},
                                            {"name": "Under", "price": 1.95 * f, "point": 2.5}]}]}
                 for title, f in (("BookA", 1.0 + 0.01 * i), ("BookB", 1.02))]
        events.append({"home_team": m.home, "away_team": m.away,
                       "commence_time": f"{m.date:%Y-%m-%d}T15:00:00Z", "bookmakers": books})
    events.append({"home_team": "Nowhere FC", "away_team": "Ghost United", "commence_time": "2030-01-01T15:00:00Z",
                   "bookmakers": [{"title": "BookA", "markets": [{"key": "h2h", "outcomes": [
                       {"name": "Nowhere FC", "price": 2.0}, {"name": "Draw", "price": 3.3},
                       {"name": "Ghost United", "price": 3.9}]}]}]})
    return events


# --------------------------------------------------------------------------- contract-checked client

class _Req:
    def __init__(self, r: httpx.Request):
        self.host_url = f"{r.url.scheme}://{r.url.netloc.decode()}"
        self.path = r.url.path
        self.method = r.method.lower()
        self.body = r.content or None
        self.content_type = r.headers.get("content-type", "")
        self.parameters = RequestParameters(
            query=ImmutableMultiDict(parse_qsl(r.url.query.decode(), keep_blank_values=True)),
            header=Headers(dict(r.headers)), cookie=ImmutableMultiDict())


class _Resp:
    def __init__(self, r: httpx.Response):
        self.data, self.status_code = r.content, r.status_code
        self.content_type = r.headers.get("content-type", "")
        self.headers = Headers(dict(r.headers))


class ContractClient(TestClient):
    """Every response must match openapi.yaml. A request the server accepts must be valid per the contract,
    and a request it rejects with 422 must be invalid per the contract: server and contract agree."""

    def request(self, method, url, **kw):
        resp = super().request(method, url, **kw)
        req = _Req(resp.request)
        try:
            CONTRACT.validate_response(req, _Resp(resp))
        except (PathNotFound, OperationNotFound):
            assert resp.status_code in (404, 405), f"{method} {url} is not in the contract"
            return resp
        if resp.status_code < 400:
            CONTRACT.validate_request(req)
        elif resp.status_code == 422:
            with pytest.raises(Exception):
                CONTRACT.validate_request(req)
        return resp


@pytest.fixture
def make_client(ledger_dir):
    def make(http: FakeOddsHttp | None = None, key: str = "", now=None, ledgers=None) -> ContractClient:
        odds = LiveOddsCache(key, get=http or FakeOddsHttp(), now=now or (lambda: datetime.now(UTC)))
        app = create_app(ledgers or CsvLedgers(ledger_dir), odds)
        return ContractClient(app, raise_server_exceptions=False)
    return make


@pytest.fixture
def client(make_client) -> ContractClient:
    return make_client()


@pytest.fixture
def fake_now():
    t = [datetime(2030, 1, 1, tzinfo=UTC)]

    def now():
        return t[0]
    now.advance = lambda **kw: t.__setitem__(0, t[0] + timedelta(**kw))
    return now
