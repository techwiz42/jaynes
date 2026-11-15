"""Files sent with a message: reading Word and Excel files, the /attachments endpoint, and what the model, the page and the saved conversation each get."""
import io

import pytest
from docx import Document
from openpyxl import Workbook

from bicameral.documents import DocumentError, extract_text
from bicameral.orchestrator import Session

from .fakes import OK, FakeActor, FakeObserver
from .test_users_api import PASSWORD, browser, join, make, wait_for


def docx_bytes():
    document = Document()
    document.add_paragraph("Quarterly plan")
    table = document.add_table(rows=2, cols=2)
    for r, row in enumerate((("item", "cost"), ("chairs", "400"))):
        for c, value in enumerate(row):
            table.cell(r, c).text = value
    document.add_paragraph("Approved by Dana.")
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def xlsx_bytes():
    book = Workbook()
    sheet = book.active
    sheet.title = "Budget"
    sheet.append(["item", "cost"])
    sheet.append(["chairs", 400])
    sheet["B3"] = "=B2*2"
    book.create_sheet("Notes").append(["remember the milk"])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def test_a_word_document_is_read_in_order_with_its_tables():
    title, text = extract_text("plan.docx", docx_bytes())
    assert title == "plan.docx"
    assert text.index("Quarterly plan") < text.index("chairs\t400") < text.index("Approved by Dana.")


def test_a_spreadsheet_is_read_sheet_by_sheet():
    _, text = extract_text("budget.xlsx", xlsx_bytes())
    assert "## Sheet: Budget\nitem\tcost\nchairs\t400" in text
    assert "## Sheet: Notes\nremember the milk" in text


@pytest.mark.parametrize("name", ["notes.txt", "data.tsv", "main.py", "config.yaml", "page.html", "README"])
def test_text_of_any_kind_is_accepted(name):
    assert extract_text(name, "héllo\nworld".encode("utf-8"))[1] == "héllo\nworld"


@pytest.mark.parametrize("name,data", [("old.doc", b"x"), ("old.xls", b"x"), ("pic.png", b"x"), ("blob.bin", b"ab\x00cd"), ("fake.docx", b"not a zip"), ("empty.txt", b"  \n")])
def test_files_that_cannot_be_read_are_refused_with_a_reason(name, data):
    with pytest.raises(DocumentError):
        extract_text(name, data)


async def test_the_attachments_endpoint_returns_the_text_and_stores_nothing():
    app, store = make()
    async with browser(app) as http:
        await join(http, "a@example.com")
        response = await http.post("/attachments", params={"name": "budget.xlsx"}, content=xlsx_bytes())
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "budget.xlsx" and "chairs\t400" in body["text"] and body["truncated"] is False
        assert (await http.get("/memory/documents")).json() == []
        assert (await http.post("/attachments", params={"name": "x.doc"}, content=b"x")).status_code == 422
    async with browser(app) as stranger:
        assert (await stranger.post("/attachments", params={"name": "a.txt"}, content=b"hi")).status_code == 401


async def test_a_message_can_carry_files_and_a_file_alone_is_enough():
    app, _ = make()
    async with browser(app) as http:
        await join(http, "a@example.com")
        session_id = (await http.post("/sessions")).json()["session_id"]
        session = app.state.sessions[session_id]
        sent = await http.post(f"/sessions/{session_id}/messages", json={"text": "", "attachments": [{"name": "plan.txt", "text": "open at nine"}]})
        assert sent.status_code == 202
        await wait_for(lambda: not session.busy and len(session.log.replay()) > 1, "the turn")
        assert (await http.post(f"/sessions/{session_id}/messages", json={"text": ""})).status_code == 422
        too_many = [{"name": f"{i}.txt", "text": "x"} for i in range(6)]
        assert (await http.post(f"/sessions/{session_id}/messages", json={"text": "hi", "attachments": too_many})).status_code == 422


async def test_the_model_reads_the_file_but_the_page_and_the_saved_message_show_only_its_name():
    actor = FakeActor("It opens at nine.")
    session = Session("s", actor, FakeObserver(OK))
    await session.handle_user_message("when do we open?", [("plan.txt", "We open at nine.")])
    shown = session.log.replay()[0].data["text"]
    assert shown == "when do we open?\n\n[Attached: plan.txt]"
    sent_to_model = actor.calls[0][0]["content"] if isinstance(actor.calls[0], list) else str(actor.calls[0])
    assert "We open at nine." in sent_to_model and "plan.txt" in sent_to_model


async def test_attaching_a_file_blocks_the_web_tools_for_the_rest_of_the_conversation():
    session = Session("s", FakeActor("ok"), FakeObserver(OK))
    await session.handle_user_message("read this", [("a.txt", "secret")])
    assert session.taint.tainted and "attached files" in session.taint.tools
    plain = Session("t", FakeActor("ok"), FakeObserver(OK))
    await plain.handle_user_message("hello")
    assert not plain.taint.tainted
