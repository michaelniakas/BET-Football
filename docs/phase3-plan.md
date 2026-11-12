# Phase 3: international model + forward paper trading

**Draft 2026-11-12, not approved.** Phase 3 of `docs/platform-plan.md` (check: walk-forward log loss vs an Elo
baseline; forward CLV accumulating). Branch `phase3` from `phase2`; deploys after 0006. Blocked by open decision 1
(§ Who owns the model code); every step below holds under either answer.

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
- **Pass**: Poisson's log loss below Elo's by > 2 SE → official international model. Else Elo is, and Poisson is
  deleted, not tuned until it passes.

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
is known and `predicted_at >= kickoff`. A trigger because a CHECK can't read another table and a composite FK on
kick-off would cascade ESPN's kick-off changes into old predictions. INSERT only: a later kick-off never invalidates a
prediction. Rollback: drop trigger and function, no data lost. Nothing else: bets, prices taken and closes derive
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
| 0 | Decision 1 answered; plan approved | Recorded at the top. Gate |
| 1 | Migration 0007 + rollback | Up/down/up; at/after kick-off rejected, before and NULL kick-off accepted; 0 existing `predicted_at >= kickoff` rows on dev and production; golden + pg ledger tests green |
| 2 | Domain: Elo, 1X2 mapping, Poisson | Hand-worked Elo update (1500 v 1500, home 2-0 friendly); Poisson recovers synthetic strengths within 0.05; probabilities sum to 1; dropping games after d changes no prediction before d |
| 3 | `store` + `backtest` CLI on dev | Runtime and peak memory printed (> 300 MB: scipy.sparse, pin scipy); walk-forward table; leak test on real data. User (and Michalis under B) reviews. Gate |
| 4 | `predict` job + 06:45 worker slot | 72 h horizon; thin-history skipped and counted; second run adds 0; trigger holds; worker due/next slot, never 07:00. Live on dev: this window predicted |
| 5 | `report` | On a built fixture set: price before/after `predicted_at`; close = last before kick-off; Pinnacle then avg; ET/pens void; gap filter; one CLV by hand; equals `find_bets` on the same frame |
| 6 | Deploy; run after the November window | Settled n > 0 per model; CLV rows > 0 with gaps; `ingest check` and paper job unchanged |

## Tradeoffs

- **Breaks / who notices**: the trigger also guards the paper job's projection — one ledger row logged after ESPN's
  kick-off fails the whole save (worker `ERROR`, stale "last update", nobody mailed). Step 1 proves none exists today;
  a club kick-off ESPN moves before a 07:00 log could create one. A failed `predict` loses predictions for games
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
  March 2027. The trigger precondition on production `bet`.
- For the user: decision 1 (A or B); betting at the average of the listed books as the stand-in price (no
  Stoiximan/Betano); 5% edge and 3 h gap cutoff; if Poisson loses, delete it or keep it predicting for the phase 5
  leaderboard; NULL `decided` settled as 90 minutes (a missed UNL play-off second leg would be mis-settled).
