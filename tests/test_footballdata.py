"""football-data parsing on rows taken from the real files (some stats columns cut)."""
from __future__ import annotations

import datetime as dt

from ingest import footballdata as fd
from ingest.footballdata import Price

E0_0001 = """Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR,Attendance,Referee,HS,AS,HST,AST,HHW,AHW,HC,AC,HF,AF,HO,AO,HY,AY,HR,AR,HBP,ABP,GBH,GBD,GBA,IWH,IWD,IWA,LBH,LBD,LBA,SBH,SBD,SBA,WHH,WHD,WHA
E0,19/08/00,Charlton,Man City,4,0,H,2,0,H,20043,Rob Harris,17,8,14,4,2,1,6,6,13,12,8,6,1,2,0,0,10,20,2,3,3.2,2.2,2.9,2.7,2.2,3.25,2.75,2.2,3.25,2.88,2.1,3.2,3.1
"""

# 2025/26 E0, cut to Pinnacle, Betfred, Betfair Exchange and Sky Bet (2026/27's code), pre-match and closing.
E0_2526 = """﻿Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,PSH,PSD,PSA,BFDH,BFDD,BFDA,BFEH,BFED,BFEA,SKBH,SKBD,SKBA,P>2.5,P<2.5,AHh,PAHH,PAHA,PSCH,PSCD,PSCA,BFECH,BFECD,BFECA,PC>2.5,PC<2.5
E0,15/08/2025,20:00,Liverpool,Bournemouth,4,2,H,1.31,6.3,9.6,1.3,6,9.5,1.33,6.6,10.5,1.29,6,9,1.53,2.6,-1.5,1.98,1.93,1.27,6.94,11.26,1.28,7,12,1.5,2.7
E0,20/12/2025,12:30,Newcastle,Chelsea,2,2,D,2.73,3.51,2.47,2.75,3.5,2.6,,,,,,,1.71,2.22,0,2.06,1.86,2.79,3.53,2.41,2.94,3.7,2.52,1.71,2.38
"""

ARG = """﻿Country,League,Season,Date,Time,Home,Away,HG,AG,Res,PSCH,PSCD,PSCA,MaxCH,MaxCD,MaxCA,AvgCH,AvgCD,AvgCA,BFECH,BFECD,BFECA,B365CH,B365CD,B365CA,,,,,,,
Argentina,Liga Profesional ,2012/2013,03/08/2012,23:00,Arsenal Sarandi,Union de Santa Fe,1,0,H,1.9,3.39,5.03,1.9,3.5,5.68,1.76,3.3,4.74,,,,,,,,,,,,,
Argentina,Liga Profesional,2026,08/11/2026,01:00,Instituto,Newells Old Boys,2,2,D,,,,1.84,3.3,5,1.79,3.21,4.71,1.91,3.35,5.4,1.76,3.3,5,,,,,,,
"""

G1_0001 = """Div,Date,HT,AT,FTHG,FTAG,FTR,HTHG,HTAG,HTR,GBH,GBD,GBA,IWH,IWD,IWA
G1,16/09/00,Olympiakos,Eth Asteras,6,0,H,2,0,H,,,,,,
"""

D2_0405 = """Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR,B365H,B365D,B365A,BWH,BWD,BWA,GBH,GBD,GBA,IWH,IWD,IWA,LB,LB,LB,SBH,SBD,SBA,WHH,WHD,WHA,GB>2.5,GB<2.5,GBAHH,GBAHA,GBAH,,,,,,
D2,07/08/04,Dresden,Duisburg,3,1,H,0,0,D,2.25,3.2,2.75,2.2,3.3,2.9,2.2,3.1,3,2.3,2.9,2.8,2.25,3.2,2.75,2.38,3.1,2.88,2.37,3.3,2.5,1.75,1.95,2.2,1.52,-0.5,,,,,,
"""


def test_first_season_has_pre_match_1x2_only():
    [g], report = fd.parse(E0_0001, "E0", 2000)
    assert (g.code, g.season, g.date, g.kickoff, g.home, g.away, g.hg, g.ag) == \
        ("E0", 2000, dt.date(2000, 8, 19), None, "Charlton", "Man City", 4, 0)
    assert g.prices == tuple(Price("1x2", None, b, "pre_match", p) for b, p in [
        ("gb", (2, 3, 3.2)), ("iw", (2.2, 2.9, 2.7)), ("lb", (2.2, 3.25, 2.75)), ("sb", (2.2, 3.25, 2.88)),
        ("wh", (2.1, 3.2, 3.1))])
    assert report.unknown == set() and report.bad_prices == report.no_score == 0


def test_recent_season_closing_ou_and_kickoff_in_utc():
    (summer, winter), report = fd.parse(E0_2526, "E0", 2025)
    assert summer.kickoff == dt.datetime(2025, 8, 15, 19, 0, tzinfo=dt.UTC)        # BST
    assert winter.kickoff == dt.datetime(2025, 12, 20, 12, 30, tzinfo=dt.UTC)      # GMT
    got = {(p.market, p.bookmaker, p.kind): (p.line, p.prices) for p in summer.prices}
    assert got == {
        ("1x2", "ps", "pre_match"): (None, (1.31, 6.3, 9.6)), ("1x2", "bfd", "pre_match"): (None, (1.3, 6, 9.5)),
        ("1x2", "bfe", "pre_match"): (None, (1.33, 6.6, 10.5)), ("1x2", "skb", "pre_match"): (None, (1.29, 6, 9)),
        ("ou", "ps", "pre_match"): (2.5, (1.53, 2.6)), ("1x2", "ps", "closing"): (None, (1.27, 6.94, 11.26)),
        ("1x2", "bfe", "closing"): (None, (1.28, 7, 12)), ("ou", "ps", "closing"): (2.5, (1.5, 2.7))}
    assert {(p.bookmaker, p.kind) for p in winter.prices if p.market == "1x2"} == \
        {("ps", "pre_match"), ("bfd", "pre_match"), ("ps", "closing"), ("bfe", "closing")}     # empty books left out
    assert report.unknown == set() and report.bad_prices == 0


def test_extra_league_rows_carry_their_season():
    games, report = fd.parse(ARG, "ARG")
    assert [(g.season, g.date, g.home) for g in games] == \
        [(2012, dt.date(2012, 8, 3), "Arsenal Sarandi"), (2026, dt.date(2026, 11, 8), "Instituto")]
    assert games[1].kickoff == dt.datetime(2026, 11, 8, 1, 0, tzinfo=dt.UTC)       # UK time, GMT in November
    assert {(p.bookmaker, p.kind) for p in games[1].prices} == \
        {("max", "closing"), ("avg", "closing"), ("bfe", "closing"), ("b365", "closing")}
    assert report.unknown == set()


def test_old_greek_team_columns_and_unlabelled_ladbrokes():
    [g], report = fd.parse(G1_0001, "G1", 2000)
    assert (g.home, g.away, g.prices) == ("Olympiakos", "Eth Asteras", ())
    [d], report2 = fd.parse(D2_0405, "D2", 2004)
    assert "lb" not in {p.bookmaker for p in d.prices}                      # three columns all headed "LB"
    assert {p.bookmaker for p in d.prices if p.market == "ou"} == {"gb"}
    assert report.unknown == report2.unknown == set()


def test_bad_prices_skip_that_book_only():
    text = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,B365H,B365D,B365A,BWH,BWD,BWA,IWH,IWD,IWA\n" \
           "E1,27/04/13,Blackpool,Derby,2,1,0,3.4,3.4,2.15,3.25,3.25,1.9,,4\n"
    [g], report = fd.parse(text, "E1", 2012)
    assert [p.bookmaker for p in g.prices] == ["bw"]
    assert report.bad_prices == 2


def test_unknown_or_repeated_price_columns_are_reported():
    text = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,XYZH,XYZD,XYZA,B365H,B365H,B365D,B365A,Referee\n" \
           "E0,01/01/26,A,B,1,0,2,3,4,2,2.1,3,4,X\n"
    [g], report = fd.parse(text, "E0", 2025)
    assert report.unknown == {"XYZH", "XYZD", "XYZA", "B365H"}
    assert g.prices == (Price("1x2", None, "b365", "pre_match", (2, 3, 4)),)     # the first B365H


def test_rows_without_a_score_are_counted():
    text = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG\nF2,01/05/26,Bastia,Laval,,\n,,,,,\n"
    games, report = fd.parse(text, "F2", 2025)
    assert games == [] and report.no_score == 1


def test_empty_file_is_an_error():
    import pytest
    with pytest.raises(ValueError, match="empty"):
        fd.parse("", "E0", 2025)


def test_decode_utf8_or_windows_1252():
    assert fd.decode(b"\xef\xbb\xbfDiv") == "Div"
    assert fd.decode(b"King\x92s Lynn") == "King’s Lynn"
