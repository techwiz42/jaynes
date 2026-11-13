import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { connectEvents, createSession, sendMessage } from "./api";
import { initialState, reduce } from "./reducer";

export function useChat() {
  const [state, dispatch] = useReducer(reduce, initialState);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const lastId = useRef(0);
  lastId.current = state.lastEventId;

  useEffect(() => {
    let cancelled = false;
    createSession()
      .then((id) => {
        if (!cancelled) setSessionId(id);
      })
      .catch((error: Error) => setProblem(error.message));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!sessionId) return;
    const connection = connectEvents(sessionId, () => lastId.current, dispatch, setConnected);
    return () => connection.close();
  }, [sessionId]);

  const send = useCallback(
    async (text: string) => {
      if (!sessionId) return;
      setProblem(null);
      try {
        await sendMessage(sessionId, text);
      } catch (error) {
        setProblem((error as Error).message);
      }
    },
    [sessionId],
  );

  return { state, sessionId, connected, problem, send };
}
