import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { addNote, deleteAccount, deleteDocument, deleteNote, listDocuments, listNotes, uploadDocument, type MemoryEntry } from "./api";

function Entries({ entries, onDelete, empty }: { entries: MemoryEntry[]; onDelete: (id: string) => void; empty: string }) {
  if (entries.length === 0) return <p className="empty">{empty}</p>;
  return (
    <ul className="entries">
      {entries.map((entry) => (
        <li key={entry.id}>
          <span className="title">{entry.title}</span>
          <span className="size">{entry.chars.toLocaleString()} characters</span>
          <button type="button" aria-label={`Delete ${entry.title}`} onClick={() => onDelete(entry.id)}>
            Delete
          </button>
        </li>
      ))}
    </ul>
  );
}

/** What Jaynes can remember for this user: documents they upload and notes they write (conversations are remembered on their own). Everything here is the user's alone. */
export function MemoryPanel({ email, onClose, onAccountDeleted }: { email: string; onClose: () => void; onAccountDeleted: () => void }) {
  const [documents, setDocuments] = useState<MemoryEntry[]>([]);
  const [notes, setNotes] = useState<MemoryEntry[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [password, setPassword] = useState("");
  const file = useRef<HTMLInputElement>(null);

  const reload = useCallback(async (): Promise<void> => {
    try {
      const [docs, ns] = await Promise.all([listDocuments(), listNotes()]);
      setDocuments(docs);
      setNotes(ns);
    } catch (error) {
      setProblem((error as Error).message);
    }
  }, []);
  useEffect(() => {
    void reload();
  }, [reload]);

  const attempt = async (action: () => Promise<unknown>): Promise<boolean> => {
    setProblem(null);
    setBusy(true);
    try {
      await action();
      await reload();
      return true;
    } catch (error) {
      setProblem((error as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const upload = async (): Promise<void> => {
    const chosen = file.current?.files?.[0];
    if (!chosen) return;
    await attempt(() => uploadDocument(chosen));
    if (file.current) file.current.value = "";
  };

  const saveNote = async (event: FormEvent): Promise<void> => {
    event.preventDefault();
    if (await attempt(() => addNote(title, text))) {
      setTitle("");
      setText("");
    }
  };

  const removeAccount = async (event: FormEvent): Promise<void> => {
    event.preventDefault();
    setProblem(null);
    try {
      await deleteAccount(password);
      onAccountDeleted();
    } catch (error) {
      setProblem((error as Error).message);
    }
  };

  return (
    <section className="memory" aria-label="Memory">
      <div className="memory-head">
        <h2>Memory</h2>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
      <p className="hint">
        Jaynes remembers your conversations, the documents you add and the notes you keep, and looks in them when you ask about something from before. Only you can see them. Searching your memory
        counts as reading private data, so web search and fetch are off for the rest of that conversation.
      </p>
      {problem && (
        <p className="problem" role="alert">
          {problem}
        </p>
      )}

      <h3>Documents</h3>
      <p className="hint">PDF, text, Markdown, CSV or JSON.</p>
      <div className="upload">
        <input ref={file} type="file" accept=".pdf,.txt,.md,.markdown,.csv,.json,.log,.rst" aria-label="Choose a document" onChange={() => void upload()} disabled={busy} />
      </div>
      <Entries entries={documents} empty="No documents." onDelete={(id) => void attempt(() => deleteDocument(id))} />

      <h3>Notes</h3>
      <form onSubmit={(event) => void saveNote(event)}>
        <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Title (optional)" aria-label="Note title" maxLength={80} />
        <textarea value={text} onChange={(event) => setText(event.target.value)} placeholder="Something for Jaynes to remember" aria-label="Note" rows={3} maxLength={4000} />
        <button type="submit" disabled={busy || !text.trim()}>
          Save note
        </button>
      </form>
      <Entries entries={notes} empty="No notes. You can also tell Jaynes “remember that…”." onDelete={(id) => void attempt(() => deleteNote(id))} />

      <h3>Account</h3>
      <p className="hint">Signed in as {email}.</p>
      {confirming ? (
        <form onSubmit={(event) => void removeAccount(event)}>
          <p className="hint">This deletes your account, every saved conversation, document and note. It cannot be undone. Enter your password to confirm.</p>
          <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" aria-label="Password" required />
          <button type="submit" className="danger" disabled={!password}>
            Delete my account and all my data
          </button>
          <button type="button" onClick={() => { setConfirming(false); setPassword(""); }}>
            Cancel
          </button>
        </form>
      ) : (
        <button type="button" className="danger" onClick={() => setConfirming(true)}>
          Delete my account…
        </button>
      )}
    </section>
  );
}
