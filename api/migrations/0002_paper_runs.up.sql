-- Slots of the paper job (Tue + Fri 07:00 UTC) that ran to the end. The worker (api/worker.py) runs the
-- latest slot when it has no row here, so a restart neither repeats a run nor skips one it slept through.

CREATE TABLE paper_runs (
    slot        timestamptz PRIMARY KEY,
    finished_at timestamptz NOT NULL DEFAULT now() CHECK (finished_at >= slot)
);
