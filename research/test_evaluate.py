"""No network. Run: python -m pytest research/test_evaluate.py"""
import math

import pandas as pd
import pytest

from research.evaluate import compare, join_settled, unjoined

EDGE = 0.05


def news(**kw):
    return pd.DataFrame([{"date": "2026-11-14", "league": "SP2", "home": "A", "away": "B", "event_id": 1,
                          "kickoff": "2026-11-14T15:00:00+00:00", "researched_at": "2026-11-12T08:00:00+00:00",
                          "days_to_kickoff": 2.29, "p_H": 0.5, "p_D": 0.3, "p_A": 0.2} | kw])


def ledger(**kw):
    return pd.DataFrame([{"date": pd.Timestamp("2026-11-14"), "league": "SP2", "home": "A", "away": "B", "season": 2026,
                          "p_H": 0.4, "p_D": 0.3, "p_A": 0.3, "mkt_h": 0.38, "mkt_d": 0.3, "mkt_a": 0.32,
                          "avg_h": 2.5, "avg_d": 3.0, "avg_a": 3.0, "result": "H",
                          "close_h": 0.45, "close_d": 0.3, "close_a": 0.25} | kw])


def test_news_scored_beside_the_model_on_the_same_row():
    scores, _ = compare(join_settled(news(), ledger()), EDGE)
    assert scores.loc["news", "log_loss"] == pytest.approx(-math.log(0.5))
    assert scores.loc["news", "brier"] == pytest.approx(0.25 + 0.09 + 0.04)
    assert scores.loc["model", "log_loss"] == pytest.approx(-math.log(0.4))
    assert scores.loc["market closing", "log_loss"] == pytest.approx(-math.log(0.45))
    assert scores.loc["news", "n"] == 1


def test_each_source_picks_with_its_own_probabilities():
    _, picks = compare(join_settled(news(), ledger()), EDGE)
    assert picks.loc["news", "clv_%"] == pytest.approx(100 * (2.5 * 0.45 - 1))   # 0.5 * 2.5 - 1 = 25% edge: bets H
    assert "model" not in picks.index                                            # 0.4 * 2.5 - 1 = 0%: no bet


def test_unsettled_fixtures_are_left_out():
    assert join_settled(news(), ledger(result=None)).empty


def test_settled_without_closing_odds_scores_nothing():
    scores, _ = compare(join_settled(news(), ledger(close_h=None, close_d=None, close_a=None)), EDGE)
    assert scores.empty


def test_game_moved_to_another_date_is_reported_not_lost():
    moved = join_settled(news(), ledger(date=pd.Timestamp("2026-11-15")))
    assert moved.empty
    assert len(unjoined(news(), moved, pd.Timestamp("2026-11-16", tz="UTC"))) == 1
    assert unjoined(news(), moved, pd.Timestamp("2026-11-13", tz="UTC")).empty  # not played yet


def test_research_after_kick_off_is_rejected():
    with pytest.raises(ValueError, match="look-ahead"):
        join_settled(news(researched_at="2026-11-14T15:00:00+00:00"), ledger())
