"""The scheduled paper job must survive Understat being unreachable and one strategy crashing."""
import sys

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
