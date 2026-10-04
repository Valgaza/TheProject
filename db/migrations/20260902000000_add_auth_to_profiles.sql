-- Real login. profiles gains what it needs for email/password and Google
-- OAuth; nothing else in the schema changes, since every other table already
-- keys off profiles.id, not off how someone proved they own that id.
--
-- Both credential types live on the same row rather than a separate
-- credentials table -- simplest thing that's still correct for one person,
-- one account. A user can have a password, a Google identity, or both;
-- the CHECK below just requires at least one.

alter table public.profiles
    add column if not exists email         text unique,
    add column if not exists password_hash text,
    add column if not exists google_id     text unique;

comment on column public.profiles.email is
    'Login identity. Unique, required for every account regardless of how they authenticate.';
comment on column public.profiles.password_hash is
    'bcrypt hash. Null for an account that only ever used Google sign-in.';
comment on column public.profiles.google_id is
    'Google''s "sub" claim -- their stable per-user id. Null for a password-only account.';

-- Backfills any profile that predates this migration -- on a fresh volume
-- there are no rows yet (migrations run before seed.sql), so this is a
-- no-op; on an existing volume it fixes up the demo user seeded under the
-- old schema, so the constraints below don't reject it. Password is
-- "nexus-demo" -- same value seed.sql now uses for a first-time install, so
-- the demo account logs in with the same credential either way.
update public.profiles
set email = 'demo@example.com',
    password_hash = '$2b$12$nEpuqHFrHzfZCoHqo8uuNuPtGjASou7f/N/fYTUVq39OW6uxYy6/C'
where email is null;

alter table public.profiles
    add constraint profiles_email_required
        check (email is not null),
    add constraint profiles_has_a_credential
        check (password_hash is not null or google_id is not null);
