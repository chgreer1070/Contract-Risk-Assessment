"""Pull JSON lists/objects out of messy LLM replies (same contract as the notebook)."""

from __future__ import annotations

import json
import re
from typing import Any


def extract_json_list(raw: str) -> list[dict[str, Any]]:
    """Best-effort: pull a JSON array of objects out of an LLM response."""
    if not raw:
        return []
    text = re.sub(r'```(?:json)?', '', raw).strip()
    match = re.search(r'\[\s*\{.*\}\s*\]', text, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def extract_json_object(raw: str) -> dict[str, Any]:
    """Best-effort: pull a JSON object out of an LLM response."""
    if not raw:
        return {}
    text = re.sub(r'```(?:json)?', '', raw).strip()
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


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
