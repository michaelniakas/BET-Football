"""Paper-trading ledgers as written by `python -m model.paper`: CSV files, or Postgres from phase 3 on."""
from __future__ import annotations

import io
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
import requests
from psycopg import sql

from model.data import MARKETS
from model.paper import KEY, SCORE, STRATEGIES

log = logging.getLogger(__name__)
# Where the paper-trading workflow commits the ledgers (scheduled workflows run on `main`).
GITHUB_MAIN = "https://raw.githubusercontent.com/michaelniakas/BET-Football/main/model/paper"
TIMEOUT = (3.05, 5)             # connect, read (seconds)

# A ledger row `l`'s fixture, by football-data's names.
TEAMS = sql.SQL("""JOIN competitions c ON c.code = l.league
            JOIN team_aliases th ON (th.source, th.name) = ('football-data', l.home)
            JOIN team_aliases ta ON (ta.source, ta.name) = ('football-data', l.away)""")
FIXTURE = sql.SQL("{} JOIN fixtures f ON (f.competition_id, f.date, f.home_id, f.away_id)"
                  " = (c.id, l.date, th.team_id, ta.team_id)").format(TEAMS)
# Logged at or after its game's kick-off: not in the record (0007 refuses it, 0008 leaves a stored one out of v_ledger_*).
LATE = sql.SQL("kicked_off(f.id, l.logged_at AT TIME ZONE 'UTC')")


def _with_result_columns(strategy: str, df: pd.DataFrame) -> pd.DataFrame:
    spec = MARKETS[STRATEGIES[strategy]["market"]]
    for c in [spec["target"]] + [f"close_{s}" for s in spec["suffixes"]] + SCORE:
        if not df.empty and c not in df:
            df[c] = np.nan
    return df


class CsvLedgers:
    """Reads each ledger once and again only when the file changes on disk."""

    def __init__(self, directory: Path):
        self._dir = directory
        self._cache: dict[str, tuple[int, pd.DataFrame]] = {}

    def read(self, strategy: str) -> pd.DataFrame:
        """The ledger, with the result and closing columns present even before anything has settled.

        Callers must not mutate the returned frame: it is shared between requests.
        """
        path = self._dir / f"ledger_{strategy}.csv"
        try:
            mtime = path.stat().st_mtime_ns
        except FileNotFoundError:
            return pd.DataFrame()
        cached = self._cache.get(strategy)
        if cached and cached[0] == mtime:
            return cached[1]
        df = _with_result_columns(strategy, pd.read_csv(path, parse_dates=["date", "logged_at"]))
        self._cache[strategy] = (mtime, df)
        return df


class GithubLedgers:
    """The ledgers as last committed to GitHub `main`, so a Tue/Fri run shows without a redeploy.

    Refetched after `ttl`. If a fetch fails, the previous copy is served for another `ttl`, or the
    image's copy (`fallback`) before the first success.
    """
    # ponytail: stopgap until phase 3 (Postgres); the request after expiry waits on GitHub (<= ~8 s).

    def __init__(self, base_url: str, fallback: CsvLedgers, ttl: timedelta = timedelta(minutes=10),
                 get: Callable[..., requests.Response] = requests.get,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self._base, self._fallback, self._ttl, self._get, self._now = base_url, fallback, ttl, get, now
        self._cache: dict[str, tuple[datetime, pd.DataFrame]] = {}
        # One lock per strategy, so concurrent requests after expiry share one fetch.
        self._locks = {s: threading.Lock() for s in STRATEGIES}

    def read(self, strategy: str) -> pd.DataFrame:
        """Same contract as `CsvLedgers.read`: callers must not mutate the returned frame."""
        with self._locks[strategy]:
            cached = self._cache.get(strategy)
            if cached and self._now() - cached[0] < self._ttl:
                return cached[1]
            try:
                r = self._get(f"{self._base}/ledger_{strategy}.csv", timeout=TIMEOUT)
                r.raise_for_status()
                df = _with_result_columns(
                    strategy, pd.read_csv(io.BytesIO(r.content), parse_dates=["date", "logged_at"]))
            except (requests.RequestException, ValueError) as e:     # pandas parse errors are ValueErrors
                log.warning("ledger %s from GitHub failed, serving the previous copy: %s", strategy, e)
                df = cached[1] if cached else self._fallback.read(strategy)
            self._cache[strategy] = (self._now(), df)
            return df


# Strategy market -> (market, line, settled-outcome column on `fixtures`) in the normalized tables (migration 0004).
NORMALIZED = {"1x2": ("1x2", None, "result"), "ou25": ("ou", 2.5, "ou25")}
FD_BOOKS = ["avg", "max", "b365", "ps", "bfe"]      # the ledgers' price columns, football-data's codes
MODEL = "michalis"
METHOD = "model.paper"                              # how the ledgers' mkt_* / close_* were computed
LOCK = 0x6C6564676572     # pg_advisory_xact_lock key: one ledger write at a time (new teams, prediction order)


class PostgresLedgers:
    """The ledgers in Postgres (api/migrations): one table per strategy, which the paper job writes, projected
    into the normalized tables on every save and read back through the v_ledger_* views.

    Reads return the same frame `CsvLedgers` does for the same rows, in the order they were logged.
    """
    # ponytail: a connection per call (2 users); a pool (psycopg_pool) once requests queue on connect.

    TEXT = ["league", "home", "away"]

    def __init__(self, url: str):
        self._url = url
        self._columns: dict[str, list[str]] = {}

    def _table(self, strategy: str) -> sql.Identifier:
        if strategy not in STRATEGIES:
            raise KeyError(strategy)
        return sql.Identifier(f"ledger_{strategy}")

    def columns(self, conn: psycopg.Connection, strategy: str) -> list[str]:
        """The table's columns in DDL order, without `seq`: the migration is the one list of them."""
        if strategy not in self._columns:
            rows = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'public'"
                                " AND table_name = %s AND column_name <> 'seq' ORDER BY ordinal_position",
                                (f"ledger_{strategy}",)).fetchall()
            if not rows:
                raise RuntimeError(f"no table ledger_{strategy}: run python -m api.migrate up")
            self._columns[strategy] = [r[0] for r in rows]
        return self._columns[strategy]

    def read(self, strategy: str, with_late: bool = False) -> pd.DataFrame:
        """The record (`v_ledger_*`), or with `with_late` the whole ledger table, rows logged after kick-off
        included. Callers must not mutate the returned frame (same contract as `CsvLedgers.read`)."""
        table = self._table(strategy)                           # an unknown strategy is a KeyError
        with psycopg.connect(self._url) as conn:
            cols = self.columns(conn, strategy)
            rows = conn.execute(sql.SQL("SELECT {} FROM {} ORDER BY seq").format(
                sql.SQL(", ").join(map(sql.Identifier, cols)),
                table if with_late else sql.Identifier(f"v_ledger_{strategy}"))).fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=cols)
        target = MARKETS[STRATEGIES[strategy]["market"]]["target"]
        for c in cols:
            if c in ("date", "logged_at"):
                df[c] = pd.to_datetime(df[c]).astype("datetime64[us]")
            elif c in self.TEXT or c == target:
                df[c] = df[c].astype("str")
            elif c == "season":
                df[c] = df[c].astype("int64")
            else:
                df[c] = df[c].astype("float64")
        return df

    def save(self, strategy: str, ledger: pd.DataFrame) -> tuple[int, int]:
        """Write back a ledger the paper job has changed, in one transaction: (rows added, rows settled).

        New fixtures are inserted; an existing fixture only gets its result and closing columns, and
        only while it is unsettled, and its final score while it has none and it agrees with the stored
        result (counted as settled). A snapshot (probabilities, odds, logged_at) is never overwritten.
        """
        table = self._table(strategy)
        spec = MARKETS[STRATEGIES[strategy]["market"]]
        settle_cols = [spec["target"]] + [f"close_{s}" for s in spec["suffixes"]]
        with psycopg.connect(self._url) as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))     # a deploy's two workers can overlap
            cols = self.columns(conn, strategy)
            unknown = set(ledger.columns) - set(cols)
            if unknown:     # the job writes a column the schema doesn't have: needs a migration, not a drop
                raise ValueError(f"ledger_{strategy} has no column(s) {sorted(unknown)}")
            cols = [c for c in cols if c in ledger.columns]
            values = ledger[cols].astype(object).where(ledger[cols].notna(), None)
            values["date"] = [v.date() for v in values["date"]]               # NOT NULL in both
            values["logged_at"] = [v.to_pydatetime() for v in values["logged_at"]]
            ident = lambda names: sql.SQL(", ").join(map(sql.Identifier, names))    # noqa: E731
            update = [c for c in settle_cols if c in cols]
            score = [c for c in SCORE if c in cols]
            q = sql.SQL("INSERT INTO {t} AS l ({cols}) VALUES ({ph}) ON CONFLICT ({key}) DO ").format(
                t=table, cols=ident(cols), ph=sql.SQL(", ").join(sql.Placeholder() * len(cols)), key=ident(KEY))
            if update:
                target = sql.Identifier(spec["target"])
                sets = [sql.SQL("{c} = CASE WHEN l.{t} IS NULL THEN EXCLUDED.{c} ELSE l.{c} END").format(
                            c=sql.Identifier(c), t=target) for c in update]
                # A score only with the result it was sent with: a stored result is kept, so one that differs
                # gets no score (as in `model.paper.settle`) instead of failing the CHECK and the whole save.
                scorable = sql.SQL("l.{c} IS NULL AND COALESCE(l.{t}, EXCLUDED.{t}) = EXCLUDED.{t}")
                sets += [sql.SQL("{c} = CASE WHEN {ok} THEN EXCLUDED.{c} ELSE l.{c} END").format(
                             c=sql.Identifier(c), ok=scorable.format(c=sql.Identifier(c), t=target)) for c in score]
                when = [sql.SQL("l.{t} IS NULL AND EXCLUDED.{t} IS NOT NULL").format(t=target)]
                when += [sql.SQL("{ok} AND EXCLUDED.{c} IS NOT NULL").format(
                             c=sql.Identifier(c), ok=scorable.format(c=sql.Identifier(c), t=target)) for c in score]
                q += sql.SQL("UPDATE SET {sets} WHERE {when}").format(
                    sets=sql.SQL(", ").join(sets), when=sql.SQL(" OR ").join(sql.SQL("({})").format(w) for w in when))
            else:
                q += sql.SQL("NOTHING")
            q += sql.SQL(" RETURNING (xmax = 0)")         # true = inserted, false = updated
            added = settled = 0
            with conn.cursor() as cur:
                cur.executemany(q, values.itertuples(index=False, name=None), returning=True)
                while True:
                    row = cur.fetchone()
                    if row is not None:
                        added, settled = added + row[0], settled + (not row[0])
                    if not cur.nextset():
                        break
            self._project(conn, strategy)
        return added, settled

    def project(self, strategy: str) -> None:
        """Copy the whole ledger into the normalized tables: the backfill, and a no-op when nothing is missing."""
        with psycopg.connect(self._url) as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            self._project(conn, strategy)

    def _project(self, conn: psycopg.Connection, strategy: str) -> None:
        """Set-based and idempotent: new competitions, teams, fixtures, predictions, odds and market probabilities;
        a market's outcome and the score once settled. What is stored first stays, as in the ledger."""
        spec = MARKETS[STRATEGIES[strategy]["market"]]
        market, line, outcome = NORMALIZED[STRATEGIES[strategy]["market"]]
        lit = {"ledger": self._table(strategy), "market": sql.Literal(market), "line": sql.Literal(line),
               "out": sql.Identifier(outcome), "target": sql.Identifier(spec["target"]),
               "model": sql.Literal(MODEL), "version": sql.Literal(STRATEGIES[strategy]["features"]),
               "method": sql.Literal(METHOD),
               "outcome": sql.Identifier(f"outcome_{STRATEGIES[strategy]['market']}")}
        cols = lambda pre, xs: sql.SQL("ARRAY[{}]").format(                            # noqa: E731
            sql.SQL(", ").join(sql.SQL("l.{}").format(sql.Identifier(f"{pre}{x}")) for x in xs))
        lit["p"] = cols("p_", spec["outcomes"])
        lit["mkt"] = cols("mkt_", spec["suffixes"])
        lit["close"] = cols("close_", spec["suffixes"])
        lit["books"] = sql.SQL(", ").join(sql.SQL("({}, {})").format(sql.Literal(b), cols(f"{b}_", spec["suffixes"]))
                                          for b in FD_BOOKS)
        lit["fx"] = FIXTURE
        lit["same"] = sql.SQL("(f.competition_id, f.date, f.home_id, f.away_id) = (c.id, l.date, th.team_id, ta.team_id)")
        lit["teams"] = TEAMS
        # The database refuses the prediction (0007), and its prices aren't pre-match: the row stays out.
        lit["before"] = sql.SQL("NOT {}").format(LATE)
        conn.execute(sql.SQL("""
            INSERT INTO competitions (code) SELECT DISTINCT league FROM {ledger} ON CONFLICT DO NOTHING;

            WITH names AS (SELECT DISTINCT n FROM {ledger}, LATERAL (VALUES (home), (away)) v(n)
                           WHERE NOT EXISTS (SELECT FROM team_aliases a WHERE (a.source, a.name) = ('football-data', n))),
                 new AS (INSERT INTO teams (name) SELECT n FROM names RETURNING id, name)
            INSERT INTO team_aliases (source, name, team_id) SELECT 'football-data', name, id FROM new;

            INSERT INTO fixtures (competition_id, season, date, home_id, away_id)
            SELECT c.id, l.season, l.date, th.team_id, ta.team_id FROM {ledger} l {teams}
            ON CONFLICT DO NOTHING;

            INSERT INTO models (name, version) VALUES ({model}, {version}) ON CONFLICT DO NOTHING;

            INSERT INTO predictions (model_id, fixture_id, fixture_date, market, line, predicted_at, p)
            SELECT m.id, f.id, f.date, {market}, {line}, l.logged_at AT TIME ZONE 'UTC', {p}
            FROM {ledger} l {fx} JOIN models m ON (m.name, m.version) = ({model}, {version})
            -- One per fixture and market whatever the version: a features change doesn't re-log the ledger.
            WHERE NOT EXISTS (SELECT FROM predictions o JOIN models om ON om.id = o.model_id
                              WHERE om.name = {model} AND o.fixture_id = f.id AND o.market = {market}
                                AND o.line IS NOT DISTINCT FROM {line})
              AND {before}
            ORDER BY l.seq
            ON CONFLICT DO NOTHING;

            INSERT INTO odds_snapshots (fixture_id, market, line, source, bookmaker, captured_at, prices)
            SELECT f.id, {market}, {line}, 'football-data', b.book, l.logged_at AT TIME ZONE 'UTC', b.prices
            FROM {ledger} l {fx}, LATERAL (VALUES {books}) b(book, prices)
            WHERE array_position(b.prices, NULL) IS NULL AND {before}
            ON CONFLICT DO NOTHING;

            INSERT INTO market_probs (fixture_id, market, line, kind, method, captured_at, p)
            SELECT f.id, {market}, {line}, 'pre_match', {method}, l.logged_at AT TIME ZONE 'UTC', {mkt}
            FROM {ledger} l {fx}
            WHERE {before}
            ON CONFLICT DO NOTHING;

            INSERT INTO market_probs (fixture_id, market, line, kind, method, captured_at, p)
            SELECT f.id, {market}, {line}, 'closing', {method}, NULL, {close}
            FROM {ledger} l {fx} WHERE array_position({close}, NULL) IS NULL
            ON CONFLICT DO NOTHING;

            -- The ledger accepted this outcome, so it is stored; a score from the other market's ledger that
            -- contradicts it (football-data corrected it in between) is withdrawn. `diff` then shows that row.
            UPDATE fixtures f SET {out} = l.{target},
                hg = CASE WHEN {outcome}(f.hg, f.ag) = l.{target} THEN f.hg END,
                ag = CASE WHEN {outcome}(f.hg, f.ag) = l.{target} THEN f.ag END
            FROM {ledger} l {teams}
            WHERE {same} AND f.{out} IS NULL AND l.{target} IS NOT NULL;

            -- A score only when it agrees with every market's stored outcome, as `save` does for the ledger.
            UPDATE fixtures f SET hg = l.hg, ag = l.ag
            FROM {ledger} l {teams}
            WHERE {same} AND f.hg IS NULL AND l.hg IS NOT NULL
              AND (f.result IS NULL OR f.result = outcome_1x2(l.hg, l.ag))
              AND (f.ou25 IS NULL OR f.ou25 = outcome_ou25(l.hg, l.ag));
        """).format(**lit))

    def late(self, strategy: str) -> int:
        """Ledger rows logged at or after their game's kick-off: kept in the table, left out of the record."""
        with psycopg.connect(self._url) as conn:
            return conn.execute(sql.SQL("SELECT count(*) FROM {t} l {fx} WHERE {late}").format(
                t=self._table(strategy), fx=FIXTURE, late=LATE)).fetchone()[0]

    def diff(self, strategy: str) -> int:
        """Rows that differ between the ledger table and its view, either way, or that are out of order.

        A score the view has and the table lacks is not a difference: it is the fixture's, known from the other
        market's ledger. A score the table has and the view lacks or contradicts is. A late row (`late`) is in the
        table only, by design: not a difference.
        """
        with psycopg.connect(self._url) as conn:
            all_cols = sql.SQL(", ").join(map(sql.Identifier, ["seq"] + self.columns(conn, strategy)))
            table = sql.SQL("(SELECT {c} FROM {t} l WHERE NOT EXISTS (SELECT FROM (SELECT) x {fx} WHERE {late}))").format(
                c=all_cols, t=self._table(strategy), fx=FIXTURE, late=LATE)
            cols = sql.SQL(", ").join(map(sql.Identifier, [c for c in self.columns(conn, strategy) if c not in SCORE]))
            view = sql.Identifier(f"v_ledger_{strategy}")
            n = conn.execute(sql.SQL("SELECT count(*) FROM ((SELECT {c} FROM {t} k EXCEPT ALL SELECT {c} FROM {v}) UNION ALL"
                                     " (SELECT {c} FROM {v} EXCEPT ALL SELECT {c} FROM {t} k)) d").format(
                c=cols, t=table, v=view)).fetchone()[0]
            n += conn.execute(sql.SQL("SELECT count(*) FROM {t} t LEFT JOIN {v} v USING (date, league, home, away)"
                                      " WHERE t.hg IS NOT NULL AND (t.hg, t.ag) IS DISTINCT FROM (v.hg, v.ag)").format(
                t=table, v=view)).fetchone()[0]
            order = [sql.SQL("SELECT array_agg((date, league, home, away) ORDER BY seq) FROM {} o").format(x)
                     for x in (table, view)]
            same_order = conn.execute(sql.SQL("SELECT ({}) IS NOT DISTINCT FROM ({})").format(*order)).fetchone()[0]
        return n + (0 if same_order else 1)
