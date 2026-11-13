-- Phase 3 step 1 (docs/phase3-plan.md): a prediction is stored before its game's kick-off, when the kick-off is
-- known. Checked at insert only: a kick-off learned or moved later doesn't void what was stored before it.
-- Rows already stored aren't checked. New function and trigger, no table rewrite.

SET LOCAL lock_timeout = '5s';

-- The game is the fixture or one of the same teams in the same competition a day either side (ingest's NEAR): ESPN
-- moves a rescheduled game, with its kick-off, while the ledger keeps fixtures.csv's old date. `api.ledgers` skips
-- what this refuses. Served by fixtures_teams.
CREATE FUNCTION kicked_off(fixture integer, at timestamptz) RETURNS boolean
    LANGUAGE sql STABLE AS $$
    SELECT EXISTS (SELECT FROM fixtures f JOIN fixtures g USING (competition_id, home_id, away_id)
                   WHERE f.id = fixture AND g.date BETWEEN f.date - 1 AND f.date + 1 AND at >= g.kickoff)
$$;

CREATE FUNCTION predictions_before_kickoff() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    IF kicked_off(NEW.fixture_id, NEW.predicted_at) THEN
        RAISE EXCEPTION 'fixture %: predicted at or after kick-off', NEW.fixture_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER predictions_before_kickoff BEFORE INSERT ON predictions FOR EACH ROW
    EXECUTE FUNCTION predictions_before_kickoff();
