-- Chats can now exist with no project -- created from /chat directly,
-- filed under one only when created from within a project (or moved there
-- later via PATCH /chats/{id}). Ownership used to come entirely through
-- project_id -> projects.user_id; that join breaks the moment project_id
-- can be null, so chats get their own user_id instead of depending on a
-- project to say who they belong to.

alter table public.chats
    add column if not exists user_id uuid references public.profiles(id) on delete cascade;

-- Backfill every existing chat's owner from the project it's currently in
-- -- on this schema, that's the only place the answer could come from.
update public.chats c
set user_id = p.user_id
from public.projects p
where c.project_id = p.id
  and c.user_id is null;

alter table public.chats
    alter column user_id set not null;

-- project_id was NOT NULL with ON DELETE CASCADE; drop and re-add it as
-- nullable with the same cascade behavior (deleting a project still takes
-- its filed chats with it -- unfiled chats have no project_id to cascade
-- from in the first place).
alter table public.chats
    drop constraint if exists chats_project_id_fkey,
    alter column project_id drop not null,
    add constraint chats_project_id_fkey
        foreign key (project_id) references public.projects(id) on delete cascade;

comment on column public.chats.user_id is
    'Who owns this chat -- always set, independent of whether it is filed under a project.';
comment on column public.chats.project_id is
    'Which project this chat is filed under, if any. Null means unfiled -- created from /chat directly, not from within a project.';
