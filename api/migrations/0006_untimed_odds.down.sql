-- Back to 0005: every price timed and pre-match. Untimed and closing prices go (`python -m ingest football-data`
-- re-imports them after a new up). Roll the code back first: the football-data job writes them.
-- SET NOT NULL scans the table under its lock: seconds at a few million rows.

SET LOCAL lock_timeout = '5s';

DELETE FROM odds_snapshots WHERE captured_at IS NULL OR kind <> 'pre_match';
ALTER TABLE odds_snapshots
    DROP CONSTRAINT odds_snapshots_key,
    ADD CONSTRAINT odds_snapshots_fixture_id_market_line_source_bookmaker_capt_key
        UNIQUE NULLS NOT DISTINCT (fixture_id, market, line, source, bookmaker, captured_at),
    ALTER COLUMN captured_at SET NOT NULL,
    DROP COLUMN kind;
