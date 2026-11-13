-- Back to 0006: predictions are checked against the fixture's date only. Roll the code back after this: its save
-- calls kicked_off().

SET LOCAL lock_timeout = '5s';

DROP TRIGGER predictions_before_kickoff ON predictions;
DROP FUNCTION predictions_before_kickoff();
DROP FUNCTION kicked_off(integer, timestamptz);
