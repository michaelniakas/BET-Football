-- Phase 2b (docs/phase2-plan.md): football-data's history has prices without a capture time: pre-match ones,
-- collected "Friday afternoons, Tuesday afternoons for midweek games", and the closing line. `kind` says which;
-- `captured_at` NULL means the source gave no time. A source's untimed price is one per book: the first one kept.
-- Metadata-only on a table in use, plus one unique index rebuilt (a few thousand rows when this runs).

SET LOCAL lock_timeout = '5s';

ALTER TABLE odds_snapshots
    ADD COLUMN kind text NOT NULL DEFAULT 'pre_match' CHECK (kind IN ('pre_match', 'closing')),
    ALTER COLUMN captured_at DROP NOT NULL,
    DROP CONSTRAINT odds_snapshots_fixture_id_market_line_source_bookmaker_capt_key,
    ADD CONSTRAINT odds_snapshots_key
        UNIQUE NULLS NOT DISTINCT (fixture_id, market, line, source, bookmaker, kind, captured_at);
