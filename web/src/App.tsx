import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ApiError, currentUser, deleteConversation, listConversations, logout, type Account, type ConversationSummary } from "./api";
import { AuthForm } from "./Auth";
import { BrainIcon } from "./BrainIcon";
import { Footer } from "./Footer";
import { MarkdownText, plainText } from "./Markdown";
import { MemoryPanel } from "./Memory";
import { Sidebar } from "./Sidebar";
import { isActorWorking, isStreaming, type AssistantItem, type ChatState, type Item, type Review, type ToolItem } from "./reducer";
import { useChat } from "./useChat";

function Badge({ children, tone }: { children: string; tone: "contested" | "correction" | "error" | "ok" }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

function Assistant({ item }: { item: AssistantItem }) {
  const classes = ["message", "assistant", item.contested ? "contested" : "", item.role === "correction" ? "correction" : ""].join(" ");
  return (
    <div className={classes}>
      <div className="label">
        {item.role === "correction" ? <Badge tone="correction">Correction</Badge> : "Jaynes"}
        {item.contested && <Badge tone="contested">contested</Badge>}
        {item.interrupted && <Badge tone="error">interrupted</Badge>}
      </div>
      <MarkdownText>{item.complete || item.interrupted ? item.text : `${item.text} ▍`}</MarkdownText>
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

function ToolBadge({ item }: { item: ToolItem }) {
  const mark = item.status === "running" ? "…" : item.status === "ok" ? "✓" : "✗";
  return (
    <div className={`tool ${item.status}`} title={item.summary || item.target}>
      <span className="tool-mark" aria-hidden="true">
        {mark}
      </span>
      <span className="tool-name">{item.tool}</span>
      {item.target && <span className="tool-target">{item.target}</span>}
      {item.summary && <span className="tool-summary">{item.summary}</span>}
      {item.truncated && <span className="tool-cut">truncated</span>}
    </div>
  );
}

function Row({ item }: { item: Item }) {
  if (item.kind === "tool") return <ToolBadge item={item} />;
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
            const text = plainText(messages.get(review.messageId) ?? "");
            return <ReviewCard key={review.messageId} review={review} preview={text.length > 90 ? `${text.slice(0, 90)}…` : text} />;
          })}
        </ul>
      )}
    </aside>
  );
}

/** Three dots in a wave, shown inside the input box while the actor is preparing a reply. */
function Typing() {
  return (
    <span className="typing" role="status" aria-label="Jaynes is preparing a reply">
      <span />
      <span />
      <span />
    </span>
  );
}

function Composer({ onSend, disabled, working }: { onSend: (text: string) => Promise<void>; disabled: boolean; working: boolean }) {
  const [text, setText] = useState("");
  const send = (): void => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setText("");
    void onSend(trimmed);
  };
  const submit = (event: FormEvent): void => {
    event.preventDefault();
    send();
  };
  // Enter sends; Shift+Enter starts a new line. Enter while an input method is composing text (for example, picking a character) is left alone.
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      send();
    }
  };
  return (
    <form className="composer" onSubmit={submit}>
      <div className="field">
      <textarea
        className={working ? "working" : undefined}
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Message (Enter to send, Shift+Enter for a new line)"
        aria-label="Message"
        rows={3}
        maxLength={8000}
        disabled={disabled}
        autoFocus
      />
        {working && <Typing />}
      </div>
      <button type="submit" disabled={disabled || !text.trim()}>
        Send
      </button>
    </form>
  );
}

function Chat({
  account,
  conversationId,
  conversations,
  onOpen,
  onNew,
  onDelete,
  onLogout,
  onLoggedOut,
  onActivity,
  onConversation,
}: {
  account: Account;
  conversationId: string | null;
  conversations: ConversationSummary[];
  onOpen: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onLogout: () => void;
  onLoggedOut: () => void;
  onActivity: () => void;
  onConversation: (id: string) => void;
}) {
  const { state, sessionId, conversationId: started, connected, problem, send } = useChat(conversationId, onLoggedOut, onActivity);
  const [memoryOpen, setMemoryOpen] = useState(false);
  useEffect(() => {
    if (started) onConversation(started);
  }, [started, onConversation]);
  return (
    <div className="app">
      <header>
        <h1 className="brand">
          <BrainIcon size={26} />
          jaynes
        </h1>
        <span className={`status ${connected ? "on" : "off"}`}>{!sessionId ? "starting…" : connected ? "connected" : "reconnecting…"}</span>
        {isStreaming(state) && <span className="status on">replying…</span>}
        <span className="spacer" />
        <span className="account">{account.email}</span>
        <button type="button" className="quiet" onClick={() => setMemoryOpen(!memoryOpen)} aria-expanded={memoryOpen}>
          Memory
        </button>
        <button type="button" className="quiet" onClick={onLogout}>
          Log out
        </button>
      </header>
      {state.privateTools.length > 0 && (
        <div className="private" role="status">
          <span>
            This conversation has read private data ({state.privateTools.join(", ")}). Web search and fetch are off for the rest of it, so nothing can be sent out.
          </span>
          <button type="button" onClick={onNew}>
            Start a new conversation
          </button>
        </div>
      )}
      {problem && (
        <div className="problem" role="alert">
          {problem}
        </div>
      )}
      <main>
        <Sidebar conversations={conversations} current={started} onOpen={onOpen} onNew={onNew} onDelete={onDelete} />
        <div className="chat">
          <Transcript state={state} />
          <Composer onSend={send} disabled={!sessionId} working={isActorWorking(state)} />
        </div>
        {memoryOpen ? <MemoryPanel email={account.email} onClose={() => setMemoryOpen(false)} onAccountDeleted={onLoggedOut} /> : <ObserverPanel state={state} />}
      </main>
      <Footer />
    </div>
  );
}

/** A logged-in user's workspace: their saved conversations and the one that is open. Opening another one starts a fresh chat component, so nothing leaks between them. */
function Workspace({ account, onLoggedOut }: { account: Account; onLoggedOut: () => void }) {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [open, setOpen] = useState<{ key: number; id: string | null }>({ key: 0, id: null });
  const [current, setCurrent] = useState<string | null>(null);

  const refresh = useCallback((): void => {
    listConversations().then(setConversations, (error: unknown) => {
      if (error instanceof ApiError && error.status === 401) onLoggedOut();
    });
  }, [onLoggedOut]);
  useEffect(refresh, [refresh]);

  const start = useCallback((id: string | null): void => setOpen((previous) => ({ key: previous.key + 1, id })), []);
  const newChat = useCallback((): void => start(null), [start]);
  const remove = (id: string): void => {
    if (!window.confirm("Delete this conversation and what Jaynes remembers from it?")) return;
    void deleteConversation(id).then(
      () => {
        if (id === current) newChat();
        refresh();
      },
      () => refresh(),
    );
  };
  const leave = (): void => {
    void logout().finally(onLoggedOut);
  };

  return (
    <Chat
      key={open.key}
      account={account}
      conversationId={open.id}
      conversations={conversations}
      onOpen={start}
      onNew={newChat}
      onDelete={remove}
      onLogout={leave}
      onLoggedOut={onLoggedOut}
      onActivity={refresh}
      onConversation={setCurrent}
    />
  );
}

export default function App() {
  const [account, setAccount] = useState<Account | null | undefined>(undefined);
  useEffect(() => {
    currentUser().then(setAccount, () => setAccount(null));
  }, []);
  const loggedOut = useCallback(() => setAccount(null), []);
  if (account === undefined) return <p className="loading">Loading…</p>;
  if (account === null) {
    return (
      <>
        <AuthForm onAuthenticated={setAccount} />
        <Footer />
      </>
    );
  }
  return <Workspace account={account} onLoggedOut={loggedOut} />;
}
