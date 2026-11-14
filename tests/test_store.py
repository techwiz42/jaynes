import os
import stat

import pytest

from bicameral.store import SCHEMA_VERSION, Store, StoreError, chunk_text, fts_query


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


def test_memory_search_finds_what_the_user_said_and_ranks_the_better_match_first(store):
    a, _ = two_users(store)
    store.add_memory(a, "note", "Dentist", "The dentist appointment is on Tuesday at nine.")
    store.add_memory(a, "note", "Groceries", "Buy milk and eggs.")
    hits = store.search_memory(a, "when is the dentist appointment")
    assert hits and hits[0]["title"] == "Dentist" and "Tuesday" in hits[0]["text"]
    assert all("milk" not in h["text"] for h in hits[:1])


def test_memory_search_never_returns_another_users_memory(store):
    a, b = two_users(store)
    store.add_memory(a, "note", "Secret", "The vault code is 4417.")
    store.add_memory(b, "note", "Mine", "My cat is called Pixel.")
    assert store.search_memory(b, "vault code") == []
    assert store.search_memory(a, "vault code")[0]["title"] == "Secret"
    conversation = store.create_conversation(a)
    store.add_message(a, conversation, "user", "remember the vault code is 4417")
    assert store.search_memory(b, "vault code 4417") == []


def test_conversations_are_searchable_and_the_current_one_can_be_left_out(store):
    a, _ = two_users(store)
    first, second = store.create_conversation(a), store.create_conversation(a)
    store.add_message(a, first, "user", "I am planning a trip to Lisbon in May")
    store.add_message(a, second, "user", "Lisbon hotels please")
    assert {h["source_ref"] for h in store.search_memory(a, "Lisbon")} == {first, second}
    assert {h["source_ref"] for h in store.search_memory(a, "Lisbon", exclude_conversation=second)} == {first}


def test_deleting_removes_it_from_search_too(store):
    a, _ = two_users(store)
    note = store.add_memory(a, "note", "Pin", "The locker combination is 12-34-56.")
    assert store.search_memory(a, "locker combination")
    assert store.delete_memory(a, "note", note) is True
    assert store.search_memory(a, "locker combination") == []
    conversation = store.create_conversation(a)
    store.add_message(a, conversation, "user", "my locker number is 88")
    assert store.search_memory(a, "locker")
    store.delete_conversation(a, conversation)
    assert store.search_memory(a, "locker") == []


def test_a_user_cannot_delete_another_users_note_or_document(store):
    a, b = two_users(store)
    note = store.add_memory(a, "note", "n", "text of the note")
    assert store.delete_memory(b, "note", note) is False
    assert store.delete_memory(a, "document", note) is False          # right id, wrong kind
    assert store.list_memory(a, "note")[0]["id"] == note


def test_deleting_an_account_removes_everything_it_owned_and_nothing_else(store):
    a, b = two_users(store)
    conversation = store.create_conversation(a)
    store.add_message(a, conversation, "user", "alpha marker")
    store.add_memory(a, "note", "n", "alpha note")
    store.add_memory(b, "note", "n", "alpha note of b")
    store.create_login(a, "tok", 3600)
    store.delete_user(a)
    assert store.user_by_email("a@example.com") is None and store.user_for_login("tok") is None
    assert store.search_memory(a, "alpha") == []
    assert [h["text"] for h in store.search_memory(b, "alpha")] == ["alpha note of b"]


def test_search_text_cannot_inject_fts_syntax(store):
    a, _ = two_users(store)
    store.add_memory(a, "note", "n", "plain words here")
    for query in ('" OR 1=1 --', "title:secret*", "NEAR(a b)", "(((", "*", "AND OR NOT", ""):
        store.search_memory(a, query)                                    # must not raise
    assert fts_query('x" OR "y') == '"x" OR "or" OR "y"'


def test_long_text_is_cut_into_pieces_at_natural_places_and_nothing_is_lost():
    text = ("Sentence number one is here. " * 200).strip()
    pieces = chunk_text(text, 500)
    assert len(pieces) > 5 and all(len(p) <= 500 for p in pieces)
    assert " ".join(" ".join(pieces).split()) == " ".join(text.split())
    assert chunk_text("short") == ["short"] and chunk_text("   ") == []


def test_too_much_text_or_nothing_is_refused(store):
    a, _ = two_users(store)
    with pytest.raises(StoreError):
        store.add_memory(a, "note", "n", "x" * 200_001)
    with pytest.raises(StoreError):
        store.add_memory(a, "note", "n", "   ")
    with pytest.raises(StoreError):
        store.add_memory(a, "conversation", "n", "text")
