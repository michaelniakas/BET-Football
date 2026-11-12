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


def test_espn_run_imports_the_other_slugs_when_one_fails(conn: psycopg.Connection, pg_url: str, monkeypatch,
                                                         capsys):
    import sys

    import requests

    from ingest import __main__ as cli
    from ingest import espn
    jobs.import_martj42(conn, martj42.parse(RESULTS, SHOOTOUTS), {})       # Greece–Cyprus, 14 Nov

    def events(first, last, slugs, pause=0.0):
        if "fifa.friendly" not in slugs:
            raise requests.ConnectionError("down")
        return espn_games(("fifa.friendly", event("1", "2026-11-14T18:00Z", "Greece", "Cyprus")))

    monkeypatch.setattr(espn, "events", events)
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setattr(sys, "argv", ["ingest", "espn", "2026-11-13", "2026-11-15"])
    assert cli.main() == 1
    out = capsys.readouterr().out
    assert "eng.1: ConnectionError" in out and "linked 1" in out
    assert conn.execute("SELECT count(*) FROM fixture_refs WHERE source = 'espn'").fetchone()[0] == 1


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


def test_odds_ref_follows_an_event_moved_by_more_than_a_day(conn: psycopg.Connection):
    from ingest import oddsapi
    jobs.import_martj42(conn, martj42.parse(RESULTS, SHOOTOUTS), {})       # Greece–Cyprus, 14 Nov
    jobs.import_odds(conn, oddsapi.parse([priced("a", "2026-11-14T18:00:00Z", "Greece", "Cyprus",
                                                 "2026-11-13T12:00:00Z")]))
    jobs.import_espn(conn, espn_games(("fifa.friendly", event("1", "2026-11-18T18:00Z", "Greece", "Cyprus"))))
    moved = oddsapi.parse([priced("a", "2026-11-18T18:00:00Z", "Greece", "Cyprus", "2026-11-16T12:00:00Z")])
    assert jobs.import_odds(conn, moved)["snapshots"] == 1
    assert conn.execute("""SELECT f.date::text FROM fixture_refs r JOIN fixtures f ON f.id = r.fixture_id
                           WHERE r.source = 'the-odds-api'""").fetchall() == [("2026-11-18",)]
    assert conn.execute("SELECT f.date::text, s.captured_at::date::text FROM odds_snapshots s"
                        " JOIN fixtures f ON f.id = s.fixture_id ORDER BY 1").fetchall() \
        == [("2026-11-14", "2026-11-13"), ("2026-11-18", "2026-11-16")]


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


# --------------------------------------------------------------------------- football-data

def fd_game(code: str, date: str, home: str, away: str, hg: int, ag: int, kickoff=None, prices=()):
    import datetime as dt

    from ingest.footballdata import Game
    return Game(code, 2025, dt.date.fromisoformat(date), kickoff, home, away, hg, ag, tuple(prices))


def test_footballdata_imports_once_and_never_overrides_espn(conn: psycopg.Connection):
    import datetime as dt

    from ingest.footballdata import Price
    jobs.seed(conn, [{"source": "espn", "league": "E0", "source_name": "Manchester City", "fd_name": "Man City"},
                     {"source": "espn", "league": "E0", "source_name": "Arsenal", "fd_name": "Arsenal"}])
    jobs.import_espn(conn, espn_games(("E0", event("3", "2025-11-22T17:30Z", "Manchester City", "Arsenal"))))
    one = Price("1x2", None, "b365", "pre_match", (1.8, 3.6, 4.5))
    games = [fd_game("E0", "2025-11-22", "Man City", "Arsenal", 2, 1,
                     dt.datetime(2025, 11, 22, 15, 0, tzinfo=dt.UTC),
                     [one, Price("1x2", None, "b365", "closing", (1.7, 3.8, 5.0)),
                      Price("ou", 2.5, "ps", "pre_match", (1.9, 1.95))]),
             fd_game("ARG", "2025-11-23", "Instituto", "Newells Old Boys", 0, 0)]
    report = jobs.import_footballdata(conn, games)
    assert report == {"games": 2, "inserted": 1, "filled": 1, "score_disagrees": 0, "prices": 3}
    assert fixture(conn, "Man City") == [("2025-11-22", dt.datetime(2025, 11, 22, 17, 30, tzinfo=dt.UTC), 2, 1, None)]
    assert conn.execute("SELECT name, kind FROM competitions ORDER BY code").fetchall() == \
        [("Argentina", "club_league"), ("England Premier League", "club_league")]
    assert jobs.import_footballdata(conn, games) | {"games": 0} == \
        {"games": 0, "inserted": 0, "filled": 0, "score_disagrees": 0, "prices": 0}
    assert jobs.check(conn, dt.date(2025, 11, 1))["seasons"] == [(2025, 2, 2, 1, 1)]
    # A corrected price for an untimed book: the first stays.
    again = [fd_game("E0", "2025-11-22", "Man City", "Arsenal", 2, 1, None,
                     [Price("1x2", None, "b365", "pre_match", (1.9, 3.5, 4.2))])]
    assert jobs.import_footballdata(conn, again)["prices"] == 0
    assert conn.execute("SELECT prices FROM odds_snapshots WHERE bookmaker = 'b365' AND kind = 'pre_match'"
                        ).fetchall() == [([1.8, 3.6, 4.5],)]


def test_espn_moves_a_club_game_by_a_day_and_footballdata_finds_it(conn: psycopg.Connection):
    jobs.seed(conn, [{"source": "espn", "league": "E0", "source_name": "Manchester City", "fd_name": "Man City"},
                     {"source": "espn", "league": "E0", "source_name": "Arsenal", "fd_name": "Arsenal"}])
    jobs.import_espn(conn, espn_games(("E0", event("3", "2025-11-22T17:30Z", "Manchester City", "Arsenal"))))
    jobs.import_espn(conn, espn_games(("E0", event("3", "2025-11-23T15:00Z", "Manchester City", "Arsenal"))))
    assert [r[0] for r in fixture(conn, "Man City")] == ["2025-11-23"]
    report = jobs.import_footballdata(conn, [fd_game("E0", "2025-11-23", "Man City", "Arsenal", 2, 1)])
    assert (report["inserted"], report["filled"]) == (0, 1)
    assert [r[0] for r in fixture(conn, "Man City")] == ["2025-11-23"]


def test_espn_keeps_the_date_of_a_settled_predicted_or_blocked_club_game(conn: psycopg.Connection):
    jobs.seed(conn, [{"source": "espn", "league": "E0", "source_name": n, "fd_name": n}
                     for n in ("Man City", "Arsenal", "Chelsea")])
    conn.execute("""
        INSERT INTO competitions (code) VALUES ('E0');
        INSERT INTO fixtures (competition_id, season, date, home_id, away_id, result)
        SELECT 1, 2025, d::date, h.team_id, a.team_id, r FROM (VALUES
            ('Man City', 'Arsenal', '2025-11-22', NULL), ('Chelsea', 'Arsenal', '2025-11-22', 'H'),
            ('Arsenal', 'Man City', '2025-11-22', NULL), ('Arsenal', 'Man City', '2025-11-23', NULL)) v(hn, an, d, r)
        JOIN team_aliases h ON (h.source, h.name) = ('football-data', v.hn)
        JOIN team_aliases a ON (a.source, a.name) = ('football-data', v.an);
        INSERT INTO models (name, version) VALUES ('m', 'v');
        INSERT INTO predictions (model_id, fixture_id, fixture_date, market, line, predicted_at, p)
        SELECT 1, f.id, f.date, '1x2', NULL, '2025-11-21', ARRAY[.5, .3, .2] FROM fixtures f
        JOIN team_aliases h ON h.team_id = f.home_id AND (h.source, h.name) = ('football-data', 'Man City')""")
    games = lambda day: espn_games(*(("E0", event(str(i), f"2025-11-{day}T15:00Z", h, a)) for i, (h, a) in  # noqa: E731
                                     enumerate((("Man City", "Arsenal"), ("Chelsea", "Arsenal"), ("Arsenal", "Man City")))))
    jobs.import_espn(conn, games(22))
    jobs.import_espn(conn, games(23))        # predicted, settled, new key taken: no date moves
    dates = """SELECT h.name, a.name, f.date::text, (f.kickoff AT TIME ZONE 'UTC')::date::text FROM fixtures f
                 JOIN team_aliases h ON h.team_id = f.home_id AND h.source = 'football-data'
                 JOIN team_aliases a ON a.team_id = f.away_id AND a.source = 'football-data' ORDER BY 1, 3"""
    assert conn.execute(dates).fetchall() == [
        ("Arsenal", "Man City", "2025-11-22", "2025-11-23"), ("Arsenal", "Man City", "2025-11-23", None),
        ("Chelsea", "Arsenal", "2025-11-22", "2025-11-23"), ("Man City", "Arsenal", "2025-11-22", "2025-11-23")]
    jobs.import_espn(conn, games(21))        # moved two days from its stored kick-off: date and kick-off together
    assert conn.execute(dates).fetchall()[0] == ("Arsenal", "Man City", "2025-11-21", "2025-11-21")


def test_footballdata_reports_a_score_against_the_settled_result(conn: psycopg.Connection):
    jobs.import_footballdata(conn, [fd_game("E0", "2025-11-22", "Man City", "Arsenal", 2, 1)])
    conn.execute("UPDATE fixtures SET hg = NULL, ag = NULL")                # as the ledger withdraws a score
    report = jobs.import_footballdata(conn, [fd_game("E0", "2025-11-22", "Man City", "Arsenal", 1, 1)])
    assert (report["filled"], report["score_disagrees"]) == (1, 0)
    conn.execute("UPDATE fixtures SET hg = NULL, ag = NULL, result = 'H'")
    report = jobs.import_footballdata(conn, [fd_game("E0", "2025-11-22", "Man City", "Arsenal", 1, 1)])
    assert (report["filled"], report["score_disagrees"]) == (0, 1)
    assert fixture(conn, "Man City")[0][2:4] == (None, None)
    later = fd_game("E0", "2025-11-29", "Man City", "Chelsea", 1, 1)
    jobs.import_footballdata(conn, [later])
    conn.execute("""UPDATE fixtures SET hg = NULL, ag = NULL WHERE date = '2025-11-29';
                    INSERT INTO models (name, version) VALUES ('m', 'v');
                    INSERT INTO predictions (model_id, fixture_id, fixture_date, market, line, predicted_at, p)
                    SELECT 1, id, date, '1x2', NULL, date - 1, ARRAY[.5, .3, .2] FROM fixtures
                    WHERE date = '2025-11-29'""")
    report = jobs.import_footballdata(conn, [later])
    assert report["filled"] == 0                                            # the ledger settles its own


def test_merge_moves_a_renamed_club(conn: psycopg.Connection):
    jobs.import_footballdata(conn, [fd_game("N1", "2005-03-01", "Roda JC", "Ajax", 1, 0),
                                    fd_game("N1", "2025-03-01", "Roda", "PSV", 0, 2),
                                    fd_game("N1", "2025-04-01", "Ajax", "PSV", 1, 1)])
    jobs.seed(conn, [{"source": "espn", "league": "N1", "source_name": "Roda JC Kerkrade", "fd_name": "Roda JC"}])
    with pytest.raises(jobs.MergeError, match="names no club"):
        jobs.merge(conn, "Roda JC Kerkrade", "Roda")
    with pytest.raises(jobs.MergeError, match="played each other"):
        jobs.merge(conn, "Ajax", "PSV")
    jobs.import_footballdata(conn, [fd_game("N1", "2025-03-01", "Roda JC", "PSV", 0, 2)])
    with pytest.raises(jobs.MergeError, match="both names"):
        jobs.merge(conn, "Roda JC", "Roda")
    conn.execute("DELETE FROM fixtures WHERE date = '2025-03-01' AND home_id IN"
                 " (SELECT team_id FROM team_aliases WHERE name = 'Roda JC')")
    roda = jobs.merge(conn, "Roda JC", "Roda")
    assert jobs.merge(conn, "Roda JC", "Roda") == roda                    # again: a no-op
    assert conn.execute("SELECT source, name FROM team_aliases WHERE team_id = %s ORDER BY 1, 2", (roda,)).fetchall() \
        == [("espn", "Roda JC Kerkrade"), ("football-data", "Roda"), ("football-data", "Roda JC")]
    assert conn.execute("SELECT count(*) FROM fixtures WHERE %s IN (home_id, away_id)", (roda,)).fetchone() == (2,)
    assert conn.execute("SELECT count(*) FROM teams").fetchone() == (3,)
    jobs.import_footballdata(conn, [fd_game("N1", "2005-03-01", "Roda JC", "Ajax", 1, 0)])
    assert conn.execute("SELECT count(*) FROM fixtures").fetchone() == (3,)


def test_merge_refuses_a_club_in_the_paper_ledgers(conn: psycopg.Connection):
    jobs.import_footballdata(conn, [fd_game("N1", "2005-03-01", "Roda JC", "Ajax", 1, 0),
                                    fd_game("N1", "2025-03-01", "Roda", "PSV", 0, 2)])
    conn.execute("""INSERT INTO models (name, version) VALUES ('m', 'v');
                    INSERT INTO predictions (model_id, fixture_id, fixture_date, market, line, predicted_at, p)
                    SELECT 1, id, date, '1x2', NULL, date - 1, ARRAY[.5, .3, .2] FROM fixtures WHERE date = '2005-03-01'""")
    with pytest.raises(jobs.MergeError, match="paper ledgers"):
        jobs.merge(conn, "Roda JC", "Roda")
    jobs.merge(conn, "Roda", "Roda JC")


def test_footballdata_lands_on_the_ledgers_fixtures(conn: psycopg.Connection, pg_url: str, ledger_dir):
    import pandas as pd

    from api.ledgers import CsvLedgers, PostgresLedgers
    from ingest.footballdata import Price
    pg, csv = PostgresLedgers(pg_url), CsvLedgers(ledger_dir)
    for s in ("1x2", "ou25"):
        pg.save(s, csv.read(s))
    rows = pd.concat([csv.read(s) for s in ("1x2", "ou25")]).drop_duplicates(["date", "league", "home", "away"])
    played = rows[rows.hg.notna()]
    before = conn.execute("SELECT count(*) FROM fixtures").fetchone()[0]
    # Untimed prices of the books the views show, on every ledger fixture: the views never pick them up.
    prices = [Price(m, line, b, k, p) for m, line, p in (("1x2", None, (2.0, 3.5, 4.0)), ("ou", 2.5, (1.9, 1.9)))
              for b in ("avg", "max", "b365", "ps", "bfe") for k in ("pre_match", "closing")]
    report = jobs.import_footballdata(conn, [
        fd_game(r.league, pd.Timestamp(r.date).date().isoformat(), r.home, r.away, int(r.hg), int(r.ag),
                prices=prices) for r in played.itertuples()])
    assert len(played) and (report["inserted"], report["score_disagrees"]) == (0, 0)
    assert conn.execute("SELECT count(*) FROM fixtures").fetchone()[0] == before
    assert (pg.diff("1x2"), pg.diff("ou25")) == (0, 0)
    for s in ("1x2", "ou25"):
        pg.save(s, csv.read(s))
    assert (pg.diff("1x2"), pg.diff("ou25")) == (0, 0)


def test_footballdata_run_order_budget_and_resume(conn: psycopg.Connection, pg_url: str, monkeypatch, capsys):
    import sys

    import pandas as pd
    import requests

    from ingest import __main__ as cli
    from ingest import footballdata as fd
    from model.data import current_season_start
    now = current_season_start(pd.Timestamp.now(tz="UTC").tz_localize(None))
    monkeypatch.setattr(fd, "MAIN", {"E0": "England Premier League"})
    monkeypatch.setattr(fd, "EXTRA", {"ARG": "Argentina"})
    monkeypatch.setattr(fd, "FIRST_SEASON", now - 2)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    monkeypatch.setenv("DATABASE_URL", pg_url)
    status: dict[str, int] = {}
    fetched = []

    def fetch(session, url: str) -> str:
        fetched.append(url)
        if url in status:
            r = requests.Response()
            r.status_code = status[url]
            raise requests.HTTPError(f"{r.status_code}", response=r)
        if url == fd.extra_url("ARG"):
            return "Country,League,Season,Date,Time,Home,Away,HG,AG,Res,AvgCH,AvgCD,AvgCA\n" \
                   f"Argentina,Liga,{now},01/03/{now},20:00,X,Y,1,1,D,2,3,4\n"
        season = 2000 + int(url.split("/")[-2][:2])
        return f"Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,B365H,B365D,B365A\nE0,15/01/{season + 1},A,B,1,0,2,3,4\n"

    def run(*args: str) -> int:
        fetched.clear()
        monkeypatch.setattr(sys, "argv", ["ingest", "football-data", *args])
        return cli.main()

    monkeypatch.setattr(fd, "fetch", fetch)
    monkeypatch.setattr(cli, "FD_BUDGET", -1)
    assert run() == 0 and fetched == [fd.main_url("E0", now)]              # at least one file, then stop
    assert "3 files left" in capsys.readouterr().out
    monkeypatch.setattr(cli, "FD_BUDGET", 600)
    assert run() == 0 and fetched == [fd.main_url("E0", now), fd.extra_url("ARG"), fd.main_url("E0", now - 1),
                                      fd.main_url("E0", now - 2)]
    assert run() == 0 and fetched == [fd.main_url("E0", now), fd.extra_url("ARG")]   # history done: not again
    status[fd.main_url("E0", now)] = 404                                   # the new season has no file yet
    assert run() == 0
    status[fd.extra_url("ARG")] = 503
    assert run() == 1 and len(fetched) == 2                                # a failed file: the rest still run
    assert run("E0", str(now - 1)) == 0 and fetched == [fd.main_url("E0", now - 1)]
    with pytest.raises(SystemExit, match="unknown football-data code"):
        run("E0")
