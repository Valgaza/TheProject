-- A chat no longer has to belong to a project.
--
-- Projects are memory scopes, not folders, and forcing every conversation into
-- one meant a throwaway question had to pick a body of work to contaminate. A
-- chat with no project reads and writes the user tier alone, which is what a
-- one-off question should do.
--
-- Ownership moves onto the chat itself. It used to be derived by joining
-- through projects, which stops working the moment project_id can be null.

alter table public.chats
    add column if not exists user_id uuid references public.profiles (id) on delete cascade;

-- Existing chats inherit the owner of the project they were filed under.
update public.chats c
   set user_id = p.user_id
  from public.projects p
 where c.project_id = p.id
   and c.user_id is null;

alter table public.chats alter column user_id    set not null;
alter table public.chats alter column project_id drop not null;

create index if not exists chats_user_id_idx
    on public.chats (user_id, updated_at desc);
