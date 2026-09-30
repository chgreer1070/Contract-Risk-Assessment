"""Minimal OpenAI-compatible chat client (stdlib only).

LM Studio and Ollama both expose ``/v1/chat/completions`` and ``/v1/models``.
No SDK is required so this stays in the CPU test loop.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

DEFAULT_PROVIDERS = {
    'lmstudio': 'http://127.0.0.1:1234/v1',
    'ollama': 'http://127.0.0.1:11434/v1',
}

ENV_BASE_URL = 'CONTRACT_RISK_LLM_BASE_URL'
ENV_MODEL = 'CONTRACT_RISK_LLM_MODEL'
ENV_API_KEY = 'CONTRACT_RISK_LLM_API_KEY'


class LocalLLMError(Exception):
    """Raised when the local model cannot be reached or returns a bad response."""


def resolve_endpoint(
    provider: str = 'lmstudio',
    base_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> dict[str, str]:
    """Resolve provider name / env / explicit overrides into a connection dict."""
    name = (provider or 'lmstudio').strip().lower()
    if name not in DEFAULT_PROVIDERS and not base_url and not os.environ.get(ENV_BASE_URL):
        raise LocalLLMError(
            f'Unknown provider {provider!r}. Use lmstudio, ollama, or pass --base-url.'
        )
    url = (
        (base_url or '').strip()
        or os.environ.get(ENV_BASE_URL, '').strip()
        or DEFAULT_PROVIDERS.get(name, DEFAULT_PROVIDERS['lmstudio'])
    )
    if not url.endswith('/v1') and not url.rstrip('/').endswith('/v1'):
        url = url.rstrip('/') + '/v1'
    chosen_model = (
        (model or '').strip()
        or os.environ.get(ENV_MODEL, '').strip()
        or ('local-model' if name != 'ollama' else 'llama3.1')
    )
    key = (api_key if api_key is not None else os.environ.get(ENV_API_KEY, '')) or ''
    return {'provider': name, 'base_url': url.rstrip('/'), 'model': chosen_model, 'api_key': key}


def _request_json(url: str, payload: dict[str, Any] | None, api_key: str, timeout: float) -> Any:
    data = None if payload is None else json.dumps(payload).encode('utf-8')
    headers = {'Accept': 'application/json'}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    req = urllib.request.Request(url, data=data, headers=headers, method='GET' if data is None else 'POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8')
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf-8', errors='replace')[:400]
        raise LocalLLMError(f'Local model HTTP {exc.code} from {url}: {body}') from exc
    except urllib.error.URLError as exc:
        raise LocalLLMError(
            f'Could not reach the local model at {url}. '
            'Start LM Studio (Developer → Start Server) or Ollama, then retry. '
            f'({exc.reason})'
        ) from exc
    except TimeoutError as exc:
        raise LocalLLMError(f'Timed out talking to the local model at {url}.') from exc
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise LocalLLMError(f'Local model returned non-JSON from {url}.') from exc


def list_models(base_url: str, api_key: str = '', timeout: float = 10.0) -> list[str]:
    """Return model ids advertised by GET /models (empty list if the body is unexpected)."""
    data = _request_json(base_url.rstrip('/') + '/models', None, api_key, timeout)
    items = data.get('data') if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    names: list[str] = []
    for item in items:
        if isinstance(item, dict) and item.get('id'):
            names.append(str(item['id']))
        elif isinstance(item, str):
            names.append(item)
    return names


def chat(
    messages: list[dict[str, str]],
    *,
    base_url: str,
    model: str,
    api_key: str = '',
    timeout: float = 180.0,
    temperature: float = 0.1,
) -> str:
    """Send a chat-completions request and return the assistant text."""
    payload = {
        'model': model,
        'messages': messages,
        'temperature': temperature,
    }
    data = _request_json(base_url.rstrip('/') + '/chat/completions', payload, api_key, timeout)
    try:
        content = data['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError) as exc:
        raise LocalLLMError('Local model response was missing choices[0].message.content.') from exc
    if content is None:
        raise LocalLLMError('Local model returned an empty message.')
    return str(content)
