-- TodAI: calendar_events + recurrence (Flutter / REST API)
-- Run after users table exists. Safe to re-run (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS calendar_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title text NOT NULL,
  description text,
  start_at timestamptz NOT NULL,
  end_at timestamptz NOT NULL,
  all_day boolean NOT NULL DEFAULT false,
  kind text NOT NULL DEFAULT 'personal',
  location text,
  source text NOT NULL DEFAULT 'user',
  status text NOT NULL DEFAULT 'confirmed',
  recurrence_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz
);

CREATE INDEX IF NOT EXISTS idx_calendar_events_user_start
  ON calendar_events(user_id, start_at)
  WHERE deleted_at IS NULL AND status = 'confirmed';

CREATE TABLE IF NOT EXISTS calendar_recurrence (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  frequency varchar NOT NULL DEFAULT 'weekly' CHECK (frequency IN ('weekly')),
  weekly_mode varchar NOT NULL DEFAULT 'same_day'
    CHECK (weekly_mode IN ('same_day', 'weekdays')),
  skip_days smallint[] NOT NULL DEFAULT '{}',
  repeat_weeks int NOT NULL DEFAULT 12 CHECK (repeat_weeks >= 1 AND repeat_weeks <= 52),
  anchor_start_at timestamptz NOT NULL,
  anchor_end_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE calendar_events
  ADD COLUMN IF NOT EXISTS recurrence_id uuid REFERENCES calendar_recurrence(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_calendar_events_recurrence
  ON calendar_events(recurrence_id)
  WHERE recurrence_id IS NOT NULL;
