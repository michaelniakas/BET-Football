# Phase 2: ingestion

Approved 2026-11-12 (user: "let's go 2a only and then 2b", "prefer database over stuff"). Phase 2 of
`docs/platform-plan.md`. Branches from `phase1a`; 0005 deploys only after phase 1 steps 4–5 passed.

**2a** (this): what phase 3 (international model) and phase 4 (research job) read: international results since
1872, club and international fixtures with kick-off times, international odds snapshots, team mapping in Postgres.
**2b** (next): football-data's full history (2000/01+, more divisions, the extra leagues) with its odds, in Postgres.
Other sources (OddsHarvester, Transfermarkt, ClubElo, openfootball UEFA, footballcharts, API-Football) arrive
with the phase whose model reads them.

## Facts checked 2026-11-12

- martj42 `results.csv`: 49,940 games, 1872-11-30 → 2026-10-06, 226 tournament names, no missing scores; columns
  date, home_team, away_team, home_score, away_score, tournament, city, country, neutral. Scores include extra
  time; `shootouts.csv` lists games decided on penalties; `former_names.csv` (current, former, start, end).
- ESPN: an unknown league slug answers 400. These answer 200: uefa.nations, fifa.friendly, fifa.worldq.{uefa,
  conmebol,concacaf,afc,caf}, uefa.euroq, uefa.euro, fifa.world, conmebol.america, concacaf.gold,
  concacaf.nations.league, caf.nations, caf.nations_qual, afc.asian.cup, afc.cupq. Finished games are
  `STATUS_FULL_TIME`, `STATUS_FINAL_AET` or `STATUS_FINAL_PEN`, score after extra time, no period scores: the
  90-minute score of an extra-time game is unknown. `neutralSite` is often null.
- Odds API (v4 docs): `/sports` and `/sports/{sport}/events` cost no credits; `/odds` costs 1 per market and
  region; every response carries `x-requests-remaining`; `commenceTimeFrom`/`To` filter both; a market carries
  `last_update`. Free tier 500 credits/month. The live-odds cache in `api/odds.py` spends from the same key.
  On 2026-11-12 its in-season soccer list held one national-team competition, `soccer_uefa_nations_league`
  (no friendlies, no other confederation): the first live run priced 25 of the window's ~90 international games.
  Prices for the rest wait for a later source (OddsHarvester, `docs/platform-plan.md`).
- martj42 lists one game under two keys at least twice: 1974-02-17 Tahiti v New Caledonia as 2-1 and 1-2 (same
  date; the first row is kept, reported as a duplicate) and Argentina v Bolivia 4-0 on both 2026-09-30 and
  2026-10-01 (different dates; both stored, as no rule says which is right; `ingest check` shows such pairs).

## Shape (migration 0005, additive, reversible)

| Table | Change | Why |
|---|---|---|
| `competitions` | + `name`, + `kind` (`club_league`, `international`), default `club_league` | martj42 tournaments beside football-data codes; `code` = football-data code or martj42 tournament name |
| `teams` | + `kind` (`club`, `national`), default `club` | A national team never matches a club |
| `fixtures` | + `kickoff timestamptz` (NULL: football-data history has dates only), + `neutral bool` (NULL = unknown), + `decided` (`regular`, `extra_time`, `penalties`; NULL = unknown). CHECK the kick-off's UTC date is within a day of `date` | Local vs UTC dates; the research job needs the kick-off; an extra-time score settles no 90-minute market (phase 3 reads `decided`) |
| `fixture_refs` | source, ref (the source's event id), fixture; key (source, ref), UNIQUE (source, fixture) | A re-run, a moved kick-off or a renamed team finds the same fixture by id, not by guessing again |
| `unmapped_names` | source, name, competition, kind, seen count, first/last seen, suggestion | The queue a human clears: jobs flag, never guess |
| `paper_runs` | + `job` default `'paper'`, key `(job, slot)` | One schedule table for every job; the running worker's insert still works |

All on tables in use are metadata-only (`ADD COLUMN` without a volatile default); the `paper_runs` key swap
rebuilds one index on a table of a few dozen rows. Rollback drops the columns and the queue: the ingest jobs
re-fill them; the ledgers are untouched.

**Dates**: a club fixture's `date` is football-data's (UK date). The ESPN job writes club fixtures with the
kick-off's Europe/London date, so the ledger projection (exact date match) finds the same row. International
fixtures from ESPN use the kick-off's UTC date; martj42 uses its local date and never changes an existing row's
date. An ESPN or Odds API game links (`fixture_refs`) to the fixture with the same teams in the same order
within a day, whatever the competition; a game ESPN moves by more than a day loses its ref and is matched again.
The Odds API job never creates fixtures. `ingest check` lists fixtures of the last 30 days that share their teams
(either order) with another within two days: a two-legged tie, or one game stored twice.

**Team mapping** (all in Postgres):
- `team_aliases` stays the one map. Exact matches after normalising (accents, case, "FC" noise) are added by the
  job; martj42 former names dropped since 1990 map to the current team (older ones no source uses, and
  "Ireland", Northern Ireland's name to 1956, is what sources call the Republic today).
- Anything else goes to `unmapped_names` with a difflib suggestion, and that fixture is skipped.
- `python -m ingest alias SOURCE NAME TEAM` confirms one (alias added, queue row removed, one transaction);
  `python -m ingest unmapped` lists the queue. Seed: `python -m ingest seed research/teams.csv` (the pilot's
  278 ESPN club names).

## Layers

- `ingest/espn.py` (moved from `research/espn.py`; research imports it), `ingest/martj42.py`, `ingest/oddsapi.py`:
  infrastructure, HTTP in, plain rows out.
- `ingest/store.py`: Postgres, set-based writes, under the ledgers' advisory lock.
- `ingest/jobs.py`: use cases (fetch → map → write → report). `ingest/__main__.py`: CLI.
- `api/worker.py`: schedules the jobs through `paper_runs (job, slot)`, each as `python -m ingest <job>`:
  martj42 and espn daily 06:00 UTC, odds 06:30, 11:00, 15:00, 18:30 UTC; never 07:00 (the previous worker looks a
  paper slot up without its job). Exit 1 is logged and the slot recorded: every run re-reads a window, so the
  next slot catches up. A timeout leaves the slot due (retry in an hour).
- Odds: per sport, `/events` (free) first; `/odds` only for a sport with a game in the next 72 h between two
  known national teams (a title match also catches club qualifiers) and only while a call leaves at least 50
  credits. Scores: ESPN fills international ones only; football-data owns club scores. A price is stored only when its `last_update` is before kick-off.

## Steps

| # | Step | Check |
|---|---|---|
| 1 | Migration 0005 + rollback | Up/down/up; each new constraint rejects a bad row; the old worker's `paper_runs` insert works |
| 2 | Mapping: normaliser, matcher, queue, `alias`/`unmapped`/`seed` CLI | Tests: exact, former name, contested target, unmapped skipped and queued, alias clears the queue |
| 3 | martj42 import (results, shootouts, former names), daily | Fixtures = CSV rows (duplicates reported); zero unmapped; re-run adds 0 rows |
| 4 | ESPN job: 16 leagues + the international slugs above, today−2 … today+7; fixtures, kick-offs, results | Tests on recorded JSON; live counts = ESPN for the window; zero unmapped after review |
| 5 | Odds API international snapshots; credit guard on `x-requests-remaining`; only sports with games in 3 days | Guard test; a finished, snapshotted fixture has a last-before-kick-off snapshot |
| 6 | Worker runs ingest jobs on `paper_runs (job, slot)` | Worker tests: due/next slot per job, no repeat after restart, paper job unchanged |
| 7 | `python -m ingest check`, deploy | Counts, unmapped, closing, near-duplicates after two daily runs |

Needs on Toft: `ODDS_API_KEY` secret on the worker.
