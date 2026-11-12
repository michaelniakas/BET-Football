-- Back to 0004's meaning: every competition a club league, every team a club. So the international games, their
-- odds and their teams go (the ingest jobs re-import them after a new up); ledger fixtures and the paper slots
-- stay. Roll the code back first: the ingest jobs write these columns.
DELETE FROM paper_runs WHERE job <> 'paper';
ALTER TABLE paper_runs DROP CONSTRAINT paper_runs_pkey, ADD PRIMARY KEY (slot);
ALTER TABLE paper_runs DROP COLUMN job;

DROP TRIGGER unmapped_names_resolved ON team_aliases;
DROP FUNCTION unmapped_names_resolved();
DROP TABLE unmapped_names;
DROP TABLE fixture_refs;

DELETE FROM odds_snapshots WHERE fixture_id IN
    (SELECT f.id FROM fixtures f JOIN competitions c ON c.id = f.competition_id WHERE c.kind = 'international');
DELETE FROM market_probs WHERE fixture_id IN
    (SELECT f.id FROM fixtures f JOIN competitions c ON c.id = f.competition_id WHERE c.kind = 'international');
DELETE FROM predictions WHERE fixture_id IN
    (SELECT f.id FROM fixtures f JOIN competitions c ON c.id = f.competition_id WHERE c.kind = 'international');
DELETE FROM fixtures WHERE competition_id IN (SELECT id FROM competitions WHERE kind = 'international');
DELETE FROM competitions WHERE kind = 'international';
DELETE FROM team_aliases WHERE team_id IN (SELECT id FROM teams WHERE kind = 'national');
DELETE FROM teams WHERE kind = 'national';

DROP INDEX fixtures_teams;
ALTER TABLE fixtures DROP CONSTRAINT fixtures_kickoff_on_date,
    DROP COLUMN decided, DROP COLUMN neutral, DROP COLUMN kickoff;
ALTER TABLE teams DROP COLUMN kind;
ALTER TABLE competitions DROP COLUMN kind, DROP COLUMN name;
