-- Run once in Neon's SQL editor (or any Postgres instance) before setting DATABASE_URL.
-- See README.md / GUIDE.md for the full one-time setup steps.

CREATE TABLE screenings (
  id                BIGSERIAL PRIMARY KEY,
  anonymous_id      TEXT NOT NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  job_description   TEXT NOT NULL,
  resume_text       TEXT,
  resume_file_name  TEXT,
  result            JSONB NOT NULL
);

CREATE INDEX idx_screenings_anonymous_id ON screenings (anonymous_id, created_at DESC);
