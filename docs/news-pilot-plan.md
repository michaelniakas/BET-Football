# News pilot + History page

Approved 2026-11-09 (user: "I agree with all, let's go"). Runs before Phase 3 step 3 (the worker), because the
$200 Claude API promotional credit expires 2026-11-13 (UTC; treat 2026-11-12 23:59 UTC as the last safe moment).

## Part A: News pilot (deadline 2026-11-12)

**Question it answers**: is per-fixture news research by Claude worth paying for after the credit is gone?
It cannot prove an edge in 4 days (needs hundreds of settled bets); it measures cost per fixture and output
quality, and starts a forward-only record whose CLV is judged later. Past news can't be backtested: articles
about past matches are written after them (look-ahead bias).

**Constraints**: spending cap **$65** (raised from $50 on 2026-11-09: all 150 fixtures measured at ~$56), enforced in code from `response.usage` (and a Console workspace spend limit
if the user sets one). Lives in `research/`, outside `model/` (Michalis's). Runs locally, not in the image.
Every record has `researched_at` before kick-off, plus model id and prompt version.

| Step | Check |
|---|---|
| 1. `requirements-research.txt`: `anthropic` 2.3.0 (`sports-skills` dropped 2026-11-09: it lacks D2, SP2, F2, G1; ESPN's scoreboard via `requests` covers all 16). Key from `ANTHROPIC_API_KEY` (user's shell profile, never in the repo). | One 1-token call succeeds; `pip check` clean. Done 2026-11-09. |
| 2. Team-name mapping `research/teams.csv` (source, league, source name -> football-data name). `difflib` first, Claude only for leftovers. | Every fixture of this week's ESPN list in our 16 leagues maps; 20 random rows checked by hand. |
| 3. Fixtures from ESPN's scoreboard (football-data's list only appears on Friday, after the credit expires). International break (decided 2026-11-09): 11 SP2 games on 13-16 Nov + 139 games on 20-23 Nov (15 leagues, P1 resumes 27 Nov), researched on 12 Nov, so 8-11 days early for the 139; a `days_to_kickoff` column keeps them separable in the evaluation. If the 3 test outputs of step 4 are mostly "no news yet", stop at the 11. | Counts per league vs ESPN day-by-day: 150 fixtures total. |
| 4. Research call per fixture: `claude-opus-5-5`, `web_search_20260209` (max_uses 15, measured 2026-11-09: at 5 the model ran out and reported no news; prompt caching on, $0.36 per fixture vs $0.92 without, one sample) + Google News RSS headlines in the prompt, answer through one strict tool: absences, rotation risk, motivation, p_H/p_D/p_A, confidence, sources (URLs), note_en, note_el. Run 3 fixtures, measure real cost, extrapolate, then the rest under the cap. Output `research/news_1x2.csv` keyed (date, league, home, away). | Cost per fixture from usage; 3 outputs read by hand; cap stops the run; researched_at < kick-off for every row. |
| 5. `research/evaluate.py`: once settled, join with `ledger_1x2` -> log loss / Brier for news vs model vs market pre-match vs closing, CLV of news picks. | Runs on the first settled fixtures, SP2 13-16 Nov (numbers, no claims: n is tiny). |

Ledger of the pilot goes to `main` next to the paper ledgers only if the user asks (the app reads `main`).

## Part B: History page (after the pilot)

"Where it worked and where it didn't": every settled fixture of a strategy with model vs market vs closing
probability, result, the pick at min_edge (if any), odds, profit, CLV; filters league, team search, date range,
outcome (won / lost / no bet); breakdown by league and edge bucket; EN/EL.

- Read `/results/bets` and `/results/summary` first: extend them (filters, a search param, a by-league breakdown)
  before adding an endpoint. Contract first in `openapi.yaml`, same pagination/sort convention as the rest.
- News notes appear on the page only once they live in Postgres (Phase 3).
- **Check**: contract + endpoint tests on both ledger implementations, values computed independently in the test
  from the fixture ledgers, Chrome screenshot per language.

Then Phase 3 step 3 (worker) resumes as in `docs/HANDOFF.md`.
