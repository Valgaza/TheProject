-- Order messages by the wall clock, not by the transaction clock.
--
-- Postgres `now()` returns the timestamp of the enclosing *transaction*, so
-- every row written in one transaction shares it exactly. A turn writes the
-- user message and the assistant reply in a single transaction, which left the
-- two rows with identical `created_at` and therefore no defined order — the
-- reply could render above the question it answered.
--
-- `clock_timestamp()` reads the actual clock on each call, so rows written
-- together still differ. It is also the more honest default here: a message's
-- created_at is a wall-clock fact about when it was said.

alter table public.messages
    alter column created_at set default clock_timestamp();

-- Existing rows keep whatever they have; the index is unchanged, only the
-- default that feeds it.
