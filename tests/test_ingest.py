"""Ingest: team mapping never guesses, martj42 imports once and fills what is missing."""
from __future__ import annotations

import psycopg
import pytest

from api import migrate
from ingest import jobs, martj42, names

RESULTS = """date,home_team,away_team,home_score,away_score,tournament,city,country,neutral
2025-03-23,France,Croatia,2,0,UEFA Nations League,Paris,France,FALSE
2025-03-23,Slovenia,Slovakia,1,0,UEFA Nations League,Ljubljana,Slovenia,FALSE
2026-11-14,Greece,Cyprus,NA,NA,Friendly,Athens,Greece,FALSE
1974-02-17,Tahiti,New Caledonia,2,1,Friendly,Papeete,Tahiti,FALSE
1974-02-17,Tahiti,New Caledonia,1,2,Friendly,Papeete,Tahiti,FALSE
"""
SHOOTOUTS = "date,home_team,away_team,winner,first_shooter\n2025-03-23,France,Croatia,France,\n"
FORMER = "current,former,start_date,end_date\nBenin,Dahomey,1959-11-08,1975-11-30\nCroatia,Hrvatska,1990-01-01,1991-01-01\n"


@pytest.fixture
def conn(pg_url: str):
    migrate.up(pg_url)
    with psycopg.connect(pg_url, autocommit=True) as c:      # as the CLI: each job its own transaction
        yield c


def test_match_only_exact_after_normalising():
    known = [("Man City", 1), ("Man United", 2), ("FC Koln", 3), ("Sporting", 4), ("Sporting", 5)]
    mapped, queued = names.match({"1. FC Köln", "Manchester City", "Sporting", "Nowhere United"}, known)
    assert mapped == {"1. FC Köln": 3}
    assert queued["Sporting"] == "Sporting"                    # two teams: ambiguous, suggested, not mapped
    assert set(queued) == {"Manchester City", "Sporting", "Nowhere United"}


def test_map_names_aliases_exact_and_queues_the_rest(conn: psycopg.Connection):
    jobs.import_martj42(conn, martj42.parse(RESULTS, SHOOTOUTS), {})
    seen = {"France": "UNL", "Türkiye": "UNL", "Croatia": "UNL"}
    assert set(jobs.map_names(conn, "espn", jobs.NATIONAL, seen)) == {"France", "Croatia"}
    assert [r[1] for r in conn.execute("SELECT * FROM unmapped_names")] == ["Türkiye"]
    jobs.map_names(conn, "espn", jobs.NATIONAL, seen)           # seen again: counted, not duplicated
    assert conn.execute("SELECT seen FROM unmapped_names").fetchone() == (2,)
    assert jobs.map_names(conn, "espn", jobs.CLUB, {"France": "E0"}) == {}   # a club never maps to a nation


def test_alias_confirms_a_queued_name(conn: psycopg.Connection):
    jobs.import_martj42(conn, martj42.parse(RESULTS, SHOOTOUTS), {})
    jobs.map_names(conn, "espn", jobs.NATIONAL, {"Hellas": "FRI"})
    with pytest.raises(jobs.AliasError, match="not in the queue"):
        jobs.alias(conn, "espn", "Elsewhere", "Greece")
    with pytest.raises(jobs.AliasError, match="names 0"):
        jobs.alias(conn, "espn", "Hellas", "Atlantis")
    greece = jobs.alias(conn, "espn", "Hellas", "Greece")
    assert jobs.map_names(conn, "espn", jobs.NATIONAL, {"Hellas": "FRI"}) == {"Hellas": greece}
    assert conn.execute("SELECT count(*) FROM unmapped_names").fetchone() == (0,)


def test_seed_adds_aliases_and_counts_conflicts(conn: psycopg.Connection):
    rows = [{"source": "espn", "league": "E0", "source_name": "Manchester City", "fd_name": "Man City"},
            {"source": "espn", "league": "E0", "source_name": "Manchester United", "fd_name": "Man United"}]
    assert jobs.seed(conn, rows) == {"rows": 2, "conflicts": 0}
    assert jobs.seed(conn, rows) == {"rows": 2, "conflicts": 0}             # idempotent
    rows[1]["fd_name"] = "Man City"
    assert jobs.seed(conn, rows)["conflicts"] == 1
    assert set(jobs.map_names(conn, "espn", jobs.CLUB, {"Manchester City": "E0", "Manchester United": "E0"})) \
        == {"Manchester City", "Manchester United"}


def test_martj42_imports_once(conn: psycopg.Connection):
    games, former = martj42.parse(RESULTS, SHOOTOUTS), martj42.parse_former(FORMER)
    report = jobs.import_martj42(conn, games, former)
    assert report == {"games": 5, "duplicates": 1, "inserted": 4, "filled": 0, "missing": 0,
                      "score_disagrees": 0, "teams": 8}
    assert jobs.import_martj42(conn, games, former)["inserted"] == 0
    assert conn.execute("SELECT decided, neutral FROM fixtures f JOIN team_aliases a ON a.team_id = f.home_id"
                        " WHERE a.name = 'France'").fetchone() == ("penalties", False)
    assert conn.execute("SELECT count(*) FROM competitions WHERE kind = 'international'").fetchone() == (2,)
    assert conn.execute("SELECT hg, ag FROM fixtures WHERE date = '1974-02-17'").fetchall() == [(2, 1)]  # first kept
    # A former name is an alias of the current team (Benin plays no game here, so it has no team).
    assert conn.execute("SELECT a.name FROM team_aliases a JOIN team_aliases c ON c.team_id = a.team_id"
                        " WHERE c.name = 'Croatia' ORDER BY 1").fetchall() == [("Croatia",), ("Hrvatska",)]


def test_martj42_fills_a_stored_game_a_day_off(conn: psycopg.Connection):
    """ESPN stored Greece–Cyprus on its UTC date, unplayed; martj42 later has it on the local date with a score."""
    jobs.import_martj42(conn, martj42.parse(RESULTS, SHOOTOUTS), {})
    played = RESULTS.replace("2026-11-14,Greece,Cyprus,NA,NA", "2026-11-15,Greece,Cyprus,3,1")
    report = jobs.import_martj42(conn, martj42.parse(played, SHOOTOUTS), {})
    assert (report["inserted"], report["filled"], report["missing"]) == (0, 1, 0)
    assert conn.execute("SELECT date::text, hg, ag FROM fixtures WHERE hg = 3").fetchall() == [("2026-11-14", 3, 1)]
    changed = played.replace("Greece,Cyprus,3,1", "Greece,Cyprus,2,1")
    assert jobs.import_martj42(conn, martj42.parse(changed, SHOOTOUTS), {})["score_disagrees"] == 1


# --------------------------------------------------------------------------- ESPN

def event(eid: str, date: str, home: str, away: str, status: str = "STATUS_SCHEDULED", score=("0", "0")) -> dict:
    """A scoreboard event in ESPN's shape (as checked on uefa.nations, 2026-11-12)."""
    state = "pre" if status == "STATUS_SCHEDULED" else "post"
    return {"id": eid, "date": date, "status": {"type": {"name": status, "state": state}},
            "competitions": [{"neutralSite": None, "competitors": [
                {"homeAway": "home", "score": score[0], "team": {"displayName": home}},
                {"homeAway": "away", "score": score[1], "team": {"displayName": away}}]}]}


def espn_games(*events: tuple[str, dict]) -> list[dict]:
    from ingest import espn
    return [espn.parse(league, e) for league, e in events]


def fixture(conn: psycopg.Connection, home: str) -> tuple:
    return conn.execute("SELECT date::text, kickoff, hg, ag, decided FROM fixtures"
                        " WHERE home_id IN (SELECT team_id FROM team_aliases WHERE name = %s) ORDER BY date",
                        (home,)).fetchall()


def test_espn_links_an_international_to_martj42_a_day_off(conn: psycopg.Connection):
    jobs.import_martj42(conn, martj42.parse(RESULTS, SHOOTOUTS), {})       # Greece–Cyprus on 14 Nov, local
    games = espn_games(("fifa.friendly", event("1", "2026-11-13T23:00Z", "Greece", "Cyprus")))
    report = jobs.import_espn(conn, games)
    assert (report["inserted"], report["linked"], report["unmapped"]) == (0, 1, 0)
    assert [r[0] for r in fixture(conn, "Greece")] == ["2026-11-14"]
    assert jobs.import_espn(conn, games) | {"games": 0} == {"games": 0, "unmapped": 0, "inserted": 0, "linked": 0,
                                                           "rescheduled": 0, "filled": 0, "score_disagrees": 0,
                                                           "unlinked": 0}
    done = espn_games(("fifa.friendly", event("1", "2026-11-13T23:00Z", "Greece", "Cyprus", "STATUS_FINAL_AET",
                                              ("2", "1"))))
    assert jobs.import_espn(conn, done)["filled"] == 1
    assert fixture(conn, "Greece")[0][2:] == (2, 1, "extra_time")


def test_espn_queues_an_unknown_team_and_skips_its_game(conn: psycopg.Connection):
    jobs.import_martj42(conn, martj42.parse(RESULTS, SHOOTOUTS), {})
    report = jobs.import_espn(conn, espn_games(("uefa.nations", event("2", "2026-11-15T19:45Z", "Türkiye", "Greece"))))
    assert (report["unmapped"], report["inserted"]) == (1, 0)
    assert conn.execute("SELECT name, suggestion FROM unmapped_names").fetchall() == [("Türkiye", None)]


def test_espn_club_game_lands_on_the_ledgers_fixture(conn: psycopg.Connection):
    jobs.seed(conn, [{"source": "espn", "league": "E0", "source_name": "Manchester City", "fd_name": "Man City"},
                     {"source": "espn", "league": "E0", "source_name": "Arsenal", "fd_name": "Arsenal"}])
    conn.execute("""INSERT INTO competitions (code) VALUES ('E0');
                    INSERT INTO fixtures (competition_id, season, date, home_id, away_id, result)
                    SELECT 1, 2026, '2026-11-22', h.team_id, a.team_id, 'H' FROM team_aliases h, team_aliases a
                    WHERE (h.source, h.name, a.source, a.name) = ('football-data', 'Man City', 'football-data', 'Arsenal')""")
    # 23:30 UTC in winter is 23:30 in London: the 22nd, football-data's date. football-data owns club scores:
    # ESPN's is never filled, and counted when it contradicts the settled result.
    games = espn_games(("E0", event("3", "2026-11-22T23:30Z", "Manchester City", "Arsenal", "STATUS_FULL_TIME",
                                    ("0", "1"))))
    report = jobs.import_espn(conn, games)
    assert (report["inserted"], report["linked"], report["filled"], report["score_disagrees"]) == (0, 1, 0, 1)
    assert fixture(conn, "Man City")[0][:3] == ("2026-11-22", __import__("datetime").datetime(
        2026, 11, 22, 23, 30, tzinfo=__import__("datetime").UTC), None)
    assert conn.execute("SELECT name, kind FROM competitions").fetchall() == [("England Premier League", "club_league")]
    agrees = espn_games(("E0", event("3", "2026-11-22T23:30Z", "Manchester City", "Arsenal", "STATUS_FULL_TIME",
                                     ("2", "0"))))
    assert [jobs.import_espn(conn, agrees)[k] for k in ("filled", "score_disagrees")] == [0, 0]


def test_espn_rescheduled_game_is_another_fixture(conn: psycopg.Connection):
    jobs.import_martj42(conn, martj42.parse(RESULTS, SHOOTOUTS), {})
    jobs.import_espn(conn, espn_games(("fifa.friendly", event("1", "2026-11-14T18:00Z", "Greece", "Cyprus"))))
    report = jobs.import_espn(conn, espn_games(("fifa.friendly", event("1", "2026-11-18T18:00Z", "Greece", "Cyprus"))))
    assert (report["rescheduled"], report["inserted"], report["linked"]) == (1, 1, 1)
    assert [r[0] for r in fixture(conn, "Greece")] == ["2026-11-14", "2026-11-18"]
    assert conn.execute("SELECT f.date::text FROM fixture_refs r JOIN fixtures f ON f.id = r.fixture_id").fetchall() \
        == [("2026-11-18",)]


# --------------------------------------------------------------------------- Odds API

def priced(eid: str, kickoff: str, home: str, away: str, updated: str, prices=(1.5, 4.0, 6.0)) -> dict:
    """An /odds event in the Odds API's v4 shape."""
    return {"id": eid, "sport_key": "soccer_international_friendly", "commence_time": kickoff,
            "home_team": home, "away_team": away, "bookmakers": [{"key": "pinnacle", "markets": [
                {"key": "h2h", "last_update": updated, "outcomes": [
                    {"name": away, "price": prices[2]}, {"name": home, "price": prices[0]},
                    {"name": "Draw", "price": prices[1]}]}]}]}


def test_odds_land_on_the_stored_fixture_before_kickoff_only(conn: psycopg.Connection):
    from ingest import oddsapi
    jobs.import_martj42(conn, martj42.parse(RESULTS, SHOOTOUTS), {})       # Greece–Cyprus, 14 Nov
    jobs.import_espn(conn, espn_games(("fifa.friendly", event("1", "2026-11-14T18:00Z", "Greece", "Cyprus"))))
    events = [priced("a", "2026-11-14T18:00:00Z", "Greece", "Cyprus", "2026-11-14T12:00:00Z"),
              priced("a", "2026-11-14T18:00:00Z", "Greece", "Cyprus", "2026-11-14T18:05:00Z"),   # in play
              priced("c", "2026-11-15T18:00:00Z", "Malta", "Cyprus", "2026-11-14T12:00:00Z"),    # unknown team
              priced("d", "2026-11-20T18:00:00Z", "Cyprus", "Greece", "2026-11-14T12:00:00Z")]   # no fixture
    rows = oddsapi.parse(events)
    assert rows[0]["prices"] == [1.5, 4.0, 6.0]                            # home, draw, away
    report = jobs.import_odds(conn, rows)
    assert report == {"events": 3, "unmapped": 1, "no_fixture": 1, "snapshots": 1}
    assert jobs.import_odds(conn, rows)["snapshots"] == 0                  # same quote again: nothing new
    assert conn.execute("SELECT bookmaker, prices FROM odds_snapshots").fetchall() == [("pinnacle", [1.5, 4.0, 6.0])]
    assert conn.execute("SELECT name FROM unmapped_names WHERE source = 'the-odds-api'").fetchall() == [("Malta",)]


class FakeResponse:
    def __init__(self, body, left):
        self.status_code, self._body, self.headers = 200, body, {"x-requests-remaining": str(left)}

    def json(self):
        return self._body


def test_odds_snapshot_keeps_the_credit_reserve(monkeypatch):
    import datetime as dt

    from ingest import oddsapi
    calls = []
    sports = [{"key": k, "group": "Soccer", "has_outrights": False, "title": t, "description": ""}
              for k, t in (("unl", "UEFA Nations League"), ("ucl", "UEFA Champions League Qualification"),
                           ("fri", "International Friendlies"), ("epl", "EPL"))]

    def get(self, url, params=None, timeout=None):
        calls.append(url.rsplit("/", 1)[-1])
        left = 11 - sum(c == "odds" for c in calls)
        if url.endswith("/sports"):
            return FakeResponse(sports, left)
        if url.endswith("/events"):
            clubs = "/ucl/" in url
            return FakeResponse([{"id": "x", "home_team": "Celtic" if clubs else "Greece", "away_team": "Cyprus"}],
                                left)
        return FakeResponse([priced("a", "2026-11-14T18:00:00Z", "Greece", "Cyprus", "2026-11-14T12:00:00Z")], left)

    monkeypatch.setattr(oddsapi.requests.Session, "get", get)
    rows, left, skipped = oddsapi.snapshot("k", dt.datetime(2026, 11, 13, tzinfo=dt.UTC), dt.timedelta(days=3),
                                           reserve=10, national=lambda n: n in {"Greece", "Cyprus"})
    assert (len(rows), left, skipped) == (1, 10, ["fri"])                  # one paid call, then the reserve held
    assert calls == ["sports", "events", "odds", "events", "events"]       # club qualifiers: free look only; no EPL


def test_only_recent_former_names_are_aliases():
    # "Ireland" was Northern Ireland's name until 1956; today it is what many sources call the Republic.
    old = "Northern Ireland,Ireland,1882-02-18,1956-04-11\n"
    assert martj42.parse_former(FORMER + old) == {"Hrvatska": "Croatia"}


def test_check_reports_the_closing_gap_and_a_reversed_near_duplicate(conn: psycopg.Connection):
    import datetime as dt

    from ingest import oddsapi
    jobs.import_martj42(conn, martj42.parse(RESULTS, SHOOTOUTS), {})
    jobs.import_espn(conn, espn_games(("fifa.friendly", event("1", "2026-11-14T18:00Z", "Greece", "Cyprus")),
                                      ("fifa.friendly", event("2", "2026-11-16T18:00Z", "Cyprus", "Greece"))))
    jobs.import_odds(conn, oddsapi.parse([priced("a", "2026-11-14T18:00:00Z", "Greece", "Cyprus",
                                                 "2026-11-14T12:00:00Z")]))
    jobs.import_espn(conn, espn_games(("fifa.friendly", event("1", "2026-11-14T18:00Z", "Greece", "Cyprus",
                                                              "STATUS_FULL_TIME", ("2", "1")))))
    r = jobs.check(conn, dt.date(2026, 11, 1))
    assert r["closing"] == (1, dt.timedelta(hours=6), dt.timedelta(hours=6))
    assert [(a_date, home, away, b_date, same) for _, a_date, home, away, _, b_date, same in r["near"]] == [
        (dt.date(2026, 11, 14), "Greece", "Cyprus", dt.date(2026, 11, 16), False)]
    assert r["unmapped"] == 0 and dict(r["refs"]) == {"espn": 2, "the-odds-api": 1}
