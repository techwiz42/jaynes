"""Turning an uploaded file into text for the user's memory: plain text and Markdown as they are, PDFs through pypdf. Nothing else is accepted."""
from __future__ import annotations

import io
from typing import Tuple

MAX_PDF_PAGES = 300
TEXT_SUFFIXES = (".txt", ".md", ".markdown", ".csv", ".json", ".log", ".rst")


class DocumentError(Exception):
    """The file cannot be used. The message is safe to show the user."""


def extract_text(name: str, data: bytes) -> Tuple[str, str]:
    """(title, text) for an uploaded file."""
    lowered = (name or "").lower()
    title = (name or "Untitled document").rsplit("/", 1)[-1]
    if lowered.endswith(".pdf") or data[:5] == b"%PDF-":
        return title, _pdf_text(data)
    if not lowered.endswith(TEXT_SUFFIXES):
        raise DocumentError("upload a PDF, or a text, Markdown, CSV or JSON file")
    if b"\x00" in data[:4096]:
        raise DocumentError("that file is not text")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    if not text.strip():
        raise DocumentError("the file is empty")
    return title, text


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
