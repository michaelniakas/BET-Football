# Phase 3: international model + forward paper trading

**Draft 2026-11-12; decisions recorded. Steps 0a and 1 live (2026-11-13).** Phase 3 of `docs/platform-plan.md`
(check: walk-forward log loss vs an Elo baseline; forward CLV accumulating). Branch `phase3` from `phase2`; deploys
after 0006.

**Decided 2026-11-12** (user):
- Open decision 1 = **B**: Michalis co-owns the models ("I just don't want to ruin his job but continue it"; the
  rebuild is the main app). Domain code in `model/intl.py`; store, jobs, CLI in `models/`.
- Stand-in price: the average of the books the Odds API lists (no Stoiximan/Betano there).
- Bet rule: `model.paper`'s 5% edge, so club and international results compare; the report sweeps other
  thresholds. CLV counted where the last price is ≤ 3 h before kick-off: the odds slots (06:30, 11:00, 15:00,
  18:30 UTC) leave ≤ 2 h before any kick-off from 08:30 UTC on, so 3 h keeps every game the schedule covers.
- The model that loses the walk-forward keeps predicting forward, unofficial (its bets not read): out-of-sample rows
  for the phase 5 leaderboard and the phase 7 ensemble, at a few hundred rows a month. Still no tuning after the
  test period is seen.

**In**: a baseline Elo and one real model (time-decayed Poisson) on national-team results since 1872; a walk-forward
comparison of the two; daily forward 1X2 predictions for internationals; a forward report (log loss vs market, bets
at an edge rule, CLV vs the last Odds API price).
**Not in**: O/U (no international O/U prices), club models, a `strategies` table, an endpoint (phase 5), the
promotion gate (phase 7), new odds sources.

## Facts checked 2026-11-12 (dev `bet_p2`, code on `phase2`)

- International fixtures: 50,114 (49,941 with a score), 1872-11-30 → 2026-11-18, 337 national teams. Scored per
  year 2016–2025: 920–1,231 (2020: 356). `decided`: 699 `penalties`, 2 `regular`, rest NULL (martj42 flags only
  shoot-outs; ESPN writes `decided` only when it fills the score first). `neutral` NULL on 175 (ESPN's).
- Odds API on dev: 25 UNL fixtures, 18 bookmakers incl. `pinnacle` (24) and `betfair_ex_eu` (25); one pull each
  (12 Nov 12:58–13:06 UTC); none on a finished game yet. No Stoiximan/Betano.
- Upcoming internationals in ESPN's window: UNL 52, AFCON qual 48, Friendly 41, CONCACAF NL 32 — only UNL priced.
- `predictions_before_match` checks the date only; nothing enforces `predicted_at < kickoff`. Key (model, fixture,
  market, line): one prediction per model version. `v_ledger_*` filter `models.name = 'michalis'`, so other models
  never reach the ledger views. Dev `bet_p2`: 0 models, 0 predictions.
- Reusable: `model/backtest.find_bets` (one bet per game, max EV, odds ≤ 10, CLV = odds × no-vig closing p − 1),
  `summarize`, `model/features.devig` (proportional). `model.paper` bets 1X2 at edge > 5% at `avg`. `model/`
  imports no psycopg; `ingest/` already imports `model.data`.
- Installed: scikit-learn 1.9.1 (`PoissonRegressor`, `LogisticRegression`); scipy 1.17.1 only transitively.
- Worker: ingest slots 05:00, 06:00, 06:30/11:00/15:00/18:30 UTC; paper 07:00. `ingest_tick` takes a `cmd`.

## Models

**Baseline: World Football Elo** (eloratings.net's formula, from memory, not checked on the site).
`R' = R + K·G·(W − We)`, `We = 1/(1 + 10^(−dr/400))`, `dr = R_home − R_away + H` (H = 0 neutral). K: 60 World
Cup; 50 continental finals; 40 qualifiers and Nations Leagues; 30 other tournaments (and any new name); 20
friendlies. G = 1 (margin ≤ 1), 1.5 (2), (11+N)/8 (N ≥ 3), as `model/features.py`. New teams 1500; a shoot-out is
a draw. Constants from a grid on validation years, then frozen: K × {0.75, 1, 1.25}, H {60, 100}. 1X2 from a
multinomial `LogisticRegression` on `dr`, refitted on the 20 years before each block.

**First real model (recommended): Poisson with time decay** (Maher; Dixon–Coles without ρ). Goals ~
Poisson(exp(attack − defence_opp + home·[not neutral])), one row per side per game; sklearn `PoissonRegressor`
(L2 pulls thin-history teams to average). Weights `2^(−age/half-life)`, × 0.5 friendlies; last 10 years. 1X2 from
the score matrix to 10 goals. Grid: half-life {1, 2, 3 y} × alpha {1e-4, 1e-3}. ρ added (1-D grid) only if
walk-forward draw calibration is off by > 2 pp.

Why over an Elo-derived 1X2 with more features: fits all teams jointly; explicit decay (squads turn over); full score
distribution, so O/U needs no second model; refits from Postgres per run (time not measured), no model file; no new
dependency. The check decides, not the literature.

Both refit every run. `models` rows `intl-elo`, `intl-poisson`; version = the frozen constants (e.g. `k1.0-h100`);
a changed constant is a new version, old predictions stay.

## Walk-forward

- Time order only: Elo burn-in from 1872; validation 2008–2013 picks constants; test 2014-01-01 → last scored game,
  reported once, no constant changed after looking.
- Elo updates per game; Poisson refits on the 1st of each month on earlier games (forward refits daily, so the
  test slightly understates it). No outcome reaches its own or an earlier prediction.
- Rows: both teams ≥ 20 games in the prior 10 years; same rows for both models.
- Label: the stored final score (extra time included; martj42 has no 90-minute score; same noise for both).
- Output: log loss, Brier, n per model; paired difference (Poisson − Elo) mean ± SE overall, per year, friendly vs
  competitive; draw calibration. No market column (no international odds history).
- **Pass**: Poisson's log loss below Elo's by > 2 SE → official international model. Else Elo is. The loser keeps
  predicting forward, unofficial; neither is tuned after the test period is seen.

## Forward paper trading

- **Predict**, daily 06:45 UTC (after the 06:30 odds job, before the 07:00 paper slot): international fixtures with
  kick-off in (now, now + 72 h], no score, no prediction from that model version; both teams ≥ 20 games in 10 years
  (else skipped, counted). `predicted_at = now()`, market `1x2`, priced or not. Append-only.
- **Price taken**, per book: last snapshot at or before `predicted_at`, else its first after. `avg` = mean over ≥ 3
  books. Bet rule as `model.paper` (`find_bets`): one outcome per game, EV = p × avg − 1 > 5%, odds ≤ 10; the report
  also sweeps thresholds.
- **Closing**: each book's last snapshot before kick-off. p_close = no-vig `pinnacle` if present, else devigged avg.
  Gap (kick-off − latest `captured_at`) reported (median, worst); CLV counted only where gap ≤ 3 h, the rest labelled
  approximate.
- **Outcome**: `outcome_1x2(hg, ag)` when `decided` is `regular` or NULL (NULL counted); `extra_time`/`penalties`
  void (a second leg's 90-minute score is unknown).
- **Report** (`python -m models report`, read-only, Run button on the Toft docs page): per model settled n; log loss
  vs the other model, the devigged pre-match avg and the close, on the same games; bets, ROI ± SE, CLV, beat-close
  %; exclusions by reason.
- **Coverage gap**: only `soccer_uefa_nations_league` is priced; its league phase ends this window, so priced games
  stop until March 2027 (UNL play-offs, maybe Euro qualifiers; not verified). Log loss accumulates on ~90
  internationals a window; CLV on a few tens.

## Shape (migration 0007, additive, reversible)

One `predictions_before_kickoff` trigger, BEFORE INSERT on `predictions`: rejects a row when the fixture's `kickoff`
is known and `predicted_at >= kickoff` — the fixture's, or that of the same teams in the same competition a day either
side (`kicked_off()`, ingest's `NEAR`: ESPN moves a rescheduled game with its kick-off while the ledger keeps the old
date). `api.ledgers` calls the same function to skip the row. A trigger because a CHECK can't read another table and a composite FK on
kick-off would cascade ESPN's kick-off changes into old predictions. INSERT only: a later kick-off never invalidates a
prediction. Rollback: `migrate down` (drops trigger and functions, no data lost), then the code (its save calls
`kicked_off()`); pre-0007 code on a 0007 database fails a whole save on a late row. Nothing else: bets, prices taken and closes derive
from `predictions` + `odds_snapshots` (a stored copy would drift); the outcome comes from the score.

## Layers

- Domain `models/intl.py`: Elo, its 1X2 mapping, Poisson; pandas/numpy/sklearn only.
- Infrastructure `models/store.py`: history, upcoming fixtures, prices; inserts under `ingest.store.lock`.
- Application `models/jobs.py`: `backtest`, `predict`, `report`.
- Transport `models/__main__.py` (CLI); `api/worker.py`: one `"predict": (06:45,)` entry, last in `INGEST`, own `cmd`.
- Reuses `model.backtest.find_bets`, `summarize`, `model.features.devig` (imported, not copied).

## Who owns the model code (open decision 1)

Steps, checks, tables and worker slot are the same either way. Only these differ:

| | A: new `models/`, `model/` stays Michalis's | B: Michalis owns/co-owns |
|---|---|---|
| Files | `models/intl.py`, `store.py`, `jobs.py`, `__main__.py` | `model/intl.py` (domain only, psycopg stays out of `model/`); store, jobs, CLI in `models/` |
| Review | user | Michalis on `intl.py` and the step 3 result before step 4 merges |
| First model | chosen at the step 3 gate | Michalis may swap it; the check stays |
| `model.backtest` reuse | cross-package: a test pins the numbers on a fixed frame | inside his package |
| Paper job | unaffected (`run_once` copies `model/` only) | `intl.py` rides along in the job's copy, unused |

## Steps

| # | Step | Check |
|---|---|---|
| 0 | Decision 1 answered (B); plan approved | Recorded at the top. Gate |
| 0a | Rescheduled club games (found 2026-11-13, **before 1 Dec**: settle gives up 30 days after the first one). See below | Failing tests first: settle, import and log each with a game moved by a day; production: the 3 ledger rows settle, `ingest check` near-duplicates 0 |
| 1 | Migration 0007 + rollback; the paper job logs no started game; 0008 takes late rows out of the record | Up/down/up; at/after kick-off rejected, before and NULL kick-off accepted; `log` skips a game whose `fixtures.csv` kick-off has passed and writes `logged_at` in UTC; a save projects the rest of a ledger with a late row and `late` counts it; `sync export` keeps it; golden + pg ledger tests green. Existing late rows: see below |
| 2 | Domain: Elo, 1X2 mapping, Poisson | Hand-worked Elo update (1500 v 1500, home 2-0 friendly); Poisson recovers synthetic strengths within 0.05; probabilities sum to 1; dropping games after d changes no prediction before d |
| 3 | `store` + `backtest` CLI on dev | Runtime and peak memory printed (> 300 MB: scipy.sparse, pin scipy); walk-forward table; leak test on real data. User (and Michalis under B) reviews. Gate |
| 4 | `predict` job + 06:45 worker slot | 72 h horizon; thin-history skipped and counted; second run adds 0; trigger holds; worker due/next slot, never 07:00. Live on dev: this window predicted |
| 5 | `report` | On a built fixture set: price before/after `predicted_at`; close = last before kick-off; Pinnacle then avg; ET/pens void; gap filter; one CLV by hand; equals `find_bets` on the same frame |
| 5a | Alerting: the worker runs `ingest check` after the 07:00 slot and notifies on a failed job (paper, ingest, predict), unmapped names > 0, near-duplicates > 0, or no new Odds API prices in 24 h. Channel: a Toft issue in BET-APP (decided 2026-11-13; other integrations later), so the worker needs a Toft API key as a secret the user sets | A forced failure of each kind notifies once; a clean run sends nothing; a notify failure is logged and does not fail the job |
| 6 | Deploy; run after the November window | Settled n > 0 per model; CLV rows > 0 with gaps; `ingest check` and paper job unchanged |

### 0a: rescheduled club games

football-data's `fixtures.csv` keeps a game's old date after it is moved; its season files list the played date.
Production `ingest check` (13 Nov) shows 3 pairs: Tenerife v Andorra 1→2 Nov (SP2), Nacional v Academico Viseu
7→8 Nov (P1), Las Palmas v Eldense 8→9 Nov (SP2); fixture ids 97/1314, 98/1725, 100/1324 (low = ledger, high =
football-data import). Everything keys on the exact date:
- `model.paper.settle` merges on `KEY` (date, league, home, away): the ledger row never settles and drops out after
  `GIVE_UP` (30 days), silently shrinking the sample.
- `ingest.jobs.import_footballdata` matches on (competition, date, teams): the played game becomes a second fixture,
  so one game would be counted twice by any model trained on `fixtures` once both carry a score.
- `model.paper.log` dedups on `KEY`: if `fixtures.csv` moves the date before kick-off, the game is logged and can be
  bet a second time.
Fix direction (plan before code; editing `model/paper.py` is fine, the user has Michalis's OK): the same teams in the same league within a day are
one game (ingest's `NEAR` rule) in settle, log and the football-data import; a one-off cleanup of the 3 duplicate
fixtures (their football-data prices move to the ledger fixture). Decided (2026-11-13): the kept fixture shows the
ledger's date, as `import_espn` already does for a predicted game (`fixtures_kickoff_on_date` allows the day's gap),
so `v_ledger_*` and `sync check` are unchanged. Built: `settle`/`log` match within a day (`model.paper.NEAR`),
`import_footballdata` takes the nearest fixture within a day (and moves an unpredicted one to the played date),
`python -m ingest dedupe` folds the duplicates.
Known gap: ESPN storing the new date before the ledger logs the old one makes a near-duplicate; 5a alerts, `dedupe`
fixes it.

### 1: rows already logged after kick-off (found 2026-11-13)

The ledgers joined to dev's ESPN kick-offs: 21 rows (1X2 16, OU2.5 5), all from the first run, 2026-10-30 23:32
(Konyaspor v Galatasaray 17:00 … Casa Pia v Sp Lisbon 20:15 UTC; late whether that time was UTC or +0200). The
2026-11-06 14:42 run was before every kick-off. Production has the same 21 (checked 2026-11-13 after 0007 went live).

**Decided 2026-11-13 (Nikos; Michalis informed): the forward record counts only predictions stored before kick-off.**
Migration 0008 (`0008_on_time_record`) adds `NOT kicked_off(f.id, pr.predicted_at)` to `v_ledger_1x2` and
`v_ledger_ou25`. Nothing is deleted: the 21 stay in `predictions` and `ledger_*`; `migrate down` brings them back.
The site and the API read the views, so the published 1X2 settled count goes from 286
to 270 and OU 2.5 from 96 to 91. `python -m api.sync check` prints them as "logged after kick-off" apart from the
differences (exit 0); `PostgresLedgers.late` counts them. One rule in `api/ledgers.py` (`LATE`) for the projection,
`diff` and `late`; one in SQL (`kicked_off()`) for the trigger and the views. `sync export`, the worker's backup
and the paper job's working copy read `ledger_*` (`PostgresLedgers.read(with_late=True)`), so the CSVs keep the 21
(review finding: the backup was the only consistent copy and would have lost them). Phase 1's step 6 (1b: drop
`ledger_*`, export from the views) must keep them exportable, e.g. with an unfiltered view. The views filter at
query time: a kick-off ESPN later moves earlier than a stored prediction takes it out too, and `late` changes;
5a alerts on a change in `late`, not only on a new row.
Rejected: deleting the rows (irreversible; the ledgers are the audit trail); keeping them (a record with predictions
made during the game can't be trusted, and `sync check` would fail forever on any new late row).

## Tradeoffs

- **Breaks / who notices**: a ledger row logged at or after its fixture's known kick-off stays in `ledger_*` but is
  left out of the normalized tables (the projection skips it rather than fail the save on the trigger), so it drops
  out of `v_ledger_*`; `python -m api.sync check` counts it as late, exit 0 (5a alerts when the count changes; until then nothing does). `log` now skips started games, so only a
  kick-off ESPN moves earlier between `fixtures.csv` and the log can make one. A failed `predict` loses predictions for games
  before the next run (`ERROR` only).
- **Cost**: no API credits; one daily run of a minute or two of CPU (not measured); a few hundred rows a month.
  Maintenance: the tournament → K map; frozen constants, changed only with a new version.
- **Rejected**: Elo alone (nothing to compare; no score distribution); eloratings.net's ratings (no download or terms
  found); full Dixon–Coles with `scipy.optimize` (+ρ, but a direct import of a transitive dependency); a bets table
  (derivable, would drift).
- **Proven wrong if**: Poisson fails the 2-SE test (Elo becomes the model); the official model's forward log loss is
  worse than the devigged pre-match price over 200+ priced games (keep logging, stop reading bets); forward CLV at 5%
  still negative after ~100 priced bets — with UNL-only prices that's mid-2027+, so the platform plan's 3-month
  verdict can't be reached on internationals without OddsHarvester prices.

## Not verified / open questions

- Elo constants and the Poisson-vs-Elo literature claim are from memory. `PoissonRegressor` runtime/memory on ~20k
  rows × ~500 one-hot columns and sparse input not checked (step 3 measures). Whether the Odds API's `last_update`
  moves only on a price change (else the gap overstates staleness). Which international sport keys it lists from
  March 2027. The late-row count on production `bet` (dev's is from the ledger CSVs).
- Still open: NULL `decided` is settled as 90 minutes (a UNL play-off second leg ESPN missed would be mis-settled).
  Under B, Michalis reviews `model/intl.py` and the step 3 result.
