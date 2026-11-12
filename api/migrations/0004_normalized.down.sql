-- Drops the normalized tables. Nothing is lost while ledger_* exist (phase 1a): the next save, or
-- `python -m api.sync project`, rebuilds them. Roll the code back first: this release reads the views.
DROP VIEW v_ledger_ou25;
DROP VIEW v_ledger_1x2;
DROP TABLE market_probs;
DROP TABLE odds_snapshots;
DROP TABLE predictions;
DROP TABLE models;
DROP TABLE fixtures;
DROP TABLE team_aliases;
DROP TABLE teams;
DROP TABLE competitions;
DROP FUNCTION append_only();
DROP FUNCTION fixtures_final();
DROP FUNCTION outcome_ou25(smallint, smallint);
DROP FUNCTION outcome_1x2(smallint, smallint);
DROP FUNCTION prices_ok(double precision[]);
DROP FUNCTION probs_ok(double precision[]);
DROP FUNCTION market_ok(text, numeric, integer);
