import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { ApiError, connectEvents, createSession, sendMessage, type Attachment } from "./api";
import { initialState, reduceAction } from "./reducer";

/**
 * One conversation. Without `conversationId` a new one is started; with one, that conversation is continued: its saved messages are the first events of its log, so they are shown first, then the live ones.
 * `onLoggedOut` is called when the server says the login has ended. `onActivity` when something the conversation list shows may have changed (a message was sent, a reply finished).
 */
export function useChat(conversationId: string | null, onLoggedOut: () => void, onActivity: () => void) {
  const [state, dispatch] = useReducer(reduceAction, initialState);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [thisConversation, setThisConversation] = useState<string | null>(conversationId);
  const [connected, setConnected] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const lastId = useRef(0);
  lastId.current = state.lastEventId;

  const failed = useCallback(
    (error: unknown): void => {
      if (error instanceof ApiError && error.status === 401) onLoggedOut();
      else setProblem((error as Error).message);
    },
    [onLoggedOut],
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const started = await createSession(conversationId ?? undefined);       // a continued conversation's saved messages are the start of the session's event log
      if (!cancelled) {
        setThisConversation(started.conversationId);
        setSessionId(started.sessionId);
      }
    })().catch((error: unknown) => {
      if (!cancelled) failed(error);
    });
    return () => {
      cancelled = true;
    };
  }, [conversationId, failed]);

  useEffect(() => {
    if (!sessionId) return;
    const connection = connectEvents(sessionId, () => lastId.current, dispatch, setConnected, onLoggedOut);
    return () => connection.close();
  }, [sessionId, onLoggedOut]);

  const replying = state.items.some((item) => item.kind === "assistant" && !item.complete && !item.interrupted);
  const wasReplying = useRef(false);
  useEffect(() => {
    if (wasReplying.current && !replying) onActivity();
    wasReplying.current = replying;
  }, [replying, onActivity]);

  const send = useCallback(
    async (text: string, attachments: Attachment[] = []) => {
      if (!sessionId) return;
      setProblem(null);
      try {
        await sendMessage(sessionId, text, attachments);
        onActivity();
      } catch (error) {
        failed(error);
      }
    },
    [sessionId, failed, onActivity],
  );

  return { state, sessionId, conversationId: thisConversation, connected, problem, send };
}
