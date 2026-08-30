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

/** Auth is deferred; every request is this user until Supabase Auth lands. */
export const USER_ID = "user_001";

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

export type ApiNode = {
  id: string;
  name: string;
  type: string;
  summary: string;
  attributes: Record<string, unknown>;
};

export type ApiEdge = {
  id: string;
  source: string;
  target: string;
  label: string;
  fact: string;
};

export type ApiGraph = { group_id: string; nodes: ApiNode[]; edges: ApiEdge[] };

export const graphUrl = (userId: string = USER_ID) =>
  `${MEMORY_URL}/graph?user_id=${encodeURIComponent(userId)}`;

export const graphSocketUrl = (userId: string = USER_ID) =>
  `${MEMORY_WS_URL}/ws/graph/${encodeURIComponent(userId)}`;

/** Fire-and-forget: the graph updating is not worth failing a chat turn over. */
export function ingest(text: string, userId: string = USER_ID): void {
  fetch(`${MEMORY_URL}/ingest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, user_id: userId }),
  }).catch(err => console.error("[memory] ingest failed:", err));
}
