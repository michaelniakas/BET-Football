"""GithubLedgers: fresh copy from GitHub, cached for the TTL, previous or image copy when GitHub fails."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests

from api.ledgers import CsvLedgers, GithubLedgers


class FakeGet:
    def __init__(self):
        self.calls, self.body, self.status = [], b"", 200

    def __call__(self, url: str, timeout):
        self.calls.append(url)
        if self.status is None:
            raise requests.ConnectionError("down")
        r = requests.Response()
        r.status_code, r._content = self.status, self.body
        return r


def test_github_copy_cached_then_falls_back(ledger_dir: Path):
    image = CsvLedgers(ledger_dir)
    full = (ledger_dir / "ledger_1x2.csv").read_bytes()
    clock = [datetime(2026, 11, 5, tzinfo=UTC)]
    get = FakeGet()
    gh = GithubLedgers("https://gh.test/paper", image, get=get, now=lambda: clock[0])

    # GitHub down before any success: the image's copy.
    get.status = None
    assert len(gh.read("1x2")) == len(image.read("1x2"))

    # Within the TTL nothing is refetched, even after a failure.
    clock[0] += timedelta(minutes=5)
    gh.read("1x2")
    assert len(get.calls) == 1

    # After the TTL: GitHub's copy (one row fewer here, so it is distinguishable).
    lines = full.splitlines(keepends=True)
    get.status, get.body = 200, b"".join(lines[:-1])
    clock[0] += timedelta(minutes=6)
    assert len(gh.read("1x2")) == len(lines) - 2
    assert get.calls[-1] == "https://gh.test/paper/ledger_1x2.csv"

    # GitHub errors or serves garbage: the previous GitHub copy, not the image's.
    for status, body in [(500, b""), (200, b"not,a\nledger")]:
        get.status, get.body = status, body
        clock[0] += timedelta(minutes=11)
        assert len(gh.read("1x2")) == len(lines) - 2
