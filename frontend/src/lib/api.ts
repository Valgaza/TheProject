/**
 * The one place the frontend knows where the backend lives.
 *
 * Two services, two jobs. `core` (8100) owns projects, chats and generation;
 * `memory` (8000) owns the knowledge graph. Everything went through hand-rolled
 * fetches with the URLs redeclared per file before this.
 */

export const CORE_URL = process.env.NEXT_PUBLIC_CORE_URL ?? "http://localhost:8100";
export const MEMORY_URL = process.env.NEXT_PUBLIC_MEMORY_URL ?? "http://localhost:8000";
export const MEMORY_WS_URL = MEMORY_URL.replace(/^http/, "ws");

/**
 * Auth is deferred; every request is this user until Supabase Auth lands.
 *
 * This is the profile seeded by supabase/seed.sql and the DEMO_USER_ID the core
 * service runs as. It used to be the string "user_001", which meant the memory
 * graph was keyed to an identity no Supabase row corresponded to -- chat history
 * and memory were about two different people.
 */
export const USER_ID = "00000000-0000-0000-0000-000000000001";

// ── Core service ─────────────────────────────────────────────────────────────

export type Project = {
  id: string;
  name: string;
  description: string | null;
  domain: string;
  created_at: string;
  updated_at: string;
};

export type Chat = {
  id: string;
  /** Null for a chat filed under no project: user-tier memory only. */
  project_id: string | null;
  title: string;
  created_at: string;
  updated_at: string;
};

export type Role = "user" | "assistant" | "system";

export type Message = {
  id: string;
  chat_id: string;
  role: Role;
  content: string;
  metadata: {
    route?: string;
    route_reason?: string;
    latency_ms?: number;
    prompt_messages?: number;
    error?: string;
    // Recorded on the assistant message, so a reloaded thread can show what
    // memory contributed just as well as a live one.
    recalled_facts?: RecalledFact[];
  };
  created_at: string;
};

export type Route = { handler: string; reason: string };

/** A fact memory supplied for a turn. "kind" says where in the graph it lived. */
export type RecalledFact = {
  id: string;
  fact: string;
  scope: "user" | "project";
  kind: "relationship" | "entity" | "episode";
};

export type Turn = {
  user_message: Message;
  assistant_message: Message;
  route: Route;
  chat_title: string;
  recalled: RecalledFact[];
};

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function core<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${CORE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.text();
    throw new ApiError(response.status, body || response.statusText);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const listProjects = () => core<Project[]>("/projects");

export const createProject = (body: { name: string; description?: string; domain?: string }) =>
  core<Project>("/projects", { method: "POST", body: JSON.stringify(body) });

/** Every chat this user has, newest activity first. */
export const listAllChats = () => core<Chat[]>("/chats");

export const listChats = (projectId: string) =>
  core<Chat[]>(`/projects/${projectId}/chats`);

/**
 * Start a conversation. No project means user-tier memory only, which is what
 * a one-off question should get — filing it under a project would write into
 * that body of work.
 */
export const createChat = (
  { projectId, title }: { projectId?: string | null; title?: string } = {}
) =>
  core<Chat>("/chats", {
    method: "POST",
    body: JSON.stringify({
      ...(projectId ? { project_id: projectId } : {}),
      ...(title ? { title } : {}),
    }),
  });

export const getChat = (chatId: string) => core<Chat>(`/chats/${chatId}`);

export const listMessages = (chatId: string) =>
  core<Message[]>(`/chats/${chatId}/messages`);

/** Send a message and wait for the answer. The reply is the response. */
export const sendMessage = (chatId: string, content: string) =>
  core<Turn>(`/chats/${chatId}/messages`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });

/**
 * Broadcast that the chat list changed — a chat was created, or a first message
 * renamed one. The sidebar refetches on it. A cache with real invalidation is
 * phase 6's job; this is one event and two listeners.
 */
export const CHATS_CHANGED = "nexus:chats-changed";

export const notifyChatsChanged = () => {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(CHATS_CHANGED));
};

// ── Memory service ───────────────────────────────────────────────────────────

/** Memory tiers. A read unions both; a write picks one. */
export type Scope = "user" | "project";

export type ApiNode = {
  id: string;
  name: string;
  type: string;
  summary: string;
  attributes: Record<string, unknown>;
  /** Which tier this came from. */
  group_id: string;
};

export type ApiEdge = {
  id: string;
  source: string;
  target: string;
  label: string;
  fact: string;
  group_id: string;
};

/** The union that was read, not a single group. */
export type ApiGraph = { group_ids: string[]; nodes: ApiNode[]; edges: ApiEdge[] };

/**
 * Which slice of the graph to draw.
 *
 * Not the same question as what a retrieval may see — that always unions both
 * tiers. "user" is the universal graph on its own, "project" is one body of
 * work on its own, and "all" is the union. Isolating them is the only way to
 * see on screen which tier a fact actually landed in.
 */
export type GraphView = "all" | "user" | "project";

/** The half of the query both graph endpoints share; the user id differs. */
const viewQuery = (view: GraphView, projectId?: string) => {
  const params = new URLSearchParams({ view });
  if (projectId) params.set("project_id", projectId);
  return params.toString();
};

export const graphUrl = (view: GraphView = "all", projectId?: string) =>
  `${MEMORY_URL}/graph?user_id=${encodeURIComponent(USER_ID)}&${viewQuery(view, projectId)}`;

export const graphSocketUrl = (view: GraphView = "all", projectId?: string) =>
  `${MEMORY_WS_URL}/ws/graph/${encodeURIComponent(USER_ID)}?${viewQuery(view, projectId)}`;

/**
 * Fire-and-forget: the graph updating is not worth failing a chat turn over.
 *
 * Scope is explicit because the memory service refuses to infer it -- a project
 * fact quietly landing in durable user memory is the hardest bug to notice.
 */
export function ingest(
  text: string,
  { scope = "user", projectId }: { scope?: Scope; projectId?: string } = {}
): void {
  fetch(`${MEMORY_URL}/ingest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text,
      user_id: USER_ID,
      project_id: projectId ?? null,
      scope,
    }),
  }).catch(err => console.error("[memory] ingest failed:", err));
}

// ── Maintenance ──────────────────────────────────────────────────────────────

/** What one group's tidy-up found. Text, not counts: when the pass is wrong,
 *  the only useful question is which fact it touched. */
export type SweepReport = {
  group_id: string;
  scope: Scope;
  duplicate_edges: string[];
  near_duplicates: string[];
  /** Two relations live between one pair. Never merged automatically — "uses X"
   *  and "no longer uses X" look almost identical to an embedding. */
  conflicts: string[];
  /** Where graphiti's own invalidation looks wrong. Reported, never repaired. */
  suspect_invalidations: string[];
  summaries_rebuilt: string[];
  episodes_suppressed: string[];
  orphans_deleted: string[];
};

export type MaintenanceResult = {
  group_ids: string[];
  dry_run: boolean;
  clean: boolean;
  total_changes: number;
  reports: SweepReport[];
};

/**
 * Tidy the graph behind one view. Reports without changing anything unless
 * `dryRun` is false — the pass will be wrong sometimes and the diff is worth
 * reading first.
 *
 * Goes through core rather than straight to the memory service, unlike the
 * graph reads: every run is recorded in Postgres, and core is what owns that.
 */
export const runMaintenance = (
  view: GraphView,
  projectId: string | undefined,
  dryRun: boolean
) =>
  core<MaintenanceResult>("/maintenance", {
    method: "POST",
    body: JSON.stringify({
      view,
      project_id: view === "project" ? projectId : null,
      dry_run: dryRun,
    }),
  });
