"""Endpoint behaviour on the fixture ledgers. Every call is also checked against openapi.yaml (conftest)."""
from __future__ import annotations

import pandas as pd
import pytest
import requests
from fastapi.testclient import TestClient

from api.ledgers import CsvLedgers
from api.main import create_app
from api.odds import LiveOddsCache
from api.usecases import SWEEP_EDGES
from tests.conftest import FakeOddsHttp, FakeResponse, odds_events

S = "/api/v1/strategies"
SNAPSHOT = {"budget": 50, "markets": ["ou25", "1x2"], "odds": {"source": "snapshot", "book": "avg"}}


def problem(resp, status: int, code: str) -> dict:
    assert resp.status_code == status, resp.text
    assert resp.headers["content-type"] == "application/problem+json"
    body = resp.json()
    assert body["code"] == code and body["type"] == f"/problems/{code}" and body["status"] == status
    return body


def all_items(client, url: str) -> list[dict]:
    """Every item of a collection (the fixtures are smaller than one 500-item page)."""
    body = client.get(url + ("&" if "?" in url else "?") + "limit=500").json()
    assert body["total"] == len(body["items"])
    return body["items"]


# --------------------------------------------------------------------------- meta

def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_catalog(client):
    c = client.get("/api/v1/catalog").json()
    by_id = {s["id"]: s for s in c["strategies"]}
    assert by_id["ou25"]["ledger"]["matches"] == 72 and by_id["1x2"]["ledger"]["matches"] == 80
    assert by_id["ou25"]["books"] == ["avg", "max", "b365"]          # ps has no odds in the ledger
    assert by_id["ou25"]["default_min_edge"] == 0.06
    assert by_id["ou25"]["ledger"]["last_logged_at"].endswith("Z")
    assert c["snapshot_books"] == ["avg", "max", "b365"]
    assert c["live_odds"] == {"configured": False, "ttl_seconds": 600}
    assert len(c["leagues"]) == 16


def test_contract_is_served(client):
    r = client.get("/api/v1/openapi.yaml")
    assert r.status_code == 200 and r.text.startswith("openapi: 3.1.0")


def test_unknown_route_and_method(client):
    problem(client.get("/api/v1/nope"), 404, "not_found")
    r = client.post("/api/v1/catalog")
    problem(r, 405, "method_not_allowed")
    assert r.headers["allow"] == "GET"


def test_serves_frontend_build(tmp_path, ledger_dir):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>x</title>")
    (tmp_path / "assets" / "index-abc.js").write_text("console.log(1)")
    web = TestClient(create_app(CsvLedgers(ledger_dir), LiveOddsCache(""), web=tmp_path))   # "/" is not in the contract

    r = web.get("/")
    assert r.status_code == 200 and r.text.startswith("<!doctype html>") and r.headers["cache-control"] == "no-cache"
    assert web.get("/assets/index-abc.js").text == "console.log(1)"
    assert web.get("/assets/missing.js").status_code == 404
    problem(web.get("/api/v1/nope"), 404, "not_found")                   # the API keeps its own 404s
    assert web.get("/api/v1/catalog").status_code == 200


def test_internal_error_does_not_leak(make_client):
    class Broken:
        def read(self, strategy):
            raise RuntimeError("secret path /etc/passwd")
    body = problem(make_client(ledgers=Broken()).get(f"{S}/ou25/picks"), 500, "internal_error")
    assert "secret" not in str(body)


# --------------------------------------------------------------------------- picks / predictions

def test_picks(client):
    items = all_items(client, f"{S}/ou25/picks?min_edge=0")
    assert items and all(i["edge"] > 0 for i in items)
    today = pd.Timestamp.today().normalize()
    assert all(pd.Timestamp(i["date"]) >= today for i in items)
    assert [i["date"] for i in items] == sorted(i["date"] for i in items)          # default sort: date
    assert len(all_items(client, f"{S}/ou25/picks?min_edge=0.5")) == 0


def test_picks_default_edge_is_the_strategy_edge(client):
    default = client.get(f"{S}/1x2/picks").json()["total"]
    assert default == client.get(f"{S}/1x2/picks?min_edge=0.05").json()["total"]


def test_pagination_is_stable_and_complete(client):
    url = f"{S}/1x2/predictions"
    full = all_items(client, url)
    pages = [client.get(url, params={"limit": 7, "offset": o}).json() for o in range(0, len(full), 7)]
    assert all(p["total"] == len(full) for p in pages)
    assert [i for p in pages for i in p["items"]] == full


def test_sort_descending_with_missing_last(client):
    items = all_items(client, f"{S}/1x2/predictions?sort=-edge")
    edges = [i["best"]["edge"] for i in items if i["best"]]
    assert edges == sorted(edges, reverse=True)
    assert all(i["best"] for i in items[:len(edges)])                                # unpriced rows last


def test_predictions(client):
    items = all_items(client, f"{S}/ou25/predictions")
    assert len(items) == 24
    assert all([o["outcome"] for o in i["outcomes"]] == ["O", "U"] for i in items)
    assert sum(i["best"] is None for i in items) == 1                                # the row with no odds
    for i in items:
        assert sum(o["p_model"] for o in i["outcomes"]) == pytest.approx(1)


def test_predictions_league_filter(client):
    items = all_items(client, f"{S}/1x2/predictions?league=E0&league=SP1")
    assert items and {i["league"] for i in items} <= {"E0", "SP1"}


@pytest.mark.parametrize("query, parameter", [
    ("book=zz", "book"), ("min_edge=-0.01", "min_edge"), ("min_edge=0.51", "min_edge"), ("limit=0", "limit"),
    ("limit=501", "limit"), ("offset=-1", "offset"), ("sort=foo", "sort"), ("sort=date,date", "sort"),
])
def test_picks_invalid_query(client, query, parameter):
    body = problem(client.get(f"{S}/ou25/picks?{query}"), 422, "validation_failed")
    assert [e["parameter"] for e in body["errors"]] == [parameter]


def test_invalid_league(client):
    problem(client.get(f"{S}/ou25/predictions?league=XX"), 422, "validation_failed")


@pytest.mark.parametrize("path", ["picks", "predictions", "results/summary", "results/bets",
                                  "results/edge-sweep", "results/probability-report"])
def test_unknown_strategy(client, path):
    problem(client.get(f"{S}/nope/{path}"), 404, "strategy_not_found")


# --------------------------------------------------------------------------- results

def test_results_summary(client):
    s = client.get(f"{S}/ou25/results/summary?min_edge=0").json()
    assert s["settled_matches"] == 48 and s["metrics"]["bets"] == len(s["series"])
    assert s["series"][-1]["cumulative_profit"] == pytest.approx(s["metrics"]["profit"])
    assert s["series"][-1]["mean_clv"] == pytest.approx(s["metrics"]["clv"])
    assert client.get(f"{S}/ou25/results/summary?min_edge=0.5").json()["metrics"] is None


def test_settled_bets(client):
    bets = all_items(client, f"{S}/1x2/results/bets?min_edge=0")
    assert bets and all(b["stake"] == 1 for b in bets)
    assert all(b["profit"] == pytest.approx(b["odds"] - 1 if b["pick"] == b["result"] else -1) for b in bets)
    assert [b["date"] for b in bets] == sorted((b["date"] for b in bets), reverse=True)   # default: -date


def test_settled_bets_without_closing_odds_have_null_clv(client):
    bets = all_items(client, f"{S}/ou25/results/bets?min_edge=0")
    no_close = [b for b in bets if b["p_close"] is None]
    assert all(b["clv"] is None for b in no_close)


def test_edge_sweep(client):
    rows = client.get(f"{S}/ou25/results/edge-sweep").json()["rows"]
    assert rows and [r["min_edge"] for r in rows] == list(SWEEP_EDGES)[:len(rows)]
    assert [r["bets"] for r in rows] == sorted((r["bets"] for r in rows), reverse=True)


def test_probability_report(client):
    rep = client.get(f"{S}/1x2/results/probability-report").json()
    assert rep["n"] == 38                                       # 40 settled, 2 without closing odds
    assert [r["source"] for r in rep["rows"]] == ["model", "market_pre_match", "market_closing"]


# --------------------------------------------------------------------------- optimize

def test_optimize_snapshot(client):
    r = client.post("/api/v1/optimize", json=SNAPSHOT).json()
    assert r["live"] is None and r["upcoming_matches"] == 64 and 0 < len(r["legs"]) <= 8
    assert [l["edge"] for l in r["legs"]] == sorted((l["edge"] for l in r["legs"]), reverse=True)
    assert len({(l["date"], l["home"]) for l in r["legs"]}) == len(r["legs"])        # one leg per match

    legs = client.post("/api/v1/optimize", json={**SNAPSHOT, "min_edge": 0.005}).json()["legs"]
    assert legs
    for l in legs:                                        # min_odds: the price at which edge == min_edge
        assert l["min_odds"] == pytest.approx(1.005 / l["p_model"]) and l["odds"] >= l["min_odds"]
    for sc in r["scenarios"]:
        assert sc["summary"]["staked"] <= 50 + 1e-9
        assert sum(sc["pnl_histogram"]["probabilities"]) == pytest.approx(1)
        assert all(round(c["stake"] * 10, 6).is_integer() for c in sc["coupons"])
    assert {sc["id"] for sc in r["scenarios"]} == {"conservative", "balanced", "aggressive"}


def test_optimize_is_deterministic(client):
    assert client.post("/api/v1/optimize", json=SNAPSHOT).json() == client.post("/api/v1/optimize", json=SNAPSHOT).json()


def test_optimize_nothing_passes_filters(client):
    r = client.post("/api/v1/optimize", json={**SNAPSHOT, "min_prob": 0.8, "min_edge": 0.15}).json()
    assert r["legs"] == [] and r["scenarios"] == [] and r["upcoming_matches"] == 64


@pytest.mark.parametrize("body, pointer", [
    ({**SNAPSHOT, "budget": 4.99}, "/budget"),
    ({**SNAPSHOT, "budget": 100_001}, "/budget"),
    ({**SNAPSHOT, "markets": []}, "/markets"),
    ({**SNAPSHOT, "markets": ["zz"]}, "/markets/0"),
    ({**SNAPSHOT, "min_prob": 0.9}, "/min_prob"),
    ({**SNAPSHOT, "min_edge": -0.2}, "/min_edge"),
    ({**SNAPSHOT, "min_prob": None}, "/min_prob"),
    ({**SNAPSHOT, "extra": 1}, "/extra"),
    ({k: v for k, v in SNAPSHOT.items() if k != "odds"}, "/odds"),
    ({**SNAPSHOT, "odds": {"source": "snapshot", "book": "ps"}}, "/odds/book"),
    ({**SNAPSHOT, "odds": {"source": "live"}}, "/odds/leagues"),
    ({**SNAPSHOT, "odds": {"source": "live", "leagues": []}}, "/odds/leagues"),
    ({**SNAPSHOT, "odds": {"source": "live", "leagues": ["E0"], "bookmakers": []}}, "/odds/bookmakers"),
    ({**SNAPSHOT, "budget": "50"}, "/budget"),
    ({**SNAPSHOT, "markets": "ou25"}, "/markets"),
])
def test_optimize_invalid_body(client, body, pointer):
    errors = problem(client.post("/api/v1/optimize", json=body), 422, "validation_failed")["errors"]
    assert pointer in [e["pointer"] for e in errors]


def test_optimize_duplicates_are_ignored(client):
    once = client.post("/api/v1/optimize", json={**SNAPSHOT, "markets": ["1x2"]}).json()
    twice = client.post("/api/v1/optimize", json={**SNAPSHOT, "markets": ["1x2", "1x2"]}).json()
    assert once == twice


def test_optimize_malformed_json(client):
    r = client.post("/api/v1/optimize", content=b"{", headers={"content-type": "application/json"})
    problem(r, 422, "validation_failed")


# --------------------------------------------------------------------------- optimize with live odds

LIVE = {**SNAPSHOT, "odds": {"source": "live", "leagues": ["E0", "SP1"]}}


def test_live_not_configured(client):
    problem(client.post("/api/v1/optimize", json=LIVE), 503, "odds_api_not_configured")


def test_live_odds(make_client, ledger_dir):
    http = FakeOddsHttp({"soccer_epl": odds_events(ledger_dir, "E0"), "soccer_spain_la_liga": odds_events(ledger_dir, "SP1")})
    c = make_client(http, key="k")
    r = c.post("/api/v1/optimize", json=LIVE).json()
    live = r["live"]
    assert live["quota"] == {"remaining": 480, "used": 20}
    assert live["bookmakers"] == ["BookA", "BookB"]
    by_league = {lg["league"]: lg for lg in live["leagues"]}
    assert set(by_league) == {"E0", "SP1"}
    for lg in by_league.values():
        assert lg["matched"] + len(lg["unmatched"]) == lg["events"]
        assert len(lg["unmatched"]) == 1
        assert [u["home"] for u in lg["unmatched"]] == ["Nowhere FC"]
    assert r["legs"] and {l["league"] for l in r["legs"]} <= {"E0", "SP1"}
    assert {l["bookmaker"] for l in r["legs"]} <= {"BookA", "BookB"}

    only_b = c.post("/api/v1/optimize", json={**LIVE, "odds": {**LIVE["odds"], "bookmakers": ["BookB"]}}).json()
    assert {l["bookmaker"] for l in only_b["legs"]} <= {"BookB"}
    assert only_b["live"]["bookmakers"] == ["BookA", "BookB"]                         # picker keeps all
    assert len(http.calls) == 2                                                       # second call was cached


def test_live_leagues_no_market_covers_cost_nothing(make_client):
    http = FakeOddsHttp()
    c = make_client(http, key="k")
    r = c.post("/api/v1/optimize", json={**SNAPSHOT, "markets": ["ou25"],
                                         "odds": {"source": "live", "leagues": ["E1", "E1"]}}).json()
    assert http.calls == [] and r["live"]["leagues"] == [] and r["legs"] == []


def test_odds_api_key_is_never_logged(make_client, caplog):
    leak = requests.ConnectionError("HTTPSConnectionPool: Max retries exceeded with url: /v4/odds?apiKey=s3cr3t")
    c = make_client(FakeOddsHttp(response=leak), key="s3cr3t")
    problem(c.post("/api/v1/optimize", json=LIVE), 502, "odds_api_unavailable")
    assert "s3cr3t" not in caplog.text and "ConnectionError" in caplog.text


def test_live_odds_matching_nothing_gives_no_legs(make_client):
    c = make_client(FakeOddsHttp({"soccer_epl": []}), key="k")
    r = c.post("/api/v1/optimize", json={**LIVE, "odds": {"source": "live", "leagues": ["E0"]}}).json()
    assert r["legs"] == [] and r["live"]["leagues"][0]["events"] == 0


@pytest.mark.parametrize("response, code", [
    (FakeResponse(401, {"message": "x", "error_code": "OUT_OF_USAGE_CREDITS"}), "odds_api_quota_exhausted"),
    (FakeResponse(401, {"message": "x", "error_code": "INVALID_KEY"}), "odds_api_unauthorized"),
    (FakeResponse(429, {"message": "x", "error_code": "EXCEEDED_FREQ_LIMIT"}), "odds_api_unavailable"),
    (requests.ConnectionError("down"), "odds_api_unavailable"),
])
def test_live_odds_failures(make_client, response, code):
    c = make_client(FakeOddsHttp(response=response), key="k")
    body = problem(c.post("/api/v1/optimize", json=LIVE), 502, code)
    assert "message" not in body and "x" != body.get("detail")
