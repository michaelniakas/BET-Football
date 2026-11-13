"""Team names across sources: a name maps to a team only on an exact match after normalising; anything else is
queued for a human with the closest known name as a suggestion."""
from __future__ import annotations

import collections
import difflib
import unicodedata
from collections.abc import Iterable

NOISE = {"fc", "cf", "afc", "sc", "ac", "as", "ssc", "sv", "vfb", "vfl", "tsv", "fsv", "kv", "krc", "rc", "cd", "ud", "sd", "1", "the"}


def norm(name: str) -> str:
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return " ".join(t for t in plain.replace(".", " ").replace("-", " ").split() if t not in NOISE)


def match(names: set[str], known: Iterable[tuple[str, int]]) -> tuple[dict[str, int], dict[str, str | None]]:
    """known: (spelling, team id) pairs. Returns (name -> team id where exactly one known team has the same
    normalised name, every other name -> the closest known spelling, or None)."""
    teams, spelling = collections.defaultdict(set), {}
    for name, team in known:
        teams[norm(name)].add(team)
        spelling.setdefault(norm(name), name)
    mapped, queued = {}, {}
    for name in names:
        hit = teams.get(norm(name), set())
        if len(hit) == 1:
            mapped[name] = next(iter(hit))
        else:
            close = difflib.get_close_matches(norm(name), spelling, n=1, cutoff=0.6)
            queued[name] = spelling[close[0]] if close else None
    return mapped, queued
