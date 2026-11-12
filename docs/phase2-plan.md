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

# Phase 2b: football-data history with its odds

Approved 2026-11-12 (user: "go with all bookmakers"; Asian handicap not wanted). Branch `phase2`; deploys after 0005.

## Facts checked 2026-11-12

- Main files `mmz4281/{season}/{code}.csv` (`www.` now redirects to the bare domain). 2000/01 E0: 5 books' pre-match
  1X2 only. 2025/26 E0: ~32 price columns a game (11 books' 1X2 + O/U 2.5, pre-match and closing). 2026/27: no
  Pinnacle; `SKB` appears, not in `notes.txt`. `Time` only in recent seasons. Pre-match prices are "collected
  Friday afternoons, Tuesday afternoons for midweek games": no capture time.
- Extra leagues `new/{CODE}.csv`: one file per country, all seasons since 2012, closing 1X2 only (PSC, MaxC, AvgC,
  BFEC, B365C); `Season` is `2012/2013` or `2012`; league names carry stray spaces.

Step 0 scan (every file, 2026-11-12): 605 files, 264,599 games with a score (4 rows without, 0 bad dates, 0 in-file
duplicates). Main codes 2000/01–2026/27 except EC (from 2005/06: earlier files absent); extra leagues 2012–2026 (CHN
2014). `Time` in main files from 2019/20, extra leagues always. 3.20M valid price rows (1X2 pre 1.79M, closing
0.71M; O/U pre 0.47M, closing 0.23M; 25 books), 52 invalid (zeros, partial). No name is used in two countries.
Quirks: 15 files are cp1252, not UTF-8 (`King’s Lynn`), values carry non-breaking spaces; G1 2000/01–2004/05 name
teams `HT`/`AT`; `SKB` (Sky Bet, 2026/27) is not in `notes.txt`; `D2_0405.csv` heads three columns `LB` (ignored).

**Spellings of one club** (decided by me, user: "you are clever enough to do it on your own"): merged with
`python -m ingest merge NAME KEEP` after the import, on dev and on Toft: Roda JC → Roda, Sparta → Sparta Rotterdam,
OFI → OFI Crete, Ruch → Ruch Chorzow, Osters → Oster, Boston → Boston Utd, Leonesa → Cultural Leonesa, Kallithea →
Athens Kallithea, AFC Telford United → Telford United, PK-35 Vantaa → PK-35, Mouscron-Peruwelz → Mouscron,
Waasland-Beveren → Beveren, U Craiova → Univ. Craiova, Guangzhou Evergrande → Guangzhou FC, Guangzhou R&F →
Guangzhou City, Wimbledon → Milton Keynes Dons. Kept apart (another club, mostly refounded): AFC Wimbledon, Airdrie /
Airdrie Utd, Lommel / Lommel SK, Molenbeek / RWD Molenbeek, U Craiova 1948, Ostersunds, Gaziantep(spor),
(Yeni) Malatyaspor, Extremadura (UD), (UCAM) Murcia, Aves / AVS, Granada 74. Only renames the spelling scan or a
season check found; a rename to an unrelated name stays two teams until someone notices.

## Shape

- **Migration 0006** (`odds_snapshots` only): + `kind` (`pre_match`, `closing`; default `pre_match`), `captured_at`
  nullable (NULL = the source gave no time), key `(fixture_id, market, line, source, bookmaker, kind, captured_at)`
  NULLS NOT DISTINCT: one untimed pre-match and one closing price per book, first kept. Same shape as
  `market_probs`. The views join on `captured_at = predicted_at`, so untimed rows never reach them. Rollback deletes
  the untimed rows, restores NOT NULL and the old key.
- **Fixtures**: football-data is the club identity source (no queue). The projection's exact key (competition, UK
  date, home, away), so ledger fixtures are reused. Only rows with a score. A score fills a fixture without one
  and only when it agrees with a settled `result`/`ou25`, and never one with a prediction (the ledger settles its
  own: a score filled first could not take a later football-data correction); a disagreement is reported. `result`/`ou25` stay the
  ledger's. `kickoff` from `Time` (UK) only where NULL: ESPN wins.
- **Odds**: 1X2 and O/U 2.5, **every bookmaker** (dropping later is a DELETE; getting back is not guaranteed).
  Allowlist of book codes + optional `C` + outcome; `P`/`PS` → `ps`. An unknown odds-looking column is reported
  (exit 1). A price ≤ 1 or a missing one skips that row, counted.
- **Job** `python -m ingest football-data [CODE SEASON]`: current season of the 22 main codes, the 16 extra-league
  files, and every (code, season) since 2000/01 without untimed football-data prices yet (every file has some;
  fixtures alone don't tell, the ledgers and ESPN make them); one transaction per file; a 10-minute budget
  stops between files, so the backfill on Toft runs itself over a day or two. Worker: daily 05:00 UTC.

## Steps

| # | Step | Check |
|---|---|---|
| 0 | Scan every file (no DB): missing files, headers/unknown columns, games and price rows per market and kind, bad prices, bad dates, names in more than one country, close spellings within a country | User reviews; fixes the allowlist and first seasons. Gate |
| 1 | Migration 0006 + rollback | Up/down/up; bad `kind` rejected; second untimed pre-match rejected; golden + pg ledger tests green |
| 2 | Parser `ingest/footballdata.py` | Tests on recorded slices (2000/01, 2005/06, 2025/26, 2026/27, ARG) |
| 3 | `jobs.import_footballdata` + CLI | Re-run adds 0; ledger fixture reused, `diff` 0; ESPN kick-off kept; disagreeing score reported. Live: counts = CSV; size measured; kick-offs vs ESPN |
| 4 | Worker slot + budget | Due/next slot; budget stops between files; next run resumes |
| 5 | `ingest check` additions | Per club season: fixtures, with a score, with football-data prices, with closing prices (per competition-season was ~600 lines; a missing file shows in the next run instead) |

### Done (2026-11-12, dev `bet_p2`)

- pytest 407 passed. Reviewer on the whole diff: fixed a score filled before settle (the ledger's fixtures are now
  left to it), an empty file or `csv.Error` ending the run, and a merge that would collide giving a traceback.
- Live: two runs (604 s, budget hit with 175 files left; then 338 s): 264,599 fixtures inserted and 3,207,475
  prices, both exactly the scan's counts; 0 disagreeing scores, 0 unknown columns. `odds_snapshots` 576 MB,
  database 647 MB. The done-seasons query (`jobs.footballdata_seasons`) is two parallel seq scans, 1.26 s a run.
- ESPN over 1–10 Nov 2026 afterwards: 216 games linked to football-data's fixtures, kick-offs identical on all 213
  with a time; 3 SP2 games ESPN has and football-data doesn't (postponed, it seems) got their own fixtures.
- The 16 merges ran. Every club season 2000–2026 is covered; closing prices from 2012/13 on (96% then, ~100% from 2016).
- Known and accepted: a game moved by a day after ESPN stored it gets a second fixture from football-data (exact
  key, as the ledger's); `ingest check`'s near-duplicates list shows it.

Not in 2b: Asian handicap, 1993–2000 results (no odds), `fixtures.csv` snapshots for all leagues.
