"""Read contract text from a .txt / .md file, or from a PDF if a PDF library is installed."""

from __future__ import annotations

import os

from local_llm.client import LocalLLMError

_PDF_HINT = (
    'This file looks like a PDF. Install pdfplumber (pip install pdfplumber) '
    'or export the contract as a .txt file and pass that instead.'
)


def read_contract(path: str, max_chars: int = 100_000) -> tuple[str, bool]:
    """Return ``(text, truncated)``. Raises LocalLLMError on missing/unreadable files."""
    if not path or not os.path.isfile(path):
        raise LocalLLMError(f'Contract file not found: {path}')
    ext = os.path.splitext(path)[1].lower()
    if ext == '.pdf':
        text = _read_pdf(path)
    else:
        with open(path, encoding='utf-8', errors='replace') as f:
            text = f.read()
    text = text.strip()
    if not text:
        raise LocalLLMError(f'No readable text in {path}.')
    if len(text) > max_chars:
        return text[:max_chars], True
    return text, False


def _read_pdf(path: str) -> str:
    try:
        import pdfplumber  # type: ignore[import-not-found]
    except ImportError:
        return _read_pdf_pypdf(path)
    pages: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or '')
    text = '\n'.join(pages).strip()
    if text:
        return text
    raise LocalLLMError(f'PDF opened but no text could be extracted: {path}')


def _read_pdf_pypdf(path: str) -> str:
    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]
    except ImportError:
        raise LocalLLMError(_PDF_HINT) from None
    reader = PdfReader(path)
    text = '\n'.join((page.extract_text() or '') for page in reader.pages).strip()
    if not text:
        raise LocalLLMError(f'PDF opened but no text could be extracted: {path}')
    return text
