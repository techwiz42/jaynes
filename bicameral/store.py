"""The database: users, their login sessions, their saved conversations, and their private memory. SQLite, one file, mode 600.

Every read and write of a user's data takes that user's id and filters on it in the SQL itself, so there is no call that returns another user's rows. Calls are plain
blocking functions; the server runs them in a thread (`Store.run`). Memory search is SQLite's FTS5 with BM25 ranking: nothing is sent to another service.
"""
from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

SCHEMA_VERSION = 1
CHUNK_CHARS = 1200                      # a long text is stored as pieces about this long, split at paragraph or sentence ends
MAX_MEMORY_CHARS = 200_000              # per document or note
TITLE_CHARS = 80

SCHEMA = """
CREATE TABLE users (
  id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE auth_sessions (
  token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, created_at REAL NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE conversations (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, title TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE INDEX conversations_user ON conversations(user_id, updated_at DESC);
CREATE TABLE messages (
  id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE, user_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant')), text TEXT NOT NULL, created_at REAL NOT NULL);
CREATE INDEX messages_conversation ON messages(conversation_id, id);
CREATE TABLE memory (
  id INTEGER PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('conversation', 'document', 'note')),
  source_ref TEXT NOT NULL,            -- the conversation id, the document id or the note id this piece came from
  title TEXT NOT NULL, text TEXT NOT NULL, created_at REAL NOT NULL);
CREATE INDEX memory_user_source ON memory(user_id, kind, source_ref);
CREATE VIRTUAL TABLE memory_fts USING fts5(text, title, content='memory', content_rowid='id', tokenize='porter unicode61');
CREATE TRIGGER memory_ai AFTER INSERT ON memory BEGIN
  INSERT INTO memory_fts(rowid, text, title) VALUES (new.id, new.text, new.title); END;
CREATE TRIGGER memory_ad AFTER DELETE ON memory BEGIN
  INSERT INTO memory_fts(memory_fts, rowid, text, title) VALUES ('delete', old.id, old.text, old.title); END;
"""


class StoreError(Exception):
    """A request the store refuses (a duplicate email, text that is too long). The message is safe to show the user."""


def chunk_text(text: str, size: int = CHUNK_CHARS) -> List[str]:
    """Pieces of about `size` characters, cut at a blank line, a line end or a sentence end where there is one."""
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    pieces: List[str] = []
    while len(text) > size:
        window = text[:size]
        cut = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind(". "), window.rfind("? "), window.rfind("! "))
        cut = cut + 1 if cut > size // 3 else size
        pieces.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        pieces.append(text)
    return [p for p in pieces if p]


def fts_query(query: str) -> str:
    """User words as a safe FTS5 query: each word quoted (so no operator or column filter in the text means anything), any of them may match."""
    words = re.findall(r"\w+", query.lower())[:24]
    return " OR ".join(f'"{w}"' for w in words)


class Store:
    def __init__(self, path: str) -> None:
        self.path = str(path)
        parent = Path(self.path).parent
        parent.mkdir(parents=True, exist_ok=True)
        fresh = not Path(self.path).exists()
        if fresh:
            fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)          # the file exists with its final permissions before anything is written to it
            os.close(fd)
        with self._connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                db.executescript(SCHEMA)
                db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            elif version != SCHEMA_VERSION:
                raise StoreError(f"the database has schema version {version}; this program expects {SCHEMA_VERSION}")
        os.chmod(self.path, 0o600)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("PRAGMA journal_mode = WAL")
            with db:                                                          # one transaction per call
                yield db

    async def run(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run one of the blocking methods off the event loop."""
        return await asyncio.to_thread(function, *args, **kwargs)

    # ---- users and login sessions -----------------------------------------------------------------------------------------------------------------------------

    def create_user(self, email: str, password_hash: str) -> str:
        user_id = uuid.uuid4().hex
        try:
            with self._connect() as db:
                db.execute("INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)", (user_id, email, password_hash, time.time()))
        except sqlite3.IntegrityError:
            raise StoreError("that email is already registered")
        return user_id

    def user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        with self._connect() as db:
            row = db.execute("SELECT id, email, password_hash FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None

    def count_users(self) -> int:
        with self._connect() as db:
            return db.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def create_login(self, user_id: str, token_hash: str, lifetime_seconds: float) -> None:
        now = time.time()
        with self._connect() as db:
            db.execute("DELETE FROM auth_sessions WHERE expires_at < ?", (now,))
            db.execute("INSERT INTO auth_sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)", (token_hash, user_id, now, now + lifetime_seconds))

    def user_for_login(self, token_hash: str) -> Optional[Dict[str, Any]]:
        with self._connect() as db:
            row = db.execute("SELECT u.id, u.email FROM auth_sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires_at > ?",
                             (token_hash, time.time())).fetchone()
        return dict(row) if row else None

    def end_login(self, token_hash: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))

    def delete_user(self, user_id: str) -> None:
        """The account and everything of its own: logins, conversations, messages and memory."""
        with self._connect() as db:
            db.execute("DELETE FROM memory WHERE user_id = ?", (user_id,))          # the delete trigger keeps the search index in step
            db.execute("DELETE FROM users WHERE id = ?", (user_id,))

    # ---- conversations ------------------------------------------------------------------------------------------------------------------------------------------

    def create_conversation(self, user_id: str, title: str = "New conversation") -> str:
        conversation_id, now = uuid.uuid4().hex, time.time()
        with self._connect() as db:
            db.execute("INSERT INTO conversations (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)", (conversation_id, user_id, title[:TITLE_CHARS], now, now))
        return conversation_id

    def list_conversations(self, user_id: str) -> List[Dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT id, title, updated_at FROM conversations WHERE user_id = ? ORDER BY updated_at DESC LIMIT 200", (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def conversation_messages(self, user_id: str, conversation_id: str) -> Optional[List[Dict[str, Any]]]:
        """The messages, oldest first; None when the conversation is not this user's (or does not exist)."""
        with self._connect() as db:
            if db.execute("SELECT 1 FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user_id)).fetchone() is None:
                return None
            rows = db.execute("SELECT role, text, created_at FROM messages WHERE conversation_id = ? AND user_id = ? ORDER BY id", (conversation_id, user_id)).fetchall()
        return [dict(r) for r in rows]

    def add_message(self, user_id: str, conversation_id: str, role: str, text: str, create: bool = False) -> bool:
        """Save a message and index it in the user's memory. False when the conversation is not this user's. With `create`, a conversation that does not exist yet is made
        for this user first (a new chat is saved when its first message is, not when the page opens); one that exists under another user is still refused."""
        now = time.time()
        with self._connect() as db:
            if create:
                db.execute("INSERT OR IGNORE INTO conversations (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                           (conversation_id, user_id, "New conversation", now, now))
            row = db.execute("SELECT title, (SELECT COUNT(*) FROM messages WHERE conversation_id = c.id) AS n FROM conversations c WHERE id = ? AND user_id = ?",
                             (conversation_id, user_id)).fetchone()
            if row is None:
                return False
            db.execute("INSERT INTO messages (conversation_id, user_id, role, text, created_at) VALUES (?, ?, ?, ?, ?)", (conversation_id, user_id, role, text, now))
            title = row["title"]
            if row["n"] == 0 and role == "user":
                title = " ".join(text.split())[:TITLE_CHARS] or title
            db.execute("UPDATE conversations SET updated_at = ?, title = ? WHERE id = ?", (now, title, conversation_id))
            for piece in chunk_text(text):
                db.execute("INSERT INTO memory (user_id, kind, source_ref, title, text, created_at) VALUES (?, 'conversation', ?, ?, ?, ?)",
                           (user_id, conversation_id, f"{'You' if role == 'user' else 'Jaynes'} in “{title}”", piece, now))
        return True

    def delete_conversation(self, user_id: str, conversation_id: str) -> bool:
        with self._connect() as db:
            db.execute("DELETE FROM memory WHERE user_id = ? AND kind = 'conversation' AND source_ref = ?", (user_id, conversation_id))
            return db.execute("DELETE FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user_id)).rowcount > 0

    # ---- memory: documents and notes (conversations are added by add_message) -------------------------------------------------------------------------------------

    def add_memory(self, user_id: str, kind: str, title: str, text: str) -> str:
        """Store a document or a note, chunked and searchable. Returns its id."""
        if kind not in ("document", "note"):
            raise StoreError("only documents and notes are added this way")
        text = text.strip()
        if not text:
            raise StoreError("there is no text to store")
        if len(text) > MAX_MEMORY_CHARS:
            raise StoreError(f"too long: at most {MAX_MEMORY_CHARS:,} characters")
        source_ref, now = uuid.uuid4().hex, time.time()
        title = " ".join(title.split())[:TITLE_CHARS] or ("Untitled note" if kind == "note" else "Untitled document")
        with self._connect() as db:
            for piece in chunk_text(text):
                db.execute("INSERT INTO memory (user_id, kind, source_ref, title, text, created_at) VALUES (?, ?, ?, ?, ?, ?)", (user_id, kind, source_ref, title, piece, now))
        return source_ref

    def list_memory(self, user_id: str, kind: str) -> List[Dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT source_ref AS id, title, MIN(created_at) AS created_at, COUNT(*) AS pieces, SUM(LENGTH(text)) AS chars FROM memory "
                              "WHERE user_id = ? AND kind = ? GROUP BY source_ref, title ORDER BY created_at DESC", (user_id, kind)).fetchall()
        return [dict(r) for r in rows]

    def delete_memory(self, user_id: str, kind: str, source_ref: str) -> bool:
        with self._connect() as db:
            return db.execute("DELETE FROM memory WHERE user_id = ? AND kind = ? AND source_ref = ?", (user_id, kind, source_ref)).rowcount > 0

    def search_memory(self, user_id: str, query: str, limit: int = 5, kinds: Tuple[str, ...] = ("conversation", "document", "note"),
                      exclude_conversation: Optional[str] = None) -> List[Dict[str, Any]]:
        """The best matches for `query` among this user's memory, best first. Never anyone else's."""
        match = fts_query(query)
        if not match:
            return []
        marks = ",".join("?" for _ in kinds)
        sql = (f"SELECT m.kind, m.source_ref, m.title, m.text, m.created_at FROM memory_fts f JOIN memory m ON m.id = f.rowid "
               f"WHERE memory_fts MATCH ? AND m.user_id = ? AND m.kind IN ({marks})")
        params: List[Any] = [match, user_id, *kinds]
        if exclude_conversation:
            sql += " AND NOT (m.kind = 'conversation' AND m.source_ref = ?)"
            params.append(exclude_conversation)
        sql += " ORDER BY bm25(memory_fts, 1.0, 2.0) LIMIT ?"
        params.append(max(1, min(limit, 20)))
        with self._connect() as db:
            rows = db.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
