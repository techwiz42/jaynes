// The events the backend sends (bicameral/events.py). Change both in the same commit.

export type Severity = "none" | "low" | "medium" | "high";
export type ErrorSource = "actor" | "observer" | "actor_correction";

export interface UserMessageEvent {
  id: number;
  type: "user_message";
  text: string;
}

export interface TokenEvent {
  id: number;
  type: "token";
  message_id: string;
  kind: "draft" | "correction";
  text: string;
  /** Correction tokens only: the message_id of the draft being corrected. */
  corrects?: string;
}

export interface DraftCompleteEvent {
  id: number;
  type: "draft_complete";
  message_id: string;
  text: string;
}

export interface ObserverVerdictEvent {
  id: number;
  type: "observer_verdict";
  message_id: string;
  verdict: "ok" | "retract";
  severity: Severity;
  issues: string[];
  correction_guidance: string;
}

export interface RetractionEvent {
  id: number;
  type: "retraction";
  message_id: string;
  issues: string[];
  /** The retracted draft. */
  text: string;
}

export interface ToolCallEvent {
  id: number;
  type: "tool_call";
  turn_id: string;
  call_id: string;
  tool: string;
  /** The arguments, with secrets removed. */
  args: Record<string, unknown>;
}

export interface ToolResultEvent {
  id: number;
  type: "tool_result";
  turn_id: string;
  call_id: string;
  tool: string;
  ok: boolean;
  /** One short line about what happened. Never the output itself, which can be private. */
  summary: string;
  truncated: boolean;
}

export interface ErrorEvent {
  id: number;
  type: "error";
  where: ErrorSource;
  message: string;
}

export type ChatEvent =
  | UserMessageEvent
  | TokenEvent
  | DraftCompleteEvent
  | ObserverVerdictEvent
  | RetractionEvent
  | ToolCallEvent
  | ToolResultEvent
  | ErrorEvent;

export type EventType = ChatEvent["type"];

export const EVENT_TYPES: readonly EventType[] = [
  "user_message",
  "token",
  "draft_complete",
  "observer_verdict",
  "retraction",
  "tool_call",
  "tool_result",
  "error",
];

/** One server-sent event's data, or null when it is not a known event. */
export function parseEvent(raw: string): ChatEvent | null {
  try {
    const value: unknown = JSON.parse(raw);
    if (typeof value !== "object" || value === null) return null;
    const event = value as { id?: unknown; type?: unknown };
    if (typeof event.id !== "number" || !EVENT_TYPES.includes(event.type as EventType)) return null;
    return value as ChatEvent;
  } catch {
    return null;
  }
}
