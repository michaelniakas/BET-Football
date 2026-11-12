-- Phase 2a (docs/phase2-plan.md): international competitions and teams, kick-off times, how a game was decided,
-- the queue of names no team is known for, and one schedule table for every worker job.
-- Tables in use only get columns without a volatile default (no rewrite); the paper_runs key swap rebuilds one
-- index on a table of a few dozen rows.

-- Fail fast rather than queue every API read behind this migration's table locks.
SET LOCAL lock_timeout = '5s';

ALTER TABLE competitions
    ADD COLUMN name text CHECK (name <> ''),
    ADD COLUMN kind text NOT NULL DEFAULT 'club_league' CHECK (kind IN ('club_league', 'international'));

ALTER TABLE teams ADD COLUMN kind text NOT NULL DEFAULT 'club' CHECK (kind IN ('club', 'national'));

-- `date` stays the source's local date (football-data's UK date, martj42's local date); the kick-off is UTC and
-- falls within a day of it. `hg`/`ag` are the score at the end of play, extra time included (martj42's and
-- ESPN's convention): after extra time it settles no 90-minute market.
ALTER TABLE fixtures
    ADD COLUMN kickoff timestamptz,
    ADD COLUMN neutral boolean,
    ADD COLUMN decided text CHECK (decided IN ('regular', 'extra_time', 'penalties')),
    ADD CONSTRAINT fixtures_kickoff_on_date CHECK (abs((kickoff AT TIME ZONE 'UTC')::date - date) <= 1);
-- Serves the jobs' "same teams within a day" match (WHERE home_id = $1 AND away_id = $2 AND date BETWEEN ...),
-- since sources disagree on the date. A plain build: fixtures has a few thousand rows when this runs.
CREATE INDEX fixtures_teams ON fixtures (home_id, away_id, date);

-- A source's own id for a fixture (ESPN's event id): a re-run finds the same row, whatever the source does to the
-- date. A game moved by more than a day is another fixture (as the ledgers see it): the ref moves to it.
CREATE TABLE fixture_refs (
    source     text    NOT NULL CHECK (source <> ''),
    ref        text    NOT NULL CHECK (ref <> ''),
    fixture_id integer NOT NULL REFERENCES fixtures ON DELETE CASCADE,
    PRIMARY KEY (source, ref),
    UNIQUE (source, fixture_id)
);

-- Names a job met and could not map: that fixture was skipped. A human confirms each one with
-- `python -m ingest alias`; `suggestion` is the closest known name, never applied by itself.
CREATE TABLE unmapped_names (
    source      text        NOT NULL CHECK (source <> ''),
    name        text        NOT NULL CHECK (name <> ''),
    kind        text        NOT NULL CHECK (kind IN ('club', 'national')),
    competition text        NOT NULL CHECK (competition <> ''),
    suggestion  text        CHECK (suggestion <> ''),
    seen        integer     NOT NULL DEFAULT 1 CHECK (seen > 0),
    first_seen  timestamptz NOT NULL DEFAULT now(),
    last_seen   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, name),
    CHECK (last_seen >= first_seen)
);

-- A name is either mapped or queued, never both, whichever path adds the alias.
CREATE FUNCTION unmapped_names_resolved() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM unmapped_names WHERE (source, name) = (NEW.source, NEW.name);
    RETURN NULL;
END $$;
CREATE TRIGGER unmapped_names_resolved AFTER INSERT ON team_aliases FOR EACH ROW
    EXECUTE FUNCTION unmapped_names_resolved();

-- Every job's slots. The running (0004) worker inserts only `slot` and looks a slot up without `job`: no other
-- job may use a paper slot (Tue + Fri 07:00 UTC), see api/worker.py.
ALTER TABLE paper_runs ADD COLUMN job text NOT NULL DEFAULT 'paper' CHECK (job <> '');
ALTER TABLE paper_runs DROP CONSTRAINT paper_runs_pkey, ADD PRIMARY KEY (job, slot);
