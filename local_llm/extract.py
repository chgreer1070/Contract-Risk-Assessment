"""Read contract text from a .txt / .md file, or from a PDF if a PDF library is installed."""

from __future__ import annotations

import hashlib
import io
import os

from local_llm.client import LocalLLMError

_PDF_HINT = (
    'This file looks like a PDF. Install pdfplumber (pip install pdfplumber) '
    'or export the contract as a .txt file and pass that instead.'
)


def read_contract_snapshot(path: str, max_chars: int = 100_000) -> tuple[str, bool, str]:
    """Read the file once.

    Returns the text sent for analysis, whether that text was truncated, and
    the SHA-256 of the exact bytes this call read. Later edits to the path are
    not part of either result.
    """
    raw = _read_snapshot_bytes(path)
    text, truncated = _text_from_snapshot(path, raw, max_chars)
    return text, truncated, hashlib.sha256(raw).hexdigest()


def read_contract(path: str, max_chars: int = 100_000) -> tuple[str, bool]:
    """Return ``(text, truncated)``. Raises LocalLLMError on missing/unreadable files."""
    text, truncated, _digest = read_contract_snapshot(path, max_chars=max_chars)
    return text, truncated


def _read_snapshot_bytes(path: str) -> bytes:
    if not path or not os.path.isfile(path):
        raise LocalLLMError(f'Contract file not found: {path}')
    with open(path, 'rb') as handle:
        return handle.read()


def _text_from_snapshot(path: str, raw: bytes, max_chars: int) -> tuple[str, bool]:
    ext = os.path.splitext(path)[1].lower()
    if ext == '.pdf':
        text = _read_pdf_bytes(path, raw)
    else:
        with io.BytesIO(raw) as stream, io.TextIOWrapper(
            stream, encoding='utf-8', errors='replace',
        ) as reader:
            text = reader.read()
    text = text.strip()
    if not text:
        raise LocalLLMError(f'No readable text in {path}.')
    if len(text) > max_chars:
        return text[:max_chars], True
    return text, False


def _read_pdf_bytes(path: str, raw: bytes) -> str:
    try:
        import pdfplumber  # type: ignore[import-not-found]
    except ImportError:
        return _read_pdf_pypdf(path, raw)
    pages: list[str] = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or '')
    text = '\n'.join(pages).strip()
    if text:
        return text
    raise LocalLLMError(f'PDF opened but no text could be extracted: {path}')


def _read_pdf_pypdf(path: str, raw: bytes) -> str:
    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]
    except ImportError:
        raise LocalLLMError(_PDF_HINT) from None
    reader = PdfReader(io.BytesIO(raw))
    text = '\n'.join((page.extract_text() or '') for page in reader.pages).strip()
    if not text:
        raise LocalLLMError(f'PDF opened but no text could be extracted: {path}')
    return text
