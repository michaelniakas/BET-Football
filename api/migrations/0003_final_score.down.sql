-- Drops the scores; the next settle after an up fills them again from football-data.
-- Optional on a rollback: the previous release runs fine with these columns. If you do run it, roll back
-- the code first (this release's job writes hg/ag, which save refuses on a schema without them), then
-- restart bet-app: it caches the column list and fails every ledger read until it does.
ALTER TABLE ledger_ou25 DROP COLUMN hg, DROP COLUMN ag;
ALTER TABLE ledger_1x2 DROP COLUMN hg, DROP COLUMN ag;
