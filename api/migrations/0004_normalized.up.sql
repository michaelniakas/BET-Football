-- Phase 1 (docs/phase1-plan.md): fixtures, teams, odds and predictions in one structure, beside the ledgers.
-- Expand step: the paper job still writes ledger_*; PostgresLedgers.save projects every save into these
-- tables in the same transaction, and the API reads the ledgers back through the v_ledger_* views.
-- Probabilities and prices are arrays in the market's outcome order: 1x2 = {H,D,A}, ou = {O,U}.
-- New, empty tables: no lock on anything in use.

CREATE FUNCTION market_ok(market text, line numeric, n integer) RETURNS boolean
    LANGUAGE sql IMMUTABLE
    RETURN CASE market WHEN '1x2' THEN line IS NULL AND n = 3
                       WHEN 'ou' THEN coalesce(line > 0, false) AND n = 2   -- a NULL would pass a CHECK
                       ELSE false END;

CREATE FUNCTION probs_ok(p double precision[]) RETURNS boolean
    LANGUAGE sql IMMUTABLE
    RETURN array_position(p, NULL) IS NULL AND 0 <= ALL (p) AND 1 >= ALL (p)
           AND abs((SELECT sum(x) FROM unnest(p) x) - 1) < 1e-6;

CREATE FUNCTION prices_ok(prices double precision[]) RETURNS boolean
    LANGUAGE sql IMMUTABLE
    RETURN array_position(prices, NULL) IS NULL AND 1 < ALL (prices);

CREATE FUNCTION outcome_1x2(hg smallint, ag smallint) RETURNS text
    LANGUAGE sql IMMUTABLE
    RETURN CASE WHEN hg > ag THEN 'H' WHEN hg = ag THEN 'D' WHEN hg < ag THEN 'A' END;

CREATE FUNCTION outcome_ou25(hg smallint, ag smallint) RETURNS text
    LANGUAGE sql IMMUTABLE
    RETURN CASE WHEN hg + ag > 2 THEN 'O' WHEN hg + ag <= 2 THEN 'U' END;

CREATE TABLE competitions (
    id   integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code text NOT NULL UNIQUE CHECK (code <> '')           -- football-data's league code (E0, SP2, ...)
);

CREATE TABLE teams (
    id   integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL CHECK (name <> '')
);

-- One identity per team across sources: each source's spelling maps to exactly one team.
CREATE TABLE team_aliases (
    source  text    NOT NULL CHECK (source <> ''),
    name    text    NOT NULL CHECK (name <> ''),
    team_id integer NOT NULL REFERENCES teams,
    PRIMARY KEY (source, name)
);

-- `result` and `ou25` are the outcomes the markets were settled at: final once set. A score is kept only
-- when it agrees with them (rows settled before scores were kept have none).
CREATE TABLE fixtures (
    id             integer  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    competition_id integer  NOT NULL REFERENCES competitions,
    season         smallint NOT NULL CHECK (season BETWEEN 1870 AND 2100),
    date           date     NOT NULL,
    home_id        integer  NOT NULL REFERENCES teams,
    away_id        integer  NOT NULL REFERENCES teams CHECK (away_id <> home_id),
    hg             smallint CHECK (hg >= 0),
    ag             smallint CHECK (ag >= 0),
    result         text     CHECK (result IN ('H', 'D', 'A')),
    ou25           text     CHECK (ou25 IN ('O', 'U')),
    UNIQUE (competition_id, date, home_id, away_id),
    UNIQUE (id, date),                                     -- target of predictions' (fixture_id, fixture_date)
    CONSTRAINT fixtures_score_both_or_none CHECK (num_nulls(hg, ag) IN (0, 2)),
    CONSTRAINT fixtures_score_matches_result CHECK (hg IS NULL OR result IS NULL OR result = outcome_1x2(hg, ag)),
    CONSTRAINT fixtures_score_matches_ou25 CHECK (hg IS NULL OR ou25 IS NULL OR ou25 = outcome_ou25(hg, ag))
);

CREATE TABLE models (
    id      integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name    text NOT NULL CHECK (name <> ''),
    version text NOT NULL CHECK (version <> ''),
    UNIQUE (name, version)
);

-- Append-only: a model's first prediction of a fixture's market is the one that counts. `id` is the order
-- they were made in (the ledgers' `seq`). The fixture's date travels with the row so the database can check
-- that the prediction came first, and a date change cascades into that check.
CREATE TABLE predictions (
    id           bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    model_id     integer     NOT NULL REFERENCES models,
    fixture_id   integer     NOT NULL,
    fixture_date date        NOT NULL,
    market       text        NOT NULL,
    line         numeric,
    predicted_at timestamptz NOT NULL,
    p            double precision[] NOT NULL CHECK (probs_ok(p)),
    FOREIGN KEY (fixture_id, fixture_date) REFERENCES fixtures (id, date) ON UPDATE CASCADE,
    UNIQUE NULLS NOT DISTINCT (model_id, fixture_id, market, line),
    CONSTRAINT predictions_market CHECK (market_ok(market, line, cardinality(p))),
    CONSTRAINT predictions_before_match CHECK ((predicted_at AT TIME ZONE 'UTC')::date <= fixture_date)
);
CREATE INDEX predictions_fixture ON predictions (fixture_id);   -- the views join every prediction to its fixture

-- Every price pulled. football-data's `avg` and `max` are the market average and best price, not bookmakers.
CREATE TABLE odds_snapshots (
    fixture_id  integer     NOT NULL REFERENCES fixtures,
    market      text        NOT NULL,
    line        numeric,
    source      text        NOT NULL CHECK (source <> ''),
    bookmaker   text        NOT NULL CHECK (bookmaker <> ''),
    captured_at timestamptz NOT NULL,
    prices      double precision[] NOT NULL CHECK (prices_ok(prices)),
    UNIQUE NULLS NOT DISTINCT (fixture_id, market, line, source, bookmaker, captured_at),
    CONSTRAINT odds_snapshots_market CHECK (market_ok(market, line, cardinality(prices)))
);

-- Market probabilities with the margin removed, as a given method computed them: the reference a model is
-- measured against. Pre-match carries when its prices were taken; football-data's closing line has no time.
CREATE TABLE market_probs (
    fixture_id  integer     NOT NULL REFERENCES fixtures,
    market      text        NOT NULL,
    line        numeric,
    kind        text        NOT NULL CHECK (kind IN ('pre_match', 'closing')),
    method      text        NOT NULL CHECK (method <> ''),
    captured_at timestamptz,
    p           double precision[] NOT NULL CHECK (probs_ok(p)),
    UNIQUE NULLS NOT DISTINCT (fixture_id, market, line, kind, method),
    CONSTRAINT market_probs_market CHECK (market_ok(market, line, cardinality(p))),
    CONSTRAINT market_probs_time CHECK ((kind = 'pre_match') = (captured_at IS NOT NULL))
);

-- "Final once set" and "first kept" hold against UPDATE too, not only against a second INSERT. A stored score
-- can be withdrawn (a correction settled the other market against it) but never changed.
CREATE FUNCTION fixtures_final() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.result IS DISTINCT FROM NEW.result AND OLD.result IS NOT NULL
       OR OLD.ou25 IS DISTINCT FROM NEW.ou25 AND OLD.ou25 IS NOT NULL
       OR OLD.hg IS NOT NULL AND NEW.hg IS NOT NULL AND (OLD.hg, OLD.ag) IS DISTINCT FROM (NEW.hg, NEW.ag) THEN
        RAISE EXCEPTION 'fixture %: a settled outcome or a score is final', OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER fixtures_final BEFORE UPDATE ON fixtures FOR EACH ROW EXECUTE FUNCTION fixtures_final();

CREATE FUNCTION append_only() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '%: rows are append-only', TG_TABLE_NAME USING ERRCODE = 'integrity_constraint_violation';
END $$;
-- fixture_date is left out: a fixture's date change cascades into it (the cascade rewrites fixture_id too,
-- to the same value, so this compares values rather than listing columns).
CREATE TRIGGER predictions_append_only BEFORE UPDATE ON predictions FOR EACH ROW
    WHEN ((OLD.id, OLD.model_id, OLD.fixture_id, OLD.market, OLD.line, OLD.predicted_at, OLD.p)
          IS DISTINCT FROM (NEW.id, NEW.model_id, NEW.fixture_id, NEW.market, NEW.line, NEW.predicted_at, NEW.p))
    EXECUTE FUNCTION append_only();
CREATE TRIGGER odds_snapshots_append_only BEFORE UPDATE ON odds_snapshots FOR EACH ROW EXECUTE FUNCTION append_only();
CREATE TRIGGER market_probs_append_only BEFORE UPDATE ON market_probs FOR EACH ROW EXECUTE FUNCTION append_only();

-- The ledgers as the API reads them (same columns as ledger_*, plus `seq`). A ledger row is a michalis
-- prediction; its outcome and score show once that market was settled.
CREATE VIEW v_ledger_1x2 AS
SELECT pr.id AS seq, f.date, c.code AS league, h.name AS home, a.name AS away, f.season,
       pr.p[1] AS "p_H", pr.p[2] AS "p_D", pr.p[3] AS "p_A",
       mk.p[1] AS mkt_h, mk.p[2] AS mkt_d, mk.p[3] AS mkt_a,
       s_avg.prices[1] AS avg_h, s_avg.prices[2] AS avg_d, s_avg.prices[3] AS avg_a,
       s_max.prices[1] AS max_h, s_max.prices[2] AS max_d, s_max.prices[3] AS max_a,
       s_b365.prices[1] AS b365_h, s_b365.prices[2] AS b365_d, s_b365.prices[3] AS b365_a,
       s_ps.prices[1] AS ps_h, s_ps.prices[2] AS ps_d, s_ps.prices[3] AS ps_a,
       s_bfe.prices[1] AS bfe_h, s_bfe.prices[2] AS bfe_d, s_bfe.prices[3] AS bfe_a,
       pr.predicted_at AT TIME ZONE 'UTC' AS logged_at,
       f.result,
       cl.p[1] AS close_h, cl.p[2] AS close_d, cl.p[3] AS close_a,
       CASE WHEN f.result IS NOT NULL THEN f.hg END AS hg,
       CASE WHEN f.result IS NOT NULL THEN f.ag END AS ag
FROM predictions pr
JOIN models m ON m.id = pr.model_id AND m.name = 'michalis'
JOIN fixtures f ON f.id = pr.fixture_id
JOIN competitions c ON c.id = f.competition_id
JOIN teams h ON h.id = f.home_id
JOIN teams a ON a.id = f.away_id
JOIN market_probs mk ON (mk.fixture_id, mk.market, mk.kind, mk.method) = (f.id, '1x2', 'pre_match', 'model.paper')
LEFT JOIN market_probs cl ON (cl.fixture_id, cl.market, cl.kind, cl.method) = (f.id, '1x2', 'closing', 'model.paper')
LEFT JOIN odds_snapshots s_avg ON (s_avg.fixture_id, s_avg.market, s_avg.source, s_avg.bookmaker, s_avg.captured_at)
                                = (f.id, '1x2', 'football-data', 'avg', pr.predicted_at)
LEFT JOIN odds_snapshots s_max ON (s_max.fixture_id, s_max.market, s_max.source, s_max.bookmaker, s_max.captured_at)
                                = (f.id, '1x2', 'football-data', 'max', pr.predicted_at)
LEFT JOIN odds_snapshots s_b365 ON (s_b365.fixture_id, s_b365.market, s_b365.source, s_b365.bookmaker, s_b365.captured_at)
                                 = (f.id, '1x2', 'football-data', 'b365', pr.predicted_at)
LEFT JOIN odds_snapshots s_ps ON (s_ps.fixture_id, s_ps.market, s_ps.source, s_ps.bookmaker, s_ps.captured_at)
                               = (f.id, '1x2', 'football-data', 'ps', pr.predicted_at)
LEFT JOIN odds_snapshots s_bfe ON (s_bfe.fixture_id, s_bfe.market, s_bfe.source, s_bfe.bookmaker, s_bfe.captured_at)
                                = (f.id, '1x2', 'football-data', 'bfe', pr.predicted_at)
WHERE pr.market = '1x2';

CREATE VIEW v_ledger_ou25 AS
SELECT pr.id AS seq, f.date, c.code AS league, h.name AS home, a.name AS away, f.season,
       pr.p[1] AS "p_O", pr.p[2] AS "p_U",
       mk.p[1] AS mkt_o, mk.p[2] AS mkt_u,
       s_avg.prices[1] AS avg_o, s_avg.prices[2] AS avg_u,
       s_max.prices[1] AS max_o, s_max.prices[2] AS max_u,
       s_b365.prices[1] AS b365_o, s_b365.prices[2] AS b365_u,
       s_ps.prices[1] AS ps_o, s_ps.prices[2] AS ps_u,
       s_bfe.prices[1] AS bfe_o, s_bfe.prices[2] AS bfe_u,
       pr.predicted_at AT TIME ZONE 'UTC' AS logged_at,
       f.ou25 AS ou,
       cl.p[1] AS close_o, cl.p[2] AS close_u,
       CASE WHEN f.ou25 IS NOT NULL THEN f.hg END AS hg,
       CASE WHEN f.ou25 IS NOT NULL THEN f.ag END AS ag
FROM predictions pr
JOIN models m ON m.id = pr.model_id AND m.name = 'michalis'
JOIN fixtures f ON f.id = pr.fixture_id
JOIN competitions c ON c.id = f.competition_id
JOIN teams h ON h.id = f.home_id
JOIN teams a ON a.id = f.away_id
JOIN market_probs mk ON (mk.fixture_id, mk.market, mk.line, mk.kind, mk.method)
                      = (f.id, 'ou', 2.5, 'pre_match', 'model.paper')
LEFT JOIN market_probs cl ON (cl.fixture_id, cl.market, cl.line, cl.kind, cl.method)
                           = (f.id, 'ou', 2.5, 'closing', 'model.paper')
LEFT JOIN odds_snapshots s_avg ON (s_avg.fixture_id, s_avg.market, s_avg.line, s_avg.source, s_avg.bookmaker, s_avg.captured_at)
                                = (f.id, 'ou', 2.5, 'football-data', 'avg', pr.predicted_at)
LEFT JOIN odds_snapshots s_max ON (s_max.fixture_id, s_max.market, s_max.line, s_max.source, s_max.bookmaker, s_max.captured_at)
                                = (f.id, 'ou', 2.5, 'football-data', 'max', pr.predicted_at)
LEFT JOIN odds_snapshots s_b365 ON (s_b365.fixture_id, s_b365.market, s_b365.line, s_b365.source, s_b365.bookmaker, s_b365.captured_at)
                                 = (f.id, 'ou', 2.5, 'football-data', 'b365', pr.predicted_at)
LEFT JOIN odds_snapshots s_ps ON (s_ps.fixture_id, s_ps.market, s_ps.line, s_ps.source, s_ps.bookmaker, s_ps.captured_at)
                               = (f.id, 'ou', 2.5, 'football-data', 'ps', pr.predicted_at)
LEFT JOIN odds_snapshots s_bfe ON (s_bfe.fixture_id, s_bfe.market, s_bfe.line, s_bfe.source, s_bfe.bookmaker, s_bfe.captured_at)
                                = (f.id, 'ou', 2.5, 'football-data', 'bfe', pr.predicted_at)
WHERE pr.market = 'ou' AND pr.line = 2.5;
