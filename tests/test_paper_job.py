"""The scheduled paper job must survive Understat being unreachable and one strategy crashing."""
import os
import sys
import time

import pandas as pd
import pytest
import requests

import model.paper
from model import xg


class _NotJson:
    status_code = 200

    def json(self):
        raise requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0)


@pytest.mark.parametrize("fail", [requests.exceptions.Timeout("timed out"), _NotJson()])
def test_xg_download_skips_unreachable_understat(tmp_path, monkeypatch, fail):
    calls = []

    def get(url, **kw):
        calls.append(url)
        if isinstance(fail, Exception):
            raise fail
        return fail

    monkeypatch.setattr(xg, "XG_DIR", tmp_path)
    monkeypatch.setattr(xg.requests, "get", get)
    xg.download([2024, 2025])
    assert len(calls) == 1  # blocked once means blocked: don't burn the job timeout on the rest
    assert not list(tmp_path.iterdir())


def test_paper_main_runs_every_strategy_then_fails(monkeypatch):
    ran = []

    def settle(name):
        ran.append(name)
        if name == "ou25":
            raise RuntimeError("boom")

    monkeypatch.setattr(model.paper, "settle", settle)
    monkeypatch.setattr(sys, "argv", ["paper", "settle"])
    with pytest.raises(SystemExit) as exc:
        model.paper.main()
    assert ran == list(model.paper.STRATEGIES)
    assert exc.value.code == "failed: ou25"  # sys.exit(str): message to stderr, exit status 1


def test_settle_fills_scores_and_backfills_settled_rows(tmp_path, monkeypatch):
    day = lambda n: pd.Timestamp.today().normalize() + pd.Timedelta(days=n)    # noqa: E731
    close = {"close_h": 0.5, "close_d": 0.3, "close_a": 0.2}
    rows = [  # home, date, result, closing odds already in the ledger
        ("Future", day(3), None, {}),                   # first, so ledger and merge indexes differ
        ("Open", day(-2), None, {}),                    # unsettled: everything comes from the season file
        ("Old", day(-model.paper.GIVE_UP.days), "D", close),  # settled before scores were kept, oldest retried
        ("Fixed", day(-2), "A", close),                 # football-data changed the result since: no score
        ("Missing", day(-2), None, {}),                 # not in the season file yet
    ]
    ledger = pd.DataFrame([{"date": d, "league": "E0", "home": h, "away": "X", "season": 2026,
                            "logged_at": day(-5), "result": r, **{c: c_ for c, c_ in cl.items()}}
                           for h, d, r, cl in rows])
    monkeypatch.setattr(model.paper, "PAPER_DIR", tmp_path)
    ledger.to_csv(model.paper.ledger_path("1x2"), index=False)
    played = pd.DataFrame([
        {"date": day(-2), "league": "E0", "home": "Open", "away": "X", "result": "H", "hg": 2.0, "ag": 0.0,
         "close_h": 0.6, "close_d": 0.25, "close_a": 0.15},
        {"date": day(-model.paper.GIVE_UP.days), "league": "E0", "home": "Old", "away": "X", "result": "D",
         "hg": 1.0, "ag": 1.0,
         "close_h": 0.1, "close_d": 0.1, "close_a": 0.8},
        {"date": day(-2), "league": "E0", "home": "Fixed", "away": "X", "result": "H", "hg": 2.0, "ag": 1.0,
         "close_h": 0.1, "close_d": 0.1, "close_a": 0.8},
    ])
    monkeypatch.setattr(model.paper.data, "download", lambda leagues, seasons: None)
    monkeypatch.setattr(model.paper.data, "load", lambda leagues, seasons: played)
    monkeypatch.setattr(model.paper, "FeatureBuilder", lambda: type("B", (), {"build": staticmethod(lambda df: df)}))
    monkeypatch.setattr(model.paper, "add_market", lambda df: df)

    model.paper.settle("1x2")
    got = model.paper.read_ledger("1x2").set_index("home")
    want = {  # result, hg, ag, close_h
        "Open": ("H", 2, 0, 0.6), "Old": ("D", 1, 1, 0.5), "Fixed": ("A", None, None, 0.5),
        "Missing": (None, None, None, None), "Future": (None, None, None, None),
    }
    for home, w in want.items():
        g = got.loc[home, ["result", "hg", "ag", "close_h"]]
        assert [None if pd.isna(v) else v for v in g] == list(w), home


def test_settle_gives_up_on_fixtures_football_data_never_scores(tmp_path, monkeypatch, capsys):
    """Postponed (match gone) or result changed since settling (no score ever): retried for GIVE_UP, then left."""
    old = pd.Timestamp.today().normalize() - model.paper.GIVE_UP - pd.Timedelta(days=1)
    ledger = pd.DataFrame([
        {"date": old, "league": "SP2", "home": "Gone", "away": "X", "season": 2026, "logged_at": old,
         "result": None, "close_h": None, "close_d": None, "close_a": None, "hg": None, "ag": None},
        {"date": old, "league": "SP2", "home": "Fixed", "away": "X", "season": 2026, "logged_at": old,
         "result": "A", "close_h": 0.5, "close_d": 0.3, "close_a": 0.2, "hg": None, "ag": None},
    ])
    monkeypatch.setattr(model.paper, "PAPER_DIR", tmp_path)
    ledger.to_csv(model.paper.ledger_path("1x2"), index=False)
    monkeypatch.setattr(model.paper.data, "download", lambda *a: pytest.fail("season files reloaded"))

    model.paper.settle("1x2")
    assert "nothing to settle" in capsys.readouterr().out


def test_settle_a_ledger_with_no_result_yet(tmp_path, monkeypatch):
    """Every `result` empty in the CSV reads as float: settling must still store the first one."""
    day = pd.Timestamp.today().normalize() - pd.Timedelta(days=2)
    ledger = pd.DataFrame([{"date": day, "league": "E0", "home": "Open", "away": "X", "season": 2026, "logged_at": day,
                            "result": None}])
    monkeypatch.setattr(model.paper, "PAPER_DIR", tmp_path)
    ledger.to_csv(model.paper.ledger_path("1x2"), index=False)
    _no_feature_build(monkeypatch, pd.DataFrame([
        {"date": day, "league": "E0", "home": "Open", "away": "X", "result": "H", "hg": 2.0, "ag": 0.0,
         "close_h": 0.6, "close_d": 0.25, "close_a": 0.15}]))

    model.paper.settle("1x2")
    got = model.paper.read_ledger("1x2").iloc[0]
    assert (got.result, got.hg, got.ag) == ("H", 2, 0)


def _no_feature_build(monkeypatch, played: pd.DataFrame) -> None:
    monkeypatch.setattr(model.paper.data, "download", lambda leagues, seasons: None)
    monkeypatch.setattr(model.paper.data, "load", lambda leagues, seasons: played)
    monkeypatch.setattr(model.paper, "FeatureBuilder", lambda: type("B", (), {"build": staticmethod(lambda df: df)}))
    monkeypatch.setattr(model.paper, "add_market", lambda df: df)


def test_settle_finds_a_game_moved_by_a_day(tmp_path, monkeypatch):
    """fixtures.csv keeps a moved game's old date; the season file lists the day it was played."""
    day = lambda n: pd.Timestamp.today().normalize() + pd.Timedelta(days=n)    # noqa: E731
    ledger = pd.DataFrame([{"date": d, "league": "SP2", "home": h, "away": "X", "season": 2026, "logged_at": day(-6),
                            "result": r} for h, d, r in (("Moved", day(-3), None), ("Far", day(-4), None),
                                                         ("Exact", day(-2), None), ("Done", day(-9), "A"))])
    monkeypatch.setattr(model.paper, "PAPER_DIR", tmp_path)
    ledger.to_csv(model.paper.ledger_path("1x2"), index=False)
    row = lambda h, d, hg, ag, r: {"date": d, "league": "SP2", "home": h, "away": "X", "result": r,   # noqa: E731
                                   "hg": hg, "ag": ag, "close_h": 0.5, "close_d": 0.3, "close_a": 0.2}
    _no_feature_build(monkeypatch, pd.DataFrame([
        row("Moved", day(-2), 1.0, 0.0, "H"), row("Far", day(-2), 0.0, 1.0, "A"),
        row("Exact", day(-3), 0.0, 0.0, "D"), row("Exact", day(-2), 2.0, 2.0, "D")]))  # the exact one wins

    model.paper.settle("1x2")
    got = model.paper.read_ledger("1x2").set_index("home")
    assert [None if pd.isna(v) else v for v in got.loc["Moved", ["date", "result", "hg"]]] == [day(-3), "H", 1]
    assert pd.isna(got.loc["Far", "result"])                    # two days off: another game
    assert got.loc["Exact", "hg"] == 2


def test_log_skips_a_logged_game_fixtures_csv_moved_by_a_day(tmp_path, monkeypatch):
    day = lambda n: pd.Timestamp.today().normalize() + pd.Timedelta(days=n)    # noqa: E731
    monkeypatch.setattr(model.paper, "PAPER_DIR", tmp_path)
    pd.DataFrame([{"date": day(1), "league": "SP2", "home": h, "away": "X", "season": 2026, "logged_at": day(-1)}
                  for h in ("Moved", "Far")]).to_csv(model.paper.ledger_path("1x2"), index=False)
    odds = {f"{b}_{s}": 2.0 for b in ("avg", "max", "b365", "ps", "bfe") for s in "hda"}
    fx = pd.DataFrame([{"date": d, "league": "SP2", "home": h, "away": "X", "season": 2026,
                        "p_H": 0.4, "p_D": 0.3, "p_A": 0.3, "mkt_h": 0.4, "mkt_d": 0.3, "mkt_a": 0.3, **odds}
                       for h, d in (("Moved", day(2)), ("Far", day(3)))]).assign(kickoff=pd.NaT)
    fx["kickoff"] = fx.kickoff.dt.tz_localize("UTC")
    monkeypatch.setattr(model.paper, "score_fixtures", lambda *a: fx)
    monkeypatch.setattr(model.paper.backtest, "find_bets", lambda *a: pd.DataFrame())

    model.paper.log("1x2")
    got = model.paper.read_ledger("1x2")
    assert sorted(zip(got.home, got.date)) == [("Far", day(1)), ("Far", day(3)), ("Moved", day(1))]


def test_load_fixtures_reads_uk_kickoff_as_utc(monkeypatch):
    csv = ("Div,Date,Time,HomeTeam,AwayTeam\n"
           "E0,14/11/2026,15:00,Winter,X\n"       # GMT
           "E0,01/08/2026,15:00,Summer,X\n"       # BST
           "E0,14/11/2026,,NoTime,X\n").encode()
    monkeypatch.setattr(model.data.requests, "get", lambda *a, **kw: type("R", (), {
        "content": csv, "raise_for_status": lambda self: None})())
    got = model.data.load_fixtures(["E0"]).set_index("home").kickoff
    assert got["Winter"] == pd.Timestamp("2026-11-14 15:00", tz="UTC")
    assert got["Summer"] == pd.Timestamp("2026-08-01 14:00", tz="UTC")
    assert pd.isna(got["NoTime"])


def test_load_fixtures_without_any_kickoff_time(monkeypatch):
    csv = b"Div,Date,Time,HomeTeam,AwayTeam\nE0,14/11/2026,,A,X\n"     # an all-blank column reads as float
    monkeypatch.setattr(model.data.requests, "get", lambda *a, **kw: type("R", (), {
        "content": csv, "raise_for_status": lambda self: None})())
    assert model.data.load_fixtures(["E0"]).kickoff.isna().all()


def test_log_skips_games_already_started(tmp_path, monkeypatch):
    """A prediction logged after kick-off isn't a prediction: the database refuses it too."""
    now = pd.Timestamp.now(tz="UTC")
    monkeypatch.setattr(model.paper, "PAPER_DIR", tmp_path)
    odds = {f"{b}_{s}": 2.0 for b in ("avg", "max", "b365", "ps", "bfe") for s in "hda"}
    fx = pd.DataFrame([{"date": now.tz_localize(None).normalize(), "league": "E0", "home": h, "away": "X",
                        "season": 2026, "kickoff": k, "p_H": 0.4, "p_D": 0.3, "p_A": 0.3,
                        "mkt_h": 0.4, "mkt_d": 0.3, "mkt_a": 0.3, **odds}
                       for h, k in (("Started", now - pd.Timedelta(minutes=1)), ("Later", now + pd.Timedelta(hours=1)),
                                    ("NoTime", pd.NaT))])
    monkeypatch.setattr(model.paper, "score_fixtures", lambda *a: fx)
    monkeypatch.setattr(model.paper.backtest, "find_bets", lambda *a: pd.DataFrame())

    tz = os.environ.get("TZ")
    os.environ["TZ"] = "Pacific/Kiritimati"   # UTC+14: logged_at is UTC whatever the machine's clock says
    time.tzset()
    try:
        model.paper.log("1x2")
    finally:
        os.environ.pop("TZ") if tz is None else os.environ.__setitem__("TZ", tz)
        time.tzset()
    got = model.paper.read_ledger("1x2")
    assert sorted(got.home) == ["Later", "NoTime"]
    assert "kickoff" not in got.columns
    assert (got.logged_at - now.tz_localize(None)).abs().max() < pd.Timedelta(minutes=1)
