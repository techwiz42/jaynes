import os
import stat

import pytest

from bicameral.store import SCHEMA_VERSION, Store, StoreError


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "data" / "jaynes.db"))


def two_users(store):
    return store.create_user("a@example.com", "h"), store.create_user("b@example.com", "h")


def test_the_database_file_is_private_and_created_with_its_schema(tmp_path):
    path = tmp_path / "x" / "jaynes.db"
    Store(str(path))
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert Store(str(path)).count_users() == 0                       # opening it again is fine


def test_an_unknown_schema_version_is_refused(tmp_path):
    import sqlite3
    path = tmp_path / "jaynes.db"
    Store(str(path))
    with sqlite3.connect(path) as db:
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(StoreError):
        Store(str(path))


def test_emails_are_unique(store):
    store.create_user("a@example.com", "h")
    with pytest.raises(StoreError):
        store.create_user("a@example.com", "h")


def test_a_login_works_until_it_expires_or_ends(store):
    user = store.create_user("a@example.com", "h")
    store.create_login(user, "tokhash", 3600)
    assert store.user_for_login("tokhash")["email"] == "a@example.com"
    store.end_login("tokhash")
    assert store.user_for_login("tokhash") is None
    store.create_login(user, "old", -1)
    assert store.user_for_login("old") is None


def test_conversations_are_titled_by_the_first_message_and_kept_in_order(store):
    user, _ = two_users(store)
    conversation = store.create_conversation(user)
    assert store.add_message(user, conversation, "user", "What is the capital of Wisconsin?")
    assert store.add_message(user, conversation, "assistant", "Madison.")
    assert [(m["role"], m["text"]) for m in store.conversation_messages(user, conversation)] == [("user", "What is the capital of Wisconsin?"), ("assistant", "Madison.")]
    assert store.list_conversations(user)[0]["title"] == "What is the capital of Wisconsin?"


def test_a_user_cannot_read_write_or_delete_another_users_conversation(store):
    a, b = two_users(store)
    conversation = store.create_conversation(a)
    store.add_message(a, conversation, "user", "my secret plan")
    assert store.conversation_messages(b, conversation) is None
    assert store.add_message(b, conversation, "user", "intruder") is False
    assert store.delete_conversation(b, conversation) is False
    assert store.list_conversations(b) == []
    assert len(store.conversation_messages(a, conversation)) == 1


def test_deleting_an_account_removes_everything_it_owned_and_nothing_else(store):
    a, b = two_users(store)
    conversation = store.create_conversation(a)
    store.add_message(a, conversation, "user", "alpha marker")
    other = store.create_conversation(b)
    store.add_message(b, other, "user", "beta marker")
    store.create_login(a, "tok", 3600)
    store.delete_user(a)
    assert store.user_by_email("a@example.com") is None and store.user_for_login("tok") is None
    assert store.conversation_messages(a, conversation) is None
    assert [m["text"] for m in store.conversation_messages(b, other)] == ["beta marker"]


def test_a_version_1_database_loses_its_memory_tables_and_keeps_everything_else(tmp_path):
    import sqlite3
    path = tmp_path / "jaynes.db"
    store = Store(str(path))
    user = store.create_user("a@example.com", "h")
    conversation = store.create_conversation(user)
    store.add_message(user, conversation, "user", "hello")
    with sqlite3.connect(path) as db:                                    # what version 1 had besides
        db.executescript("""
            DROP TABLE usage;
            CREATE TABLE memory (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT, source_ref TEXT, title TEXT, text TEXT, created_at REAL);
            CREATE VIRTUAL TABLE memory_fts USING fts5(text, title, content='memory', content_rowid='id');
            CREATE TRIGGER memory_ai AFTER INSERT ON memory BEGIN INSERT INTO memory_fts(rowid, text, title) VALUES (new.id, new.text, new.title); END;
            INSERT INTO memory (user_id, kind, source_ref, title, text, created_at) VALUES ('u', 'note', 'r', 't', 'secret', 0);
            PRAGMA user_version = 1;""")
    reopened = Store(str(path))
    with sqlite3.connect(path) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master")}
        assert not any(name.startswith("memory") for name in tables)
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert [m["text"] for m in reopened.conversation_messages(user, conversation)] == ["hello"]
