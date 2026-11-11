-- Final score of a settled fixture (football-data FTHG/FTAG), filled by `model.paper settle`.
-- Nullable: rows settled before this column existed get it on the next settle; a fixture football-data
-- lacks has none. A score must agree with the stored result, so it can never contradict it.
-- Tables are a few hundred rows: the CHECK scans under the ADD COLUMN lock take milliseconds. The lock
-- timeout keeps it from queueing behind a slow reader and blocking every request behind it; retry if it hits.
SET LOCAL lock_timeout = '5s';

ALTER TABLE ledger_1x2
    ADD COLUMN hg smallint CHECK (hg >= 0),
    ADD COLUMN ag smallint CHECK (ag >= 0),
    ADD CONSTRAINT ledger_1x2_score_both_or_none CHECK (num_nulls(hg, ag) IN (0, 2)),
    ADD CONSTRAINT ledger_1x2_score_matches_result CHECK (
        hg IS NULL OR result IS NOT DISTINCT FROM (CASE WHEN hg > ag THEN 'H' WHEN hg = ag THEN 'D' ELSE 'A' END));

ALTER TABLE ledger_ou25
    ADD COLUMN hg smallint CHECK (hg >= 0),
    ADD COLUMN ag smallint CHECK (ag >= 0),
    ADD CONSTRAINT ledger_ou25_score_both_or_none CHECK (num_nulls(hg, ag) IN (0, 2)),
    ADD CONSTRAINT ledger_ou25_score_matches_result CHECK (
        hg IS NULL OR ou IS NOT DISTINCT FROM (CASE WHEN hg + ag > 2 THEN 'O' ELSE 'U' END));
