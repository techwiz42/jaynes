"""The database: users, their login sessions and their saved conversations. SQLite, one file, mode 600.

Every read and write of a user's data takes that user's id and filters on it in the SQL itself, so there is no call that returns another user's rows. Calls are plain
blocking functions; the server runs them in a thread (`Store.run`).
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

SCHEMA_VERSION = 3
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
CREATE TABLE usage (
  day TEXT NOT NULL, user_id TEXT NOT NULL, tokens INTEGER NOT NULL DEFAULT 0, messages INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (day, user_id));
"""

# Version 1 also had a private "memory" (an index of conversations, uploaded documents and notes, searched by two tools). That feature is gone; its data goes with it.
MIGRATE_1_TO_2 = """
DROP TRIGGER IF EXISTS memory_ai;
DROP TRIGGER IF EXISTS memory_ad;
DROP TABLE IF EXISTS memory_fts;
DROP TABLE IF EXISTS memory;
"""

# Version 3 counts what each user spends per day (see limits.py).
MIGRATE_2_TO_3 = """
CREATE TABLE usage (
  day TEXT NOT NULL, user_id TEXT NOT NULL, tokens INTEGER NOT NULL DEFAULT 0, messages INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (day, user_id));
"""

DELETED_USER = "-"                      # what is left of a deleted user's spending: the day's total must not shrink, but nothing says whose it was


class StoreError(Exception):
    """A request the store refuses (a duplicate email, text that is too long). The message is safe to show the user."""


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
            elif version in (1, 2):
                if version == 1:
                    db.executescript(MIGRATE_1_TO_2)
                db.executescript(MIGRATE_2_TO_3)
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
        """The account and everything of its own: logins, conversations and messages."""
        with self._connect() as db:
            for day, tokens, messages in db.execute("SELECT day, tokens, messages FROM usage WHERE user_id = ?", (user_id,)).fetchall():
                db.execute("INSERT INTO usage (day, user_id, tokens, messages) VALUES (?, ?, ?, ?) ON CONFLICT(day, user_id) DO UPDATE SET tokens = tokens + excluded.tokens, "
                           "messages = messages + excluded.messages", (day, DELETED_USER, tokens, messages))
            db.execute("DELETE FROM usage WHERE user_id = ?", (user_id,))
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
        """Save a message. False when the conversation is not this user's. With `create`, a conversation that does not exist yet is made
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
        return True

    def delete_conversation(self, user_id: str, conversation_id: str) -> bool:
        with self._connect() as db:
            return db.execute("DELETE FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user_id)).rowcount > 0

    # ---- spending (see limits.py) ---------------------------------------------------------------------------------------------------------------------------------

    def add_usage(self, user_id: str, day: str, tokens: int, messages: int = 1) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO usage (day, user_id, tokens, messages) VALUES (?, ?, ?, ?) ON CONFLICT(day, user_id) DO UPDATE SET tokens = tokens + excluded.tokens, "
                       "messages = messages + excluded.messages", (day, user_id, max(0, int(tokens)), messages))

    def user_tokens(self, user_id: str, day: str) -> int:
        with self._connect() as db:
            row = db.execute("SELECT tokens FROM usage WHERE day = ? AND user_id = ?", (day, user_id)).fetchone()
        return int(row[0]) if row else 0

    def day_totals(self, day: str) -> Dict[str, int]:
        with self._connect() as db:
            tokens, messages, users = db.execute("SELECT COALESCE(SUM(tokens), 0), COALESCE(SUM(messages), 0), COUNT(DISTINCT user_id) FROM usage WHERE day = ?", (day,)).fetchone()
        return {"tokens": int(tokens), "messages": int(messages), "users": int(users)}

    def accounts_created_since(self, since: float) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM users WHERE created_at >= ?", (since,)).fetchone()[0])
