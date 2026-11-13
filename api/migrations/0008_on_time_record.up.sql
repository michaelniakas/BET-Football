-- Decided 2026-11-13 (docs/phase3-plan.md, "1: rows already logged after kick-off"): the forward record counts only
-- predictions stored before their game's kick-off. A prediction stored at or after it (the 2026-10-30 23:32 run: 21
-- rows) stays in `predictions` and `ledger_*` but leaves the v_ledger_* views, so the API and the site no longer see
-- it (`sync export`, the backup and the paper job's copy read `ledger_*` and keep it); `python -m api.sync check`
-- counts it as late, not as a difference. Read at query time: unlike 0007's insert check, a kick-off ESPN later
-- moves earlier than a stored prediction takes it out of the record too. Same columns: replaced in place, no lock
-- beyond the views'.

SET LOCAL lock_timeout = '5s';

CREATE OR REPLACE VIEW v_ledger_1x2 AS
SELECT pr.id AS seq, f.date, c.code AS league, h.name AS home, a.name AS away, f.season,
       pr.p[1] AS "p_H", pr.p[2] AS "p_D", pr.p[3] AS "p_A",
       mk.p[1] AS mkt_h, mk.p[2] AS mkt_d, mk.p[3] AS mkt_a,
       s_avg.prices[1] AS avg_h, s_avg.prices[2] AS avg_d, s_avg.prices[3] AS avg_a,
       s_max.prices[1] AS max_h, s_max.prices[2] AS max_d, s_max.prices[3] AS max_a,
       s_b365.prices[1] AS b365_h, s_b365.prices[2] AS b365_d, s_b365.prices[3] AS b365_a,
       s_ps.prices[1] AS ps_h, s_ps.prices[2] AS ps_d, s_ps.prices[3] AS ps_a,
       s_bfe.prices[1] AS bfe_h, s_bfe.prices[2] AS bfe_d, s_bfe.prices[3] AS bfe_a,
       pr.predicted_at AT TIME ZONE 'UTC' AS logged_at,
       f.result,
       cl.p[1] AS close_h, cl.p[2] AS close_d, cl.p[3] AS close_a,
       CASE WHEN f.result IS NOT NULL THEN f.hg END AS hg,
       CASE WHEN f.result IS NOT NULL THEN f.ag END AS ag
FROM predictions pr
JOIN models m ON m.id = pr.model_id AND m.name = 'michalis'
JOIN fixtures f ON f.id = pr.fixture_id
JOIN competitions c ON c.id = f.competition_id
JOIN teams h ON h.id = f.home_id
JOIN teams a ON a.id = f.away_id
JOIN market_probs mk ON (mk.fixture_id, mk.market, mk.kind, mk.method) = (f.id, '1x2', 'pre_match', 'model.paper')
LEFT JOIN market_probs cl ON (cl.fixture_id, cl.market, cl.kind, cl.method) = (f.id, '1x2', 'closing', 'model.paper')
LEFT JOIN odds_snapshots s_avg ON (s_avg.fixture_id, s_avg.market, s_avg.source, s_avg.bookmaker, s_avg.captured_at)
                                = (f.id, '1x2', 'football-data', 'avg', pr.predicted_at)
LEFT JOIN odds_snapshots s_max ON (s_max.fixture_id, s_max.market, s_max.source, s_max.bookmaker, s_max.captured_at)
                                = (f.id, '1x2', 'football-data', 'max', pr.predicted_at)
LEFT JOIN odds_snapshots s_b365 ON (s_b365.fixture_id, s_b365.market, s_b365.source, s_b365.bookmaker, s_b365.captured_at)
                                 = (f.id, '1x2', 'football-data', 'b365', pr.predicted_at)
LEFT JOIN odds_snapshots s_ps ON (s_ps.fixture_id, s_ps.market, s_ps.source, s_ps.bookmaker, s_ps.captured_at)
                               = (f.id, '1x2', 'football-data', 'ps', pr.predicted_at)
LEFT JOIN odds_snapshots s_bfe ON (s_bfe.fixture_id, s_bfe.market, s_bfe.source, s_bfe.bookmaker, s_bfe.captured_at)
                                = (f.id, '1x2', 'football-data', 'bfe', pr.predicted_at)
WHERE pr.market = '1x2' AND NOT kicked_off(f.id, pr.predicted_at);

CREATE OR REPLACE VIEW v_ledger_ou25 AS
SELECT pr.id AS seq, f.date, c.code AS league, h.name AS home, a.name AS away, f.season,
       pr.p[1] AS "p_O", pr.p[2] AS "p_U",
       mk.p[1] AS mkt_o, mk.p[2] AS mkt_u,
       s_avg.prices[1] AS avg_o, s_avg.prices[2] AS avg_u,
       s_max.prices[1] AS max_o, s_max.prices[2] AS max_u,
       s_b365.prices[1] AS b365_o, s_b365.prices[2] AS b365_u,
       s_ps.prices[1] AS ps_o, s_ps.prices[2] AS ps_u,
       s_bfe.prices[1] AS bfe_o, s_bfe.prices[2] AS bfe_u,
       pr.predicted_at AT TIME ZONE 'UTC' AS logged_at,
       f.ou25 AS ou,
       cl.p[1] AS close_o, cl.p[2] AS close_u,
       CASE WHEN f.ou25 IS NOT NULL THEN f.hg END AS hg,
       CASE WHEN f.ou25 IS NOT NULL THEN f.ag END AS ag
FROM predictions pr
JOIN models m ON m.id = pr.model_id AND m.name = 'michalis'
JOIN fixtures f ON f.id = pr.fixture_id
JOIN competitions c ON c.id = f.competition_id
JOIN teams h ON h.id = f.home_id
JOIN teams a ON a.id = f.away_id
JOIN market_probs mk ON (mk.fixture_id, mk.market, mk.line, mk.kind, mk.method)
                      = (f.id, 'ou', 2.5, 'pre_match', 'model.paper')
LEFT JOIN market_probs cl ON (cl.fixture_id, cl.market, cl.line, cl.kind, cl.method)
                           = (f.id, 'ou', 2.5, 'closing', 'model.paper')
LEFT JOIN odds_snapshots s_avg ON (s_avg.fixture_id, s_avg.market, s_avg.line, s_avg.source, s_avg.bookmaker, s_avg.captured_at)
                                = (f.id, 'ou', 2.5, 'football-data', 'avg', pr.predicted_at)
LEFT JOIN odds_snapshots s_max ON (s_max.fixture_id, s_max.market, s_max.line, s_max.source, s_max.bookmaker, s_max.captured_at)
                                = (f.id, 'ou', 2.5, 'football-data', 'max', pr.predicted_at)
LEFT JOIN odds_snapshots s_b365 ON (s_b365.fixture_id, s_b365.market, s_b365.line, s_b365.source, s_b365.bookmaker, s_b365.captured_at)
                                 = (f.id, 'ou', 2.5, 'football-data', 'b365', pr.predicted_at)
LEFT JOIN odds_snapshots s_ps ON (s_ps.fixture_id, s_ps.market, s_ps.line, s_ps.source, s_ps.bookmaker, s_ps.captured_at)
                               = (f.id, 'ou', 2.5, 'football-data', 'ps', pr.predicted_at)
LEFT JOIN odds_snapshots s_bfe ON (s_bfe.fixture_id, s_bfe.market, s_bfe.line, s_bfe.source, s_bfe.bookmaker, s_bfe.captured_at)
                                = (f.id, 'ou', 2.5, 'football-data', 'bfe', pr.predicted_at)
WHERE pr.market = 'ou' AND pr.line = 2.5 AND NOT kicked_off(f.id, pr.predicted_at);
