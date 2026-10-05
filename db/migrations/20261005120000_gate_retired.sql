-- What a turn retired from memory because the person changed their mind.
--
-- When the write gate sees that a message replaces something already known
-- ("the deadline moved to the 21st"), core asks memory to retire the old
-- fact: kept in the graph as history, never recalled again. This column is
-- the record on the turn of what was retired and why a fact stopped coming
-- back: [{"fact", "id"}, {"items": [graph ids]}].

alter table public.gate_decisions
    add column if not exists retired jsonb not null default '[]'::jsonb;
