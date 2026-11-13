# Platform plan: many models, club + international, full history

Approved 2026-11-12 (user: "ok i agree"). Follows `docs/rebuild-plan.md` (phases 1–3 done) and
`docs/news-pilot-plan.md`. One phase per session; each phase deploys on its own.

## Why this shape

Michalis's backtest (`model/README.md`, ~56k out-of-sample matches) found that public stats (Elo, form, shots, xG)
add nothing beyond the odds; the one lead (O/U stats+market, positive CLV) rests on 223 bets. More models on the
same public data will not find an edge. What can: information the price hasn't absorbed yet (team news via LLM
research) and markets priced less sharply (international friendlies, Nations League). The platform exists to
measure that honestly: every prediction stored before kick-off, scored against pre-match and closing prices.

## Target architecture

A modular monolith: one app + one worker (same image) + Postgres. No new service: training is logistic regression
on ~100k rows, and nothing needs its own scaling, runtime or reliability tier.

**Schema** (normalized; the per-strategy `ledger_*` tables become a view over it):

| Table | Holds |
|---|---|
| `competitions`, `teams`, `team_aliases` | One identity per team across football-data, ESPN, The Odds API, the international results set |
| `fixtures` | Competition, kick-off, neutral venue, status, final score; club and national teams alike |
| `odds_snapshots` | Every price pulled: source, bookmaker, market, line, prices, `captured_at`; closing = last before kick-off |
| `research` | LLM output per fixture: provider, model, prompt version, `researched_at`, structured fields, sources, cost |
| `models`, `predictions` | Each model version's probabilities per fixture and market, append-only, `predicted_at` < kick-off |
| `strategies` | Model + market + edge rule + book; the official strategy per market is what Picks/Results show |

DDD tier: service + repository, no aggregates (reporting and computation). Invariants live in the database:
append-only predictions, `predicted_at < kickoff`, a score that agrees with the result, first snapshot kept.

**Models** (each a producer writing `predictions`):
1. Michalis's model (`model/`), unchanged, through an adapter as the worker runs it today.
2. International model: Elo / Dixon-Coles-style on national-team results history; walk-forward log loss.
3. LLM as a model: its own p_*, scored like any other.
4. Base + news: base probabilities adjusted by the research features.
5. Ensemble: only once each member has ~1,000 settled forward predictions (before that a stacker fits noise).

**Worker jobs** (scheduled in Postgres, as `paper_runs`): ingest fixtures/results (daily), odds snapshots (several
a day, denser near kick-off), research (~24 h before kick-off), predict, settle, weekly retrain with a promotion
gate (a new version replaces the old only if it beats it on the holdout; old versions kept).

**LLM provider**: one function per provider behind one call site; Claude first (what the pilot measures). A second
provider is added when used, not as a speculative interface.

## Frontend: three concepts, one place each

- **Competition** (where): one scope filter, "All · Club leagues · National teams", league chips inside.
- **Market** (what): the existing switch.
- **Model** (who predicted): Picks and Results show the official strategy per market, so the main pages read as
  today. Other models appear as badges on a match ("News +4%", "Intl Elo agrees").
- **Match drawer**: any match → every model's probabilities, odds history sparkline, Claude's note (EN/EL) with
  sources, result. Depth lives here, not in the lists.
- **Models leaderboard** (tab under Results): log loss vs market, CLV, bets, verdict per model.

## Phases

| # | Phase | Check |
|---|---|---|
| 0 | News pilot run (12 Nov) | Cost per fixture; `researched_at` < kick-off on every row; decides the LLM scale |
| 1 | Normalized schema beside the ledgers (expand/contract), backfill, API reads the new tables | Golden tests: API responses identical to today |
| 2 | Ingestion: club + international fixtures/results, historical odds import, odds snapshots, team mapping | Counts vs sources; zero unmapped teams; closing line present where the source has one |
| 3 | International model + forward paper trading | Walk-forward log loss vs Elo baseline; forward CLV accumulating |
| 3b | UEFA club competitions (Champions, Europa, Conference League): fixtures, results, odds snapshots; one club rating across leagues; forward paper trading | Zero unmapped clubs between UEFA and domestic sources; walk-forward log loss on UEFA games vs the per-league model and vs the market; forward CLV accumulating |
| 4 | Research job + LLM and news-adjusted models | Provenance + cost on every row; monthly cap enforced in code |
| 5 | Model scoring + leaderboard endpoint (contract first) | Metrics recomputed independently in tests |
| 6 | Frontend: scope filter, badges, match drawer, leaderboard | Screenshots EN/EL × light/dark × desktop/phone |
| 7 | Retraining with promotion gate; ensemble when data allows | A worse version is never promoted (test) |

## Historical data sources

Research requested 2026-11-12: club and international history with odds, for measuring models against market
prices. Findings below; nothing is downloaded or paid for until the user picks.

"Checked" = confirmed on the source itself this session; the rest is from the research agents' reading of vendor
pages (several returned 403) and is marked "not verified".

**Club leagues**

| Source | What it adds | Cost | Status |
|---|---|---|---|
| football-data.co.uk, more files | Results from 1993/94, odds from 2000/01, O/U + Asian handicap from 2005/06, closing ("C" columns) from 2019/20 (Pinnacle closing from 2012/13); E2, E3, EC, SC1–3 and 16 "extra leagues" (ARG, AUT, BRA, CHN, DNK, FIN, IRL, JPN, MEX, NOR, POL, ROU, RUS, SWE, SWZ, USA, from 2012/13). No timestamps, no UEFA club competitions, no cups. | Free | Pinnacle note **checked**; terms ("private use; no commercial/AI-training use") not verified |
| The Odds API, historical | Snapshots of any market/bookmaker from 2020-06-06, every 10 min (5 min from Sep 2022): a real time series, so opening and closing. Covers UEFA club competitions if the sport keys exist historically (not verified). | Paid plans only; 10 credits per region per market per call. 20K credits $30/mo, 100K $59, 5M $119 | Start date, interval, cost **checked**; plan prices not verified |
| Betfair Exchange historic data | Nearly all Exchange markets since 2016, Basic tier free (1-min last traded price) | Basic free; others not verified | Not verified (403); terms unread |
| API-Football (api-sports) | All competitions incl. national teams, cups, UEFA; fixtures, results, line-ups, injuries, sidelined, events, statistics. Pre-match odds 1–14 days before kick-off, refreshed every 3 h, kept **7 days** only: no odds history, a forward feed we would snapshot ourselves. | Free 100 req/day (fewer seasons); Pro $16/mo 7.5K/day, Ultra $26 75K/day, Mega $36 150K/day | Prices, plan contents, odds window and refresh **checked** (official pricing page + docs, 2026-11-12) |
| OddsPapi, TheStatsAPI, Sportmonks | Many bookmakers/markets claimed | Free tier / $50–379 / custom | Vendor claims only: trial before trusting |
| OddsPortal, BetExplorer | Wide history, opening + closing | Scraping only | Avoid: ToS unread, scraping likely forbidden |

**International games**

| Source | What it adds | Cost | Status |
|---|---|---|---|
| martj42/international_results (GitHub) | 49,940 men's internationals 1872 → 2026-10-06: date, teams, score, tournament, city, country, neutral; plus shootouts, goalscorers, former names. No odds. CC0. | Free | Row count, columns, last update (2026-10-31) **checked** |
| The Odds API, historical | The only confirmed route to national-team odds: from 2020-06 at best. Which international sport keys have history is not verified. | As above | One cheap test call before relying on it |
| TheStatsAPI | Opening + last-seen prices, several markets; national-team coverage unknown | $50–129/mo, 7-day trial | Not verified |
| eloratings.net | World Football Elo | — | No download/terms found: compute Elo ourselves from martj42 |

Bulk free odds history for national teams does not exist as far as the research found: results go back to 1872,
odds to 2020 at most. The international model is trained on results (log loss) and judged against the market only
on 2020+ and forward data.

**Chosen: free-first** (user, 2026-11-12: everything free except the LLM news; ready datasets and scrapers)

| Need | Free source | Already in the code |
|---|---|---|
| Club history + odds | football-data full set: 2000/01+, more divisions, extra leagues; closing from 2019/20 | `model/data.py` (fewer files) |
| Club forward odds + closing | football-data `fixtures.csv` (pre-match) and the weekly result files (closing) | `model/predict.py`, `model/paper.py` |
| Older odds time series | BeatTheBookie files (Dropbox, ~2005–2015 by its README); Kaggle European Soccer Database (2008–2016, up to 10 bookmakers) | No. Size, licence and coverage not verified: check before importing |
| International history | martj42 (CC0, 1872+) | No |
| International fixtures + results | ESPN scoreboard JSON | `research/espn.py`, `research/intl.py`. Unofficial, terms not verified |
| International forward odds | The Odds API free tier: 500 credits/month, 1 credit per sport × market × region call (~16 calls/day) | `research/intl.py` |
| Injuries / line-ups baseline | API-Football Free, 100 requests/day; which seasons it covers not verified | No |
| Team news | LLM research (paid, the exception) | `research/news.py` |

Added 2026-11-12 (user: "go with best options"; private tool for 2–3 people, nothing redistributed):

| Need | Source | Note |
|---|---|---|
| Team-name mapping seed | `openfootball/clubs` (CC0) | Names, alternative spellings, stadiums worldwide |
| UEFA club results | `openfootball/champions-league`, `europe` (CC0), 2011/12+ | Only place clubs from different leagues meet: cross-league ratings. No odds |
| Club Elo history | `xgabora/Club-Football-Match-Data` `EloRatings.csv` (ClubElo, names matched to football-data) | Baseline feature; its match file is a thinner football-data copy, not used |
| Historical team news | `salimt/football-datasets` (Transfermarkt): 143k injuries with dates, appearances | No licence; Transfermarkt terms likely forbid reuse. Private training only, never shown or shared |
| Outside benchmark model | footballcharts (free key, personal use, 100–200 req/day) | Forward only, on the leaderboard |
| Odds history incl. internationals/UEFA, opening + movement | OddsPortal via `jordantete/OddsHarvester` (Playwright) | Blocked in Greece by law; runs from the worker's real location (Hetzner, Germany), no VPN or proxy. One page at a time, seconds apart, honest browser identity; a block stops the job. New dependency: Playwright + Chromium in the worker image |
| Price the bets are judged at | Stoiximan (users' main book) or Betano (same company) where a source carries it | Which sources carry either: not verified |

Not used: `omkarcloud/flashscore-scraper` (impersonates a browser's TLS fingerprint and replays the site's private
feed token: bot-detection circumvention); pipeworx and `datasets/football-datasets` (football-data wrappers);
StatsBomb open data (too narrow); `football-docs`, `livescoreFootball` (not data / England+Spain only).

Scrapers only where the site's terms and `robots.txt` allow it, rate-limited, identifying as a normal client,
never past a bot check or CAPTCHA. OddsPortal/BetExplorer stay out.

What free costs:
- No timestamped odds history for internationals or UEFA club competitions: the international model is judged
  against the market only on forward data, and that accumulates from now.
- Forward international odds are one or two snapshots a day (credit limit), so "closing" is the last snapshot
  before kick-off, not the true close. CLV on internationals is therefore approximate; stored as such.
- Club closing odds come from football-data a few days after the match: settle stays as it is today.
- Upgrade path if free proves too thin: the paid combination below, unchanged.

**Paid combination (not chosen; kept for reference)**
1. History: football-data's full set (free), 2000/01+, more divisions and the extra leagues; martj42 for
   internationals.
2. Odds history 2020+ (closing line with timestamps, UEFA club competitions, internationals): one month of The Odds
   API 5M plan ($119) for the backfill, then down to 20K ($30). Before paying for it, one month of 20K to test which
   sport keys have history. Backfill estimate (mine, unmeasured): ~20 sports × 6 seasons × 100–200 kick-off slots ×
   1–2 snapshots × 10 credits × 2 markets ≈ 0.25–1M credits.
3. Forward snapshots: The Odds API live `/odds` costs markets × regions per call and returns every event of a
   sport: ~23 sports × 4 a day × 30 × 2 markets × 1 region ≈ 5.5K credits/month, inside the $30 plan.
4. API-Football Pro ($16/mo) as the fixtures/results/line-ups/injuries backbone for every competition (club, UEFA,
   national teams): one ID system instead of ESPN + per-source name mapping, and structured injuries as a cheap
   "team news" baseline the LLM research has to beat. Its odds only as a second forward snapshot source (7-day
   retention: we store them ourselves). Not a history source.
5. Betfair Basic (free) for an opening-to-close series since 2016 once its terms and soccer coverage are checked.
6. Running cost: ~$46/mo (Odds API $30 + API-Football $16) after a one-off ~$119 month; LLM research on top.

**Found while checking**: football-data says Pinnacle odds are systematically stale since 2025-07-23 (pre-closing
and closing). `model/features.py` `add_market` takes Pinnacle first when present: E0 2025/26 has it on 210 of 380
rows, 2026/27 on none (falls back to Betfair). So 2025/26 training rows use stale market and closing probabilities.
`model/` is Michalis's: reported, not changed. The paper ledgers (Oct 2026+) are not affected.

## Tradeoffs

- **Breaks**: phase 1 moves the source of truth off the ledger tables; golden tests + expand/contract keep the old
  path until the switch.
- **Running cost**: data free (free tiers; dense snapshots and odds history given up, see above). LLM measured at $0.36–0.92 per fixture on few samples: ~$230–660/month at full coverage
  (~150–180 fixtures/week); researching only fixtures where some model already sees an edge is ~5–10× cheaper but
  leaves news features only on those, so they can't be trained on.
- **Maintenance**: team-name mapping needs a human for each new name (the job flags, never guesses).
- **Rejected**: one more `ledger_*` table per strategy (fine for one, a mess at N models with odds history).
- **Proven wrong if**: after ~3 months of forward data, no model or news variant shows positive CLV at average
  odds. Then the honest answer is "not beatable with these inputs" and research spend stops.

## Open decisions

1. Michalis: new models in a new `models/` package with `model/` his, or does he own/co-own them? Needed before
   phase 3.
2. Budget caps per month: odds data and LLM. Suggested: after the pilot's numbers.
3. Scope beyond the 16 football-data leagues: UEFA club competitions need another source. **Decided 2026-11-13**
   (user): in scope as phase 3b, after phase 3 (internationals first). Paid sources allowed ("no cheap stuff");
   which ones is decided in its plan. Why a separate model: `model/features.py` keys Elo per country, so ratings
   of clubs from different countries are on unrelated scales; UEFA games are the only cross-league results.
