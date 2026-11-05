"""Accuracy: the API returns exactly the numbers the Streamlit pages showed, on the same ledgers.

The reference is what the pages themselves rendered (run headless with Streamlit's AppTest), not a
re-implementation, so a bug shared by the API and a copy of the page logic cannot pass. The pages'
outputs are frozen in tests/fixtures/golden_*.json, dates relative to ANCHOR.

    RECORD_GOLDEN=1 pytest tests/test_golden.py   # re-run the pages and rewrite the JSON (needs pages/)
"""
from __future__ import annotations

import base64
import json
import os

import numpy as np
import pandas as pd
import pytest

import model.paper
from model.optimizer import PICK_LABELS, scenarios
from model.paper import STRATEGIES
from api.usecases import RISK_FIELDS
from tests.conftest import FIXTURES, ROOT, SHIFT

KEY = ["date", "league", "home", "away"]
ATOL = 1e-12
RECORD = os.environ.get("RECORD_GOLDEN") == "1"


# --------------------------------------------------------------------------- recorded page outputs

def _undate(values, sign: int) -> list[str]:
    """Dates moved between today's frame (sign -1: to ANCHOR's, for storing) and back (sign +1)."""
    return [d.isoformat() for d in pd.to_datetime(list(values)) + sign * SHIFT]


def encode_df(df: pd.DataFrame | None) -> dict | None:
    if df is None:
        return None
    df = df.copy()
    if "date" in df:
        df["date"] = _undate(df["date"], -1)
    split = df.to_dict("split")
    return json.loads(json.dumps(split, default=lambda o: o.item() if hasattr(o, "item") else str(o)))


def decode_df(d: dict | None) -> pd.DataFrame | None:
    if d is None:
        return None
    df = pd.DataFrame(d["data"], index=d["index"], columns=d["columns"])
    if "date" in df:
        df["date"] = pd.to_datetime(_undate(df["date"], +1))
    return df


class Golden:
    """One JSON file of page outputs per page, keyed by test case."""

    def __init__(self, page: str):
        self.path = FIXTURES / f"golden_{page}.json"

    def save(self, case: str, value) -> None:
        data = json.loads(self.path.read_text()) if self.path.exists() else {}
        data[case] = value
        self.path.write_text(json.dumps(data, sort_keys=True, ensure_ascii=False))

    def load(self, case: str):
        return json.loads(self.path.read_text())[case]


def run(page: str, setup):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "pages" / page), default_timeout=120).run()
    setup(at)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def table(block, has: str, lacks: str = "") -> pd.DataFrame | None:
    """The one table in `block` with column `has` (and without column `lacks`), or None."""
    found = [d.value for d in block.dataframe if has in d.value.columns and lacks not in d.value.columns]
    assert len(found) <= 1
    return found[0] if found else None


def chart_traces(block) -> dict[str, list]:
    """Plotly traces of the one chart in `block`: name -> [x, y]. Plotly may ship y as a typed array."""
    (chart,) = block.get("plotly_chart")
    out = {}
    for t in json.loads(chart.proto.spec)["data"]:
        y = t["y"]
        if isinstance(y, dict):
            y = np.frombuffer(base64.b64decode(y["bdata"]), dtype=y["dtype"])
        out[t["name"]] = [_undate(t["x"], -1), np.asarray(y, dtype=float).tolist()]
    return out


# --------------------------------------------------------------------------- comparison helpers

def per_date_last(dates, values) -> pd.Series:
    """Value at the end of each day: order-independent for cumulative series, unlike same-day points."""
    index = pd.DatetimeIndex(pd.to_datetime(list(dates)), name="date")
    return pd.Series(np.asarray(values, dtype=float), index=index).groupby(level=0).last()


def items(client, url: str) -> pd.DataFrame:
    body = client.get(url + "&limit=500").json()
    assert body["total"] == len(body["items"])
    df = pd.DataFrame(body["items"])
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def assert_same_rows(page: pd.DataFrame, api: pd.DataFrame, cols: list[str]) -> None:
    """Same set of matches, and equal values per match (numbers to 1e-12, missing == missing)."""
    page = page.reset_index(drop=True)
    assert len(page) == len(api)
    if page.empty:
        return
    page = page.assign(date=pd.to_datetime(page["date"]))
    m = page.merge(api, on=KEY, how="outer", suffixes=("_page", "_api"), indicator=True)
    assert (m._merge == "both").all(), m[m._merge != "both"][KEY]
    for c in cols:
        a, b = m[f"{c}_page"], m[f"{c}_api"]
        if pd.api.types.is_numeric_dtype(a) or pd.api.types.is_numeric_dtype(b):
            np.testing.assert_allclose(a.astype(float), b.astype(float), rtol=0, atol=ATOL, err_msg=c)
        else:
            assert a.fillna("").tolist() == b.fillna("").tolist(), c


# --------------------------------------------------------------------------- Paper Trading

@pytest.fixture(autouse=True)
def pages_read_fixture_ledgers(monkeypatch, ledger_dir):
    monkeypatch.setattr(model.paper, "PAPER_DIR", ledger_dir)


def paper_tabs(case: str, setup) -> list[dict]:
    """Per strategy tab: its tables, metrics and chart, as recorded from the Paper Trading page."""
    golden = Golden("paper_trading")
    if RECORD:
        at = run("1_Paper_Trading.py", setup)
        golden.save(case, [{
            "picks": encode_df(table(tab, "pick", lacks="result")),
            "preds": encode_df(table(tab, "Καλύτερο")),
            "bets": encode_df(table(tab, "result")),
            "metrics": {m.label: m.value for m in tab.metric},
            "traces": chart_traces(tab) if tab.get("plotly_chart") else None,
            "report": encode_df(table(tab, "log_loss")),
            "sweep": encode_df(table(tab, "roi_%")),
        } for tab in at.tabs])
    tabs = golden.load(case)
    assert len(tabs) == len(STRATEGIES)
    return [{k: decode_df(v) if k not in ("metrics", "traces") else v for k, v in tab.items()} for tab in tabs]


@pytest.mark.parametrize("book", ["avg", "max", "b365"])
@pytest.mark.parametrize("edge", [None, 0.0, 0.03])
def test_paper_trading(client, book, edge):
    def setup(at):
        for sid in STRATEGIES:
            at.selectbox(key=f"book_{sid}").set_value(book)
            if edge is not None:
                at.slider(key=f"edge_{sid}").set_value(edge)
    tabs = paper_tabs(f"{book}|{edge}", setup)

    for tab, sid in zip(tabs, STRATEGIES):
        e = STRATEGIES[sid]["edge"] if edge is None else edge
        base, q = f"/api/v1/strategies/{sid}", f"?book={book}&min_edge={e}"

        # upcoming picks
        picks = tab["picks"]
        api = items(client, f"{base}/picks{q}")
        assert_same_rows(picks if picks is not None else pd.DataFrame(columns=KEY), api,
                         ["pick", "odds", "p_model", "edge"])

        # all predictions
        preds = tab["preds"]
        api = items(client, f"{base}/predictions?book={book}")
        flat = api[KEY].copy()
        for i, row in api.iterrows():
            for o in row.outcomes:
                flat.loc[i, f"Μοντέλο {o['outcome']}"] = o["p_model"]
                flat.loc[i, f"Αγορά {o['outcome']}"] = o["p_market"]
            flat.loc[i, "Καλύτερο"] = row.best["outcome"] if row.best else None
            flat.loc[i, "Edge"] = row.best["edge"] if row.best else np.nan
        assert_same_rows(preds, flat, [c for c in preds.columns if c not in KEY])

        # settled picks, metrics, probability report, edge sweep
        bets = tab["bets"]
        api = items(client, f"{base}/results/bets{q}")
        assert_same_rows(bets if bets is not None else pd.DataFrame(columns=KEY), api,
                         ["pick", "odds", "p_model", "edge", "result", "profit", "clv"])

        summary = client.get(f"{base}/results/summary{q}").json()
        shown = tab["metrics"]
        if summary["metrics"] is None:
            assert shown == {} and tab["traces"] is None
        else:
            traces, series = tab["traces"], pd.DataFrame(summary["series"])
            x, profit = traces["Κέρδος (μονάδες)"]
            _, clv_pct = traces["Μέσο CLV %"]
            x = _undate(x, +1)
            pd.testing.assert_series_equal(per_date_last(x, profit), per_date_last(series.date, series.cumulative_profit),
                                           check_exact=False, atol=ATOL, rtol=0)
            pd.testing.assert_series_equal(per_date_last(x, clv_pct),
                                           per_date_last(series.date, 100 * series.mean_clv.astype(float)),
                                           check_exact=False, atol=1e-9, rtol=0)
            s = summary["metrics"]
            assert shown == {"Bets": f"{s['bets']}", "Κέρδος": f"{s['profit']:+.2f} μον.",
                             "ROI": f"{100 * s['roi']:+.1f}%", "Μέσο CLV": f"{100 * s['clv']:+.2f}%",
                             "Beat close": f"{100 * s['beat_close_rate']:.0f}%"}

        report = tab["report"]
        api = client.get(f"{base}/results/probability-report").json()
        if report is None:
            assert summary["settled_matches"] == 0
        else:
            assert list(report.n) == [api["n"]] * 3
            np.testing.assert_allclose(report[["log_loss", "brier"]].to_numpy(dtype=float),
                                       [[r["log_loss"], r["brier"]] for r in api["rows"]], rtol=0, atol=ATOL)

        sweep = tab["sweep"]
        api = client.get(f"{base}/results/edge-sweep?book={book}").json()["rows"]
        if sweep is None:
            assert api == []
        else:
            assert list(sweep.index) == [f">{r['min_edge']:.0%}" for r in api]
            pct = {"roi_%": "roi", "roi_se_%": "roi_se", "hit_%": "hit_rate", "clv_%": "clv",
                   "beat_close_%": "beat_close_rate"}
            for col in sweep.columns:
                field = pct.get(col, col)
                expected = [np.nan if r[field] is None else r[field] * (100 if col in pct else 1) for r in api]
                np.testing.assert_allclose(sweep[col].astype(float), expected, rtol=0, atol=1e-9, err_msg=col)


def test_paper_trading_league_filter(client):
    leagues = ["E0", "SP1"]
    def setup(at):
        for sid in STRATEGIES:
            at.multiselect(key=f"leagues_{sid}").set_value(leagues)
    tabs = paper_tabs("leagues=E0,SP1", setup)
    for tab, sid in zip(tabs, STRATEGIES):
        api = items(client, f"/api/v1/strategies/{sid}/predictions?book=avg&league=E0&league=SP1")
        assert len(api) and set(api.league) <= set(leagues)
        assert_same_rows(tab["preds"], api[KEY], [])


# --------------------------------------------------------------------------- Optimizer (snapshot odds)

@pytest.mark.parametrize("book", ["avg", "max", "b365"])
@pytest.mark.parametrize("budget, min_prob, min_edge, markets", [
    (50.0, 0.35, 0.0, ["ou25", "1x2"]),
    (500.0, 0.30, -0.05, ["ou25", "1x2"]),
    (20.0, 0.50, 0.02, ["1x2"]),
    (75.0, 0.25, -0.10, ["ou25"]),
    (50.0, 0.80, 0.15, ["ou25", "1x2"]),
])
def test_optimizer(client, book, budget, min_prob, min_edge, markets):
    def setup(at):
        at.number_input[0].set_value(budget)
        at.multiselect[0].set_value(markets)
        at.slider[0].set_value(min_prob)
        at.slider[1].set_value(min_edge)
        at.selectbox[0].set_value(book)
    golden, case = Golden("optimizer"), f"{book}|{budget}|{min_prob}|{min_edge}|{','.join(markets)}"
    if RECORD:
        at = run("2_Optimizer.py", setup)
        golden.save(case, {"legs": encode_df(table(at, "bookmaker")), "summary": encode_df(table(at, "Ποντάρισμα")),
                           "coupons": [encode_df(tab.dataframe[0].value) for tab in at.tabs]})
    page = golden.load(case)

    api = client.post("/api/v1/optimize", json={"budget": budget, "markets": markets, "min_prob": min_prob,
                                                "min_edge": min_edge, "odds": {"source": "snapshot", "book": book}}).json()
    legs = decode_df(page["legs"])
    if legs is None:
        assert api["legs"] == []
        return
    al = pd.DataFrame(api["legs"])
    assert len(legs) == len(al)
    assert list(legs.pick) == [PICK_LABELS[o] for o in al.outcome]
    assert list(pd.to_datetime(legs.date)) == list(pd.to_datetime(al.date))
    assert list(legs.home) == list(al.home) and list(legs.bookmaker) == list(al.bookmaker)
    for c in ["odds", "p_model", "p_market", "edge"]:
        np.testing.assert_allclose(legs[c], al[c], rtol=0, atol=ATOL, err_msg=c)

    summary = decode_df(page["summary"])
    titles = {sid: sc["title"] for sid, sc in scenarios(len(al)).items()}
    assert list(summary.index) == [titles[s["id"]] for s in api["scenarios"]]
    for s in api["scenarios"]:
        row = summary.loc[titles[s["id"]]]
        for greek, field in RISK_FIELDS.items():
            assert row[greek] == pytest.approx(s["summary"][field], abs=ATOL), (s["id"], field)

    assert len(page["coupons"]) == len(api["scenarios"])
    for coupons, s in zip(map(decode_df, page["coupons"]), api["scenarios"]):
        ac = pd.DataFrame(s["coupons"])
        text = [" + ".join(f"{al.home[i]}-{al.away[i]}: {PICK_LABELS[al.outcome[i]]}" for i in c) for c in ac.legs]
        assert list(coupons.coupon) == text
        for c in ["odds", "p_model", "p_market", "ev_model", "ev_market", "stake", "payout"]:
            np.testing.assert_allclose(coupons[c], ac[c], rtol=0, atol=ATOL, err_msg=c)
