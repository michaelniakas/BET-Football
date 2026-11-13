# Phase 1: normalized schema under the ledgers

Approved 2026-11-12. Phase 1 of `docs/platform-plan.md`. API responses unchanged; `model/` unchanged.

**Goal**: fixtures, teams, odds and predictions in their own tables with one id per team and fixture;
Michalis's two strategies are the first model writing into them.

**Shape** (migration 0004, reversible)

| Table | Holds | Enforced by the database |
|---|---|---|
| `competitions`, `teams`, `team_aliases` | football-data codes and names to start | A name is unique per source, maps to one team |
| `fixtures` | Competition, season, date, home, away, score, settled outcome per market (`result`, `ou25`) | Home ≠ away; score agrees with every stored outcome; outcome final |
| `odds_snapshots` | Source, bookmaker, market, line, prices (array in outcome order), `captured_at` | Prices > 1, one per outcome, first snapshot kept |
| `market_probs` | Pre-match and closing no-vig probabilities (the ledgers' `mkt_*`, `close_*`) and the method | Sum to 1, first kept |
| `models`, `predictions` | `michalis` + features; probabilities per fixture/market/line, `predicted_at` | Sum to 1; predicted on or before the match date; first kept |

Arrays per market (1x2 = {H,D,A}, ou = {O,U}) keep "sums to 1" and "one per outcome" as plain CHECKs. Markets are
a CHECK in one SQL function (`market_ok`), not a table: adding a market is a migration either way.

The settled outcome is stored per market (not only derived from the score) because rows settled before scores
were kept have none, and a result football-data changed after we settled never gets one.

"Final" and "first kept" are triggers as well as constraints: UPDATE can't change a settled outcome, a score, a
prediction, a price or a market probability. One exception: if football-data corrects a score between the two
markets' settles, the second market's outcome is still stored (the ledger accepted it; a failed save would stop
paper trading), the contradicted score is withdrawn and the corrected one taken if it fits both outcomes. That row
then shows in `check` (the ledger table keeps the old score): a real disagreement, checked by hand, not hidden.
Saves take an advisory lock, so a deploy's overlapping workers queue instead of failing on a new team.

**Steps**

| # | Step | Check |
|---|---|---|
| 1 | Migration 0004 + rollback | Up/down/up; each constraint rejects an invalid row |
| 2 | `PostgresLedgers.save` projects the ledger into the new tables, same transaction; backfill = same code | Existing save tests, now read back through the views |
| 3 | Views `v_ledger_1x2`, `v_ledger_ou25`; `PostgresLedgers.read` reads them | Golden + API tests (CSV vs Postgres) unchanged and green |
| 4 | Backfill on production after the Fri 13 Nov run filled the scores | `python -m api.sync check`: tables vs views, zero differences |
| 5 | Deploy 1a; two worker runs | Same check after each |
| 6 | 1b: save writes the new tables only, export reads the views, drop `ledger_*` (rollback rebuilds them) | Suite green; rollback run on a production copy |

**Not in phase 1**: `strategies` table (phase 3), `research` (phase 4), new sources (phase 2).
