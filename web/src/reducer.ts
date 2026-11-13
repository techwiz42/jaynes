import type { ChatEvent, ErrorSource, ObserverVerdictEvent } from "./events";

export interface UserItem {
  kind: "user";
  key: string;
  text: string;
}

export interface AssistantItem {
  kind: "assistant";
  messageId: string;
  /** "draft" is the actor's reply as first streamed; "correction" is the one reply that follows a retraction. */
  role: "draft" | "correction";
  text: string;
  complete: boolean;
  /** The stream broke before the reply was finished. */
  interrupted: boolean;
  /** The observer retracted this draft. */
  contested: boolean;
  retractionIssues: string[];
  corrects?: string;
}

export interface ErrorItem {
  kind: "error";
  key: string;
  where: ErrorSource;
  message: string;
}

export type Item = UserItem | AssistantItem | ErrorItem;

export interface Review {
  messageId: string;
  verdict: ObserverVerdictEvent["verdict"];
  severity: ObserverVerdictEvent["severity"];
  issues: string[];
  correctionGuidance: string;
}

export interface ChatState {
  items: Item[];
  reviews: Review[];
  /** The id of the last event applied; events at or below it are ignored, so a replay after a reconnect cannot duplicate anything. */
  lastEventId: number;
}

export const initialState: ChatState = { items: [], reviews: [], lastEventId: 0 };

function updateAssistant(items: Item[], messageId: string, change: (item: AssistantItem) => AssistantItem): Item[] {
  return items.map((item) => (item.kind === "assistant" && item.messageId === messageId ? change(item) : item));
}

function hasAssistant(items: Item[], messageId: string): boolean {
  return items.some((item) => item.kind === "assistant" && item.messageId === messageId);
}

export function reduce(state: ChatState, event: ChatEvent): ChatState {
  if (event.id <= state.lastEventId) return state;
  const next = (patch: Partial<ChatState>): ChatState => ({ ...state, ...patch, lastEventId: event.id });

  switch (event.type) {
    case "user_message":
      return next({ items: [...state.items, { kind: "user", key: `u${event.id}`, text: event.text }] });

    case "token": {
      if (hasAssistant(state.items, event.message_id)) {
        return next({ items: updateAssistant(state.items, event.message_id, (item) => ({ ...item, text: item.text + event.text })) });
      }
      const item: AssistantItem = {
        kind: "assistant",
        messageId: event.message_id,
        role: event.kind,
        text: event.text,
        complete: false,
        interrupted: false,
        contested: false,
        retractionIssues: [],
        ...(event.corrects ? { corrects: event.corrects } : {}),
      };
      return next({ items: [...state.items, item] });
    }

    case "draft_complete": {
      // The complete text is authoritative: it replaces whatever the tokens added up to.
      if (hasAssistant(state.items, event.message_id)) {
        return next({ items: updateAssistant(state.items, event.message_id, (item) => ({ ...item, text: event.text, complete: true })) });
      }
      const item: AssistantItem = {
        kind: "assistant",
        messageId: event.message_id,
        role: "draft",
        text: event.text,
        complete: true,
        interrupted: false,
        contested: false,
        retractionIssues: [],
      };
      return next({ items: [...state.items, item] });
    }

    case "observer_verdict":
      return next({
        reviews: [
          ...state.reviews,
          {
            messageId: event.message_id,
            verdict: event.verdict,
            severity: event.severity,
            issues: event.issues,
            correctionGuidance: event.correction_guidance,
          },
        ],
      });

    case "retraction":
      return next({
        items: updateAssistant(state.items, event.message_id, (item) => ({
          ...item,
          contested: true,
          retractionIssues: event.issues,
        })),
      });

    case "error": {
      // An actor error ends any reply that was still streaming.
      const interrupt = event.where === "actor" || event.where === "actor_correction";
      const items = interrupt
        ? state.items.map((item) => (item.kind === "assistant" && !item.complete ? { ...item, interrupted: true } : item))
        : state.items;
      return next({ items: [...items, { kind: "error", key: `e${event.id}`, where: event.where, message: event.message }] });
    }
  }
}

/** True while a reply is being streamed. */
export function isStreaming(state: ChatState): boolean {
  return state.items.some((item) => item.kind === "assistant" && !item.complete && !item.interrupted);
}

/** The review of a message, if the observer has given one. */
export function reviewOf(state: ChatState, messageId: string): Review | undefined {
  return state.reviews.find((review) => review.messageId === messageId);
}
