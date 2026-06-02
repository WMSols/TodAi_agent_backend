-- TodAI: message buckets + goal planner tables
-- Run in Supabase SQL Editor (safe to re-run sections that use IF NOT EXISTS).
-- Backward compatible: keeps messages.conversation_id; adds bucket_id for bucket storage.

-- ─── Goals (if not already created) ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS goals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title text NOT NULL,
  description text,
  difficulty varchar NOT NULL DEFAULT 'medium',
  status varchar NOT NULL DEFAULT 'active',
  target_date date,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_goals_user_status ON goals(user_id, status);

CREATE TABLE IF NOT EXISTS goal_week_plans (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  goal_id uuid NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
  start_date date NOT NULL,
  end_date date NOT NULL,
  difficulty varchar NOT NULL DEFAULT 'medium',
  plan_notes text,
  only_free_days boolean NOT NULL DEFAULT false,
  status varchar NOT NULL DEFAULT 'draft',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_plans_goal_start ON goal_week_plans(goal_id, start_date);

CREATE TABLE IF NOT EXISTS goal_tasks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  goal_id uuid NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
  plan_id uuid REFERENCES goal_week_plans(id) ON DELETE SET NULL,
  title text NOT NULL,
  description text,
  task_date date NOT NULL,
  start_time time,
  end_time time,
  status varchar NOT NULL DEFAULT 'pending',
  calendar_event_id uuid UNIQUE REFERENCES calendar_events(id) ON DELETE SET NULL,
  sort_order smallint NOT NULL DEFAULT 0,
  source varchar NOT NULL DEFAULT 'agent',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  done_at timestamptz,
  skipped_at timestamptz
);

CREATE INDEX IF NOT EXISTS idx_tasks_user_date_status ON goal_tasks(user_id, task_date, status);
CREATE INDEX IF NOT EXISTS idx_tasks_goal_date ON goal_tasks(goal_id, task_date);

-- ─── Conversations: channel for main chat vs goal planner ───────────────────

ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS channel varchar NOT NULL DEFAULT 'chat';

ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS goal_week_plan_id uuid REFERENCES goal_week_plans(id) ON DELETE SET NULL;

COMMENT ON COLUMN conversations.channel IS 'chat | goal_plan';

CREATE INDEX IF NOT EXISTS idx_conversations_user_channel
  ON conversations(user_id, channel)
  WHERE archived_at IS NULL;

-- ─── Message buckets ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS message_buckets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  channel varchar NOT NULL DEFAULT 'chat',
  goal_week_plan_id uuid REFERENCES goal_week_plans(id) ON DELETE SET NULL,
  bucket_index int NOT NULL DEFAULT 0,
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT message_buckets_channel_check CHECK (channel IN ('chat', 'goal_plan'))
);

CREATE INDEX IF NOT EXISTS idx_message_buckets_conv_active
  ON message_buckets(conversation_id, is_active)
  WHERE is_active = true;

CREATE INDEX IF NOT EXISTS idx_message_buckets_user_channel
  ON message_buckets(user_id, channel);

-- ─── Messages: link to bucket + optional UI meta ───────────────────────────

ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS bucket_id uuid REFERENCES message_buckets(id) ON DELETE CASCADE;

ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS meta jsonb;

ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS sequence_no int;

CREATE INDEX IF NOT EXISTS idx_messages_bucket_seq
  ON messages(bucket_id, sequence_no);

CREATE INDEX IF NOT EXISTS idx_messages_bucket_created
  ON messages(bucket_id, created_at);

-- ─── Migrate existing flat messages into one bucket per conversation ───────

INSERT INTO message_buckets (conversation_id, user_id, channel, bucket_index, is_active)
SELECT c.id, c.user_id, COALESCE(c.channel, 'chat'), 0, true
FROM conversations c
WHERE NOT EXISTS (
  SELECT 1 FROM message_buckets b WHERE b.conversation_id = c.id AND b.is_active = true
);

UPDATE messages m
SET
  bucket_id = b.id,
  sequence_no = sub.rn
FROM (
  SELECT
    m2.id AS message_id,
    b.id AS bucket_id,
    ROW_NUMBER() OVER (
      PARTITION BY m2.conversation_id
      ORDER BY m2.created_at ASC NULLS LAST, m2.id ASC
    ) - 1 AS rn
  FROM messages m2
  JOIN message_buckets b
    ON b.conversation_id = m2.conversation_id AND b.is_active = true
  WHERE m2.bucket_id IS NULL
) sub
JOIN message_buckets b ON b.id = sub.bucket_id
WHERE m.id = sub.message_id;

-- Optional: enforce bucket_id for new rows after migration (uncomment when ready)
-- ALTER TABLE messages ALTER COLUMN bucket_id SET NOT NULL;
