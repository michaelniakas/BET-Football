"""Endpoint behaviour on the fixture ledgers. Every call is also checked against openapi.yaml (conftest)."""
from __future__ import annotations

import math
import statistics
from urllib.parse import quote

import pandas as pd
import pytest
import requests
from fastapi.testclient import TestClient

from api.ledgers import CsvLedgers
from api.main import create_app
from api.odds import LiveOddsCache
from api.usecases import SWEEP_EDGES
from model.paper import STRATEGIES
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
    assert web.head("/").status_code == 200                               # uptime checks and link previews send HEAD
    assert web.get("/assets/index-abc.js").text == "console.log(1)"
    assert web.get("/assets/missing.js").status_code == 404
    problem(web.get("/api/v1/nope"), 404, "not_found")                   # the API keeps its own 404s
    assert web.get("/api/v1/catalog").status_code == 200


def test_no_page_is_indexed(tmp_path, ledger_dir):
    """Private site (D11): every response, page, asset, API and error alike, tells crawlers not to index it."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>x</title>")
    web = TestClient(create_app(CsvLedgers(ledger_dir), LiveOddsCache(""), web=tmp_path))
    for path in ("/", "/?page=results&lang=en", "/assets/missing.js", "/api/v1/catalog", "/api/v1/nope", "/health"):
        assert web.get(path).headers["x-robots-tag"] == "noindex, nofollow", path


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


@pytest.mark.parametrize("edge", [0.0, 0.005])
def test_pick_min_odds_is_the_price_at_min_edge(client, edge):
    items = all_items(client, f"{S}/ou25/picks?min_edge={edge}")
    assert items
    for i in items:
        assert i["min_odds"] == pytest.approx((1 + edge) / i["p_model"]) and i["odds"] >= i["min_odds"]


def test_pick_min_odds_uses_the_strategy_edge_by_default(client, monkeypatch):
    monkeypatch.setitem(STRATEGIES["1x2"], "edge", 0.01)        # fixture picks top out below the real defaults
    items = all_items(client, f"{S}/1x2/picks")
    assert items and all(i["min_odds"] == pytest.approx(1.01 / i["p_model"]) for i in items)


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


@pytest.mark.parametrize("path", ["picks", "predictions", "results/summary", "results/fixtures",
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


def test_edge_sweep(client):
    rows = client.get(f"{S}/ou25/results/edge-sweep").json()["rows"]
    assert rows and [r["min_edge"] for r in rows] == list(SWEEP_EDGES)[:len(rows)]
    assert [r["bets"] for r in rows] == sorted((r["bets"] for r in rows), reverse=True)


def test_probability_report(client):
    rep = client.get(f"{S}/1x2/results/probability-report").json()
    assert rep["n"] == 38                                       # 40 settled, 2 without closing odds
    assert [r["source"] for r in rep["rows"]] == ["model", "market_pre_match", "market_closing"]


def expected_report(ledger_dir, strategy: str) -> dict:
    """Favourite hit rate, model-closer rate, calibration and per-league scores, row by row (no model.backtest)."""
    outcomes, suffixes, target = LEDGER_COLS[strategy]
    sources = {"model": [f"p_{o}" for o in outcomes], "market_pre_match": [f"mkt_{s}" for s in suffixes],
               "market_closing": [f"close_{s}" for s in suffixes]}
    rows = [r for r in pd.read_csv(ledger_dir / f"ledger_{strategy}.csv").to_dict("records")
            if not pd.isna(r[target]) and not any(pd.isna(r[c]) for cols in sources.values() for c in cols)]

    def scores(rs: list[dict]) -> dict:
        out = {"n": len(rs), "rows": []}
        for src, cols in sources.items():
            fav = []
            for r in rs:
                top = [o for c, o in zip(cols, outcomes) if r[c] == max(r[c_] for c_ in cols)]
                fav.append((r[target] in top) / len(top))
            p_y = [r[cols[outcomes.index(r[target])]] for r in rs]
            out["rows"].append({"source": src, "favourite_hit_rate": sum(fav) / len(rs),
                                "log_loss": -sum(math.log(p) for p in p_y) / len(rs)})
        on_result = lambda r, src: r[sources[src][outcomes.index(r[target])]]     # noqa: E731
        out["model_closer_rate"] = sum(on_result(r, "model") > on_result(r, "market_pre_match") for r in rs) / len(rs)
        gaps = [math.log(on_result(r, "market_pre_match")) - math.log(on_result(r, "model")) for r in rs]
        out["log_loss_gap_se"] = statistics.stdev(gaps) / math.sqrt(len(rs)) if len(rs) > 1 else None
        return out

    bins: dict[int, list] = {}
    for r in rows:
        for o, c in zip(outcomes, sources["model"]):
            bins.setdefault(min(int(r[c] * 10), 9), []).append((r[c], o == r[target]))
    calibration = [{"min_p": b / 10, "max_p": (b + 1) / 10, "n": len(v), "mean_predicted": sum(p for p, _ in v) / len(v),
                    "observed": sum(h for _, h in v) / len(v)} for b, v in sorted(bins.items())]
    leagues = sorted({r["league"] for r in rows})
    return {**scores(rows), "calibration": calibration,
            "by_league": [{"league": lg, **scores([r for r in rows if r["league"] == lg])} for lg in leagues]}


@pytest.mark.parametrize("strategy", ["1x2", "ou25"])
def test_probability_report_accuracy_fields(client, ledger_dir, strategy):
    got = client.get(f"{S}/{strategy}/results/probability-report").json()
    want = expected_report(ledger_dir, strategy)

    def check(g: dict, w: dict) -> None:
        assert g["n"] == w["n"] and g["model_closer_rate"] == pytest.approx(w["model_closer_rate"])
        assert g["log_loss_gap_se"] == pytest.approx(w["log_loss_gap_se"])
        for gr, wr in zip(g["rows"], w["rows"], strict=True):
            assert gr["source"] == wr["source"]
            assert (gr["favourite_hit_rate"], gr["log_loss"]) == pytest.approx((wr["favourite_hit_rate"], wr["log_loss"]))

    check(got, want)
    if strategy == "ou25":     # the fixture has a 0.5/0.5 closing line: a tie counts half
        assert want["rows"][2]["favourite_hit_rate"] * want["n"] % 1 == pytest.approx(0.5)
    assert got["calibration"] == [pytest.approx(b) for b in want["calibration"]]
    assert sum(b["n"] for b in got["calibration"]) == got["n"] * len(LEDGER_COLS[strategy][0])
    assert [lg["league"] for lg in got["by_league"]] == [lg["league"] for lg in want["by_league"]]
    assert sum(lg["n"] for lg in got["by_league"]) == got["n"]
    for g, w in zip(got["by_league"], want["by_league"]):
        check(g, w)


def test_probability_report_without_settled_matches(make_client, tmp_path):
    rep = make_client(ledgers=CsvLedgers(tmp_path)).get(f"{S}/1x2/results/probability-report").json()
    assert rep == {"n": 0, "model_closer_rate": None, "log_loss_gap_se": None, "calibration": [], "by_league": [],
                   "rows": [{"source": s, "log_loss": None, "brier": None, "favourite_hit_rate": None}
                            for s in ("model", "market_pre_match", "market_closing")]}


# --------------------------------------------------------------------------- history

LEDGER_COLS = {"1x2": ("HDA", "hda", "result"), "ou25": ("OU", "ou", "ou")}     # outcomes, suffixes, result column


def expected_fixtures(ledger_dir, strategy: str, book: str, min_edge: float) -> list[dict]:
    """The settled fixtures and their bets, worked out from the ledger file row by row (no model.backtest)."""
    outcomes, suffixes, target = LEDGER_COLS[strategy]
    num = lambda x: None if pd.isna(x) else float(x)                            # noqa: E731
    rows = []
    for r in pd.read_csv(ledger_dir / f"ledger_{strategy}.csv", parse_dates=["date"]).to_dict("records"):
        if pd.isna(r.get(target)):
            continue
        best = None
        for o, s in zip(outcomes, suffixes):
            odds = r[f"{book}_{s}"]
            if pd.isna(odds) or odds > 10:
                continue
            edge = r[f"p_{o}"] * odds - 1
            if best is None or edge > best["edge"]:
                best = {"pick": o, "odds": odds, "edge": edge, "p_close": num(r.get(f"close_{s}"))}
        bet = None
        if best is not None and best["edge"] > min_edge:
            p_close = best.pop("p_close")
            bet = {**best, "stake": 1.0, "profit": best["odds"] - 1 if best["pick"] == r[target] else -1.0,
                   "clv": None if p_close is None else best["odds"] * p_close - 1}
        rows.append({
            "date": r["date"].date().isoformat(), "league": r["league"], "home": r["home"], "away": r["away"],
            "result": r[target],
            "bet_status": "no_bet" if bet is None else "won" if bet["pick"] == r[target] else "lost",
            "outcomes": [{"outcome": o, "p_model": r[f"p_{o}"], "p_market": r[f"mkt_{s}"],
                          "p_close": num(r.get(f"close_{s}"))} for o, s in zip(outcomes, suffixes)],
            "bet": bet,
            "score": None if pd.isna(r["hg"]) else {"home": int(r["hg"]), "away": int(r["ag"])},
        })
    return rows


def match_key(r: dict) -> tuple:
    return r["date"], r["league"], r["home"], r["away"]


@pytest.mark.parametrize("strategy, settled", [("ou25", 48), ("1x2", 40)])
def test_settled_fixtures_match_the_ledger(client, ledger_dir, strategy, settled):
    want = {match_key(r): r for r in expected_fixtures(ledger_dir, strategy, "max", 0.0)}
    got = all_items(client, f"{S}/{strategy}/results/fixtures?book=max&min_edge=0")
    assert len(got) == len(want) == settled                     # one row per settled fixture, bet or not
    assert {g["bet_status"] for g in got} == {"won", "lost", "no_bet"}
    for g in got:
        w = want[match_key(g)]
        assert (g["result"], g["bet_status"]) == (w["result"], w["bet_status"])
        assert g["outcomes"] == [pytest.approx(o) for o in w["outcomes"]]
        assert g["bet"] == (None if w["bet"] is None else pytest.approx(w["bet"]))
        assert g["score"] == w["score"]
    assert {g["score"] is None for g in got} == {True, False}  # two settled rows predate the score
    assert [g["date"] for g in got] == sorted((g["date"] for g in got), reverse=True)     # default: -date


def test_settled_fixtures_follow_book_and_min_edge(client, ledger_dir):
    want = {match_key(r): r["bet"] for r in expected_fixtures(ledger_dir, "1x2", "avg", 0.01)}
    got = all_items(client, f"{S}/1x2/results/fixtures?min_edge=0.01")
    assert sorted(map(match_key, got)) == sorted(want)
    assert 0 < sum(g["bet"] is not None for g in got) < 25                 # fewer bets than at max / 0
    assert all(g["bet"] == (None if want[match_key(g)] is None else pytest.approx(want[match_key(g)])) for g in got)


def test_settled_fixtures_filters(client, ledger_dir):
    rows = expected_fixtures(ledger_dir, "1x2", "max", 0.0)
    url = f"{S}/1x2/results/fixtures?book=max&min_edge=0"
    league, bet = rows[0]["league"], next(r for r in rows if r["bet"] is not None)
    days = sorted({r["date"] for r in rows})
    lo, hi = days[1], days[-2]

    def team(part: str):
        return lambda r: part.lower() in r["home"].lower() or part.lower() in r["away"].lower()

    home, away = rows[0]["home"][1:-1], rows[0]["away"][1:-1]
    cases = {
        f"league={league}": lambda r: r["league"] == league,
        f"league={league}&league=E0": lambda r: r["league"] in (league, "E0"),
        f"team={quote(home.upper())}": team(home),                               # any case, home ...
        f"team={quote(away.upper())}": team(away),                               # ... or away
        f"date_from={lo}&date_to={hi}": lambda r: lo <= r["date"] <= hi,         # both inclusive
        f"date_from={hi}": lambda r: r["date"] >= hi,
        "bet_status=no_bet": lambda r: r["bet"] is None,
        "bet_status=won&bet_status=lost": lambda r: r["bet"] is not None,
        f"league={bet['league']}&date_from={bet['date']}&bet_status={bet['bet_status']}":
            lambda r: (r["league"], r["bet_status"]) == (bet["league"], bet["bet_status"]) and r["date"] >= bet["date"],
    }
    for query, keep in cases.items():
        want = [r for r in rows if keep(r)]
        assert 0 < len(want) < len(rows), query                 # the filter keeps some rows and drops some
        got = all_items(client, f"{url}&{query}")
        assert sorted(map(match_key, got)) == sorted(map(match_key, want)), query

    body = client.get(f"{url}&date_from={hi}&date_to={lo}").json()               # reversed range: nothing, no error
    assert body["items"] == [] and body["total"] == 0


def test_settled_fixtures_sort_puts_no_bet_last(client):
    for sort in ("profit", "-profit", "-edge", "clv"):
        got = all_items(client, f"{S}/1x2/results/fixtures?book=max&min_edge=0&sort={sort}")
        field = sort.lstrip("-")
        values = [g["bet"][field] for g in got if g["bet"] is not None and g["bet"][field] is not None]
        assert values == sorted(values, reverse=sort.startswith("-")), sort
        missing = [g["bet"] is None or g["bet"][field] is None for g in got]
        assert True in missing and missing == sorted(missing), sort              # all the missing ones at the end


def test_settled_fixtures_pages(client):
    url = f"{S}/1x2/results/fixtures?book=max&min_edge=0&bet_status=no_bet"
    everything = all_items(client, url)
    page = client.get(f"{url}&limit=5&offset=5").json()
    assert page["total"] == len(everything) > 10 and page["items"] == everything[5:10]


@pytest.mark.parametrize("query, parameter", [
    ("bet_status=zz", "bet_status"), ("date_from=2026-13-01", "date_from"), ("date_to=yesterday", "date_to"),
    ("date_from=0", "date_from"), ("date_to=2026-11-01T00:00:00", "date_to"), ("date_from=20261101", "date_from"),
    ("team=", "team"), ("team=" + "x" * 65, "team"), ("league=XX", "league"), ("sort=league", "sort"),
    ("min_edge=0.51", "min_edge"), ("limit=501", "limit"),
])
def test_settled_fixtures_invalid_query(client, query, parameter):
    body = problem(client.get(f"{S}/1x2/results/fixtures?{query}"), 422, "validation_failed")
    assert [e["parameter"] for e in body["errors"]] == [parameter]


@pytest.mark.parametrize("query, parameter", [
    ("date_from=2026-13-01", "date_from"), ("date_to=0", "date_to"), ("team=" + "x" * 65, "team"),
    ("league=XX", "league"),
])
def test_results_summary_invalid_filter(client, query, parameter):
    body = problem(client.get(f"{S}/1x2/results/summary?{query}"), 422, "validation_failed")
    assert [e["parameter"] for e in body["errors"]] == [parameter]


def expected_buckets(bets: list[dict], min_edge: float) -> list[tuple]:
    """(min_edge, max_edge, bets, profit) per non-empty bucket: the sweep's thresholds above `min_edge`."""
    bounds = [min_edge, *(e for e in SWEEP_EDGES if e > min_edge), None]
    buckets = []
    for a, b in zip(bounds, bounds[1:]):
        mine = [x for x in bets if x["edge"] > a and (b is None or x["edge"] <= b)]
        if mine:
            buckets.append((a, b, len(mine), pytest.approx(sum(x["profit"] for x in mine))))
    return buckets


def test_results_summary_edge_buckets_start_at_min_edge(client, ledger_dir):
    bets = [r["bet"] for r in expected_fixtures(ledger_dir, "1x2", "max", 0.05) if r["bet"] is not None]
    s = client.get(f"{S}/1x2/results/summary?book=max&min_edge=0.05").json()
    assert s["by_edge"][0]["min_edge"] == 0.05 and s["by_edge"][0]["max_edge"] == 0.06     # not (0.04, 0.06]
    assert [(x["min_edge"], x["max_edge"], x["bets"], x["profit"]) for x in s["by_edge"]] == expected_buckets(bets, 0.05)
    assert len(s["by_edge"]) > 1


@pytest.mark.parametrize("strategy", ["ou25", "1x2"])
def test_results_summary_filters_and_breakdowns(client, ledger_dir, strategy):
    rows = expected_fixtures(ledger_dir, strategy, "max", 0.0)
    days = sorted({r["date"] for r in rows})
    with_bet = [r["league"] for r in rows if r["bet"] is not None]
    lo, league = days[1], max(with_bet, key=with_bet.count)
    cases = {
        "": lambda r: True,
        f"&date_from={lo}": lambda r: r["date"] >= lo,
        f"&league={league}&date_to={days[-2]}": lambda r: r["league"] == league and r["date"] <= days[-2],
    }
    for query, keep in cases.items():
        kept = [r for r in rows if keep(r)]
        bets = [(r["league"], r["bet"]) for r in kept if r["bet"] is not None]
        assert bets, query
        s = client.get(f"{S}/{strategy}/results/summary?book=max&min_edge=0{query}").json()
        m = s["metrics"]
        assert s["settled_matches"] == len(kept) and m["bets"] == len(bets) == len(s["series"]), query
        profit = sum(b["profit"] for _, b in bets)
        assert m["profit"] == pytest.approx(profit) and m["roi"] == pytest.approx(profit / len(bets))
        assert m["hit_rate"] == pytest.approx(sum(b["profit"] > 0 for _, b in bets) / len(bets))

        leagues = sorted({lg for lg, _ in bets})
        assert [x["league"] for x in s["by_league"]] == leagues
        for x in s["by_league"]:
            mine = [b for lg, b in bets if lg == x["league"]]
            assert x["bets"] == len(mine) and x["profit"] == pytest.approx(sum(b["profit"] for b in mine))
            assert x["avg_odds"] == pytest.approx(sum(b["odds"] for b in mine) / len(mine))

        buckets = expected_buckets([b for _, b in bets], 0.0)
        assert [(x["min_edge"], x["max_edge"], x["bets"], x["profit"]) for x in s["by_edge"]] == buckets
        assert sum(x["bets"] for x in s["by_edge"]) == m["bets"] == sum(x["bets"] for x in s["by_league"])
    assert len(buckets) > 1


def test_results_summary_open_ended_edge_bucket(make_client, ledger_dir, tmp_path):
    ledger = pd.read_csv(ledger_dir / "ledger_1x2.csv")
    ledger[["avg_h", "avg_d", "avg_a"]] *= 1.3                  # the fixture ledgers have no edge above 15%
    ledger.to_csv(tmp_path / "ledger_1x2.csv", index=False)
    edges = [r["bet"]["edge"] for r in expected_fixtures(tmp_path, "1x2", "avg", 0.0) if r["bet"] is not None]
    s = make_client(ledgers=CsvLedgers(tmp_path)).get(f"{S}/1x2/results/summary?min_edge=0").json()
    last = s["by_edge"][-1]
    assert (last["min_edge"], last["max_edge"]) == (0.15, None)
    assert last["bets"] == sum(e > 0.15 for e in edges) > 0
    assert sum(x["bets"] for x in s["by_edge"]) == s["metrics"]["bets"] == len(edges)


def test_results_summary_with_nothing_left(client):
    s = client.get(f"{S}/1x2/results/summary?team=zzzz-no-such-team").json()
    assert s == {"settled_matches": 0, "metrics": None, "series": [], "by_league": [], "by_edge": []}


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
