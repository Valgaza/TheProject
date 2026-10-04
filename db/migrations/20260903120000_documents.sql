-- Documents become records.
--
-- Until now an uploaded file existed only as anonymous vectors in Qdrant:
-- nothing recorded what was uploaded, by whom, or to what. You could not list
-- a project's files, delete one, or stop one project's corpus from answering
-- another project's questions.
--
-- The split matches the one this schema already makes. Postgres owns what was
-- uploaded -- the record, its owner, its tier. Qdrant owns the chunks and
-- their vectors, keyed back here by document_id. Neither is a cache of the
-- other, exactly as Postgres and Neo4j are not caches of each other.

create table if not exists public.documents (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid        not null references public.profiles (id) on delete cascade,
    -- Null means the universal tier, mirroring chats.project_id exactly. A
    -- loose document and a loose chat are then the same shape, and the
    -- sidebar's existing project-versus-loose grouping needs no new concept
    -- to handle files.
    project_id  uuid        references public.projects (id) on delete cascade,
    file_name   text        not null,
    -- Bytes as received. Kept for the UI, which has to say something about a
    -- file before its chunk count exists.
    byte_size   bigint      not null default 0,
    -- What the ingest actually produced. Zero while pending, and the number
    -- to delete from Qdrant when this row goes away.
    chunk_count integer     not null default 0,
    status      text        not null default 'pending'
                check (status in ('pending', 'ingested', 'failed')),
    -- Why an ingest failed, for the same reason memory_episodes.error exists:
    -- a failure you cannot read is a failure you cannot fix.
    error       text,
    -- Set once the topic facts from this document reach the graph. Null means
    -- they never did -- either it failed or the bridge was not run -- which is
    -- the difference between "the graph does not know about this file" and
    -- "the graph knows and found nothing worth keeping".
    graph_synced_at timestamptz,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

-- The listing query: one user's documents in one tier, newest first. Partial
-- on project_id being null would need two indexes; this one covers both the
-- loose case and the project case because project_id sorts nulls together.
create index if not exists documents_user_project_idx
    on public.documents (user_id, project_id, created_at desc);

-- The same file uploaded twice to the same tier is almost always a mistake
-- rather than an intent, and silently doubling every chunk in retrieval is a
-- bad way to find out. Not a hard constraint: nulls never conflict in a
-- unique index, so loose uploads would escape it anyway, and re-uploading a
-- corrected version of a file is legitimate. Left to the application.
comment on table public.documents is
    'Uploaded files. Postgres owns the record; Qdrant owns the chunks, keyed by document_id.';
comment on column public.documents.project_id is
    'Null = universal tier, visible in every chat. Set = isolated to that project.';
comment on column public.documents.graph_synced_at is
    'When this document''s topics were written to the knowledge graph. Null = never.';
