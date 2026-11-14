"""retrieve_docs: search the user's own Markdown and text files. Local only: a BM25 index built in this process from DOCS_DIR, nothing sent to an embedding service.
Read-only; a file outside the directory (through a symlink) is never read. Excerpts are quoted verbatim from the file."""
from __future__ import annotations

import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

from pydantic import BaseModel, Field

from .base import Tool, ToolResult

EXTENSIONS = {".md", ".markdown", ".txt"}
MAX_FILE_BYTES = 1_000_000
CHUNK_CHARS = 1200
EXCERPT_CHARS = 800
_WORD = re.compile(r"[a-z0-9]+")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def tokens(text: str) -> List[str]:
    return _WORD.findall(text.lower())


@dataclass
class Chunk:
    path: str            # relative to the directory
    heading: str
    text: str            # verbatim from the file
    terms: Counter
    length: int


def _chunks_of(relative: str, content: str) -> List[Chunk]:
    """Split at headings, then at blank lines so a chunk stays near CHUNK_CHARS. Every chunk is a verbatim slice of the file."""
    out: List[Chunk] = []
    heading = relative
    block: List[str] = []
    size = 0

    def flush() -> None:
        nonlocal block, size
        text = "\n".join(block).strip()
        if text:
            words = tokens(heading + " " + text)
            out.append(Chunk(relative, heading, text, Counter(words), len(words)))
        block, size = [], 0

    for line in content.split("\n"):
        match = _HEADING.match(line)
        if match:
            flush()
            heading = match.group(2) or relative
        if not line.strip() and size >= CHUNK_CHARS:
            flush()
            continue
        block.append(line)
        size += len(line) + 1
    flush()
    return out


class DocIndex:
    def __init__(self, root: str) -> None:
        self.root = Path(root).expanduser().resolve()
        self._chunks: List[Chunk] = []
        self._df: Counter = Counter()
        self._avg_len = 1.0
        self._signature: Tuple = ()
        self.refresh()

    def _files(self) -> List[Path]:
        found = []
        if not self.root.is_dir():
            return found
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                path = Path(dirpath, name)
                if path.suffix.lower() not in EXTENSIONS or name.startswith("."):
                    continue
                try:
                    resolved = path.resolve()
                    if self.root not in resolved.parents or resolved.stat().st_size > MAX_FILE_BYTES:
                        continue                                  # a symlink that leads outside the directory, or a huge file
                except OSError:
                    continue
                found.append(path)
        return found

    def refresh(self) -> None:
        """Rebuild when a file was added, removed or changed."""
        files = self._files()
        signature = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files)
        if signature == self._signature and self._chunks:
            return
        chunks: List[Chunk] = []
        for path in files:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            chunks.extend(_chunks_of(str(path.relative_to(self.root)), content))
        self._chunks = chunks
        self._df = Counter(term for chunk in chunks for term in chunk.terms)
        self._avg_len = (sum(c.length for c in chunks) / len(chunks)) if chunks else 1.0
        self._signature = signature

    def __len__(self) -> int:
        return len(self._chunks)

    def search(self, query: str, k: int, k1: float = 1.5, b: float = 0.75) -> List[Tuple[float, Chunk]]:
        self.refresh()
        words = tokens(query)
        n = len(self._chunks)
        scored: List[Tuple[float, Chunk]] = []
        for chunk in self._chunks:
            score = 0.0
            for word in set(words):
                tf = chunk.terms.get(word, 0)
                if not tf:
                    continue
                df = self._df[word]
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * chunk.length / self._avg_len))
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda pair: (-pair[0], pair[1].path, pair[1].heading))
        return scored[:k]


class RetrieveDocsArgs(BaseModel):
    query: str = Field(min_length=1, max_length=300, description="What to look for in the user's documents.")
    k: int = Field(default=4, ge=1, le=8, description="How many passages to return.")


class RetrieveDocs(Tool):
    name = "retrieve_docs"
    description = ("Search the user's own documents (Markdown and text files) and return the best-matching passages, each with its file path, heading and a verbatim excerpt. "
                   "When you rely on a passage, cite its file path.")
    args_model = RetrieveDocsArgs
    timeout_seconds = 10.0

    def __init__(self, index: DocIndex) -> None:
        self._index = index

    async def run(self, args: RetrieveDocsArgs) -> ToolResult:
        hits = self._index.search(args.query, args.k)
        if not hits:
            return ToolResult(ok=True, content="(no matching passages)", summary="no matching passages", meta={"hits": 0})
        blocks, paths = [], []
        truncated = False
        for _, chunk in hits:
            excerpt = chunk.text[:EXCERPT_CHARS]
            truncated = truncated or len(chunk.text) > EXCERPT_CHARS
            blocks.append(f"source_path: {chunk.path}\nheading: {chunk.heading}\nexcerpt:\n{excerpt}")
            paths.append(chunk.path)
        return ToolResult(ok=True, content="\n\n---\n\n".join(blocks), truncated=truncated, summary=f"{len(hits)} passages from {len(set(paths))} files",
                          meta={"hits": len(hits), "paths": sorted(set(paths))})
