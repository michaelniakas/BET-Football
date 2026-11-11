"""No API calls. Run: python -m pytest research/test_intl.py"""
import numpy as np
import pandas as pd
import pytest

from model import backtest
from research.intl import EDGE, log_loss, match, priced

FX = [
    {"event_id": "1", "kickoff": pd.Timestamp("2026-11-12 17:00", tz="UTC"), "home": "Türkiye", "away": "Belgium"},
    {"event_id": "2", "kickoff": pd.Timestamp("2026-11-12 19:45", tz="UTC"), "home": "Czechia", "away": "Spain"},
    {"event_id": "3", "kickoff": pd.Timestamp("2026-11-14 19:45", tz="UTC"), "home": "Northern Ireland", "away": "Ukraine"},
]


def test_match_national_team_names_and_kickoff():
    events = pd.DataFrame([
        ("2026-11-12T17:00:00Z", "Turkey", "Belgium"),
        ("2026-11-12T19:45:00Z", "Czech Republic", "Spain"),
        ("2026-11-14T19:45:00Z", "Republic of Ireland", "Austria"),  # shares "Ireland", nothing else
        ("2026-11-20T19:45:00Z", "Northern Ireland", "Ukraine"),      # another day
    ], columns=["kickoff", "api_home", "api_away"])
    assert match(events, FX) == {("2026-11-12T17:00:00Z", "Turkey", "Belgium"): "1",
                                 ("2026-11-12T19:45:00Z", "Czech Republic", "Spain"): "2"}


def odds_rows(event_id, fetched_at, h, d, a, book="b1"):
    return [{"event_id": event_id, "kickoff": "x", "bookmaker": book, "outcome": o, "odds": p, "fetched_at": fetched_at}
            for o, p in zip("HDA", (h, d, a))]


def test_an_espn_game_claimed_by_two_events_gets_no_price():
    events = pd.DataFrame([("2026-11-12T17:00:00Z", "Turkey", "Belgium"), ("2026-11-12T18:00:00Z", "Turkiye", "Belgium")],
                          columns=["kickoff", "api_home", "api_away"])
    assert match(events, FX) == {}


def test_bets_use_the_last_snapshot_before_the_research():
    news = pd.DataFrame([
        {"event_id": "1", "date": "2026-11-12", "league": "UNL", "home": "Türkiye", "away": "Belgium",
         "kickoff": "2026-11-12T17:00:00+00:00", "researched_at": "2026-11-12T16:50:00+00:00",
         "p_H": 0.6, "p_D": 0.25, "p_A": 0.15},
        {"event_id": "2", "date": "2026-11-12", "league": "UNL", "home": "Czechia", "away": "Spain",
         "kickoff": "2026-11-12T19:45:00+00:00", "researched_at": "2026-11-12T08:00:00+00:00",
         "p_H": 0.2, "p_D": 0.3, "p_A": 0.5},
    ])
    odds = pd.DataFrame(
        odds_rows("1", "2026-11-12T14:00:00+00:00", 1.5, 4.0, 7.0)          # older
        + odds_rows("1", "2026-11-12T16:00:00+00:00", 2.0, 3.6, 4.0)        # last before the research: this one
        + odds_rows("1", "2026-11-12T16:00:00+00:00", 2.2, 3.4, 3.8, "b2")
        + odds_rows("1", "2026-11-12T16:55:00+00:00", 9.0, 9.0, 9.0)        # nearer, but after the research
        + odds_rows("1", "2026-11-12T17:05:00+00:00", 9.0, 9.0, 9.0)        # after kick-off
        + odds_rows("2", "2026-11-12T07:30:00+00:00", 4.5, 3.2, 1.6))
    df = priced(news, odds, {"1": "H"}).set_index("event_id")
    assert df.loc["1", "avg_h"] == pytest.approx(2.1) and df.loc["1", "max_h"] == 2.2
    assert df.loc[["1", "2"], ["mkt_h", "mkt_d", "mkt_a"]].sum(axis=1).tolist() == pytest.approx([1, 1])
    assert np.isnan(df.loc["2", "result"])

    bets = backtest.find_bets(df.reset_index(), "avg", EDGE).set_index("home")
    assert bets.loc["Türkiye", "pick"] == "H" and bets.loc["Türkiye", "edge"] == pytest.approx(0.6 * 2.1 - 1)
    assert bets.loc["Türkiye", "profit"] == pytest.approx(1.1)
    assert "Czechia" not in bets.index  # every p x price - 1 < 0: no value anywhere
    assert log_loss(df.loc[["1"]], ["p_H", "p_D", "p_A"]) == pytest.approx(-np.log(0.6))
    assert priced(news, odds[odds.fetched_at > "2026-11-12T16:56"], {}).empty  # only later prices: none usable
