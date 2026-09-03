/**
 * The one place the frontend knows where the backend lives.
 *
 * Three services now. `core` (8100) owns projects, chats and generation;
 * `memory` (8000) owns the knowledge graph; `RAG` (8001) owns document
 * ingestion and retrieval. Everything went through hand-rolled fetches with
 * the URLs redeclared per file before this.
 */

export const CORE_URL = process.env.NEXT_PUBLIC_CORE_URL ?? "http://localhost:8100";
export const MEMORY_URL = process.env.NEXT_PUBLIC_MEMORY_URL ?? "http://localhost:8000";
export const MEMORY_WS_URL = MEMORY_URL.replace(/^http/, "ws");
export const RAG_URL = process.env.NEXT_PUBLIC_RAG_URL ?? "http://localhost:8001";

/**
 * The logged-in user's id, set once by AuthProvider (lib/auth-context.tsx)
 * after `/auth/me` resolves, and read by everything below that still talks
 * to the memory service directly by user_id (core gets this from the
 * session cookie instead -- see the `core()` helper -- so only the memory
 * calls below need it threaded through explicitly).
 *
 * Module-level rather than a parameter on every call: the alternative is
 * threading a userId prop through every component between AuthProvider and
 * the graph panel, for a value that's really app-wide state, not
 * per-component data.
 */
let currentUserId: string | null = null;

export function setCurrentUserId(id: string | null): void {
  currentUserId = id;
}

function requireCurrentUserId(): string {
  if (!currentUserId) {
    throw new Error("no logged-in user -- called before auth resolved, or after logout");
  }
  return currentUserId;
}

// ── Core service ─────────────────────────────────────────────────────────────

export type Project = {
  id: string;
  name: string;
  description: string | null;
  domain: string;
  pinned: boolean;
  created_at: string;
  updated_at: string;
};

export type Chat = {
  id: string;
  // null for a chat created from /chat directly -- not filed under any
  // project (2026-09-03: chats no longer have to belong to one).
  project_id: string | null;
  title: string;
  pinned: boolean;
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
    // The session lives in an httpOnly cookie -- without this, the browser
    // never attaches it (or stores one core sets on signup/login), and
    // every request looks like it came from nobody.
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.text();
    // FastAPI's error responses are `{"detail": "..."}` -- surface just the
    // message, not the raw JSON envelope, since ApiError.message ends up
    // shown to the user as-is (see login/page.tsx). Falls back to the raw
    // body for a non-JSON error (a proxy's HTML error page, for instance).
    let message = body || response.statusText;
    try {
      const parsed = JSON.parse(body);
      if (typeof parsed?.detail === "string") message = parsed.detail;
    } catch {
      // Not JSON -- use the raw body/statusText already set above.
    }
    throw new ApiError(response.status, message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

// ── Auth ─────────────────────────────────────────────────────────────────────

export type Profile = {
  id: string;
  display_name: string;
  email: string;
};

export type AuthConfig = { google_oauth_enabled: boolean };

export const signup = (body: { email: string; password: string; display_name: string }) =>
  core<Profile>("/auth/signup", { method: "POST", body: JSON.stringify(body) });

export const login = (body: { email: string; password: string }) =>
  core<Profile>("/auth/login", { method: "POST", body: JSON.stringify(body) });

export const logout = () => core<{ status: string }>("/auth/logout", { method: "POST" });

/** 401s (as an ApiError) when there's no valid session -- callers treat that
 * as "not logged in", not as an unexpected failure. */
export const me = () => core<Profile>("/auth/me");

export const authConfig = () => core<AuthConfig>("/auth/config");

/** Where the browser should be sent to start the Google OAuth handshake --
 * a real navigation, not a fetch, since core replies with a redirect to
 * Google itself. */
export const googleLoginUrl = () => `${CORE_URL}/auth/google/login`;

export const listProjects = () => core<Project[]>("/projects");

export const createProject = (body: { name: string; description?: string; domain?: string }) =>
  core<Project>("/projects", { method: "POST", body: JSON.stringify(body) });

/** Rename or pin/unpin -- whichever fields are set. Powers the project
 * card's three-dot menu. */
export const updateProject = (
  projectId: string,
  patch: { name?: string; description?: string; pinned?: boolean }
) => core<Project>(`/projects/${projectId}`, { method: "PATCH", body: JSON.stringify(patch) });

/** Removes the project and every chat inside it. */
export const deleteProject = (projectId: string) =>
  core<void>(`/projects/${projectId}`, { method: "DELETE" });

export const listChats = (projectId: string) =>
  core<Chat[]>(`/projects/${projectId}/chats`);

export const createChat = (projectId: string, title?: string) =>
  core<Chat>(`/projects/${projectId}/chats`, {
    method: "POST",
    body: JSON.stringify(title ? { title } : {}),
  });

/** Chats not filed under any project -- merged into the sidebar's "Recent"
 * alongside whichever project is currently selected. */
export const listUnfiledChats = () => core<Chat[]>("/chats");

/** The counterpart to createChat: what /chat's "New conversation" actually
 * calls now, since a chat no longer has to belong to a project to exist. */
export const createUnfiledChat = (title?: string) =>
  core<Chat>("/chats", { method: "POST", body: JSON.stringify(title ? { title } : {}) });

export const getChat = (chatId: string) => core<Chat>(`/chats/${chatId}`);

/** Rename, pin/unpin, or move to another project -- whichever fields are
 * set. Powers the sidebar's three-dot menu. */
export const updateChat = (
  chatId: string,
  patch: { title?: string; pinned?: boolean; project_id?: string }
) => core<Chat>(`/chats/${chatId}`, { method: "PATCH", body: JSON.stringify(patch) });

export const deleteChat = (chatId: string) =>
  core<void>(`/chats/${chatId}`, { method: "DELETE" });

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

const scopeQuery = (projectId?: string) =>
  `user_id=${encodeURIComponent(requireCurrentUserId())}` +
  (projectId ? `&project_id=${encodeURIComponent(projectId)}` : "");

/**
 * Reads union the user tier with the project tier, when a project is given.
 *
 * NOTE: the memory service takes user_id as a plain query parameter and
 * does not itself check the session cookie -- it trusts whichever id is
 * passed. Using the real logged-in id here (instead of the old hardcoded
 * demo constant) makes each account's graph actually separate, but does not
 * close that gap: anyone who knew another user's id could still pass it
 * here directly. Tightening that is tracked in steps-to-implement-Bhoumish.md,
 * not fixed by this frontend change.
 */
export const graphUrl = (projectId?: string) =>
  `${MEMORY_URL}/graph?${scopeQuery(projectId)}`;

export const graphSocketUrl = (projectId?: string) =>
  `${MEMORY_WS_URL}/ws/graph/${encodeURIComponent(requireCurrentUserId())}` +
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
      user_id: requireCurrentUserId(),
      project_id: projectId ?? null,
      scope,
    }),
  }).catch(err => console.error("[memory] ingest failed:", err));
}

// ── RAG ──────────────────────────────────────────────────────────────────────

export type UploadResult = {
  success: boolean;
  file_name: string;
  documents_processed: number;
  chunks_created: number;
  embeddings_generated: number;
  points_upserted: number;
  errors: string[];
};

/**
 * Called directly on the RAG API (:8001), same as memory's calls above go
 * directly to :8000 -- not proxied through core. Fire-and-await, not
 * fire-and-forget: the composer shows real progress/failure per file.
 *
 * NOTE: RAG/rag_api.py's /api/ingest takes no project or user id -- every
 * upload lands in one shared, global document pool, not scoped to the
 * project it was uploaded from. Tracked as a known gap ("Per-user RAG
 * scoping" in steps-to-implement-Bhoumish.md), not fixed by this.
 */
export async function uploadDocument(file: File): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(`${RAG_URL}/api/ingest`, { method: "POST", body: form });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body?.detail || response.statusText;
    throw new ApiError(response.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body as UploadResult;
}
