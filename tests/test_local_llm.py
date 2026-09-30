"""Tests for the local LM Studio / Ollama path (stdlib HTTP stand-in, no real model)."""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from local_llm.__main__ import main as cli_main  # noqa: E402
from local_llm.client import (  # noqa: E402
    LocalLLMError,
    chat,
    list_models,
    resolve_endpoint,
)
from local_llm.extract import read_contract  # noqa: E402
from local_llm.parse import (  # noqa: E402
    actions_to_md,
    clauses_to_md,
    extract_json_list,
    extract_json_object,
    risks_to_md,
)
from local_llm.pipeline import analyze_contract  # noqa: E402

# --------------------------------------------------------------------------- #
# Stand-in OpenAI-compatible server
# --------------------------------------------------------------------------- #

_CLAUSES = [
    {
        'clauseType': 'Limitation of Liability',
        'section': 'Section 8.1',
        'extractedClause': 'Liability is capped at fees paid in the prior 12 months.',
        'summary': 'Recovery is limited to fees paid.',
    }
]
_RISKS = [
    {
        'riskType': 'Limitation of Liability',
        'clauseReference': 'Section 8.1',
        'riskLevel': 'High',
        'likelihood': 'Likely',
        'potentialConsequence': 'Unrecoverable losses above the cap.',
    }
]
_ACTIONS = [
    {
        'clause': 'Section 8.1',
        'riskLevel': 'High',
        'action': 'Negotiate a higher cap.',
    }
]
_META = {
    'title': 'SaaS Agreement',
    'parties': ['Acme', 'CloudServe'],
    'effectiveDate': '2025-02-04',
    'expirationDate': '2028-02-04',
    'contractValue': '$15M',
    'jurisdiction': 'Delaware',
}


def _reply_for(prompt: str) -> str:
    low = prompt.lower()
    if 'strict json' in low or 'effectiveDate' in prompt:
        return 'Here you go:\n' + json.dumps(_META)
    if 'recommended action' in low or 'suggest specific actions' in low:
        return '```json\n' + json.dumps(_ACTIONS) + '\n```'
    if 'potential risks' in low or 'risk assessment' in low:
        return json.dumps(_RISKS)
    return json.dumps(_CLAUSES)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: ARG002
        return

    def do_GET(self):
        if self.path.rstrip('/').endswith('/models'):
            body = json.dumps({'data': [{'id': 'stand-in-model'}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0'))
        payload = json.loads(self.rfile.read(length) or b'{}')
        prompt = ''
        try:
            prompt = payload['messages'][0]['content']
        except (KeyError, IndexError, TypeError):
            prompt = ''
        body = json.dumps({
            'choices': [{'message': {'role': 'assistant', 'content': _reply_for(prompt)}}],
        }).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def standin():
    server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    yield f'http://{host}:{port}/v1'
    server.shutdown()


# --------------------------------------------------------------------------- #
# Endpoint resolution
# --------------------------------------------------------------------------- #

def test_resolve_lmstudio_default(monkeypatch):
    monkeypatch.delenv('CONTRACT_RISK_LLM_BASE_URL', raising=False)
    monkeypatch.delenv('CONTRACT_RISK_LLM_MODEL', raising=False)
    ep = resolve_endpoint('lmstudio')
    assert ep['base_url'] == 'http://127.0.0.1:1234/v1'
    assert ep['model'] == 'local-model'


def test_resolve_ollama_default(monkeypatch):
    monkeypatch.delenv('CONTRACT_RISK_LLM_BASE_URL', raising=False)
    monkeypatch.delenv('CONTRACT_RISK_LLM_MODEL', raising=False)
    ep = resolve_endpoint('ollama')
    assert ep['base_url'] == 'http://127.0.0.1:11434/v1'
    assert ep['model'] == 'llama3.1'


def test_resolve_env_overrides_provider(monkeypatch):
    monkeypatch.setenv('CONTRACT_RISK_LLM_BASE_URL', 'http://10.0.0.5:9000/v1')
    monkeypatch.setenv('CONTRACT_RISK_LLM_MODEL', 'qwen2.5')
    ep = resolve_endpoint('lmstudio')
    assert ep['base_url'] == 'http://10.0.0.5:9000/v1'
    assert ep['model'] == 'qwen2.5'


def test_resolve_unknown_provider_without_url():
    with pytest.raises(LocalLLMError):
        resolve_endpoint('mystery')


def test_resolve_appends_v1_when_missing():
    ep = resolve_endpoint('lmstudio', base_url='http://127.0.0.1:1234', model='x')
    assert ep['base_url'] == 'http://127.0.0.1:1234/v1'


# --------------------------------------------------------------------------- #
# JSON parsing
# --------------------------------------------------------------------------- #

def test_extract_json_list_from_fenced_prose():
    raw = 'Sure.\n```json\n[{"clauseType": "Liability", "summary": "Cap"}]\n```\n'
    items = extract_json_list(raw)
    assert items[0]['clauseType'] == 'Liability'


def test_extract_json_list_empty_on_garbage():
    assert extract_json_list('no json here') == []
    assert extract_json_list('') == []


def test_extract_json_object_from_prose():
    raw = 'Result: {"title": "MSA", "parties": ["A"]}'
    assert extract_json_object(raw)['title'] == 'MSA'


def test_markdown_helpers_roundtrip_keys():
    assert 'Limitation of Liability' in clauses_to_md(_CLAUSES)
    assert 'Section 8.1' in risks_to_md(_RISKS)
    assert 'Negotiate' in actions_to_md(_ACTIONS)


# --------------------------------------------------------------------------- #
# Extract
# --------------------------------------------------------------------------- #

def test_read_contract_txt_and_truncate(tmp_path):
    p = tmp_path / 'c.txt'
    p.write_text('hello contract ' * 20)
    text, truncated = read_contract(str(p), max_chars=20)
    assert truncated is True
    assert len(text) == 20


def test_read_contract_missing():
    with pytest.raises(LocalLLMError):
        read_contract('/no/such/file.txt')


def test_read_pdf_without_library_explains(tmp_path, monkeypatch):
    p = tmp_path / 'c.pdf'
    p.write_bytes(b'%PDF-1.4 fake')
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name in ('pdfplumber', 'pypdf'):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr('builtins.__import__', fake_import)
    with pytest.raises(LocalLLMError, match='pdfplumber'):
        read_contract(str(p))


# --------------------------------------------------------------------------- #
# Client + stand-in server
# --------------------------------------------------------------------------- #

def test_list_models_and_chat_against_standin(standin):
    names = list_models(standin)
    assert names == ['stand-in-model']
    reply = chat(
        [{'role': 'user', 'content': 'Extract key clauses from the contract'}],
        base_url=standin,
        model='stand-in-model',
        timeout=5,
    )
    assert extract_json_list(reply)[0]['section'] == 'Section 8.1'


def test_chat_unreachable_is_local_llm_error():
    with pytest.raises(LocalLLMError, match='Could not reach'):
        chat(
            [{'role': 'user', 'content': 'hi'}],
            base_url='http://127.0.0.1:1/v1',
            model='x',
            timeout=0.3,
        )


# --------------------------------------------------------------------------- #
# Pipeline + CLI against the stand-in
# --------------------------------------------------------------------------- #

def test_analyze_contract_end_to_end(tmp_path, standin):
    src = tmp_path / 'msa.txt'
    src.write_text('Section 8.1 Liability is capped at fees paid.')
    state = analyze_contract(str(src), base_url=standin, model='stand-in-model', timeout=5)
    assert len(state['key_clauses_data']) == 1
    assert len(state['risk_assessment_data']) == 1
    assert len(state['recommended_actions_data']) == 1
    assert state['metadata']['title'] == 'SaaS Agreement'
    assert state['local_llm']['model'] == 'stand-in-model'
    assert 'Liability is capped' in state['key_clauses']


def test_cli_ping_and_analyze(tmp_path, standin):
    assert cli_main(['ping', '--base-url', standin, '--timeout', '5']) == 0
    src = tmp_path / 'msa.txt'
    src.write_text('Section 8.1 Liability is capped at fees paid.')
    out = tmp_path / 'dash.html'
    assert cli_main([
        'analyze', str(src), '-o', str(out),
        '--base-url', standin, '--model', 'stand-in-model', '--timeout', '5',
    ]) == 0
    html = out.read_text(encoding='utf-8')
    assert 'contract-data' in html
    assert 'Limitation of Liability' in html


def test_cli_analyze_missing_file_is_error():
    assert cli_main(['analyze', '/no/such/contract.txt']) == 1
