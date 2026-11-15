"""Turning an uploaded file into text, for a message: text of every kind as it is, PDFs through pypdf, Word (.docx) and Excel (.xlsx) files."""
from __future__ import annotations

import io
import zipfile
from typing import Tuple

MAX_PDF_PAGES = 300
MAX_SHEET_ROWS = 5000
MAX_UNZIPPED_BYTES = 100_000_000                       # a .docx or .xlsx is a zip file; refuse one that would unpack to something huge
OLD_OFFICE_SUFFIXES = (".doc", ".xls", ".ppt", ".pptx", ".odt", ".ods", ".pages", ".numbers", ".key")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".zip", ".gz", ".mp3", ".mp4", ".mov", ".wav", ".exe")


class DocumentError(Exception):
    """The file cannot be used. The message is safe to show the user."""


def extract_text(name: str, data: bytes) -> Tuple[str, str]:
    """(title, text) for an uploaded file."""
    lowered = (name or "").lower()
    title = (name or "Untitled document").rsplit("/", 1)[-1]
    if lowered.endswith(".pdf") or data[:5] == b"%PDF-":
        return title, _pdf_text(data)
    if lowered.endswith((".docx", ".docm")):
        return title, _docx_text(data)
    if lowered.endswith((".xlsx", ".xlsm")):
        return title, _xlsx_text(data)
    if lowered.endswith(OLD_OFFICE_SUFFIXES):
        raise DocumentError("that format cannot be read; save it as .docx, .xlsx or PDF and upload that")
    if lowered.endswith(IMAGE_SUFFIXES) or b"\x00" in data[:4096]:
        raise DocumentError("that file is not text, a PDF, a Word document or a spreadsheet")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    if not text.strip():
        raise DocumentError("the file is empty")
    return title, text


def _open_zip(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_UNZIPPED_BYTES:
                raise DocumentError("that file is too large once unpacked")
    except zipfile.BadZipFile:
        raise DocumentError("that file is damaged or is not a Word or Excel file")


def _docx_text(data: bytes) -> str:
    """Paragraphs and tables in the order they appear (a table's cells separated by tabs)."""
    _open_zip(data)
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        document = Document(io.BytesIO(data))
        blocks = []
        for child in document.element.body.iterchildren():
            if child.tag.endswith("}p"):
                blocks.append(Paragraph(child, document).text)
            elif child.tag.endswith("}tbl"):
                blocks.append("\n".join("\t".join(cell.text.replace("\n", " ") for cell in row.cells) for row in Table(child, document).rows))
        text = "\n\n".join(block for block in blocks if block.strip())
    except DocumentError:
        raise
    except Exception:
        raise DocumentError("could not read that Word document")
    if not text.strip():
        raise DocumentError("that document has no text")
    return text


def _xlsx_text(data: bytes) -> str:
    """Each sheet as tab-separated rows under its name; formulas as their last calculated values."""
    _open_zip(data)
    try:
        from openpyxl import load_workbook
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        parts = []
        for sheet in workbook.worksheets:
            rows = []
            for number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                if number > MAX_SHEET_ROWS:
                    rows.append(f"(more rows not shown: only the first {MAX_SHEET_ROWS:,} are read)")
                    break
                cells = ["" if value is None else str(value).replace("\t", " ").replace("\n", " ") for value in row]
                if any(cells):
                    rows.append("\t".join(cells).rstrip("\t"))
            if rows:
                parts.append(f"## Sheet: {sheet.title}\n" + "\n".join(rows))
        workbook.close()
    except DocumentError:
        raise
    except Exception:
        raise DocumentError("could not read that spreadsheet")
    if not parts:
        raise DocumentError("that spreadsheet is empty")
    return "\n\n".join(parts)


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise DocumentError("that PDF is password-protected")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise DocumentError(f"that PDF has more than {MAX_PDF_PAGES} pages")
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except DocumentError:
        raise
    except Exception:                                   # a damaged or unusual PDF: the reader's message is not for the user
        raise DocumentError("could not read that PDF")
    if not text.strip():
        raise DocumentError("that PDF has no text to read (it may be a scan)")
    return text
