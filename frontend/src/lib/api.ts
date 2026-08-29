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
  project_id: string;
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
  };
  created_at: string;
};

export type Route = { handler: string; reason: string };

export type Turn = {
  user_message: Message;
  assistant_message: Message;
  route: Route;
  chat_title: string;
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

export const listChats = (projectId: string) =>
  core<Chat[]>(`/projects/${projectId}/chats`);

export const createChat = (projectId: string, title?: string) =>
  core<Chat>(`/projects/${projectId}/chats`, {
    method: "POST",
    body: JSON.stringify(title ? { title } : {}),
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

/**
 * The project every chat lands in until the project switcher is wired up in
 * phase 6. Reuses the first existing project rather than creating a duplicate
 * on every cold start.
 */
export async function defaultProject(): Promise<Project> {
  const existing = await listProjects();
  if (existing.length > 0) return existing[0];
  return createProject({ name: "General", domain: "general" });
}

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

const scopeQuery = (projectId?: string) =>
  `user_id=${encodeURIComponent(USER_ID)}` +
  (projectId ? `&project_id=${encodeURIComponent(projectId)}` : "");

/** Reads union the user tier with the project tier, when a project is given. */
export const graphUrl = (projectId?: string) =>
  `${MEMORY_URL}/graph?${scopeQuery(projectId)}`;

export const graphSocketUrl = (projectId?: string) =>
  `${MEMORY_WS_URL}/ws/graph/${encodeURIComponent(USER_ID)}` +
  (projectId ? `?project_id=${encodeURIComponent(projectId)}` : "");

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
