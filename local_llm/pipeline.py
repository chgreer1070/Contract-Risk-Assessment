"""Run the four analysis steps against a local model and build dashboard state."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from local_llm import prompts
from local_llm.client import chat, resolve_endpoint
from local_llm.extract import read_contract
from local_llm.parse import (
    actions_to_md,
    clauses_to_md,
    extract_json_list,
    extract_json_object,
    risks_to_md,
)

ChatFn = Callable[..., str]


def _ask(
    chat_fn: ChatFn,
    endpoint: dict[str, str],
    prompt: str,
    timeout: float,
) -> str:
    return chat_fn(
        [{'role': 'user', 'content': prompt}],
        base_url=endpoint['base_url'],
        model=endpoint['model'],
        api_key=endpoint['api_key'],
        timeout=timeout,
    )


def analyze_contract(
    path: str,
    *,
    provider: str = 'lmstudio',
    base_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    timeout: float = 180.0,
    max_chars: int = 100_000,
    chat_fn: ChatFn | None = None,
) -> dict[str, Any]:
    """Read a contract, call the local model four times, return dashboard state.

    ``chat_fn`` is injectable so tests can run without a real model. Production
    uses :func:`local_llm.client.chat`.
    """
    text, truncated = read_contract(path, max_chars=max_chars)
    endpoint = resolve_endpoint(provider, base_url, model, api_key)
    talk = chat_fn or chat

    clause_raw = _ask(talk, endpoint, prompts.CLAUSES.format(context=text), timeout)
    clauses = extract_json_list(clause_raw)

    risk_raw = _ask(talk, endpoint, prompts.RISKS.format(context=text), timeout)
    risks = extract_json_list(risk_raw)

    action_raw = _ask(talk, endpoint, prompts.ACTIONS.format(report=risks_to_md(risks) or risk_raw), timeout)
    actions = extract_json_list(action_raw)

    meta_raw = _ask(talk, endpoint, prompts.METADATA.format(context=text[:20_000]), timeout)
    metadata = extract_json_object(meta_raw)

    state: dict[str, Any] = {
        'key_clauses_data': clauses,
        'risk_assessment_data': risks,
        'recommended_actions_data': actions,
        'key_clauses': clauses_to_md(clauses) if clauses else clause_raw,
        'risk_assessment_report': risks_to_md(risks) if risks else risk_raw,
        'recommended_actions': actions_to_md(actions) if actions else action_raw,
        'metadata': metadata,
        'contract_text': text,
        'contract_type': 'default',
        'local_llm': {
            'provider': endpoint['provider'],
            'base_url': endpoint['base_url'],
            'model': endpoint['model'],
            'truncated': truncated,
        },
    }
    return state
