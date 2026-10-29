"""Offline tests for contract-file reading and local-model error handling.

PDF libraries and HTTP are fakes. These tests do not open sockets or call a model.
"""

from __future__ import annotations

import os
import sys
import types
import urllib.error
from io import BytesIO

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from local_llm.client import (  # noqa: E402
    LocalLLMError,
    chat,
    list_models,
)
from local_llm.extract import read_contract  # noqa: E402


class _Page:
    def __init__(self, text: str | None) -> None:
        self._text = text

    def extract_text(self) -> str | None:
        return self._text


class _Pdf:
    def __init__(self, pages: list[_Page]) -> None:
        self.pages = pages

    def __enter__(self) -> _Pdf:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


class _HttpBody:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> _HttpBody:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def _write_pdf(tmp_path):
    path = tmp_path / 'contract.pdf'
    path.write_bytes(b'%PDF-1.4 fake')
    return path


def _install_pdfplumber(monkeypatch: pytest.MonkeyPatch, pages: list[str | None]) -> None:
    module = types.ModuleType('pdfplumber')

    def open_pdf(_path: str) -> _Pdf:
        return _Pdf([_Page(text) for text in pages])

    module.open = open_pdf  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, 'pdfplumber', module)


def _install_pypdf(monkeypatch: pytest.MonkeyPatch, pages: list[str | None]) -> None:
    module = types.ModuleType('pypdf')

    class PdfReader:
        def __init__(self, _path: str) -> None:
            self.pages = [_Page(text) for text in pages]

    module.PdfReader = PdfReader  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, 'pdfplumber', None)
    monkeypatch.setitem(sys.modules, 'pypdf', module)


def _install_urlopen(monkeypatch: pytest.MonkeyPatch, *, body: bytes | None = None, error: BaseException | None = None):
    seen: dict[str, object] = {}

    def _open(req, timeout=0):
        seen['url'] = req.full_url
        seen['method'] = req.get_method()
        seen['timeout'] = timeout
        seen['auth'] = req.get_header('Authorization')
        if error is not None:
            raise error
        return _HttpBody(b'{}' if body is None else body)

    monkeypatch.setattr('local_llm.client.urllib.request.urlopen', _open)
    return seen


# --------------------------------------------------------------------------- #
# Text extraction
# --------------------------------------------------------------------------- #

def test_read_text_file_strips_and_keeps_full_body(tmp_path):
    path = tmp_path / 'contract.md'
    path.write_text('  Payment is due in 30 days.  \n', encoding='utf-8')
    text, truncated = read_contract(str(path))
    assert text == 'Payment is due in 30 days.'
    assert truncated is False


def test_read_text_file_replaces_invalid_utf8(tmp_path):
    path = tmp_path / 'notes.txt'
    path.write_bytes(b'clause \xff remains')
    text, truncated = read_contract(str(path))
    assert truncated is False
    assert 'clause' in text
    assert 'remains' in text
    assert '\ufffd' in text


def test_read_blank_text_file_raises(tmp_path):
    path = tmp_path / 'blank.txt'
    path.write_text(' \n\t', encoding='utf-8')
    with pytest.raises(LocalLLMError, match='No readable text'):
        read_contract(str(path))


# --------------------------------------------------------------------------- #
# PDF extraction (fake libraries, nothing installed)
# --------------------------------------------------------------------------- #

def test_read_pdf_with_pdfplumber(tmp_path, monkeypatch):
    _install_pdfplumber(monkeypatch, ['Page one indemnity.', None, '  '])
    text, truncated = read_contract(str(_write_pdf(tmp_path)))
    assert truncated is False
    assert text == 'Page one indemnity.'


def test_read_pdf_with_pdfplumber_truncates(tmp_path, monkeypatch):
    _install_pdfplumber(monkeypatch, ['abcdefghij'])
    text, truncated = read_contract(str(_write_pdf(tmp_path)), max_chars=4)
    assert text == 'abcd'
    assert truncated is True


def test_read_pdf_with_pdfplumber_empty_raises(tmp_path, monkeypatch):
    _install_pdfplumber(monkeypatch, ['   ', None])
    with pytest.raises(LocalLLMError, match='no text could be extracted'):
        read_contract(str(_write_pdf(tmp_path)))


def test_read_pdf_with_pypdf_when_pdfplumber_missing(tmp_path, monkeypatch):
    _install_pypdf(monkeypatch, ['Fallback page text.', ''])
    text, truncated = read_contract(str(_write_pdf(tmp_path)))
    assert truncated is False
    assert text == 'Fallback page text.'


def test_read_pdf_with_pypdf_empty_raises(tmp_path, monkeypatch):
    _install_pypdf(monkeypatch, [None, '  '])
    with pytest.raises(LocalLLMError, match='no text could be extracted'):
        read_contract(str(_write_pdf(tmp_path)))


# --------------------------------------------------------------------------- #
# Client error handling (fake HTTP, no sockets)
# --------------------------------------------------------------------------- #

def test_chat_http_error_includes_status_and_truncated_body(monkeypatch):
    raw = b'ERR-' + (b'x' * 500) + b'-TAIL'
    error = urllib.error.HTTPError(
        'http://127.0.0.1:9/v1/chat/completions',
        502,
        'bad gateway',
        None,
        BytesIO(raw),
    )
    seen = _install_urlopen(monkeypatch, error=error)
    with pytest.raises(LocalLLMError, match='HTTP 502') as caught:
        chat(
            [{'role': 'user', 'content': 'hello'}],
            base_url='http://127.0.0.1:9/v1',
            model='local-model',
            timeout=2,
        )
    message = str(caught.value)
    assert 'ERR-' in message
    assert 'TAIL' not in message
    assert seen['method'] == 'POST'
    assert str(seen['url']).endswith('/chat/completions')
    assert seen['timeout'] == 2


def test_chat_timeout_raises_local_llm_error(monkeypatch):
    seen = _install_urlopen(monkeypatch, error=TimeoutError('deadline'))
    with pytest.raises(LocalLLMError, match='Timed out talking to the local model'):
        chat(
            [{'role': 'user', 'content': 'hello'}],
            base_url='http://127.0.0.1:9/v1',
            model='local-model',
            timeout=0.25,
        )
    assert seen['timeout'] == 0.25
    assert str(seen['url']).endswith('/chat/completions')


def test_malformed_json_raises_and_sends_api_key(monkeypatch):
    seen = _install_urlopen(monkeypatch, body=b'<html>not json</html>')
    with pytest.raises(LocalLLMError, match='non-JSON'):
        list_models('http://127.0.0.1:9/v1', api_key='secret-token', timeout=3)
    assert seen['method'] == 'GET'
    assert seen['auth'] == 'Bearer secret-token'
    assert seen['timeout'] == 3
    assert str(seen['url']).endswith('/models')


def test_list_models_unexpected_body_is_empty(monkeypatch):
    _install_urlopen(monkeypatch, body=b'{"object": "list"}')
    assert list_models('http://127.0.0.1:9/v1') == []


def test_list_models_accepts_string_ids(monkeypatch):
    body = b'{"data": ["alpha", {"id": "beta"}, {"id": ""}, 7, "gamma"]}'
    _install_urlopen(monkeypatch, body=body)
    assert list_models('http://127.0.0.1:9/v1') == ['alpha', 'beta', 'gamma']


def test_chat_missing_message_content_raises(monkeypatch):
    body = b'{"choices": [{"message": {"role": "assistant"}}]}'
    _install_urlopen(monkeypatch, body=body)
    with pytest.raises(LocalLLMError, match='missing choices'):
        chat(
            [{'role': 'user', 'content': 'hello'}],
            base_url='http://127.0.0.1:9/v1',
            model='local-model',
        )


def test_chat_null_message_raises(monkeypatch):
    body = b'{"choices": [{"message": {"content": null}}]}'
    _install_urlopen(monkeypatch, body=body)
    with pytest.raises(LocalLLMError, match='empty message'):
        chat(
            [{'role': 'user', 'content': 'hello'}],
            base_url='http://127.0.0.1:9/v1',
            model='local-model',
        )
