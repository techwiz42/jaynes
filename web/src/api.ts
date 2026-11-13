import { EVENT_TYPES, parseEvent, type ChatEvent } from "./events";

export async function createSession(): Promise<string> {
  const response = await fetch("/sessions", { method: "POST" });
  if (!response.ok) throw new Error(`could not start a session (${response.status})`);
  const body = (await response.json()) as { session_id: string };
  return body.session_id;
}

export async function sendMessage(sessionId: string, text: string): Promise<void> {
  const response = await fetch(`/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!response.ok) throw new Error(`the message was not accepted (${response.status})`);
}

export interface EventConnection {
  close(): void;
}

const RECONNECT_DELAY_MS = 1000;

/**
 * Subscribe to a session's events. When the connection drops it is reopened after a moment with `?after=<last id>`, so the server replays only what was missed.
 * `lastId` says how far the caller has got; the reducer also ignores any event it has already applied.
 */
export function connectEvents(
  sessionId: string,
  lastId: () => number,
  onEvent: (event: ChatEvent) => void,
  onStatus: (connected: boolean) => void,
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
      if (!closed) timer = setTimeout(open, RECONNECT_DELAY_MS);
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
