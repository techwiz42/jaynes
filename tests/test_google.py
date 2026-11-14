import base64
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from bicameral.google_auth import SCOPES, GoogleAuth, GoogleAuthError, build_auth_url, exchange_code, pkce_pair
from bicameral.tools.audit import AuditLog
from bicameral.tools.base import ToolRegistry, TurnContext
from bicameral.tools.drive import DriveRead, escape
from bicameral.tools.gmail import GmailRead

CLIENT_SECRET, REFRESH, ACCESS = "client-secret-XYZ", "refresh-token-ABC", "ya29.access-token-123"


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


class Google:
    """A scripted Google: a token endpoint, and an API answered by `api(request)`. Records every request."""

    def __init__(self, api, token_status=200, expires_in=3600):
        self.api, self.token_status, self.expires_in = api, token_status, expires_in
        self.requests, self.token_calls = [], 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "oauth2.googleapis.com":
            self.token_calls += 1
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": ACCESS, "expires_in": self.expires_in})
        return self.api(request)

    @property
    def transport(self):
        return httpx.MockTransport(self)

    @property
    def api_requests(self):
        return [r for r in self.requests if r.url.host != "oauth2.googleapis.com"]


def auth_for(google, clock=None):
    return GoogleAuth("client-id", CLIENT_SECRET, REFRESH, transport=google.transport, **({"clock": clock} if clock else {}))


# ---- sign-in -----------------------------------------------------------------------------------------------------------------------------------------------------

def test_pkce_challenge_is_the_s256_of_the_verifier():
    verifier, challenge = pkce_pair()
    assert challenge == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=") and 43 <= len(verifier) <= 128
    assert pkce_pair()[0] != verifier


def test_the_sign_in_url_asks_only_for_read_only_scopes_and_an_offline_token():
    url = build_auth_url("cid", "http://127.0.0.1:8123/", "state123", "challenge")
    q = parse_qs(urlsplit(url).query)
    assert set(q["scope"][0].split()) == set(SCOPES) and all(s.endswith(".readonly") for s in SCOPES)
    assert q["access_type"] == ["offline"] and q["prompt"] == ["consent"] and q["code_challenge_method"] == ["S256"] and q["state"] == ["state123"]
    assert urlsplit(url).netloc == "accounts.google.com"


async def test_the_code_exchange_posts_the_verifier_and_reports_failure_without_details():
    seen = {}

    def handler(request):
        seen.update(dict(parse_qs(request.content.decode())))
        return httpx.Response(200, json={"refresh_token": "r", "access_token": "a"})

    tokens = await exchange_code("cid", "sec", "the-code", "http://127.0.0.1:1/", "verifier", transport=httpx.MockTransport(handler))
    assert tokens["refresh_token"] == "r" and seen["grant_type"] == ["authorization_code"] and seen["code_verifier"] == ["verifier"] and seen["code"] == ["the-code"]
    with pytest.raises(GoogleAuthError) as error:
        await exchange_code("cid", "sec", "bad", "http://x/", "v", transport=httpx.MockTransport(lambda r: httpx.Response(400, text="secret detail sec")))
    assert "sec" not in str(error.value).replace("sign-in", "")


async def test_the_access_token_is_renewed_when_needed_and_cached_until_just_before_it_expires():
    now = [0.0]
    google = Google(lambda r: httpx.Response(200, json={}), expires_in=3600)
    auth = auth_for(google, clock=lambda: now[0])
    assert await auth.access_token() == ACCESS and await auth.access_token() == ACCESS and google.token_calls == 1
    now[0] = 3600 - 61
    await auth.access_token()
    assert google.token_calls == 1
    now[0] = 3600 - 59                                                                       # within a minute of expiry: renewed
    await auth.access_token()
    assert google.token_calls == 2


async def test_a_revoked_authorization_gives_a_clear_error_and_no_token_in_it():
    auth = auth_for(Google(lambda r: httpx.Response(200), token_status=400))
    with pytest.raises(GoogleAuthError) as error:
        await auth.access_token()
    assert "set up again" in str(error.value) and REFRESH not in str(error.value) and CLIENT_SECRET not in str(error.value)


async def test_secrets_lists_everything_that_must_be_scrubbed_including_the_current_access_token():
    google = Google(lambda r: httpx.Response(200, json={}))
    auth = auth_for(google)
    assert set(auth.secrets()) == {CLIENT_SECRET, REFRESH}
    await auth.access_token()
    assert set(auth.secrets()) == {CLIENT_SECRET, REFRESH, ACCESS}


# ---- Gmail --------------------------------------------------------------------------------------------------------------------------------------------------------

def message(mid="abc123", sender="Alice <alice@example.org>", subject="Hello", labels=("INBOX",), plain=None, html=None, attachments=()):
    parts = []
    if plain is not None:
        parts.append({"mimeType": "text/plain", "body": {"data": b64(plain)}})
    if html is not None:
        parts.append({"mimeType": "text/html", "body": {"data": b64(html)}})
    parts += [{"mimeType": "application/pdf", "filename": name, "body": {"attachmentId": "x"}} for name in attachments]
    return {"id": mid, "labelIds": list(labels), "payload": {"mimeType": "multipart/alternative", "headers": [
        {"name": "From", "value": sender}, {"name": "To", "value": "me@example.com"}, {"name": "Subject", "value": subject}, {"name": "Date", "value": "Mon, 9 Nov 2026 10:00:00 -0800"}], "parts": parts}}


def gmail_api(messages, labels=None):
    labels = labels or [{"id": "INBOX", "name": "INBOX"}, {"id": "Label_7", "name": "Finance"}]

    def api(request):
        path = request.url.path
        if path.endswith("/labels"):
            return httpx.Response(200, json={"labels": labels})
        if path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": m["id"]} for m in messages]})
        mid = path.rsplit("/", 1)[1]
        found = next((m for m in messages if m["id"] == mid), None)
        return httpx.Response(200, json=found) if found else httpx.Response(404, json={})

    return api


def gmail(messages, **kwargs):
    google = Google(gmail_api(messages, kwargs.pop("labels", None)))
    tool = GmailRead(auth_for(google), transport=google.transport, **kwargs)
    return ToolRegistry([tool], secrets=[CLIENT_SECRET, REFRESH]), google


async def test_search_lists_id_date_sender_and_subject_and_brackets_the_query():
    reg, google = gmail([message("a1", subject="Invoice 42"), message("b2", sender="Bob <bob@example.org>", subject="Lunch")])
    result = await reg.run("gmail_read", {"operation": "search", "query": "invoice OR lunch"})
    assert result.ok and result.content.splitlines()[0].startswith("a1 | Mon, 9 Nov 2026") and "Invoice 42" in result.content and "Bob" in result.content
    listing = [r for r in google.api_requests if r.url.path.endswith("/messages")][0]
    assert listing.url.params["q"] == "(invoice OR lunch)"


async def test_excluded_labels_and_senders_are_in_the_query_and_also_filtered_from_the_results_and_refused_by_id():
    msgs = [message("0a1"), message("0f1", labels=("Label_7",), subject="Bank statement"), message("0c1", sender="Payroll <payroll@corp.example>")]
    reg, google = gmail(msgs, exclude_labels=["Finance"], exclude_senders=["payroll@"])
    result = await reg.run("gmail_read", {"operation": "search", "query": "statement OR salary"})
    assert "0a1" in result.content and "0f1" not in result.content and "0c1" not in result.content and result.meta["hidden"] == 2
    q = [r for r in google.api_requests if r.url.path.endswith("/messages")][0].url.params["q"]
    assert q == '(statement OR salary) -label:"Finance" -from:"payroll@"'
    for hidden in ("0f1", "0c1"):
        refused = await reg.run("gmail_read", {"operation": "read", "message_id": hidden})
        assert not refused.ok and "excluded" in refused.error
    assert (await reg.run("gmail_read", {"operation": "read", "message_id": "0a1"})).ok


async def test_the_label_list_hides_excluded_labels():
    reg, _ = gmail([], exclude_labels=["finance"])
    assert (await reg.run("gmail_read", {"operation": "list_labels"})).content == "INBOX"


async def test_read_returns_headers_and_text_marks_attachments_unread_and_truncates():
    body = "Dear user, " + "word " * 4000
    reg, _ = gmail([message("0b1", plain=body, attachments=["statement.pdf"])])
    result = await reg.run("gmail_read", {"operation": "read", "message_id": "0b1", "max_chars": 600})
    assert result.ok and result.content.startswith("From: Alice <alice@example.org>\nTo: me@example.com\nDate: Mon, 9 Nov 2026") and "Subject: Hello" in result.content
    assert "Attachments (not read): statement.pdf" in result.content and result.truncated and "[message truncated]" in result.content


async def test_html_only_mail_is_read_as_text_without_scripts():
    reg, _ = gmail([message("0d1", html="<html><body><script>steal()</script><p>Hello <b>there</b></p></body></html>")])
    result = await reg.run("gmail_read", {"operation": "read", "message_id": "0d1"})
    assert "Hello there" in result.content and "steal" not in result.content


async def test_only_get_requests_are_sent_and_the_page_summary_has_no_mail_content():
    reg, google = gmail([message("0b1", subject="Secret plans", plain="the launch code is 0000")])
    results = [await reg.run("gmail_read", {"operation": op, "query": "plans", "message_id": "0b1"}) for op in ("search", "read", "list_labels")]
    assert {r.method for r in google.api_requests} == {"GET"}
    assert not any("Secret plans" in r.summary or "launch code" in r.summary for r in results)


async def test_bad_arguments_and_errors_are_reported_without_leaking_tokens():
    reg, _ = gmail([])
    assert "query" in (await reg.run("gmail_read", {"operation": "search"})).error
    assert "message_id" in (await reg.run("gmail_read", {"operation": "read"})).error
    assert not (await reg.run("gmail_read", {"operation": "read", "message_id": "not hex!"})).ok
    assert not (await reg.run("gmail_read", {"operation": "send"})).ok
    assert "no such message" in (await reg.run("gmail_read", {"operation": "read", "message_id": "dead"})).error
    google = Google(lambda r: httpx.Response(403, json={"error": f"{ACCESS} {REFRESH}"}))
    reg = ToolRegistry([GmailRead(auth_for(google), transport=google.transport)], secrets=[CLIENT_SECRET, REFRESH])
    result = await reg.run("gmail_read", {"operation": "search", "query": "x"})
    assert not result.ok and "denied" in result.error and ACCESS not in result.error and REFRESH not in result.error
    revoked = Google(lambda r: httpx.Response(200), token_status=400)
    reg = ToolRegistry([GmailRead(auth_for(revoked), transport=revoked.transport)])
    assert "set up again" in (await reg.run("gmail_read", {"operation": "search", "query": "x"})).error


async def test_reading_mail_marks_the_conversation_private_and_blocks_outgoing_tools_after():
    from .test_taint import Outgoing
    reg, _ = gmail([message("0b1", plain="hi")])
    reg._tools["web"] = Outgoing()
    ctx = TurnContext("s", "t1")
    assert (await reg.run("web", {}, ctx)).ok
    read = await reg.run("gmail_read", {"operation": "read", "message_id": "0b1"}, ctx)
    assert read.private and ctx.taint.tools == ["gmail_read"] and not (await reg.run("web", {}, ctx)).ok


async def test_the_access_token_never_reaches_a_result_or_the_audit_log(tmp_path):
    google = Google(gmail_api([message("0b1", plain=f"the token is {ACCESS}")]))
    auth = auth_for(google)
    reg = ToolRegistry([GmailRead(auth, transport=google.transport)], audit=AuditLog(str(tmp_path / "a.jsonl")), secrets=[CLIENT_SECRET, REFRESH])
    reg.add_secret_source(auth)
    result = await reg.run("gmail_read", {"operation": "read", "message_id": "0b1"}, TurnContext("s", "t1"), "c1")
    assert ACCESS not in result.content and "[redacted]" in result.content
    audit = (tmp_path / "a.jsonl").read_text()
    assert ACCESS not in audit and "the token is" not in audit and '"message_id": "0b1"' in audit


# ---- Drive --------------------------------------------------------------------------------------------------------------------------------------------------------

DOC = "application/vnd.google-apps.document"


def drive_api(files, contents=None):
    contents = contents or {}

    def api(request):
        path = request.url.path
        if path == "/drive/v3/files":
            return httpx.Response(200, json={"files": files})
        fid = path.split("/")[4]
        info = next((f for f in files if f["id"] == fid), None)
        if info is None:
            return httpx.Response(404, json={})
        if path.endswith("/export"):
            return httpx.Response(200, text=contents.get(fid, ""), headers={"content-type": "text/plain"})
        if request.url.params.get("alt") == "media":
            return httpx.Response(200, text=contents.get(fid, ""))
        return httpx.Response(200, json=info)

    return api


def drive(files, contents=None, **kwargs):
    google = Google(drive_api(files, contents))
    return ToolRegistry([DriveRead(auth_for(google), transport=google.transport, **kwargs)], secrets=[CLIENT_SECRET, REFRESH]), google


def f(fid, name, mime=DOC, **extra):
    return {"id": fid, "name": name, "mimeType": mime, "modifiedTime": "2026-11-01T10:00:00Z", **extra}


async def test_search_builds_a_safe_query_and_lists_files():
    reg, google = drive([f("doc1", "Budget 2027"), f("sheet1", "Numbers", "application/vnd.google-apps.spreadsheet")])
    result = await reg.run("drive_read", {"operation": "search", "text": "O'Brien's plan", "name": "budget", "kind": "document", "folder_id": "root"})
    assert result.ok and "doc1 | google-document | 2026-11-01 | Budget 2027" in result.content
    q = [r for r in google.api_requests if r.url.path == "/drive/v3/files"][0].url.params["q"]
    assert q == ("trashed = false and fullText contains 'O\\'Brien\\'s plan' and name contains 'budget' and mimeType = 'application/vnd.google-apps.document' and 'root' in parents")
    assert escape("a\\b'c") == "a\\\\b\\'c"
    assert "needs" in (await reg.run("drive_read", {"operation": "search"})).error


async def test_a_folder_listing_and_excluded_file_names():
    reg, google = drive([f("a", "Notes"), f("b", "Passwords 2026"), f("c", "TAX return")], exclude_terms=["password", "tax"])
    result = await reg.run("drive_read", {"operation": "list_folder", "folder_id": "root"})
    assert "Notes" in result.content and "Passwords" not in result.content and "TAX" not in result.content
    assert [r for r in google.api_requests if r.url.path == "/drive/v3/files"][0].url.params["q"] == "'root' in parents and trashed = false"
    for fid in ("b", "c"):
        refused = await reg.run("drive_read", {"operation": "read", "file_id": fid})
        assert not refused.ok and "excluded" in refused.error


async def test_google_docs_are_exported_as_text_and_sheets_as_csv_and_text_files_read_as_they_are():
    files = [f("d1", "Doc"), f("s1", "Sheet", "application/vnd.google-apps.spreadsheet"), f("t1", "notes.md", "text/markdown", size="12")]
    reg, google = drive(files, {"d1": "Hello from a doc", "s1": "a,b\n1,2", "t1": "# plain markdown"})
    assert "Hello from a doc" in (await reg.run("drive_read", {"operation": "read", "file_id": "d1"})).content
    assert "a,b\n1,2" in (await reg.run("drive_read", {"operation": "read", "file_id": "s1"})).content
    assert "# plain markdown" in (await reg.run("drive_read", {"operation": "read", "file_id": "t1"})).content
    exports = [r for r in google.api_requests if r.url.path.endswith("/export")]
    assert [r.url.params["mimeType"] for r in exports] == ["text/plain", "text/csv"]


async def test_other_kinds_folders_trash_and_big_files_are_refused_clearly():
    files = [f("p1", "scan.pdf", "application/pdf"), f("fo", "Folder", "application/vnd.google-apps.folder"), f("tr", "Old", DOC, trashed=True), f("big", "log.txt", "text/plain", size="9000000")]
    reg, _ = drive(files)
    errors = {fid: (await reg.run("drive_read", {"operation": "read", "file_id": fid})).error for fid in ("p1", "fo", "tr", "big")}
    assert "cannot be read as text" in errors["p1"] and "list_folder" in errors["fo"] and "trash" in errors["tr"] and "too large" in errors["big"]
    assert "no such file" in (await reg.run("drive_read", {"operation": "read", "file_id": "nope1"})).error


async def test_drive_output_is_truncated_and_marked_and_only_get_is_used():
    reg, google = drive([f("d1", "Long")], {"d1": "word " * 9000})
    result = await reg.run("drive_read", {"operation": "read", "file_id": "d1", "max_chars": 700})
    assert result.truncated and "[file truncated]" in result.content and len(result.content) < 900
    assert {r.method for r in google.api_requests} == {"GET"}
    assert "Long" not in result.summary


async def test_drive_read_marks_the_conversation_private_and_does_not_leak_the_token():
    google = Google(drive_api([f("d1", "Doc")], {"d1": f"token {ACCESS}"}))
    auth = auth_for(google)
    reg = ToolRegistry([DriveRead(auth, transport=google.transport)], secrets=[CLIENT_SECRET, REFRESH])
    reg.add_secret_source(auth)
    ctx = TurnContext("s", "t1")
    result = await reg.run("drive_read", {"operation": "read", "file_id": "d1"}, ctx)
    assert result.private and ctx.taint.tainted and ACCESS not in result.content


# ---- gating -------------------------------------------------------------------------------------------------------------------------------------------------------

async def test_the_google_tools_need_the_credentials_and_the_egress_flag():
    from bicameral.config import Settings
    from bicameral.tools import build_registry

    base = {"GEMINI_API_KEY": "g", "ANTHROPIC_API_KEY": "a", "AUDIT_LOG": "/tmp/jaynes-test-audit2.jsonl"}
    google = {"GOOGLE_CLIENT_ID": "i", "GOOGLE_CLIENT_SECRET": "s", "GOOGLE_REFRESH_TOKEN": "r"}
    registry, notes = await build_registry(Settings.from_env(base))
    assert "gmail_read" not in registry.names and any("scripts/google-auth.py" in n for n in notes)
    registry, notes = await build_registry(Settings.from_env({**base, **google}))
    assert "gmail_read" not in registry.names and any("ALLOW_PRIVATE_EGRESS" in n for n in notes)
    registry, notes = await build_registry(Settings.from_env({**base, **google, "ALLOW_PRIVATE_EGRESS": "1", "GMAIL_EXCLUDE_LABELS": "Finance", "DRIVE_EXCLUDE_TERMS": "password"}))
    assert {"gmail_read", "drive_read"} <= set(registry.names)
    settings = Settings.from_env({**base, **google})
    assert "s" in settings.secrets() and "r" in settings.secrets()
    distinct = Settings.from_env({**base, "GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "sec-VALUE-1", "GOOGLE_REFRESH_TOKEN": "ref-VALUE-2"})
    assert "sec-VALUE-1" not in repr(distinct) and "ref-VALUE-2" not in str(distinct)
