import { useEffect, useRef, useState, type FormEvent } from "react";
import { isStreaming, type AssistantItem, type ChatState, type Item, type Review } from "./reducer";
import { useChat } from "./useChat";

function Badge({ children, tone }: { children: string; tone: "contested" | "correction" | "error" | "ok" }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

function Assistant({ item }: { item: AssistantItem }) {
  const classes = ["message", "assistant", item.contested ? "contested" : "", item.role === "correction" ? "correction" : ""].join(" ");
  return (
    <div className={classes}>
      <div className="label">
        {item.role === "correction" ? <Badge tone="correction">Correction</Badge> : "Actor"}
        {item.contested && <Badge tone="contested">contested</Badge>}
        {item.interrupted && <Badge tone="error">interrupted</Badge>}
      </div>
      <p className="text">
        {item.text}
        {!item.complete && !item.interrupted && <span className="caret" aria-hidden="true" />}
      </p>
      {item.contested && item.retractionIssues.length > 0 && (
        <ul className="issues">
          {item.retractionIssues.map((issue) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Row({ item }: { item: Item }) {
  if (item.kind === "user") {
    return (
      <div className="message user">
        <div className="label">You</div>
        <p className="text">{item.text}</p>
      </div>
    );
  }
  if (item.kind === "error") {
    return (
      <div className="message error" role="alert">
        <div className="label">
          <Badge tone="error">error</Badge> {item.where}
        </div>
        <p className="text">{item.message}</p>
      </div>
    );
  }
  return <Assistant item={item} />;
}

function Transcript({ state }: { state: ChatState }) {
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [state.items, state.lastEventId]);
  return (
    <section className="transcript" aria-label="Conversation" aria-live="polite">
      {state.items.length === 0 && <p className="empty">Say something. The actor replies as it writes; the observer reviews each finished reply.</p>}
      {state.items.map((item) => (
        <Row key={item.kind === "assistant" ? item.messageId : item.key} item={item} />
      ))}
      <div ref={end} />
    </section>
  );
}

function ReviewCard({ review, preview }: { review: Review; preview: string }) {
  const retract = review.verdict === "retract";
  return (
    <li className={`review ${retract ? "retract" : "ok"}`}>
      <div className="label">
        {retract ? <Badge tone="contested">retract</Badge> : <Badge tone="ok">ok</Badge>}
        {retract && <span className="severity">{review.severity}</span>}
      </div>
      <p className="preview">{preview}</p>
      {retract && (
        <>
          <ul className="issues">
            {review.issues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
          {review.correctionGuidance && <p className="guidance">{review.correctionGuidance}</p>}
        </>
      )}
    </li>
  );
}

function ObserverPanel({ state }: { state: ChatState }) {
  const messages = new Map(state.items.flatMap((item) => (item.kind === "assistant" ? [[item.messageId, item.text] as const] : [])));
  return (
    <aside className="observer" aria-label="Observer">
      <h2>Observer</h2>
      {state.reviews.length === 0 ? (
        <p className="empty">No reviews yet.</p>
      ) : (
        <ul>
          {state.reviews.map((review) => {
            const text = messages.get(review.messageId) ?? "";
            return <ReviewCard key={review.messageId} review={review} preview={text.length > 90 ? `${text.slice(0, 90)}…` : text} />;
          })}
        </ul>
      )}
    </aside>
  );
}

function Composer({ onSend, disabled }: { onSend: (text: string) => Promise<void>; disabled: boolean }) {
  const [text, setText] = useState("");
  const submit = (event: FormEvent): void => {
    event.preventDefault();
    const trimmed = text.trim();
    if (!trimmed) return;
    setText("");
    void onSend(trimmed);
  };
  return (
    <form className="composer" onSubmit={submit}>
      <input value={text} onChange={(event) => setText(event.target.value)} placeholder="Message" aria-label="Message" maxLength={8000} disabled={disabled} autoFocus />
      <button type="submit" disabled={disabled || !text.trim()}>
        Send
      </button>
    </form>
  );
}

export default function App() {
  const { state, sessionId, connected, problem, send } = useChat();
  return (
    <div className="app">
      <header>
        <h1>jaynes</h1>
        <span className={`status ${connected ? "on" : "off"}`}>{!sessionId ? "starting…" : connected ? "connected" : "reconnecting…"}</span>
        {isStreaming(state) && <span className="status on">replying…</span>}
      </header>
      {problem && (
        <div className="problem" role="alert">
          {problem}
        </div>
      )}
      <main>
        <div className="chat">
          <Transcript state={state} />
          <Composer onSend={send} disabled={!sessionId} />
        </div>
        <ObserverPanel state={state} />
      </main>
    </div>
  );
}
