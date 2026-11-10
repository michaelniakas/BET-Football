"""No API calls. Run: python -m pytest research/test_news.py"""
import datetime as dt
from types import SimpleNamespace as NS

import pytest

from research import news
from research.news import SUBMIT, WORST_CALL_USD, Budget, CapReached, research, response_cost, validated


def usage(**kw):
    base = dict(input_tokens=0, output_tokens=0, cache_creation_input_tokens=0, cache_read_input_tokens=0,
                iterations=None, server_tool_use=None)
    return NS(**base | kw)


def answer(**kw):
    return dict(p_H=0.5, p_D=0.3, p_A=0.2, news_found=True, sources=["https://x.test/a"], absences=[]) | kw


def test_cap_counts_calls_in_flight_and_survives_restart(tmp_path):
    path = tmp_path / "spend.csv"
    budget = Budget(cap=2 * WORST_CALL_USD, path=path)
    budget.reserve()
    budget.reserve()
    with pytest.raises(CapReached):
        budget.reserve()
    charged = 0.75 * WORST_CALL_USD
    budget.charge(charged, "e1", "req1")
    budget.release()
    restarted = Budget(cap=2 * WORST_CALL_USD, path=path)
    assert restarted.spent == pytest.approx(charged)
    restarted.reserve()  # 0.75 + 1 worst calls fit under 2
    with pytest.raises(CapReached):
        restarted.reserve()  # 0.75 + 2 worst calls don't


def test_cost_prices_tokens_and_searches():
    u = usage(input_tokens=1_000_000, output_tokens=100_000, cache_read_input_tokens=1_000_000,
              server_tool_use=NS(web_search_requests=3))
    assert response_cost(u) == pytest.approx(4 + 2 + 0.20 + 0.03)


def test_cost_sums_attempts_at_each_models_rate():
    attempts = [usage(type="message", model="claude-opus-5-5", input_tokens=1_000_000),
                usage(type="fallback_message", model="claude-opus-4-8", input_tokens=1_000_000)]
    assert response_cost(usage(input_tokens=1, iterations=attempts)) == pytest.approx(4 + 5)


@pytest.mark.parametrize("bad", [
    {"p_H": 0.9},                              # sum is 1.4
    {"p_H": 0.0, "p_D": 0.5, "p_A": 0.5},      # a certainty is not a forecast
    {"sources": []},                           # news without a source
    {"sources": ["javascript:alert(1)"]},
    {"absences": [{"source_url": "javascript:alert(1)"}]},
])
def test_rejects_results_we_would_not_store(bad):
    with pytest.raises(ValueError):
        validated(answer(**bad))


def stub_run(monkeypatch, tmp_path, *turns):
    """research() against canned responses; returns (result or None, request kwargs, budget)."""
    monkeypatch.setattr(news, "headlines", lambda *a: [])
    calls, replies = [], iter(turns)

    def create(**kw):
        calls.append(kw)
        return next(replies)

    client = NS(beta=NS(messages=NS(create=create)))
    budget = Budget(cap=50, path=tmp_path / "spend.csv")
    fixture = dict(event_id="e1", league="E0", home="A", away="B",
                   kickoff=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1))
    return lambda: research(client, budget, fixture), calls, budget


def turn(stop, content, container=None):
    return NS(stop_reason=stop, content=content, container=container, model=news.MODEL, _request_id="r",
              usage=usage(input_tokens=1_000_000))


SUBMITTED = NS(type="tool_use", name=SUBMIT["name"], input=answer())


def test_paused_turn_resumes_in_its_container_and_charges_both(monkeypatch, tmp_path):
    paused = [NS(type="text", text="searching")]
    run, calls, budget = stub_run(monkeypatch, tmp_path, turn("pause_turn", paused, NS(id="c1")),
                                  turn("tool_use", [SUBMITTED]))
    row = run()
    assert "container" not in calls[0] and calls[1]["container"] == "c1"
    assert calls[1]["messages"][-1] == {"role": "assistant", "content": paused}
    assert row["cost_usd"] == pytest.approx(8) and budget.spent == pytest.approx(8)


def test_answer_cut_by_max_tokens_is_rejected_but_charged(monkeypatch, tmp_path):
    run, _, budget = stub_run(monkeypatch, tmp_path, turn("max_tokens", [SUBMITTED]))
    with pytest.raises(ValueError, match="max_tokens"):
        run()
    assert budget.spent == pytest.approx(4)


def test_normalises_probabilities():
    out = validated(answer(p_H=0.51, p_D=0.3, p_A=0.2))
    assert out["p_H"] + out["p_D"] + out["p_A"] == pytest.approx(1)
