-- Run once in Neon's SQL editor (or any Postgres instance) before setting DATABASE_URL.
-- See README.md / GUIDE.md for the full one-time setup steps.

CREATE TABLE users (
  id             BIGSERIAL PRIMARY KEY,
  email          TEXT NOT NULL UNIQUE,
  password_hash  TEXT NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE screenings (
  id                BIGSERIAL PRIMARY KEY,
  user_id           BIGINT REFERENCES users(id),
  anonymous_id      TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  job_description   TEXT NOT NULL,
  resume_text       TEXT,
  resume_file_name  TEXT,
  result            JSONB NOT NULL,
  CONSTRAINT screenings_owner_chk CHECK (user_id IS NOT NULL OR anonymous_id IS NOT NULL)
);

CREATE INDEX idx_screenings_anonymous_id ON screenings (anonymous_id, created_at DESC);
CREATE INDEX idx_screenings_user_id ON screenings (user_id, created_at DESC) WHERE user_id IS NOT NULL;
