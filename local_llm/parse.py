"""Pull JSON lists/objects out of messy LLM replies (same contract as the notebook)."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any


def _strip_fences(raw: str) -> str:
    return re.sub(r'```(?:json)?', '', raw).strip()


def _json_values(text: str, opener: str) -> Iterator[Any]:
    """Yield JSON values that start at each ``opener``, skipping invalid spans.

    A greedy ``{.*}`` / ``[.*]`` match runs from the first opener to the last
    closer. Model replies often add a later brace note (``{YYYY-MM-DD}``) or a
    stray ``} ]``, which makes ``json.loads`` fail and drops a valid answer.
    ``raw_decode`` stops at the end of one value, so later prose is ignored.
    """
    decoder = json.JSONDecoder()
    start = 0
    while True:
        index = text.find(opener, start)
        if index < 0:
            return
        try:
            value, _end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            start = index + 1
            continue
        yield value
        # Step one character, not to the end of this value. An outer array such
        # as [[{...}]] has no object elements, and jumping to its end would hide
        # the inner array the old bracket search still found.
        start = index + 1


def extract_json_list(raw: str) -> list[dict[str, Any]]:
    """Best-effort: pull a JSON array of objects out of an LLM response."""
    if not raw:
        return []
    text = _strip_fences(raw)
    for data in _json_values(text, '['):
        if not isinstance(data, list):
            continue
        items = [item for item in data if isinstance(item, dict)]
        if items:
            return items
    return []


def extract_json_object(raw: str) -> dict[str, Any]:
    """Best-effort: pull a JSON object out of an LLM response."""
    if not raw:
        return {}
    text = _strip_fences(raw)
    for data in _json_values(text, '{'):
        if isinstance(data, dict) and data:
            return data
    return {}


def clauses_to_md(items: list[dict[str, Any]]) -> str:
    return '\n\n'.join(
        '- **Clause Type**: {}\n- **Extracted Clause**: {}\n- **Summary**: {}'.format(
            it.get('clauseType', it.get('type', '')),
            it.get('extractedClause', it.get('clause', '')),
            it.get('summary', ''),
        )
        for it in items
    )


def risks_to_md(items: list[dict[str, Any]]) -> str:
    return '\n\n'.join(
        '- **Risk Type**: {}\n- **Clause Reference**: {}\n- **Risk Level**: {}\n'
        '- **Likelihood**: {}\n- **Potential Consequence**: {}'.format(
            it.get('riskType', ''),
            it.get('clauseReference', ''),
            it.get('riskLevel', ''),
            it.get('likelihood', ''),
            it.get('potentialConsequence', ''),
        )
        for it in items
    )


def actions_to_md(items: list[dict[str, Any]]) -> str:
    return '\n\n'.join(
        '**Clause**: {}\n**Risk Level**: {}\n**Recommended Action**: {}'.format(
            it.get('clause', ''),
            it.get('riskLevel', ''),
            it.get('action', it.get('recommendedAction', '')),
        )
        for it in items
    )
