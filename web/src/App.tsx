import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ApiError, currentUser, deleteConversation, listConversations, logout, readAttachment, type Account, type Attachment, type ConversationSummary } from "./api";
import { AccountPanel } from "./Account";
import { AuthForm } from "./Auth";
import { BrainIcon } from "./BrainIcon";
import { Footer } from "./Footer";
import { MarkdownText, plainText } from "./Markdown";
import { Sidebar } from "./Sidebar";
import { downloadText, transcriptFileName, transcriptMarkdown } from "./transcript";
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

const MAX_FILES = 5;

interface PendingFile {
  key: number;
  name: string;
  status: "reading" | "ready" | "failed";
  text: string;
  note: string;
}

function Composer({ onSend, disabled, working }: { onSend: (text: string, attachments: Attachment[]) => Promise<void>; disabled: boolean; working: boolean }) {
  const [text, setText] = useState("");
  const [files, setFiles] = useState<PendingFile[]>([]);
  const [dragging, setDragging] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const nextKey = useRef(0);
  const reading = files.some((file) => file.status === "reading");
  const ready = files.filter((file) => file.status === "ready");

  const update = (key: number, change: Partial<PendingFile>): void => setFiles((all) => all.map((file) => (file.key === key ? { ...file, ...change } : file)));
  const attach = (chosen: File[]): void => {
    const room = Math.max(0, MAX_FILES - files.length);
    const taken = chosen.slice(0, room);
    const added: PendingFile[] = taken.map((file) => ({ key: nextKey.current++, name: file.name, status: "reading", text: "", note: "" }));
    const skipped = chosen.length - taken.length;
    setFiles((all) => [...all, ...added, ...(skipped > 0 ? [{ key: nextKey.current++, name: `${skipped} more file${skipped > 1 ? "s" : ""}`, status: "failed" as const, text: "", note: `at most ${MAX_FILES} files in one message` }] : [])]);
    taken.forEach((file, index) => {
      const key = added[index]?.key ?? -1;
      readAttachment(file).then(
        (read) => update(key, { status: "ready", text: read.text, note: read.truncated ? `only the first ${read.text.length.toLocaleString()} of ${read.chars.toLocaleString()} characters are sent` : "" }),
        (error: unknown) => update(key, { status: "failed", note: (error as Error).message }),
      );
    });
  };

  // Files dropped anywhere on the page are attached (the browser would otherwise open the file in place of the page).
  const attachRef = useRef(attach);
  attachRef.current = attach;
  useEffect(() => {
    const carriesFiles = (event: DragEvent): boolean => Array.from(event.dataTransfer?.types ?? []).includes("Files");
    const over = (event: DragEvent): void => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      setDragging(true);
    };
    const leave = (event: DragEvent): void => {
      if (event.relatedTarget === null) setDragging(false);
    };
    const drop = (event: DragEvent): void => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      setDragging(false);
      if (!disabled) attachRef.current(Array.from(event.dataTransfer?.files ?? []));
    };
    window.addEventListener("dragover", over);
    window.addEventListener("dragleave", leave);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragover", over);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("drop", drop);
    };
  }, [disabled]);

  const canSend = !disabled && !reading && (text.trim() !== "" || ready.length > 0);
  const send = (): void => {
    if (!canSend) return;
    const attachments = ready.map((file) => ({ name: file.name, text: file.text }));
    const trimmed = text.trim();
    setText("");
    setFiles([]);
    void onSend(trimmed, attachments);
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
    <form className={dragging ? "composer dragging" : "composer"} onSubmit={submit}>
      <div className="stack">
        {files.length > 0 && (
          <ul className="attachments" aria-label="Attached files">
            {files.map((file) => (
              <li key={file.key} className={file.status}>
                <span className="file-name">{file.name}</span>
                <span className="file-note">{file.status === "reading" ? "reading…" : file.note}</span>
                <button type="button" aria-label={`Remove ${file.name}`} onClick={() => setFiles((all) => all.filter((other) => other.key !== file.key))}>
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}
        {ready.length > 0 && <p className="attach-hint">With a file attached, web search and fetch are off for the rest of this conversation.</p>}
        <div className="field">
          <textarea
            className={working ? "working" : undefined}
            value={text}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder={dragging ? "Drop files to attach them" : "Message (Enter to send, Shift+Enter for a new line). Drop or attach PDF, Word, Excel and text files."}
            aria-label="Message"
            rows={3}
            maxLength={8000}
            disabled={disabled}
            autoFocus
          />
          {working && <Typing />}
        </div>
      </div>
      <input
        ref={picker}
        type="file"
        multiple
        hidden
        aria-label="Attach files"
        onChange={(event) => {
          attach(Array.from(event.target.files ?? []));
          event.target.value = "";
        }}
      />
      <button type="submit" className="send" disabled={!canSend} title="Send (Enter)" aria-label="Send">
        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" fill="currentColor">
          <path d="M2.4 20.6 22 12 2.4 3.4 2.4 10.1 16 12 2.4 13.9z" />
        </svg>
      </button>
      <button type="button" className="attach" disabled={disabled || files.length >= MAX_FILES} onClick={() => picker.current?.click()} title="Attach files" aria-label="Choose files to attach">
        📎
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
  // After a restart the server has lost this chat's session. A saved conversation is opened again (it comes back with all its messages); one that was never saved has nothing to bring back.
  const savedRef = useRef<{ started: string | null; saved: boolean }>({ started: null, saved: false });
  const sessionGone = useCallback(() => (savedRef.current.saved && savedRef.current.started ? onOpen(savedRef.current.started) : onNew()), [onOpen, onNew]);
  const { state, sessionId, conversationId: started, connected, problem, send } = useChat(conversationId, onLoggedOut, onActivity, sessionGone);
  savedRef.current = { started, saved: conversations.some((conversation) => conversation.id === started) };
  const [accountOpen, setAccountOpen] = useState(false);
  const title = conversations.find((conversation) => conversation.id === started)?.title ?? "Jaynes conversation";
  useEffect(() => {
    if (started) onConversation(started);
  }, [started, onConversation]);
  return (
    <div className="app">
      <header>
        <h1 className="brand">
          <BrainIcon size={26} />
          Jaynes - a bicameral chatbot
        </h1>
        <span className={`status ${connected ? "on" : "off"}`}>{!sessionId ? "starting…" : connected ? "connected" : "reconnecting…"}</span>
        {isStreaming(state) && <span className="status on">replying…</span>}
        <span className="spacer" />
        <span className="account">{account.email}</span>
        {state.items.some((item) => item.kind === "user") && (
          <>
            <button type="button" className="quiet" onClick={() => downloadText(transcriptFileName(title), transcriptMarkdown(state.items, title))}>
              Download chat (.md)
            </button>
            <button
              type="button"
              className="quiet"
              onClick={() => void import("./transcriptHtml").then(({ transcriptHtml }) => downloadText(transcriptFileName(title, "html"), transcriptHtml(state.items, title), "text/html"))}
            >
              Download chat (.html)
            </button>
          </>
        )}
        <button type="button" className="quiet" onClick={() => setAccountOpen(!accountOpen)} aria-expanded={accountOpen}>
          Account
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
        {accountOpen ? <AccountPanel email={account.email} onClose={() => setAccountOpen(false)} onAccountDeleted={onLoggedOut} /> : <ObserverPanel state={state} />}
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
