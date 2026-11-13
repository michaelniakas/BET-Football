# BET-Football: notes for Claude (and for us)

Shared by Nikos and Michalis. Decisions and their reasons live in `docs/*-plan.md`; read the current phase plan
(`docs/phase3-plan.md`) before changing `model/`, `api/` or `ingest/`. When a decision changes, record it there (dated,
with what was rejected) and add a line below.

## Decisions that constrain code (newest first)

- **2026-11-13: the site is never indexed.** Every response carries `X-Robots-Tag: noindex, nofollow` (middleware in
  `api/main.py`; `tests/test_api.py::test_no_page_is_indexed`). No `robots.txt` Disallow: it would hide the header from
  crawlers. Google had no pages on 2026-11-13. This is not privacy: the site has no login; a password is still open.

- **2026-11-13: the forward record counts only predictions stored before kick-off.** The paper job skips games whose
  `fixtures.csv` kick-off has passed; migration 0007 (`kicked_off()` + trigger) refuses a prediction at or after its
  game's kick-off; migration 0008 leaves the ones stored earlier (21 from the 2026-10-30 23:32 run) out of
  `v_ledger_*` (the site and API). They stay in `predictions`, `ledger_*` and the CSV export/backup;
  `python -m api.sync check` counts them as late. Don't re-log or backfill past games. `docs/phase3-plan.md`, "1: rows already logged after kick-off".
- **2026-11-13: `logged_at` is UTC** (naive in the CSVs; the database reads it as UTC).
- **2026-11-13: a club game moved by a day is the same game** (`model.paper.NEAR`, ingest's 1-day rule) in settle, log
  and the football-data import; `python -m ingest dedupe` folds duplicates. `docs/phase3-plan.md`, "0a".

## Where things run

- Production is on Toft: a web service and a worker (`python -m api.worker`: paper job Tue/Fri 07:00 UTC, ingest
  jobs daily), both from `main`, with Postgres. The ledgers live in Postgres (`ledger_*` tables, read through the
  `v_ledger_*` views); the GitHub paper-trading workflow is disabled, so `model/paper/*.csv` in git is a snapshot.
- Schema changes are migrations in `api/migrations/` (up + down, reversible, tested with up/down/up); Toft runs
  `python -m api.migrate up` before each worker deploy.
- Tests: `python -m pytest -q`; the database tests need `TEST_DATABASE_URL` pointing at a local Postgres.
- Never commit `research/odds_intl.csv` (The Odds API terms; the repo is public).
