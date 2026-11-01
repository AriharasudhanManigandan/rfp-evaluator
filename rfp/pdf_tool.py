"""Document Tool: extract clean text from an uploaded supplier PDF.

PyMuPDF is the primary extractor (fast, good layout handling). If it is not installed, fails on a
particular file, or finds almost no text, pypdf is tried next. The tool raises ``DocumentError`` for
files that are not PDFs, are encrypted, or contain no extractable text (e.g. scans), so the UI can show
a clear message *before* any LLM call is made.
"""
from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass, field

from .config import MAX_DOC_CHARS, MAX_PDF_BYTES, MIN_TEXT_CHARS


class DocumentError(Exception):
    """The PDF cannot be used (corrupt, encrypted, empty/scanned, too large...)."""


@dataclass
class ExtractedDocument:
    text: str
    pages: int
    chars: int
    sha256: str
    extractor: str
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)


def _clean(text: str) -> str:
    text = text.replace("\x00", " ").replace("\u00a0", " ").replace("\u2022", "-")
    text = re.sub(r"-\n(?=[a-z])", "", text)          # re-join words hyphenated across lines
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _with_page_markers(pages: list[str]) -> str:
    parts = []
    for number, raw in enumerate(pages, start=1):
        cleaned = _clean(raw or "")
        if cleaned:
            parts.append(f"[Page {number}]\n{cleaned}")
    return "\n\n".join(parts)


def _extract_pymupdf(data: bytes) -> list[str]:
    try:
        import pymupdf                      # PyMuPDF >= 1.24.3
    except ImportError:
        import fitz as pymupdf              # older releases expose the same API as "fitz"
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        if doc.needs_pass:
            raise DocumentError("PDF is password protected.")
        return [page.get_text("text") for page in doc]


def _extract_pypdf(data: bytes) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted and not reader.decrypt(""):
        raise DocumentError("PDF is password protected.")
    return [page.extract_text() or "" for page in reader.pages]


EXTRACTORS = (("pymupdf", _extract_pymupdf), ("pypdf", _extract_pypdf))


def extract_text(data: bytes, filename: str = "document.pdf") -> ExtractedDocument:
    if not data:
        raise DocumentError(f"{filename}: file is empty.")
    if len(data) > MAX_PDF_BYTES:
        raise DocumentError(f"{filename}: file exceeds {MAX_PDF_BYTES // (1024 * 1024)} MB limit.")
    if not data.lstrip()[:5] == b"%PDF-":
        raise DocumentError(f"{filename}: not a valid PDF file.")

    problems: list[str] = []
    shortest_seen: int | None = None
    for name, extractor in EXTRACTORS:
        try:
            pages = extractor(data)
        except DocumentError as exc:                   # definitive (e.g. password protected)
            raise DocumentError(f"{filename}: {exc}") from exc
        except Exception as exc:                       # library missing or file confuses it -> next extractor
            problems.append(f"{name}: {exc}")
            continue
        text = _with_page_markers(pages)
        body_chars = len(re.sub(r"\[Page \d+\]", "", text).strip())
        if body_chars < MIN_TEXT_CHARS:
            shortest_seen = body_chars if shortest_seen is None else max(shortest_seen, body_chars)
            problems.append(f"{name}: only {body_chars} characters")
            continue
        warnings, truncated = [], False
        if len(text) > MAX_DOC_CHARS:
            text, truncated = text[:MAX_DOC_CHARS], True
            warnings.append(f"Document text truncated to {MAX_DOC_CHARS:,} characters for the LLM.")
        return ExtractedDocument(text=text, pages=len(pages), chars=len(text),
                                 sha256=hashlib.sha256(data).hexdigest(), extractor=name,
                                 truncated=truncated, warnings=warnings)

    if shortest_seen is not None:
        raise DocumentError(
            f"{filename}: only {shortest_seen} characters of text found - the PDF looks empty or scanned "
            "(OCR is not supported).")
    raise DocumentError(f"{filename}: could not read PDF ({'; '.join(problems)}).")
