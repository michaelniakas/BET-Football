"""Accuracy: the API returns exactly the numbers the current Streamlit pages show, on the same ledgers.

The pages themselves are the reference (run headless with Streamlit's AppTest), not a re-implementation,
so a bug shared by the API and a copy of the page logic cannot pass. Phase 2 deletes the pages; these
tests then switch to recorded outputs.
"""
from __future__ import annotations

import base64
import json

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import model.paper
from model.optimizer import PICK_LABELS, scenarios
from model.paper import STRATEGIES
from api.usecases import RISK_FIELDS
from tests.conftest import ROOT

KEY = ["date", "league", "home", "away"]
ATOL = 1e-12


@pytest.fixture(autouse=True)
def pages_read_fixture_ledgers(monkeypatch, ledger_dir):
    monkeypatch.setattr(model.paper, "PAPER_DIR", ledger_dir)


def run(page: str, setup) -> AppTest:
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


def chart_traces(block) -> dict[str, tuple[list, np.ndarray]]:
    """Plotly traces of the one chart in `block`: name -> (x, y). Plotly may ship y as a typed array."""
    (chart,) = block.get("plotly_chart")
    out = {}
    for t in json.loads(chart.proto.spec)["data"]:
        y = t["y"]
        if isinstance(y, dict):
            y = np.frombuffer(base64.b64decode(y["bdata"]), dtype=y["dtype"])
        out[t["name"]] = (t["x"], np.asarray(y, dtype=float))
    return out


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

@pytest.mark.parametrize("book", ["avg", "max", "b365"])
@pytest.mark.parametrize("edge", [None, 0.0, 0.03])
def test_paper_trading(client, book, edge):
    def setup(at):
        for sid in STRATEGIES:
            at.selectbox(key=f"book_{sid}").set_value(book)
            if edge is not None:
                at.slider(key=f"edge_{sid}").set_value(edge)
    at = run("1_Paper_Trading.py", setup)

    for tab, sid in zip(at.tabs, STRATEGIES):
        e = STRATEGIES[sid]["edge"] if edge is None else edge
        base, q = f"/api/v1/strategies/{sid}", f"?book={book}&min_edge={e}"

        # upcoming picks
        picks = table(tab, "pick", lacks="result")
        api = items(client, f"{base}/picks{q}")
        assert_same_rows(picks if picks is not None else pd.DataFrame(columns=KEY), api,
                         ["pick", "odds", "p_model", "edge"])

        # all predictions
        preds = table(tab, "Καλύτερο")
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
        bets = table(tab, "result")
        api = items(client, f"{base}/results/bets{q}")
        assert_same_rows(bets if bets is not None else pd.DataFrame(columns=KEY), api,
                         ["pick", "odds", "p_model", "edge", "result", "profit", "clv"])

        summary = client.get(f"{base}/results/summary{q}").json()
        shown = {m.label: m.value for m in tab.metric}
        if summary["metrics"] is None:
            assert shown == {} and not tab.get("plotly_chart")
        else:
            traces, series = chart_traces(tab), pd.DataFrame(summary["series"])
            x, profit = traces["Κέρδος (μονάδες)"]
            _, clv_pct = traces["Μέσο CLV %"]
            pd.testing.assert_series_equal(per_date_last(x, profit), per_date_last(series.date, series.cumulative_profit),
                                           check_exact=False, atol=ATOL, rtol=0)
            pd.testing.assert_series_equal(per_date_last(x, clv_pct),
                                           per_date_last(series.date, 100 * series.mean_clv.astype(float)),
                                           check_exact=False, atol=1e-9, rtol=0)
            s = summary["metrics"]
            assert shown == {"Bets": f"{s['bets']}", "Κέρδος": f"{s['profit']:+.2f} μον.",
                             "ROI": f"{100 * s['roi']:+.1f}%", "Μέσο CLV": f"{100 * s['clv']:+.2f}%",
                             "Beat close": f"{100 * s['beat_close_rate']:.0f}%"}

        report = table(tab, "log_loss")
        api = client.get(f"{base}/results/probability-report").json()
        if report is None:
            assert summary["settled_matches"] == 0
        else:
            assert list(report.n) == [api["n"]] * 3
            np.testing.assert_allclose(report[["log_loss", "brier"]].to_numpy(dtype=float),
                                       [[r["log_loss"], r["brier"]] for r in api["rows"]], rtol=0, atol=ATOL)

        sweep = table(tab, "roi_%")
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
    at = run("1_Paper_Trading.py", setup)
    for tab, sid in zip(at.tabs, STRATEGIES):
        preds = table(tab, "Καλύτερο")
        api = items(client, f"/api/v1/strategies/{sid}/predictions?book=avg&league=E0&league=SP1")
        assert len(api) and set(api.league) <= set(leagues)
        assert_same_rows(preds, api[KEY], [])


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
    at = run("2_Optimizer.py", setup)

    api = client.post("/api/v1/optimize", json={"budget": budget, "markets": markets, "min_prob": min_prob,
                                                "min_edge": min_edge, "odds": {"source": "snapshot", "book": book}}).json()
    legs = table(at, "bookmaker")
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

    summary = table(at, "Ποντάρισμα")
    titles = {sid: sc["title"] for sid, sc in scenarios(len(al)).items()}
    assert list(summary.index) == [titles[s["id"]] for s in api["scenarios"]]
    for s in api["scenarios"]:
        row = summary.loc[titles[s["id"]]]
        for greek, field in RISK_FIELDS.items():
            assert row[greek] == pytest.approx(s["summary"][field], abs=ATOL), (s["id"], field)

    for tab, s in zip(at.tabs, api["scenarios"]):
        coupons = tab.dataframe[0].value
        ac = pd.DataFrame(s["coupons"])
        text = [" + ".join(f"{al.home[i]}-{al.away[i]}: {PICK_LABELS[al.outcome[i]]}" for i in c) for c in ac.legs]
        assert list(coupons.coupon) == text
        for c in ["odds", "p_model", "p_market", "ev_model", "ev_market", "stake", "payout"]:
            np.testing.assert_allclose(coupons[c], ac[c], rtol=0, atol=ATOL, err_msg=c)
