"""HTTP transport: parse the request, call a use case, map the result to the contract (openapi.yaml).

Run: uvicorn --factory api.main:from_env
Handlers are plain `def`, so FastAPI runs them in its threadpool: the work is CPU-bound pandas/numpy
and must not block the event loop.
"""
from __future__ import annotations

import logging
import math
import os
import re
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Annotated, Any, get_args

import pandas as pd
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BeforeValidator
from starlette.exceptions import HTTPException

from model.paper import PAPER_DIR

from . import usecases as uc
from .contract import (BetStatus, Book, Catalog, EdgeSweep, Health, League, OptimizeRequest, OptimizeResponse,
                       PickPage, PredictionPage, ProbabilityReport, ResultsSummary,
                       SettledFixturePage, SnapshotBook)
from .ledgers import GITHUB_MAIN, CsvLedgers, GithubLedgers, PostgresLedgers
from .odds import LiveOddsCache

log = logging.getLogger(__name__)
CONTRACT = Path(__file__).resolve().parent.parent / "openapi.yaml"
WEB_DIST = CONTRACT.parent / "web" / "dist"          # the built frontend (`npm run build` in web/)

TITLES = {
    "validation_failed": "The request is invalid",
    "not_found": "Not found",
    "method_not_allowed": "Method not allowed",
    "strategy_not_found": "Unknown strategy",
    "odds_api_not_configured": "Live odds are not configured",
    "odds_api_quota_exhausted": "The Odds API credits are used up",
    "odds_api_unauthorized": "The Odds API rejected the server's key",
    "odds_api_unavailable": "The Odds API is unavailable",
    "internal_error": "Internal error",
}

MinEdge = Annotated[float | None, Query(ge=0, le=0.5)]
Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0)]
Sort = Annotated[str | None, Query()]
Leagues = Annotated[list[League] | None, Query()]
Team = Annotated[str | None, Query(min_length=1, max_length=64)]


def _full_date(v: str) -> str:
    """The contract's `format: date`. pydantic alone also reads `0`, unix seconds and midnight datetimes as dates."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        raise ValueError("expected YYYY-MM-DD")
    return v


Day = Annotated[date | None, BeforeValidator(_full_date)]


class InvalidParameter(Exception):
    def __init__(self, parameter: str, detail: str):
        self.parameter, self.detail = parameter, detail


def problem(status: int, code: str, detail: str | None = None, headers: dict | None = None,
            **extra: Any) -> JSONResponse:
    body = {"type": f"/problems/{code}", "title": TITLES[code], "status": status, "code": code}
    if detail:
        body["detail"] = detail
    return JSONResponse({**body, **extra}, status_code=status, media_type="application/problem+json",
                        headers=headers)


def clean(x: Any) -> Any:
    """pandas values -> JSON-ready values: NaN/NaT -> None, timestamps -> date or datetime."""
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if x is None or x is pd.NaT or (isinstance(x, float) and math.isnan(x)):
        return None
    if isinstance(x, pd.Timestamp):
        return x.date() if x.tz is None and x == x.normalize() else x.to_pydatetime()
    return x


def records(df: pd.DataFrame) -> list[dict]:
    return clean(df.to_dict("records"))


def parse_sort(raw: str | None, allowed: set[str], default: list[str]) -> list[str]:
    if raw is None:
        return default
    keys = raw.split(",")
    bad = [k for k in keys if k not in allowed]
    if bad:
        raise InvalidParameter("sort", f"unknown sort field(s): {', '.join(bad)}; allowed: {', '.join(sorted(allowed))}")
    if len(set(keys)) != len(keys):
        raise InvalidParameter("sort", "a sort key appears more than once")
    return keys


def _both(*fields: str) -> set[str]:
    return {f for name in fields for f in (name, f"-{name}")}


def create_app(ledgers: uc.Ledgers, odds: uc.LiveOdds,
               today: Callable[[], pd.Timestamp] = lambda: pd.Timestamp.today().normalize(),
               web: Path | None = None) -> FastAPI:
    app = FastAPI(title="BET-Football API", openapi_url=None, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def noindex(request, call_next):
        # Private site: no search engine indexes a page, asset or API response. A robots.txt Disallow would
        # stop crawlers reading this header, so a linked URL could still be listed without its content.
        response = await call_next(request)
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response

    # ----------------------------------------------------------------------- errors
    @app.exception_handler(RequestValidationError)
    def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = []
        for e in exc.errors():
            where, *path = e["loc"]
            if path[:1] == ["odds"] and len(path) > 1 and path[1] in ("snapshot", "live"):
                del path[1]                                     # pydantic's union tag, not part of the body
            item = {"detail": e["msg"]}
            if where == "body":
                item["pointer"] = "/" + "/".join(str(p) for p in path)
            else:
                item["parameter"] = str(path[0]) if path else str(where)
            errors.append(item)
        return problem(422, "validation_failed", errors=errors)

    @app.exception_handler(InvalidParameter)
    def _param(_: Request, exc: InvalidParameter) -> JSONResponse:
        return problem(422, "validation_failed", errors=[{"detail": exc.detail, "parameter": exc.parameter}])

    @app.exception_handler(HTTPException)
    def _http(_: Request, exc: HTTPException) -> JSONResponse:
        if exc.status_code == 405:
            return problem(405, "method_not_allowed", headers=exc.headers)      # carries Allow
        if exc.status_code == 404:
            return problem(404, "not_found")
        if 400 <= exc.status_code < 500:
            return problem(exc.status_code, "validation_failed")
        log.error("unmapped HTTP error %s", exc.status_code)
        return problem(500, "internal_error")

    @app.exception_handler(uc.StrategyNotFound)
    def _strategy(_: Request, exc: uc.StrategyNotFound) -> JSONResponse:
        return problem(404, "strategy_not_found", f"No strategy '{exc}'.")

    @app.exception_handler(uc.OddsApiNotConfigured)
    def _odds_off(_: Request, __: uc.OddsApiNotConfigured) -> JSONResponse:
        return problem(503, "odds_api_not_configured", "Use snapshot odds.")

    @app.exception_handler(uc.OddsApiFailed)
    def _odds_failed(_: Request, exc: uc.OddsApiFailed) -> JSONResponse:
        # Never log the cause itself: requests puts the full URL, apiKey included, in its messages.
        log.warning("Odds API call failed: %s (%s)", exc.kind, type(exc.__cause__).__name__)
        return problem(502, f"odds_api_{exc.kind}")

    @app.exception_handler(Exception)
    def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", exc_info=exc)
        return problem(500, "internal_error")

    # ----------------------------------------------------------------------- meta
    @app.get("/health")
    async def health() -> Health:          # on the event loop: answers even when the threadpool is busy
        return Health(status="ok")

    @app.get("/api/v1/openapi.yaml")
    def contract() -> FileResponse:
        return FileResponse(CONTRACT, media_type="application/yaml")

    @app.get("/api/v1/catalog")
    def catalog() -> Catalog:
        body = uc.catalog(ledgers, odds)
        body["snapshot_books"] = list(get_args(get_args(SnapshotBook.__value__)[0]))  # Annotated[Literal[...], ...]
        return clean(body)

    # ----------------------------------------------------------------------- paper trading
    base = "/api/v1/strategies/{strategy}"

    @app.get(base + "/picks")
    def picks(strategy: str, book: Book = "avg", min_edge: MinEdge = None, limit: Limit = 100,
              offset: Offset = 0, sort: Sort = None) -> PickPage:
        keys = parse_sort(sort, _both("date", "league", "odds", "edge"), ["date"])
        page, total = uc.paginate(uc.picks(ledgers, strategy, book, min_edge, today()), keys, limit, offset)
        return {"items": records(page), "total": total, "limit": limit, "offset": offset}

    @app.get(base + "/predictions")
    def predictions(strategy: str, book: Book = "avg", league: Annotated[list[League] | None, Query()] = None,
                    limit: Limit = 100, offset: Offset = 0, sort: Sort = None) -> PredictionPage:
        keys = parse_sort(sort, _both("date", "league", "edge"), ["date", "-edge"])
        page, total = uc.paginate(uc.predictions(ledgers, strategy, book, league, today()), keys, limit, offset)
        items = [{**{k: r[k] for k in uc.MATCH_KEY}, "outcomes": r["outcomes"],
                  "best": None if r["best_outcome"] is None else {"outcome": r["best_outcome"], "edge": r["edge"]}}
                 for r in records(page)]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @app.get(base + "/results/summary")
    def results_summary(strategy: str, book: Book = "avg", min_edge: MinEdge = None, league: Leagues = None,
                        team: Team = None, date_from: Day = None, date_to: Day = None) -> ResultsSummary:
        where = uc.MatchFilter(league, team, date_from, date_to)
        return clean(uc.results_summary(ledgers, strategy, book, min_edge, where))

    @app.get(base + "/results/fixtures")
    def results_fixtures(strategy: str, book: Book = "avg", min_edge: MinEdge = None, league: Leagues = None,
                         team: Team = None, date_from: Day = None, date_to: Day = None,
                         bet_status: Annotated[list[BetStatus] | None, Query()] = None, limit: Limit = 100,
                         offset: Offset = 0, sort: Sort = None) -> SettledFixturePage:
        keys = parse_sort(sort, _both("date", "edge", "profit", "clv"), ["-date"])
        where = uc.MatchFilter(league, team, date_from, date_to)
        page, total = uc.paginate(uc.settled_fixtures(ledgers, strategy, book, min_edge, where, bet_status),
                                  keys, limit, offset)
        items = [{**{k: r[k] for k in uc.FIXTURE_COLS},
                  "bet": None if r["pick"] is None else {k: r[k] for k in uc.BET_COLS}}
                 for r in records(page)]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @app.get(base + "/results/edge-sweep")
    def edge_sweep(strategy: str, book: Book = "avg") -> EdgeSweep:
        return {"rows": clean(uc.edge_sweep(ledgers, strategy, book))}

    @app.get(base + "/results/probability-report")
    def probability_report(strategy: str) -> ProbabilityReport:
        return clean(uc.probability_report(ledgers, strategy))

    # ----------------------------------------------------------------------- optimizer
    @app.post("/api/v1/optimize")
    def optimize(req: OptimizeRequest) -> OptimizeResponse:
        o = req.odds
        live = o.source == "live"
        return clean(uc.optimize(
            ledgers, odds, budget=req.budget, markets=req.markets, min_prob=req.min_prob, min_edge=req.min_edge,
            source=o.source, book=None if live else o.book, leagues=o.leagues if live else None,
            bookmakers=o.bookmakers if live else None, today=today()))

    # ----------------------------------------------------------------------- frontend
    if web is not None:
        # Hashed file names, so assets can be cached; index.html must be revalidated or a deploy
        # leaves browsers on an old page that points at assets which no longer exist.
        app.mount("/assets", StaticFiles(directory=web / "assets"), name="assets")

        @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(web / "index.html", headers={"Cache-Control": "no-cache"})

    return app


def from_env() -> FastAPI:
    """The production app: ledgers from Postgres when DATABASE_URL is set, else from GitHub `main` (model/paper as
    fallback); Odds API key from the ODDS_API_KEY secret."""
    logging.basicConfig(level=logging.INFO)
    web = WEB_DIST if (WEB_DIST / "index.html").exists() and (WEB_DIST / "assets").is_dir() else None
    if web is None:
        log.warning("no frontend build at %s: serving the API only", WEB_DIST)
    url = os.environ.get("DATABASE_URL")
    ledgers = PostgresLedgers(url) if url else GithubLedgers(GITHUB_MAIN, CsvLedgers(PAPER_DIR))
    return create_app(ledgers, LiveOddsCache(os.environ.get("ODDS_API_KEY", "")), web=web)
