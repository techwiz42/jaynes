import { EVENT_TYPES, parseEvent, type ChatEvent } from "./events";

/** A request the server refused. `message` is the server's own explanation when it gave one. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}, fallback = "the request failed"): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin", ...init });
  if (!response.ok) {
    let detail = "";
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* not JSON: the fallback says enough */
    }
    throw new ApiError(response.status, detail || `${fallback} (${response.status})`);
  }
  return (await response.json().catch(() => ({}))) as T;
}

const json = (method: string, body: unknown): RequestInit => ({ method, headers: { "content-type": "application/json" }, body: JSON.stringify(body) });

// ---- accounts ---------------------------------------------------------------------------------------------------------------------------------------------

export interface Account {
  email: string;
  /** True for the accounts the server lists as admins; they get the usage page. */
  is_admin?: boolean;
}

/** The logged-in user, or null when nobody is logged in. */
export async function currentUser(): Promise<Account | null> {
  try {
    return await request<Account>("/auth/me");
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}

export const register = (email: string, password: string): Promise<Account> => request("/auth/register", json("POST", { email, password }), "could not register");
export const login = (email: string, password: string): Promise<Account> => request("/auth/login", json("POST", { email, password }), "could not log in");
export const logout = (): Promise<unknown> => request("/auth/logout", { method: "POST" });
export interface UsageRow {
  email: string;
  tokens: number;
  login_count: number;
  last_login_at: number | null;
}

/** For the admin accounts only: the server answers anyone else as if the page did not exist. */
export const adminUsage = (): Promise<UsageRow[]> => request("/admin/usage", {}, "could not load the usage page");
export const deleteAccount = (password: string): Promise<unknown> => request("/auth/delete-account", json("POST", { password }), "could not delete the account");

// ---- conversations ----------------------------------------------------------------------------------------------------------------------------------------

export interface StartedSession {
  sessionId: string;
  conversationId: string;
  /** True when a session for this conversation was already running, so its events replay everything and the saved messages must not be loaded again. */
  live: boolean;
}

export async function createSession(conversationId?: string): Promise<StartedSession> {
  const body = await request<{ session_id: string; conversation_id: string; live?: boolean }>(
    "/sessions",
    conversationId ? json("POST", { conversation_id: conversationId }) : { method: "POST" },
    "could not start a session",
  );
  return { sessionId: body.session_id, conversationId: body.conversation_id, live: Boolean(body.live) };
}

/** A file sent with a message: its name and the text read from it. */
export interface Attachment {
  name: string;
  text: string;
}

export async function sendMessage(sessionId: string, text: string, attachments: Attachment[] = []): Promise<void> {
  await request(`/sessions/${sessionId}/messages`, json("POST", { text, attachments: attachments.map(({ name, text: body }) => ({ name, text: body })) }), "the message was not accepted");
}

export interface ReadAttachment extends Attachment {
  chars: number;
  /** The file had more text than is sent; the rest was cut off. */
  truncated: boolean;
}

/** The server reads the file (PDF, Word, Excel or text) and returns its text. Nothing is stored until the message is sent. */
export const readAttachment = (file: File): Promise<ReadAttachment> =>
  request(`/attachments?name=${encodeURIComponent(file.name)}`, { method: "POST", body: file, headers: { "content-type": "application/octet-stream" } }, "could not read the file");

export interface ConversationSummary {
  id: string;
  title: string;
  updated_at: number;
}

export interface SavedMessage {
  role: "user" | "assistant";
  text: string;
  created_at: number;
}

export const listConversations = (): Promise<ConversationSummary[]> => request("/conversations");
export const conversationMessages = (id: string): Promise<SavedMessage[]> => request(`/conversations/${id}/messages`);
export const deleteConversation = (id: string): Promise<unknown> => request(`/conversations/${id}`, { method: "DELETE" }, "could not delete the conversation");

// ---- the live event stream --------------------------------------------------------------------------------------------------------------------------------

export interface EventConnection {
  close(): void;
}

const RECONNECT_DELAY_MS = 1000;

/**
 * Subscribe to a session's events. When the connection drops it is reopened after a moment with `?after=<last id>`, so the server replays only what was missed.
 * `lastId` says how far the caller has got; the reducer also ignores any event it has already applied. `onSessionGone` is called when the server no longer has the session (it restarted).
 */
export function connectEvents(
  sessionId: string,
  lastId: () => number,
  onEvent: (event: ChatEvent) => void,
  onStatus: (connected: boolean) => void,
  onLoggedOut: () => void = () => undefined,
  onSessionGone: () => void = () => undefined,
): EventConnection {
  let source: EventSource | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let closed = false;

  const open = (): void => {
    source = new EventSource(`/sessions/${sessionId}/events?after=${lastId()}`);
    source.onopen = () => onStatus(true);
    source.onerror = () => {
      onStatus(false);
      source?.close();
      if (closed) return;
      // An EventSource cannot see why it failed. Ask whether the login is still good before trying again, so a logged-out page does not retry forever.
      void currentUser().then(
        (user) => {
          if (closed) return;
          if (!user) {
            onLoggedOut();
            return;
          }
          // A restart ends every session; asking for a dead one never works, so the caller is told to start the conversation again.
          void fetch(`/sessions/${sessionId}/alive`, { credentials: "same-origin" }).then(
            (response) => {
              if (closed) return;
              if (response.status === 404) onSessionGone();
              else timer = setTimeout(open, RECONNECT_DELAY_MS);
            },
            () => {
              if (!closed) timer = setTimeout(open, RECONNECT_DELAY_MS);
            },
          );
        },
        () => {
          if (!closed) timer = setTimeout(open, RECONNECT_DELAY_MS);
        },
      );
    };
    for (const type of EVENT_TYPES) {
      source.addEventListener(type, (message) => {
        const event = parseEvent((message as MessageEvent<string>).data);
        if (event) onEvent(event);
      });
    }
  };

  open();
  return {
    close() {
      closed = true;
      clearTimeout(timer);
      source?.close();
    },
  };
}
