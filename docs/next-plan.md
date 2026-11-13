# Next plan: better data, honest models, analytics, a UI anyone can read

Draft 2026-11-13, written for Michalis and Nick. **Nothing here is approved or built.** It follows
`docs/platform-plan.md` (phases 0–2 and 3/0a done) and reorders what is left of it. One phase = one branch + one PR
with tests; each can ship alone.

## 1. Where we are (read from the repo on `main` @ `599193a`, 2026-11-13)

**Running in production — do not break**
- Toft worker `bet-app-worker` (`api/worker.py`) + Postgres `bet-app-db`. Paper job Tue/Fri 07:00 UTC runs
  `model.paper weekly` unchanged in a copy of `model/`, ledgers exported from and written back to Postgres.
  Ingest slots: football-data 05:00, martj42 + ESPN 06:00, Odds API (national teams) 06:30/11:00/15:00/18:30 UTC.
- **The GitHub Actions `paper-trading` workflow is no longer the job.** `docs/rebuild-plan.md` records the cutover on
  2026-11-11 and the workflow as disabled on GitHub; the file is still in the repo as the rollback path. The CSV
  ledgers in `model/paper/` stopped at the 2026-11-10 commit; Postgres is the truth. (Not verified from here: no `gh`
  on this machine, no production access.)
- Two frozen strategies in `model/paper.py`: `ou25` (top-5, stats+market, edge > 6%, the hypothesis) and `1x2`
  (16 leagues, stats+market, edge > 5%, the control). Their definition must not change: a changed feature set is a
  new model row, never an edit, or the forward record is worthless.
- App: FastAPI (`openapi.yaml` is the contract, 10 operations) + React SPA (Picks, Results, Coupon, Explained;
  EN/EL typed dictionary; light/dark). CI: contract lint + drift, pytest with Postgres, web typecheck + build, image.

**In Postgres** (migrations 0001–0006): `competitions`, `teams`, `team_aliases`, `fixtures`, `fixture_refs`,
`odds_snapshots`, `market_probs`, `models`, `predictions`, `unmapped_names`, `paper_runs`. 264,599 club games
2000/01+ with 3.2M prices (25 books, 1X2 + O/U 2.5, pre-match and closing; closing from 2012/13), ~50k
internationals since 1872, national-team prices for the Nations League only.

**Forward record so far** (last CSV, to 2026-11-09): 1X2 286 settled, log loss model 0.925 / pre-match market 0.927 /
closing 0.921; 0 picks above 5%; at edge > 0%, 49 bets, CLV −3.0%. O/U 96 settled, 0 picks above 6%.

**Not built yet**: phase 3 steps 1–6 (international Elo/Poisson, `models/` package, migration 0007, alerting 5a),
3b (UEFA), 4 (research job), 5 (leaderboard), 6 (match drawer), 7 (retrain gate). News pilot produced 52 club + 33
international research rows; `research/evaluate.py` exists, verdict not recorded.

**Gaps found while reading** (each is addressed by a phase below)
1. `model/` has no direct tests: nothing asserts features are pre-kick-off or that closing odds cannot be a feature.
   CI does not run `research/test_*.py`.
2. No club odds time series. football-data gives one untimed pre-match price (collected Fri/Tue) and one closing
   price. "Opening to close" exists only for Nations League games, 4 pulls a day on the free 500-credit key that the
   Coupon page's live odds also spend from.
3. `features.add_market` takes Pinnacle first; football-data says Pinnacle is stale from 2025-07-23, so 2025/26
   training rows use stale market and closing probabilities (already noted in `platform-plan.md`, not fixed).
4. The market feature mixes sources over time (Pinnacle → Betfair Exchange → average) and de-vigs all three
   proportionally; exchange prices carry commission, not margin.
5. CLV is measured at the *average* book against a *no-vig* close, so a bettor with zero skill scores about minus
   the book margin. The reported −3 to −5% may be "no skill", not "negative skill". My inference from the formula in
   `backtest.find_bets`, not measured: Phase A measures the no-skill reference so every CLV number has a zero point.
6. `hgb` uses sklearn's random 15% early-stopping split inside the training seasons (not time-ordered). It does not
   leak into the test season but it flatters model selection.
7. ROI has a standard error, CLV has none; neither accounts for many bets on the same weekend.
8. The UI has no responsible-gambling notice, no stake limits, no frontend tests.
9. `platform-plan.md` contradicts itself on scraping: OddsPortal via OddsHarvester and a Transfermarkt dump are in
   the "added" table, while the text says OddsPortal stays out and Transfermarkt's terms likely forbid reuse. Under
   the constraints of this plan (ToS/robots only) both are out unless you decide otherwise (decision D2).

## 2. Rules every phase follows

- **Promotion gate**: a feature, source or model reaches the paper ledgers only after a walk-forward ablation against
  the current baseline on the *same matches*, with paired block-bootstrap CIs (blocks = match week). Pass = log loss
  better than the pre-match market with the CI excluding zero **and** CLV above the no-skill reference with the CI
  excluding zero. Thresholds are fixed before the run (decision D6).
- **One touched-once holdout**: the last two full seasons are never used for tuning. With ~6 model families × 3–4
  markets × league groups, some cell will look good by chance; only the holdout result counts.
- **No leakage, asserted**: features pre-kick-off only; closing columns rejected by name in the feature builder;
  truncation test (deleting every game after date *d* changes no prediction before *d*) on each model.
- **Production safety**: new models write `predictions` under their own `models` row (the ledger views filter
  `michalis`, so nothing new reaches Picks/Results until promoted); migrations additive with a tested rollback; new
  worker slots never at 07:00; `model/paper.py` strategies and the ledgers change only through a reviewed PR.
- **Reporting**: each PR description carries what changed, the ablation table, what is still unverified.
- No real-money placement, no bookmaker-account automation, no CAPTCHA/bot-check bypass, no logins we don't own.

## 3. Phases

Order: A and B start together (B is a document). C starts as soon as its budget is approved, because a time series
only accumulates forward. D can ship any time. E–G need A. H needs A (and C for timing). I needs G or H.

### A. Evaluation harness and guardrails — no new model
Branch `eval-harness`. The foundation; nothing else is credible without it.
- `eval/` package: loads one frozen evaluation frame from Postgres (fixtures + `odds_snapshots`), runs a model
  config walk-forward, writes a registry row (version, git sha, training window, features, constants, metrics) to
  `models` + a JSON artefact. One command: `python -m eval run CONFIG`.
- Metrics per model / market / league / season / edge bucket: log loss and Brier vs pre-match and vs closing (paired
  differences with CIs), calibration table + reliability plot, CLV with SE, beat-close %, ROI with bootstrap CI,
  and the **no-skill CLV reference** (random and market-only picks at the same book).
- Ablations on the existing model only: Pinnacle dropped after 2025-07-23; de-vig proportional vs power vs Shin;
  one market source for the feature vs the current mix.
- Tests: leakage (name guard, truncation), `devig`, `find_bets` and CLV against hand-worked numbers; CI also runs
  `research/` tests.
- **Check**: reproduces the `model/README.md` tables within rounding from Postgres (or explains each difference);
  this becomes registry entry #0, the baseline everything is compared with.
- **Could show**: that today's numbers move once Pinnacle-stale rows are fixed. Reported either way.

### B. Data source inventory — document only
Branch `data-sources` → `docs/data-sources.md`. For each candidate: coverage (leagues, seasons), fields, cost, rate
limits, licence/ToS and robots (quoted, with date read), reliability, and "does it carry information the market
prices late?". Each verified by one live request this month, not from memory or from the older plan. Candidates:
football-data, Understat (already blocks GitHub runners), FBref/StatsBomb open data, ClubElo, API-Football,
Sportmonks, The Odds API (live + historical), Betfair historical, ESPN (in use, terms unread), Transfermarkt,
Open-Meteo (weather), referee data, openfootball (UEFA fixtures for congestion).
- **Check**: every row has a "verified on" date and a ToS verdict (allowed / forbidden / unclear → not used).
- **Output**: a costed recommendation for D1. Starting estimate from `platform-plan.md`, unverified this month:
  The Odds API 20K plan ~$30/mo + API-Football Pro ~$16/mo.

### C. Our own odds time series (clubs) + data-quality gates
Branch `odds-series`. Needs D1 (a paid Odds API plan; the free 500 credits cannot cover 16 leagues).
- Snapshot job for the 16 leagues: 1X2, totals, Asian handicap, BTTS where offered; sparse from opening, dense in the
  last 24 h, a final pull close to kick-off. Stored in `odds_snapshots` as today (timed rows). Credit budget
  enforced in code, as the national-team job does.
- CLV for new models is measured against a *timed* close per book, with the gap to kick-off stored.
- Quality gates that fail the run loudly: rows per league-day vs fixtures, NULL and bad-price rates, share of
  fixtures with a snapshot within 3 h of kick-off, unmapped names, near-duplicates, drift vs the trailing 4 weeks.
- Alerting (phase 3 step 5a, carried over): a failed job or a failed gate notifies once (decision D9: channel).
- **Check**: two weeks live; every played fixture has ≥ N timed snapshots and a close within 3 h; a forced failure of
  each gate alerts.
- **Why first among the paid items**: line movement is the one dataset nobody gives us later, and "bet early vs bet
  late" (H) cannot be studied without it.

### D. UI part 1 — honesty and safety, no model dependency
Branch `ui-plain`. Greek first.
- "Does the model work?" page: one verdict sentence (today: "Not proven. In N matches the model has not beaten the
  bookmakers' closing prices."), a CLV-over-time trend, three plain sentences on what would change the verdict.
- Plain-language pass over Picks/Results/Coupon (jargon moved behind the existing "i" glossary); an evidence note
  on every pick while the verdict is "not proven".
- Responsible-gambling notice, age notice and helpline (exact Greek wording and number to be verified against the
  regulator's current text, not written from memory), optional personal stake limit in the Coupon builder.
- Frontend tests (new dev dependencies, decision D8): component tests + one Playwright flow per page on phone and
  desktop widths, axe accessibility check, in CI.
- **Check**: a first-time-bettor walkthrough script (find a pick, understand why, build a coupon, read the risk)
  passes in EL and EN; contract-first for any new endpoint.

### E. Model ablations on data we already have
Branches `model-dc`, `model-residual`, `model-gbm`, `model-stack` — one PR each, each with its table.
1. Dixon–Coles with time decay (one model → 1X2, O/U lines, BTTS, AH probabilities).
2. Market-anchored residual: the market logit as an offset, the model predicts only the correction. This is the
   form later information features (G) plug into.
3. Gradient boosting with strict forward-chaining CV (replaces the random early-stopping split).
4. Calibrated stacking of whatever survived, only if at least two members pass on validation.
5. Bayesian hierarchical team strength: only if (1) shows signal, because it adds a heavy dependency (decision D8).
- **Check**: the promotion gate on the holdout. Calibration plot per model.
- **Honest prior**: on 1X2 in the big leagues these will most likely tie the pre-match market and lose to the close,
  as Elo/form/xG did. The value of this phase is the reusable residual model and a clean "no" with numbers.

### F. Markets and leagues the current model never tested
Branch `markets`. From data already in Postgres or free in football-data files: lower divisions (E2, E3, EC, SC1–3)
and the 16 extra leagues (closing 1X2 only), O/U 2.5 across all leagues, Asian handicap from 2005/06 if D4 says yes.
BTTS and alternative totals have no free price history: forward only, from C.
- **Check**: same gate, per league group. The 130–230-bet O/U result is re-run here with CIs and the holdout; it is
  confirmed or retired.

### G. Late-information features
Branches per feature family. Backtestable first: rest days / fixture congestion incl. European games, travel
distance, referee, weather, manager change. Then forward-only: confirmed line-ups, injuries and suspensions
(API-Football snapshots), LLM team news (existing pilot).
- A late-information feature is judged against the price available *when the information existed*, so most of these
  can only be proven forward against C's time series; the backtest against football-data's untimed pre-match price
  is a screen, not proof.
- **Check**: residual model with vs without the feature family, same matches; forward predictions stored for both.

### H. Analytics
Branch `analytics`. Contract first (`openapi.yaml`), then endpoints, then the page.
- Leaderboard per model / market / league / edge bucket (the metrics of A, from stored predictions).
- Sequential test per strategy on CLV above the no-skill reference: verdict "real / not yet / dead" with pre-set error
  rates, so a strategy is stopped by rule, not by mood. Method (mixture SPRT vs Bayesian) proposed with a simulation
  of its false-stop rate before it is trusted.
- Bankroll simulation (flat vs fractional Kelly), drawdown, time-to-ruin.
- Drift: PSI on features, calibration drift; a weekly report job (its own worker slot) stored and shown in the app.
- **Check**: every metric recomputed independently in tests; the sequential test's error rates confirmed on
  simulated data with known edge 0 and known edge > 0.

### I. UI part 2 — picks that explain themselves
Branch `ui-picks`. Needs H (verdict) and a promoted or at least scored model.
- Each pick: what, why (top 2–3 reasons in one sentence, from the model's own contributions, never invented),
  traffic light for confidence, the evidence note.
- Coupon builder: correlation warning (same match, same league-day), risk in plain words, limit reminder.
- Match drawer: every model's probabilities, our odds history sparkline, result.
- **Check**: the walkthrough of D on a phone; screenshots EL/EN × light/dark × phone/desktop.

## 4. What happens to the older plan

| `platform-plan.md` item | Here |
|---|---|
| 3 International model | **Pause proposed** (D3): priced national-team games stop until March 2027, so it cannot be judged |
| 3b UEFA club competitions | Fixtures only, as a congestion feature in G; a UEFA model waits |
| 4 Research job (LLM news) | G, forward-only, after the pilot's 85 rows are evaluated and D1 sets a cap |
| 5 Leaderboard | H |
| 6 Frontend drawer, badges | D and I |
| 7 Retrain + promotion gate | The gate is §2; scheduled retraining once a model is promoted |

## 5. Decisions that are yours (Michalis / Nick)

| # | Decision | My recommendation |
|---|---|---|
| D1 | Monthly budget: odds data, API-Football, LLM research (separate caps) | Approve ~$50/mo data after B verifies prices; LLM cap separate and small until G |
| D2 | Scraping: OddsPortal/OddsHarvester and the Transfermarkt dump | Out. Only sources whose terms allow it |
| D3 | International model: continue or pause | Pause until priced games return |
| D4 | Asian handicap in scope? (phase 2b recorded "not wanted") | Yes for modelling: lowest-margin market, history is free |
| D5 | Which price a bet is judged at: average book, or a named book you can really bet with | Keep average for continuity, add one named book once C shows which feeds carry it |
| D6 | Pass / kill numbers, fixed in advance: CI level, minimum bets, the holdout seasons | 95% CI, ≥ 1,000 bets in backtest, holdout = last two full seasons |
| D7 | The two running strategies stay frozen until the sequential test (H) rules on them | Yes |
| D8 | New dependencies: scipy (direct), a Bayesian library, LightGBM vs sklearn, Playwright + a test runner | scipy and frontend tests yes; Bayesian library only if E.1 earns it |
| D9 | Alert channel (Toft issue needs an API key as a secret) and who gets alerts | Toft issue, as decided on 2026-11-13 |
| D10 | Ownership and review: who must approve PRs touching `model/`, `api/`, `web/`; branch protection on `main` | Michalis on `model/` + `eval/`, Nick on `api/` + `web/`, CI required |
| D11 | Audience: stays 2–3 private users, or wider (changes the legal weight of the gambling notice and data licences) | Private; revisit before sharing |
| D12 | `paper-trading.yml`: keep as rollback or delete | Keep, with a header comment that it is disabled |
| D13 | Stop rule for the whole project | If after C + E + G no model passes the gate forward by an agreed date, keep the app as an honest odds-comparison and tracking tool and stop model spend |

## 6. Not verified in this draft

- Production state (worker health, Postgres row counts, whether phase 1 steps 4–6 and the 0a cleanup ran); that the
  workflow is disabled on GitHub. Read from the docs only.
- No code or backtest was run. Gap 5 (CLV zero point) is reasoning from the formula.
- No data source was contacted; prices and limits quoted are the 2026-11-12 notes in `platform-plan.md`.
- The news pilot's evaluation result.
