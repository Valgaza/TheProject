/**
 * The one place the frontend knows where the backend lives.
 *
 * Three services now. `core` (8100) owns projects, chats and generation;
 * `memory` (8000) owns the knowledge graph; `RAG` (8001) owns document
 * ingestion and retrieval, but the browser only ever reaches it through core,
 * which scopes every file to its owner. Everything went through hand-rolled fetches with
 * the URLs redeclared per file before this.
 */

export const CORE_URL = process.env.NEXT_PUBLIC_CORE_URL ?? "http://localhost:8100";
export const CORE_WS_URL = CORE_URL.replace(/^http/, "ws");

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
  /** Null for a chat filed under no project: user-tier memory only. */
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
    // Same idea for documents. Stored on the message rather than derived,
    // because which chunks were retrieved is a fact about that moment and
    // re-running the query later would answer differently.
    rag_sources?: RagSource[];
    // Only on turns that searched the web; query_sent is what left the machine.
    web?: WebMeta;
  };
  created_at: string;
};

export type WebMeta = {
  query_sent: string | null;
  error: string | null;
  results: { title: string; url: string }[];
};

export type Route = { handler: string; reason: string };

/**
 * A document chunk that informed an answer.
 *
 * `rerank_score` is the cross-encoder's 0-1 judgement of how well this chunk
 * matched the question. It is shown because "why did it cite that?" is the
 * first thing anyone asks about a wrong answer.
 */
export type RagSource = {
  file_name: string;
  page_number?: number | string | null;
  chunk_index?: number | string | null;
  heading_path?: string;
  rerank_score?: number;
};

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
  rag_sources: RagSource[];
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
    // Required, and easy to miss: the frontend (:3000) and core (:8100) are
    // different origins, and a browser sends no cookies on a cross-origin
    // request unless asked. Without this every call is anonymous and every
    // protected route answers 401 -- which is exactly what happened between
    // real auth landing and this line being added.
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

/** Every chat this user has, newest activity first. */
export const listAllChats = () => core<Chat[]>("/chats");

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

/** Chats not filed under any project. GET /chats returns every chat, so the
 * `loose` flag is what narrows it -- without it this returned the lot. */
export const listUnfiledChats = () => core<Chat[]>("/chats?loose=true");

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
export const sendMessage = (chatId: string, content: string, { useWeb = false } = {}) =>
  core<Turn>(`/chats/${chatId}/messages`, {
    method: "POST",
    body: JSON.stringify({ content, use_web: useWeb }),
  });

export type Document = {
  id: string;
  project_id: string | null;
  file_name: string;
  byte_size: number;
  chunk_count: number;
  status: "pending" | "ingested" | "failed";
  error: string | null;
  /** When this file's topics reached the knowledge graph. Null means never. */
  graph_synced_at: string | null;
  created_at: string;
};

/**
 * This person's files. No argument lists everything; `projectId` narrows to
 * one project; `loose` narrows to the universal tier -- the files readable
 * from every conversation.
 */
export const listDocuments = ({
  projectId,
  loose,
}: { projectId?: string | null; loose?: boolean } = {}) => {
  const query = new URLSearchParams();
  if (projectId) query.set("project_id", projectId);
  else if (loose) query.set("loose", "true");
  const suffix = query.toString();
  return core<Document[]>(`/documents${suffix ? `?${suffix}` : ""}`);
};

/**
 * Upload a file into one tier. No project means universal.
 *
 * Deliberately not routed through core(): that helper forces a JSON content
 * type, and a multipart body needs the browser to set its own boundary. Set
 * Content-Type here by hand and the upload fails in a way that reads like a
 * server problem.
 */
export async function uploadDocument(
  file: File,
  projectId?: string | null
): Promise<Document> {
  const body = new FormData();
  body.append("file", file);
  const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";

  const response = await fetch(`${CORE_URL}/documents${query}`, {
    method: "POST",
    credentials: "include",
    body,
  });
  if (!response.ok) {
    throw new ApiError(response.status, (await response.text()) || response.statusText);
  }
  return response.json() as Promise<Document>;
}

export const deleteDocument = (documentId: string) =>
  core<void>(`/documents/${documentId}`, { method: "DELETE" });

/** Broadcast that the file list changed, so open views refetch. */
export const DOCUMENTS_CHANGED = "nexus:documents-changed";

export const notifyDocumentsChanged = () => {
  window.dispatchEvent(new Event(DOCUMENTS_CHANGED));
};

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

/**
 * Reads union the user tier with the project tier, when a project is given.
 *
 * Through core, which takes the user from the session cookie. The browser
 * never names a user here: memory only answers core, and core only ever asks
 * about whoever is signed in.
 */
export const graphUrl = (view: GraphView = "all", projectId?: string) =>
  `${CORE_URL}/graph?${viewQuery(view, projectId)}`;

export const graphSocketUrl = (view: GraphView = "all", projectId?: string) =>
  `${CORE_WS_URL}/ws/graph?${viewQuery(view, projectId)}`;

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

// ── RAG ──────────────────────────────────────────────────────────────────────

