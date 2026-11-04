# Rebuild plan: API + React frontend

Branch: `rebuild`. Every phase lands here; `main` keeps the current Streamlit app
until the rebuild is accepted. Goal: fast and accurate. Running cost is not a constraint.

## Decisions (2026-11-05)

| Topic | Decision | Why |
|---|---|---|
| WC 2026 page (`app.py`) | Delete | No model behind it; tournament is over; Optimizer already covers upcoming picks + Kelly sizing. Claude per-match analysis can return later as an endpoint. |
| Backend | Python, FastAPI, thin layer over `model/` | The model is pandas/sklearn/numpy and the optimizer simulates in numpy per request. Go/PHP would mean rewriting it or a second runtime. |
| Contract | `openapi.yaml`, hand-written, source of truth | Pydantic models generated with `datamodel-code-generator`, TS client with `openapi-typescript`. No code-first spec. Errors as RFC 9457 problem details with stable codes. |
| Frontend | React + Vite + TypeScript SPA | Built to static files and served by the same FastAPI container: one service, one deploy. |
| Languages | English + Greek in the UI | Pick i18n approach in phase 2 (typed dictionary vs `react-i18next`). |
| Auth | None in the app | Locked with Toft access control. |
| Paper-trading job | GitHub Actions until phase 3, then a Toft worker + Postgres | Toft has only `web`/`worker` service types, no cron type: the worker runs a scheduler. |
| Model code (`model/`) | Untouched in phases 1–2 | Michalis owns it; he is on board with leaving Streamlit. |

## Layers

- **Transport**: FastAPI routers. Parse, call a use case, map to the contract.
- **Application**: use cases (`list_picks`, `results_summary`, `edge_sweep`, `optimize_coupon`, `live_odds`).
- **Domain**: `model/` as it is.
- **Infrastructure**: `LedgerRepository` (CSV now, Postgres in phase 3: the interface has two real implementations), Odds API client (`model/live_odds.py`), server-side cache.

Weight: service + repository. No aggregates: this is reporting and computation.

## Phases

### Phase 1: API (no frontend yet)
- Write `openapi.yaml`: picks, all predictions, settled results + metrics, edge sweep, probability report, live odds, `POST /optimize`.
- Generate Pydantic models; FastAPI app under `api/`; delete `app.py` (WC page).
- Odds API key server-side (Toft secret); cache live odds responses server-side with a TTL (speed + saves the 500 credits/month).
- Measure `POST /optimize` (20k simulations) per request before deciding on caching.
- **Check**: pytest per endpoint against a fixture ledger; every response validated against `openapi.yaml`; golden tests prove API numbers equal what the current Streamlit pages compute from the same ledger (accuracy).

### Phase 2: Frontend
- Vite + React + TS, generated client (`openapi-typescript` + `openapi-fetch`), chart library chosen in this phase.
- Pages: Paper Trading, Optimizer; EN/EL switch.
- Dockerfile: Node build stage → Python runtime serving `/api/*` and the static build. Delete Streamlit and `pages/`.
- CI: `tsc --noEmit`, frontend build, API tests, image build.
- **Check**: parity checklist from the current Streamlit pages, screenshot per page in both languages, CI green.

### Phase 3: Job on Toft
- Postgres service (volume + backups); schema with constraints, e.g. `UNIQUE (strategy, match_date, home, away)`; reversible migration.
- One-off CSV → Postgres import; `LedgerRepository` Postgres implementation.
- Worker service: same image, scheduler command, Tue + Fri 07:00 UTC.
- Disable `.github/workflows/paper-trading.yml`.
- **Check**: one run on Toft; row counts match the CSVs; rollback (re-enable the workflow, CSV reader) tested.

## Testing the branch on Toft

Deploy `rebuild` to a separate staging service (same repo, branch `rebuild`, own domain, Toft access control),
never to the production `bet-app` service, which stays on `main`.

## Open risks

- `POST /optimize` may be too slow per request (not measured yet) → precompute or cache.
- Rebuilding Michalis's pages means new model work must be exposed through the API, not a new Streamlit page.
- `README.md` deploy instructions (Streamlit Cloud) become wrong in phase 2.
