-- Local username/password accounts (replaces Supabase Auth for web/dev).
-- Run once in Supabase SQL editor after users table exists.

CREATE TABLE IF NOT EXISTS local_auth_users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  login_name text NOT NULL,
  email text,
  password_hash text NOT NULL,
  display_name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT local_auth_users_login_name_key UNIQUE (login_name)
);

CREATE UNIQUE INDEX IF NOT EXISTS local_auth_users_email_key
  ON local_auth_users (lower(email))
  WHERE email IS NOT NULL AND email <> '';
