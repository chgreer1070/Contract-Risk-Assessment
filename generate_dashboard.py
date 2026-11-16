"""
generate_dashboard.py

Converts the output of risk_graph.invoke() into an interactive HTML dashboard.

Usage:
    from generate_dashboard import generate_dashboard_html

    output = risk_graph.invoke(input={'pdf_path': 'contract.pdf'})
    html = generate_dashboard_html(output)
    # When that state includes pdf_path, the call also writes
    # ``<contract-name>.score-audit.jsonl`` next to the contract. The HTML
    # does not contain the audit, so the page security hash stays the same.

    # Save to file:
    with open('dashboard.html', 'w') as f:
        f.write(html)

    # Or render in Streamlit:
    import streamlit.components.v1 as components
    components.html(html, height=2400, scrolling=True)
"""

import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl
    _fcntl = None  # type: ignore[assignment]

try:
    import msvcrt as _msvcrt
except ImportError:
    _msvcrt = None  # type: ignore[assignment]

# --- Tolerant parsing helpers -------------------------------------------------
# Real Mixtral/Mistral output deviates from the prompt's bullet+**bold** template:
# it emits numbered lists, drops bold markers, and adds prose qualifiers. These
# helpers extract fields tolerantly so a single format slip doesn't collapse the
# whole parse into one fallback blob.

# Field label/value separator. Accepts a colon or any dash the LLM might use,
# including the en dash (U+2013) and em dash (U+2014) it often substitutes.
_SEP = r'[:\-\u2013\u2014]'


def _split_blocks(text, anchor_label):
    """Split text before each occurrence of an anchor field label.

    Tolerates an optional leading bullet ('-'/'*'), numbered prefix ('1.'/'2)'),
    and optional '**bold**' around the label, followed by ':' or a dash.
    """
    lab = anchor_label.replace(' ', r'\s+')
    return re.split(
        r'\n(?=\s*(?:[-*]|\d+[.)])?\s*(?:\*\*)?\s*' + lab + r'\s*(?:\*\*)?\s*' + _SEP + r')',
        text,
    )


# Field labels that mark the start of the NEXT field (used as stop-points when
# capturing a multi-line value). Kept broad on purpose.
_STOP_LABELS = [
    'Clause Type', 'Extracted Clause', 'Summary', 'Risk Type', 'Clause Reference',
    'Risk Level', 'Likelihood', 'Potential Consequence', 'Clause',
    'Recommended Action', 'Priority', 'Category',
]


def _field(label, block, multiline=False):
    """Extract a labeled field value from a block.

    Tolerant of optional bullet/number prefix, optional '**bold**' around the
    label, and ':' or '-' as the separator. When ``multiline`` is True, captures
    until the next recognized field label (or end of block).
    """
    lab = label.replace(' ', r'\s+')
    head = r'(?:[-*]|\d+[.)])?\s*(?:\*\*)?\s*' + lab + r'\s*(?:\*\*)?\s*' + _SEP + r'\s*\[?'
    if multiline:
        stops = '|'.join(
            r'(?:[-*]|\d+[.)])?\s*(?:\*\*)?\s*' + s.replace(' ', r'\s+') + r'\s*(?:\*\*)?\s*' + _SEP
            for s in _STOP_LABELS
        )
        m = re.search(head + r'(.*?)(?=\n\s*(?:' + stops + r')|\Z)', block, re.S)
    else:
        m = re.search(head + r'(.*?)\]?\s*$', block, re.M)
    if not m:
        return None
    return m.group(1).strip().strip('[]').rstrip('-* \n').strip()


# Synonym vocabularies for canonicalizing free-text risk levels / likelihoods.
# Real LLM output phrases these many ways; matching on whole words (see
# _has_word) avoids substring traps like 'insignificant' containing
# 'significant', or 'improbable' containing 'probable'.
_LEVEL_HIGH = ('critical', 'severe', 'very high', 'high', 'extreme',
               'significant', 'substantial', 'major', 'serious', 'grave', 'elevated')
_LEVEL_LOW = ('very low', 'low', 'minor', 'negligible', 'minimal', 'immaterial',
              'trivial', 'nominal', 'insignificant', 'slight')
_LEVEL_MEDIUM = ('medium', 'moderate', 'average', 'intermediate')

_LIKE_UNLIKELY = ('unlikely', 'rare', 'improbable', 'seldom', 'remote', 'doubtful')
_LIKE_LIKELY = ('likely', 'probable', 'certain', 'frequent', 'expected',
                'anticipated', 'foreseeable', 'foreseen', 'often')
_LIKE_POSSIBLE = ('possible', 'occasional', 'conceivable', 'sometimes')

# Prefixes that cancel the positive stem that follows. 'not a' is its own prefix
# so 'not a significant' is removed whole. Longest phrases are matched first.
# 'unlikely' is not in this list: it is a real low-likelihood word, not a
# negated 'likely'. 'no material risk' is stripped so it cannot raise severity
# even though 'material' itself is not a high-severity stem.
_NEGATION_PREFIXES = ('not a', 'not', 'no', 'non', 'less', 'never', 'without')
_NEGATION_EXTRAS = ('no material risk', 'not a material risk')


def _build_negated_phrases():
    """Every negated form of a positive likelihood or severity stem."""
    stems = tuple(dict.fromkeys((*_LEVEL_HIGH, *_LIKE_LIKELY)))
    phrases = {f'{prefix} {stem}' for stem in stems for prefix in _NEGATION_PREFIXES}
    phrases.update(_NEGATION_EXTRAS)
    return tuple(sorted(phrases, key=len, reverse=True))


_NEGATED_PHRASES = _build_negated_phrases()
_NEGATED_RES = tuple(re.compile(r'\b' + re.escape(phrase) + r'\b') for phrase in _NEGATED_PHRASES)


def _has_word(text, words):
    """True if any of ``words`` appears as a whole word in ``text``."""
    return any(re.search(r'\b' + re.escape(w) + r'\b', text) for w in words)


def _scoring_text(value):
    """Lowercase ``value``, flatten separators, and drop negated phrases.

    Hyphens and dashes become spaces, and repeated whitespace collapses, so
    'not-likely' and 'not  likely' are the same phrase as 'not likely'. What
    remains is what likelihood and severity are scored from: the same result as
    the clause with those phrases deleted. The word 'unlikely' is kept, so a
    sentence that says it is still Unlikely.
    """
    cleaned = value.lower()
    cleaned = re.sub(r'[\u2010-\u2015\-]+', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    # 'un-likely' flattens to 'un likely'. Join it back into the low-likelihood
    # word so the later 'likely' check cannot read it as Likely.
    cleaned = re.sub(r'\bun likely\b', 'unlikely', cleaned)
    for pattern in _NEGATED_RES:
        cleaned = pattern.sub(' ', cleaned)
    return cleaned


def _canon_level(value, default='Medium'):
    """Normalize a risk level / impact to one of High / Medium / Low."""
    if not value:
        return default
    s = _scoring_text(value)
    # Low first so negations like 'insignificant' or 'very low' win over the
    # High/Medium tokens they contain.
    if _has_word(s, _LEVEL_LOW):
        return 'Low'
    if _has_word(s, _LEVEL_HIGH):
        return 'High'
    if _has_word(s, _LEVEL_MEDIUM):
        return 'Medium'
    return default


def _canon_likelihood(value, default='Possible'):
    """Normalize a likelihood to one of Unlikely / Possible / Likely."""
    if not value:
        return default
    # 'unlikely' contains 'likely' — check the negatives first, after negated
    # phrases have been removed so a leftover positive stem cannot win.
    s = _scoring_text(value)
    if _has_word(s, _LIKE_UNLIKELY):
        return 'Unlikely'
    if _has_word(s, _LIKE_LIKELY):
        return 'Likely'
    if _has_word(s, _LIKE_POSSIBLE):
        return 'Possible'
    return default


# --- Shared derivations (used by both the markdown parsers and the JSON path) -

# ISO 31000 / IEC 31010 style risk matrix. Rows are the consequence/impact
# level, columns are the likelihood; each cell is the 0-10 risk score used across
# the dashboard. Encoding it as an explicit, documented table (instead of an
# ad-hoc multiplier) keeps the scoring auditable and defensible, per ISO 31000's
# emphasis on a documented likelihood x consequence method.
RISK_MATRIX = {
    'High':   {'Unlikely': 6, 'Possible': 8, 'Likely': 9},
    'Medium': {'Unlikely': 4, 'Possible': 5, 'Likely': 6},
    'Low':    {'Unlikely': 2, 'Possible': 3, 'Likely': 3},
}

# Relative weight of each severity band, used to prioritize the most severe risks
# in the weighted index and the "top risks" selection.
RISK_LEVEL_WEIGHTS = {'High': 3, 'Medium': 2, 'Low': 1}


def _score_for(level, likelihood):
    """0-10 risk score from the documented likelihood x consequence matrix."""
    row = RISK_MATRIX.get(level, RISK_MATRIX['Medium'])
    return row.get(likelihood, row['Possible'])


def _priority_for(level):
    return {'High': 'Urgent', 'Medium': 'High', 'Low': 'Medium'}.get(level, 'Medium')


def _category_for(action_text):
    t = (action_text or '').lower()
    if any(w in t for w in ('negotiate', 'increase', 'reduce', 'remove cap')):
        return 'Negotiation'
    if any(w in t for w in ('compliance', 'audit', 'gdpr', 'insurance')):
        return 'Compliance'
    if any(w in t for w in ('monitor', 'track', 'review period')):
        return 'Monitoring'
    return 'Legal Review'


def _impact_for(text):
    t = (text or '').lower()
    if any(w in t for w in ('liability', 'indemnif', 'data breach', 'gdpr', 'penalty')):
        return 'High'
    if any(w in t for w in ('confidential', 'force majeure', 'minor')):
        return 'Low'
    return 'Medium'


def _pick_ci(d, *keys, default=''):
    """Case-/separator-insensitive lookup over a dict's keys."""
    norm = {k.lower().replace('_', '').replace(' ', ''): v for k, v in d.items()}
    for k in keys:
        v = norm.get(k)
        if v not in (None, ''):
            return v
    return default


def parse_key_clauses(raw_markdown):
    """Parse LLM markdown output for key clauses into structured dicts."""
    clauses = []
    blocks = _split_blocks(raw_markdown, 'Clause Type')
    for block in blocks:
        clause = {
            'id': '',  # assigned sequentially over surviving clauses below
            'clauseType': 'General',
            'section': '',
            'extractedClause': '',
            'summary': '',
            'riskImpact': 'Medium'
        }
        clause['clauseType'] = _field('Clause Type', block) or 'General'
        clause['extractedClause'] = _field('Extracted Clause', block, multiline=True) or ''
        clause['summary'] = _field('Summary', block, multiline=True) or ''

        # Pull a section/article identifier from the clause text if present.
        # Require a leading digit so field labels like "Clause Type" aren't
        # matched. Recognizes '§ 8.1', abbreviations ('Sec.', 'Art.'), and
        # schedule/exhibit/appendix references in addition to the long forms.
        sec = re.search(
            r'(?:§\s*\d[\dA-Za-z.]*'
            r'|\b(?:Sections?|Secs?\.?|Articles?|Arts?\.?|Clauses?'
            r'|Schedules?|Exhibits?|Appendix|Appendices)\s+\d[\dA-Za-z.]*)',
            block,
        )
        if sec:
            clause['section'] = sec.group(0).rstrip('.')

        # Infer risk impact from keywords
        clause['riskImpact'] = _impact_for(clause['extractedClause'] + ' ' + clause['summary'])

        if clause['extractedClause'] or clause['summary']:
            clause['id'] = f'KC-{len(clauses) + 1:03d}'
            clauses.append(clause)

    if not clauses and raw_markdown.strip():
        clauses.append({
            'id': 'KC-001',
            'clauseType': 'General',
            'section': '',
            'extractedClause': raw_markdown[:500],
            'summary': 'Auto-extracted clause content',
            'riskImpact': 'Medium'
        })
    return clauses


def parse_risk_assessment(raw_text):
    """Parse LLM risk assessment report into structured dicts."""
    risks = []
    blocks = _split_blocks(raw_text, 'Risk Type')
    for block in blocks:
        risk = {
            'riskType': 'General',
            'clauseReference': '',
            'riskLevel': 'Medium',
            'likelihood': 'Possible',
            'potentialConsequence': '',
            'score': 5
        }
        risk['riskType'] = _field('Risk Type', block) or 'General'
        risk['clauseReference'] = _field('Clause Reference', block) or ''
        # Normalize so the numeric score AND the HTML's level/likelihood filters agree.
        risk['riskLevel'] = _canon_level(_field('Risk Level', block))
        risk['likelihood'] = _canon_likelihood(_field('Likelihood', block))
        risk['potentialConsequence'] = _field('Potential Consequence', block, multiline=True) or ''
        risk['score'] = _score_for(risk['riskLevel'], risk['likelihood'])

        if risk['potentialConsequence'] or risk['clauseReference']:
            risks.append(risk)

    if not risks and raw_text.strip():
        risks.append({
            'riskType': 'General',
            'clauseReference': 'See full report',
            'riskLevel': 'Medium',
            'likelihood': 'Possible',
            'potentialConsequence': raw_text[:300],
            'score': 5
        })
    return risks


def parse_recommended_actions(raw_text):
    """Parse LLM recommended actions into structured dicts."""
    actions = []
    blocks = _split_blocks(raw_text, 'Clause')
    for block in blocks:
        action = {
            'clause': '',
            'riskLevel': 'Medium',
            'action': '',
            'priority': 'Medium',
            'category': 'Legal Review'
        }
        action['clause'] = _field('Clause', block) or ''
        action['riskLevel'] = _canon_level(_field('Risk Level', block))
        action['action'] = _field('Recommended Action', block, multiline=True) or ''

        action['priority'] = _priority_for(action['riskLevel'])
        action['category'] = _category_for(action['action'])

        if action['clause'] or action['action']:
            actions.append(action)

    if not actions and raw_text.strip():
        actions.append({
            'clause': 'General',
            'riskLevel': 'Medium',
            'action': raw_text[:300],
            'priority': 'Medium',
            'category': 'Legal Review'
        })
    return actions


# --- Structured (JSON) path -------------------------------------------------
# When the pipeline nodes emit structured lists (preferred), consume them
# directly and skip the brittle markdown parsing. Each normalizer is tolerant of
# LLM key casing / synonyms and reuses the same canonicalizers as the parsers,
# so the dashboard shape is identical regardless of which path produced it.

def _clauses_from_data(items):
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        extracted = str(_pick_ci(it, 'extractedclause', 'clause', 'text', default=''))
        summary = str(_pick_ci(it, 'summary', 'description', default=''))
        raw_impact = _pick_ci(it, 'riskimpact', 'impact', 'risklevel', 'severity', default='')
        impact = _canon_level(raw_impact) if raw_impact else _impact_for(extracted + ' ' + summary)
        if extracted or summary:
            out.append({
                'id': f'KC-{len(out) + 1:03d}',
                'clauseType': str(_pick_ci(it, 'clausetype', 'type', default='General')) or 'General',
                'section': str(_pick_ci(it, 'section', 'reference', default='')),
                'extractedClause': extracted,
                'summary': summary,
                'riskImpact': impact,
            })
    return out


def _risks_from_data(items):
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        ref = str(_pick_ci(it, 'clausereference', 'reference', 'clause', default=''))
        conseq = str(_pick_ci(it, 'potentialconsequence', 'consequence', 'impact', default=''))
        level = _canon_level(_pick_ci(it, 'risklevel', 'level', 'severity', default=''))
        like = _canon_likelihood(_pick_ci(it, 'likelihood', 'probability', default=''))
        if conseq or ref:
            out.append({
                'riskType': str(_pick_ci(it, 'risktype', 'type', 'category', default='General')) or 'General',
                'clauseReference': ref,
                'riskLevel': level,
                'likelihood': like,
                'potentialConsequence': conseq,
                'score': _score_for(level, like),
            })
    return out


def _actions_from_data(items):
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        clause = str(_pick_ci(it, 'clause', 'clausereference', 'reference', default=''))
        action = str(_pick_ci(it, 'recommendedaction', 'action', 'recommendation', default=''))
        level = _canon_level(_pick_ci(it, 'risklevel', 'level', 'severity', default=''))
        if clause or action:
            out.append({
                'clause': clause,
                'riskLevel': level,
                'action': action,
                'priority': str(_pick_ci(it, 'priority', default='')) or _priority_for(level),
                'category': str(_pick_ci(it, 'category', default='')) or _category_for(action),
            })
    return out


def _structured_list(state, *keys):
    """Return the first state value under keys that is a non-empty list, else None."""
    for k in keys:
        v = state.get(k)
        if isinstance(v, list) and v:
            return v
    return None


def _norm_key(*parts):
    """Whitespace-collapsed, lowercased join used to detect duplicate entries."""
    return '\u0001'.join(
        re.sub(r'\s+', ' ', str(p if p is not None else '')).strip().lower()
        for p in parts
    )


def _dedupe(items, keyfn):
    """Drop later items whose key repeats an earlier one, preserving order."""
    seen = set()
    out = []
    for it in items:
        k = keyfn(it)
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def _dedupe_risks_by_highest_score(risks):
    """Collapse repeated risks, keeping the highest-severity assessment."""
    indexes = {}
    out = []
    for risk in risks:
        key = _norm_key(risk['riskType'], risk['clauseReference'], risk['potentialConsequence'])
        rank = (risk.get('score', 0), RISK_LEVEL_WEIGHTS.get(risk.get('riskLevel'), 1))
        if key in indexes:
            existing = out[indexes[key]]
            existing_rank = (
                existing.get('score', 0),
                RISK_LEVEL_WEIGHTS.get(existing.get('riskLevel'), 1),
            )
            if rank > existing_rank:
                out[indexes[key]] = risk
            continue
        indexes[key] = len(out)
        out.append(risk)
    return out


def compute_risk_score(risks):
    """Compute overall 0-10 risk score from parsed risks (flat mean)."""
    if not risks:
        return 0
    return round(sum(r['score'] for r in risks) / len(risks), 1)


def compute_weighted_risk_score(risks):
    """Severity-weighted 0-10 index that emphasizes the most severe risks.

    Unlike the flat mean (:func:`compute_risk_score`), each risk contributes in
    proportion to its severity band (High=3, Medium=2, Low=1), so a handful of
    catastrophic risks are not diluted by many minor ones. This reflects
    ISO 31000's prioritization intent, where worst-case exposure drives
    treatment decisions.
    """
    if not risks:
        return 0
    acc = 0.0
    total_w = 0
    for r in risks:
        w = RISK_LEVEL_WEIGHTS.get(r.get('riskLevel'), 1)
        acc += r.get('score', 0) * w
        total_w += w
    return round(acc / total_w, 1) if total_w else 0


def top_risks(risks, n=3):
    """Return the ``n`` highest-priority risks, by score then severity weight."""
    ordered = sorted(
        risks,
        key=lambda r: (r.get('score', 0), RISK_LEVEL_WEIGHTS.get(r.get('riskLevel'), 1)),
        reverse=True,
    )
    return ordered[:n]


# --- Configurable playbook (deterministic) ----------------------------------
# A playbook encodes the firm's standard position per clause type: preferred
# wording, an acceptable fallback, and a walk-away trigger, plus whether the
# clause must always escalate. It is versioned data under ``playbooks/<type>.json``
# so scoring reflects your standard rather than a generic template.

_BUILTIN_PLAYBOOK = {'version': 'builtin', 'entries': []}

# Action bands from a 0-10 score, highest threshold first (ISO 31000 style:
# accept / negotiate to fallback / escalate / walk away).
_BANDS = ((9, 'Walk'), (7, 'Escalate'), (4, 'Negotiate'), (0, 'Accept'))


def load_playbook(contract_type='default'):
    """Load ``playbooks/<contract_type>.json``, falling back to default then builtin.

    Always returns a dict with a ``version`` and an ``entries`` list; never raises.
    """
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'playbooks')
    seen = []
    for name in (contract_type, 'default'):
        if name in seen:
            continue
        seen.append(name)
        path = os.path.join(base, f'{name}.json')
        if os.path.isfile(path):
            try:
                with open(path, encoding='utf-8') as f:
                    pb = json.load(f)
                if isinstance(pb, dict) and isinstance(pb.get('entries'), list):
                    pb.setdefault('version', 'unversioned')
                    return pb
            except (OSError, ValueError):
                continue
    return dict(_BUILTIN_PLAYBOOK)


def match_playbook_entry(risk, playbook):
    """Return the first playbook entry whose matchers appear in the risk, else None."""
    hay = (str(risk.get('riskType', '')) + ' ' + str(risk.get('clauseReference', ''))).lower()
    for entry in playbook.get('entries', []):
        matchers = entry.get('match', [])
        if isinstance(matchers, str):
            matchers = [matchers]
        if any(str(m).lower() in hay for m in matchers if str(m).strip()):
            return entry
    return None


def _band_for(score, mandatory_escalation=False):
    """Map a 0-10 risk score to an action band; escalate at minimum when required."""
    band = 'Accept'
    for threshold, name in _BANDS:
        if score >= threshold:
            band = name
            break
    if mandatory_escalation and band in ('Accept', 'Negotiate'):
        return 'Escalate'
    return band


def _apply_playbook(risks, playbook):
    """Enrich each risk with its playbook reference, action band, and escalation flag."""
    version = playbook.get('version', 'builtin')
    for r in risks:
        entry = match_playbook_entry(r, playbook)
        mandatory = bool(entry.get('mandatoryEscalation', False)) if entry else False
        r['mandatoryEscalation'] = mandatory
        r['band'] = _band_for(r.get('score', 0), mandatory)
        r['playbookRef'] = (
            {'id': entry.get('id', ''), 'version': version} if entry else None
        )
    return risks


# --- Reasoning + source citation (deterministic) ----------------------------

def _verify_citation(quote, source_text):
    """True if ``quote`` appears in ``source_text`` (whitespace- and case-insensitive)."""
    if not quote or not source_text:
        return False
    q = re.sub(r'\s+', ' ', str(quote)).strip().lower()
    s = re.sub(r'\s+', ' ', str(source_text)).strip().lower()
    return bool(q) and q in s


def _reference_contains_exact_token(reference, token):
    """True when ``token`` appears in ``reference`` as a complete normalized token."""
    ref = re.sub(r'\s+', ' ', str(reference)).strip().lower()
    tok = re.sub(r'\s+', ' ', str(token)).strip().lower()
    if not ref or not tok:
        return False
    pattern = rf'(?<![a-z0-9.]){re.escape(tok)}(?![a-z0-9.])'
    return re.search(pattern, ref) is not None


def _find_source_clause(risk, clauses):
    """Best-effort match of a risk to the clause it references (by section, then type)."""
    ref = str(risk.get('clauseReference', '')).lower()
    if not ref:
        return None
    for c in clauses:
        sec = c.get('section', '')
        if _reference_contains_exact_token(ref, sec):
            return c
    for c in clauses:
        ct = c.get('clauseType', '')
        if _reference_contains_exact_token(ref, ct):
            return c
    return None


def _attach_reasoning(risks, clauses):
    """Add a deterministic rationale and a verified source citation to each risk.

    Call after :func:`_apply_playbook` so the rationale can reference the band.
    """
    for r in risks:
        pb = r.get('playbookRef') or {}
        pb_note = f" {r.get('band', '')} per playbook '{pb['id']}'." if pb.get('id') else f" {r.get('band', '')}."
        r['rationale'] = (
            f"{r.get('riskLevel', 'Medium')} severity, {r.get('likelihood', 'Possible')} likelihood "
            f"(score {r.get('score', 0)}/10).{pb_note}"
        )
        src = _find_source_clause(r, clauses)
        if src and src.get('extractedClause'):
            quote = str(src['extractedClause'])[:200]
            r['citations'] = [{
                'clauseId': src.get('id', ''),
                'clauseReference': r.get('clauseReference', ''),
                'quote': quote,
                'verified': _verify_citation(quote, src['extractedClause']),
            }]
        else:
            r['citations'] = []
    return risks


# --- Reliability: deterministic confidence + human-review (abstention) --------
# Production risk tooling must say how much to trust each score and when to defer
# to a human (design doc Section 10; EU AI Act / NIST human-oversight, Section 11).
# These signals are fully deterministic (no model): they read observable facts
# about each already-enriched risk -- whether its citation was verified, whether
# a source clause and playbook entry were found, and whether required fields are
# present -- so the same input always yields the same confidence and review flag.

# (penalty, reason) applied when a given signal is missing/weak. Weights sum to
# 1.30 so that a risk failing every check floors at 0.0 confidence.
_CONF_NO_CITATION = (0.30, 'no source citation')
_CONF_CITATION_UNVERIFIED = (0.25, 'citation not verified against clause text')
_CONF_NO_SOURCE_CLAUSE = (0.25, 'no matching source clause')
_CONF_OUT_OF_PLAYBOOK = (0.20, 'clause type not covered by playbook')
_CONF_NO_CONSEQUENCE = (0.15, 'missing potential consequence')
_CONF_NO_REFERENCE = (0.15, 'missing clause reference')


def _confidence_level(score):
    """Bucket a 0-1 confidence score into High / Medium / Low."""
    if score >= 0.75:
        return 'High'
    if score >= 0.5:
        return 'Medium'
    return 'Low'


def assess_risk_confidence(risk, source_clause, playbook_entry):
    """Return a deterministic confidence assessment for a single risk.

    Args:
        risk: an enriched risk dict (after playbook + reasoning passes).
        source_clause: the clause matched by :func:`_find_source_clause`, or None.
        playbook_entry: the entry matched by :func:`match_playbook_entry`, or None.

    Returns a dict ``{'score', 'level', 'reviewRequired', 'reasons'}`` where score
    is in [0, 1]. ``reviewRequired`` is True when confidence is low, the risk is
    unlinked/out-of-playbook, or the playbook mandates escalation -- i.e. the tool
    abstains to human review rather than presenting an untrustworthy verdict.
    """
    score = 1.0
    reasons = []
    cites = risk.get('citations') or []
    if not cites:
        score -= _CONF_NO_CITATION[0]
        reasons.append(_CONF_NO_CITATION[1])
    elif not any(c.get('verified') for c in cites):
        score -= _CONF_CITATION_UNVERIFIED[0]
        reasons.append(_CONF_CITATION_UNVERIFIED[1])
    if source_clause is None:
        score -= _CONF_NO_SOURCE_CLAUSE[0]
        reasons.append(_CONF_NO_SOURCE_CLAUSE[1])
    if playbook_entry is None:
        score -= _CONF_OUT_OF_PLAYBOOK[0]
        reasons.append(_CONF_OUT_OF_PLAYBOOK[1])
    if not str(risk.get('potentialConsequence', '')).strip():
        score -= _CONF_NO_CONSEQUENCE[0]
        reasons.append(_CONF_NO_CONSEQUENCE[1])
    if not str(risk.get('clauseReference', '')).strip():
        score -= _CONF_NO_REFERENCE[0]
        reasons.append(_CONF_NO_REFERENCE[1])
    score = round(max(0.0, min(1.0, score)), 2)

    review = (
        score < 0.5
        or source_clause is None
        or playbook_entry is None
        or bool(risk.get('mandatoryEscalation'))
    )
    if risk.get('mandatoryEscalation'):
        reasons.append('mandatory escalation - human sign-off required')
    return {
        'score': score,
        'level': _confidence_level(score),
        'reviewRequired': review,
        'reasons': reasons,
    }


# Optional post-hoc calibration. A calibrator is a JSON file produced by
# ``python -m calibration.evaluate --fit --save-calibrator <path>`` once real
# labeled data exists (see docs/confidence_calibration.md). It is applied here
# with a self-contained histogram-binning lookup rather than importing the
# calibration package, because this module is fetched standalone into Colab.
# With no calibrator file present, behaviour is unchanged (raw scores only).
CALIBRATOR_ENV = 'CONTRACT_RISK_CALIBRATOR'


def load_calibrator(path=None):
    """Return a calibrator dict ``{'n_bins', 'bin_accuracy'}`` or None.

    Resolution order: explicit ``path`` -> ``$CONTRACT_RISK_CALIBRATOR`` ->
    ``calibration/calibrator.json`` next to this module. Missing or malformed
    files yield None so dashboard generation never fails because of calibration.
    """
    candidate = path or os.environ.get(CALIBRATOR_ENV) or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'calibration', 'calibrator.json')
    if not os.path.isfile(candidate):
        return None
    try:
        with open(candidate, encoding='utf-8') as f:
            data = json.load(f)
        n_bins = int(data['n_bins'])
        bins = list(data['bin_accuracy'])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if n_bins < 1 or len(bins) != n_bins or not data.get('fitted', True):
        return None
    normalized_bins = []
    for value in bins:
        if value is None:
            normalized_bins.append(None)
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        score = float(value)
        if not 0.0 <= score <= 1.0:
            return None
        normalized_bins.append(score)
    return {'n_bins': n_bins, 'bin_accuracy': normalized_bins}


def calibrate_score(score, calibrator):
    """Map a raw 0-1 confidence through a histogram-binning calibrator.

    Mirrors ``calibration.calibrator.HistogramBinningCalibrator.predict`` (a
    parity test keeps them in sync): the score's bin maps to that bin's learned
    accuracy, or to the bin midpoint if the bin was never seen during fitting.
    """
    n = calibrator['n_bins']
    c = min(1.0, max(0.0, float(score)))
    i = min(n - 1, int(c * n))
    learned = calibrator['bin_accuracy'][i]
    if learned is None:
        return round((i + 0.5) / n, 4)
    return round(float(learned), 4)


def attach_confidence(risks, clauses, playbook, calibrator=None):
    """Attach a ``confidence`` object to each risk; return an aggregate summary.

    Run after dedupe so the aggregate counts reflect the final risk set. The
    per-risk source clause and playbook entry are recomputed here (both are pure
    functions) to keep this pass independent of the reasoning/playbook passes.

    When a ``calibrator`` is supplied, each confidence also gets a
    ``calibratedScore`` and the summary reports ``meanCalibratedConfidence``.
    The raw ``score`` and the review policy are left unchanged.
    """
    review_count = 0
    total = 0.0
    total_cal = 0.0
    for r in risks:
        source = _find_source_clause(r, clauses)
        entry = match_playbook_entry(r, playbook)
        conf = assess_risk_confidence(r, source, entry)
        if calibrator is not None:
            conf['calibratedScore'] = calibrate_score(conf['score'], calibrator)
            total_cal += conf['calibratedScore']
        r['confidence'] = conf
        total += conf['score']
        if conf['reviewRequired']:
            review_count += 1
    n = len(risks)
    mean = round(total / n, 2) if n else 1.0
    summary = {
        'riskCount': n,
        'reviewRequired': review_count,
        'meanConfidence': mean,
        'confidenceLevel': _confidence_level(mean),
        'autoAcceptable': n > 0 and review_count == 0,
        'calibrated': calibrator is not None,
    }
    if calibrator is not None:
        summary['meanCalibratedConfidence'] = round(total_cal / n, 2) if n else 1.0
    return summary


# --- Contract metadata + timeline --------------------------------------------
# The pipeline's metadata-extraction node writes a dict into state['metadata'].
# It is LLM output, so key casing / shape varies; normalize defensively.

_DATE_FORMATS = ('%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y', '%B %d, %Y', '%b %d, %Y',
                 '%d %B %Y', '%d %b %Y', '%Y/%m/%d')


def _parse_date(value):
    """Parse a date string in a few common formats; return a datetime or None."""
    if not value or not isinstance(value, str):
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def _build_metadata(state):
    """Normalize state['metadata'] (LLM output) into the CONTRACT_DATA shape."""
    raw = state.get('metadata') or {}
    # Index the raw dict by a normalized key (lowercase, no spaces/underscores).
    norm = {k.lower().replace('_', '').replace(' ', ''): v
            for k, v in raw.items()} if isinstance(raw, dict) else {}

    def pick(*keys, default=''):
        for k in keys:
            v = norm.get(k)
            if v:
                return v
        return default

    parties = pick('parties', default=[])
    if isinstance(parties, str):
        parties = [p.strip() for p in re.split(r'\s+(?:and|&)\s+|,', parties) if p.strip()]
    if not isinstance(parties, list):
        parties = []

    # Legacy fallback: dates may arrive as flat state keys from older callers.
    eff = pick('effectivedate', 'startdate', 'commencementdate',
               default=state.get('effective_date', ''))
    exp = pick('expirationdate', 'enddate', 'expirydate', 'terminationdate',
               default=state.get('expiration_date', ''))

    return {
        'title': pick('title', 'agreement', default='Contract Analysis Results') or 'Contract Analysis Results',
        'parties': parties,
        'effectiveDate': eff or '',
        'expirationDate': exp or '',
        'contractValue': pick('contractvalue', 'value', 'totalvalue', default='N/A') or 'N/A',
        'jurisdiction': pick('jurisdiction', 'governinglaw', 'law', default='N/A') or 'N/A',
    }


def _build_timeline(metadata):
    """Derive timeline events from extracted dates. Empty if no dates parse."""
    eff = _parse_date(metadata.get('effectiveDate', ''))
    exp = _parse_date(metadata.get('expirationDate', ''))
    events = []
    if eff:
        events.append((eff, 'Contract Effective', 'milestone'))
    if eff and exp and exp > eff:
        mid = eff + (exp - eff) / 2
        events.append((mid, 'Mid-term Review', 'review'))
        notice = exp - timedelta(days=30)
        if notice > eff:
            events.append((notice, 'Termination Notice (30d)', 'deadline'))
    if exp:
        events.append((exp, 'Contract Expiration', 'milestone'))
    events.sort(key=lambda e: e[0])
    return [{'date': d.strftime('%Y-%m-%d'), 'event': name, 'type': kind}
            for d, name, kind in events]


def build_contract_data(state):
    """Score and enrich a pipeline state into the dashboard data object.

    This is the same object embedded in the dashboard. It does not include the
    append-only score audit. ``generate_dashboard_html`` writes that log to its
    own file so this object, and the dashboard security hash, stay unchanged.
    """
    # Prefer structured data from the pipeline nodes; fall back to parsing the
    # LLM markdown when structured lists aren't present (backward compatible).
    cl = _structured_list(state, 'key_clauses_data', 'clauses')
    clauses = _clauses_from_data(cl) if cl else []
    if not clauses:
        clauses = parse_key_clauses(state.get('key_clauses', ''))

    rk = _structured_list(state, 'risk_assessment_data', 'risk_assessment', 'risks')
    risks = _risks_from_data(rk) if rk else []
    if not risks:
        risks = parse_risk_assessment(state.get('risk_assessment_report', ''))

    ac = _structured_list(state, 'recommended_actions_data', 'actions')
    actions = _actions_from_data(ac) if ac else []
    if not actions:
        actions = parse_recommended_actions(state.get('recommended_actions', ''))

    # Collapse duplicate entries the LLM sometimes repeats (same clause/risk/
    # action emitted twice), then renumber clause ids so they stay sequential
    # after any removals. Do this before citations are attached so cited IDs
    # always point at the final clause list rendered in the dashboard.
    clauses = _dedupe(clauses, lambda c: _norm_key(c['clauseType'], c['extractedClause'], c['summary']))
    for i, c in enumerate(clauses, 1):
        c['id'] = f'KC-{i:03d}'
    risks = _dedupe_risks_by_highest_score(risks)
    actions = _dedupe(actions, lambda a: _norm_key(a['clause'], a['action']))

    # Apply the configurable playbook (action band + escalation) and attach the
    # deterministic reasoning + verified source citation to each risk.
    playbook = load_playbook(state.get('contract_type', 'default'))
    _apply_playbook(risks, playbook)
    _attach_reasoning(risks, clauses)

    # Attach a deterministic confidence + human-review signal to each risk and
    # summarize how much of the assessment can be auto-accepted vs needs review.
    reliability = attach_confidence(risks, clauses, playbook, load_calibrator())

    overall_score = compute_risk_score(risks)
    metadata = _build_metadata(state)
    timeline = _build_timeline(metadata)

    return {
        'metadata': metadata,
        'keyClauses': clauses,
        'riskAssessment': risks,
        'recommendedActions': actions,
        'pipelineStages': [
            {'name': 'Extract Chunks', 'icon': '📄', 'status': 'complete', 'duration': 'done'},
            {'name': 'Embed & Store', 'icon': '🔢', 'status': 'complete', 'duration': 'done'},
            {'name': 'Key Clauses', 'icon': '📋', 'status': 'complete', 'duration': 'done'},
            {'name': 'Risk Assessment', 'icon': '⚠️', 'status': 'complete', 'duration': 'done'},
            {'name': 'Actions', 'icon': '✅', 'status': 'complete', 'duration': 'done'}
        ],
        'timeline': timeline,
        'overallRiskScore': overall_score,
        'weightedRiskScore': compute_weighted_risk_score(risks),
        'topRisks': top_risks(risks, 3),
        'playbookVersion': playbook.get('version', 'builtin'),
        'reliability': reliability,
    }


def generate_dashboard_html(
    state,
    input_path=None,
    timestamp=None,
    audit_path=None,
    cases_dir=None,
    input_sha256=None,
):
    """
    Main entry point: takes pipeline State dict, returns complete HTML string.

    Args:
        state: dict with keys 'key_clauses' (str), 'risk_assessment_report' (str),
               'recommended_actions' (str). All are markdown/text from LLM output.
               Notebook state also carries ``pdf_path``.
        input_path: contract file to hash into the score audit. Falls back to
               ``state['pdf_path']`` when that file exists.
        timestamp: UTC time ``YYYY-MM-DDTHH:MM:SSZ`` stored on new score records.
               The current UTC second is used when this is omitted.
        audit_path: where to append the score audit. Defaults to a
               ``.score-audit.jsonl`` file next to the contract.
        cases_dir: golden case directory for the manifest hash. A missing
               directory is recorded as ``unavailable``.
        input_sha256: SHA-256 of the contract bytes already analyzed. When this
               is set, the audit stores it instead of hashing ``input_path``
               again. Local analysis passes the digest of the snapshot it read
               before the model calls.

    Returns:
        Complete HTML string for the visualization dashboard. The audit is a
        separate file, never part of the HTML, so the security hash is unchanged.
    """
    data = build_contract_data(state)

    # Read the HTML template
    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  'contract_visualization.html')
    with open(template_path) as f:
        template = f.read()

    data_json = json.dumps(data, indent=2, ensure_ascii=False)

    # Escape for safe embedding inside an HTML <script> block. Contract text is
    # attacker-influenceable (it comes from a parsed PDF), so this is required.
    # We follow the OWASP / Flask ``htmlsafe_json_dumps`` pattern and escape every
    # character that could let contract text break out of the script context, as
    # Unicode escapes that JSON.parse still decodes back to the original text:
    #  - '<' and '>' so a literal '</script>' (or any '<...>') cannot close the
    #    <script> element or introduce markup during HTML parsing, before JS runs.
    #  - '&' to avoid ambiguous HTML entities in the embedded source.
    #  - "'" so the payload is also safe if embedded in a single-quoted context.
    #  - U+2028 / U+2029 are valid in JSON but are JS line terminators that would
    #    break the string literal.
    data_json = (data_json
                 .replace('<', '\\u003c')
                 .replace('>', '\\u003e')
                 .replace('&', '\\u0026')
                 .replace("'", '\\u0027')
                 .replace('\u2028', '\\u2028')
                 .replace('\u2029', '\\u2029'))

    # Inject the data into the non-executable JSON data island
    # (<script type="application/json" id="contract-data">). Keeping the data out
    # of the executable <script> lets the page ship a strict, hash-based
    # Content-Security-Policy (the code block stays byte-stable) and follows the
    # OWASP-recommended "JSON in a data island" pattern. Use a function as the
    # replacement so backslashes / group references in contract text are inserted
    # literally (a plain string replacement would interpret '\\1', '\\g', etc.).
    pattern = r'(<script type="application/json" id="contract-data">\n).*?(\n</script>)'
    result, n = re.subn(
        pattern,
        lambda m: f'{m.group(1)}{data_json}{m.group(2)}',
        template,
        flags=re.DOTALL,
    )
    if n != 1:
        raise ValueError(
            f"Expected exactly one contract-data island in the template, found {n}. "
            "The template may have changed; the dashboard cannot be generated safely."
        )

    _record_dashboard_audit(
        state,
        input_path=input_path,
        timestamp=timestamp,
        audit_path=audit_path,
        cases_dir=cases_dir,
        input_sha256=input_sha256,
    )
    return result


# --- Append-only score audit (CRA-01 In-3) and human override (In-4) ---------
# One record is appended per scored risk. A human override appends its own
# later record for the same risk id. That record carries the reviewer, the old
# band, the new band, a non-empty rationale, a timestamp, and the original
# score record id. Nothing in this log rewrites or deletes an earlier record.
# The log is not embedded in the dashboard HTML.

SCORER_VERSION = 'scorer-3x3-1'
# Stored when the notebook download has no golden/cases directory. This is not
# a hash, and it is not the hash of an empty directory.
GOLDEN_MANIFEST_UNAVAILABLE = 'unavailable'
AUDIT_SUFFIX = '.score-audit.jsonl'
AUDIT_EVENTS = ('score', 'override')
AUDIT_FIELDS = (
    'recordId',
    'event',
    'riskId',
    'clauseReference',
    'likelihood',
    'severity',
    'score',
    'playbookVersion',
    'scorerVersion',
    'goldenManifestHash',
    'inputFileSha256',
    'timestamp',
    'rule',
)
# Human-override fields. Score records do not carry these, so an override cannot
# be written by editing the original line. ``oldBand`` is the action band of the
# stored score (the same thresholds as the dashboard, without a later mandatory
# escalation, which the score record does not store).
OVERRIDE_FIELDS = (
    'reviewer',
    'oldBand',
    'newBand',
    'rationale',
    'originalRecordId',
)
ACTION_BANDS = tuple(name for _threshold, name in _BANDS)
_TIMESTAMP_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
_SHA256_RE = re.compile(r'^[0-9a-f]{64}$')
_AUDIT_NONEMPTY = (
    'riskId',
    'likelihood',
    'severity',
    'playbookVersion',
    'scorerVersion',
)
# An override may change the score, the matrix inputs, the timestamp, and the
# rule. These fields identify the contract and the scoring setup, so they have
# to match the original score record.
_PROVENANCE_FIELDS = (
    'clauseReference',
    'inputFileSha256',
    'goldenManifestHash',
    'playbookVersion',
    'scorerVersion',
)


def _read_regular_file(path: str) -> bytes:
    """Read a regular file without following a symlink and without writing it."""
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, 'rb') as handle:
        return handle.read()


def file_sha256(path: str) -> str:
    """SHA-256 of a regular file's exact bytes. Does not follow links or write it."""
    return hashlib.sha256(_read_regular_file(path)).hexdigest()


def golden_manifest_hash(cases_dir: str | None = None) -> str:
    """SHA-256 of the sorted golden case files (name and bytes).

    Only ``golden/cases/*.json`` is hashed. Each name and body is length-prefixed
    so one file cannot be rearranged into two. Audit fixtures live elsewhere, so
    committing a fixture does not change this manifest. A missing cases directory
    returns ``unavailable`` rather than the hash of an empty manifest or an error.
    The notebook download does not include ``golden/cases``.
    """
    if cases_dir is None:
        cases_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'golden', 'cases')
    if not os.path.isdir(cases_dir):
        return GOLDEN_MANIFEST_UNAVAILABLE
    digest = hashlib.sha256()
    for name in sorted(os.listdir(cases_dir)):
        if not name.endswith('.json'):
            continue
        name_bytes = name.encode('utf-8')
        body = _read_regular_file(os.path.join(cases_dir, name))
        digest.update(len(name_bytes).to_bytes(4, 'big'))
        digest.update(name_bytes)
        digest.update(len(body).to_bytes(8, 'big'))
        digest.update(body)
    return digest.hexdigest()


def _copy_json(value):
    """Deep copy through JSON so a caller cannot change a stored record."""
    return json.loads(json.dumps(value))


def _require_timestamp(value: object) -> None:
    if not isinstance(value, str) or not _TIMESTAMP_RE.fullmatch(value):
        raise ValueError('timestamp must be YYYY-MM-DDTHH:MM:SSZ')
    try:
        datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ')
    except ValueError:
        raise ValueError('timestamp must be a real UTC time') from None


def _require_matrix_score(record: dict) -> None:
    """A score event must cite the matrix cell that produced its score."""
    severity = record['severity']
    likelihood = record['likelihood']
    score = record['score']
    expected = RISK_MATRIX.get(severity, {}).get(likelihood)
    if score != expected:
        raise ValueError(
            f'score {score} does not match RISK_MATRIX[{severity}][{likelihood}] = {expected}'
        )
    needle = f'RISK_MATRIX[{severity}][{likelihood}] = {score}'
    if needle not in record['rule']['evidence']:
        raise ValueError('rule.evidence must include the matrix cell that produced the score')


def _fields_for(event: str) -> tuple[str, ...]:
    """Field order for one stored line. Score lines stay on ``AUDIT_FIELDS``."""
    if event == 'override':
        return AUDIT_FIELDS[:-1] + OVERRIDE_FIELDS + AUDIT_FIELDS[-1:]
    return AUDIT_FIELDS


def _is_legacy_override(record: dict) -> bool:
    """An override saved before the reviewer fields existed.

    Those lines have only the original score-record fields. Reopening the file
    must keep them as written. A new override cannot use this shape.
    """
    return record.get('event') == 'override' and not any(field in record for field in OVERRIDE_FIELDS)


def _validate_override_shape(record: dict) -> None:
    """Reject an override that has no reviewer, no rationale, or the same band."""
    reviewer = record['reviewer']
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError('reviewer must be a non-empty string')
    rationale = record['rationale']
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValueError('rationale must be a non-empty string')
    original_id = record['originalRecordId']
    if not isinstance(original_id, str) or not original_id.strip():
        raise ValueError('unknown original record id')
    allowed = ', '.join(ACTION_BANDS)
    old_band = record['oldBand']
    new_band = record['newBand']
    if old_band not in ACTION_BANDS:
        raise ValueError(f'oldBand must be one of {allowed}')
    if new_band not in ACTION_BANDS:
        raise ValueError(f'newBand must be one of {allowed}')
    if new_band == old_band:
        raise ValueError('override band is unchanged')


def _validate_audit_body(record: dict, *, from_replay: bool = False) -> None:
    legacy = from_replay and _is_legacy_override(record)
    required: tuple[str, ...]
    if legacy or record.get('event') not in AUDIT_EVENTS:
        required = AUDIT_FIELDS
    else:
        required = _fields_for(record['event'])
    missing = [field for field in required if field != 'recordId' and field not in record]
    if missing:
        raise ValueError('audit record missing fields: ' + ', '.join(missing))
    if record['event'] not in AUDIT_EVENTS:
        raise ValueError(f'event must be one of {AUDIT_EVENTS}')
    if record['event'] == 'override' and not legacy:
        _validate_override_shape(record)
    _require_timestamp(record['timestamp'])
    manifest = record['goldenManifestHash']
    if manifest != GOLDEN_MANIFEST_UNAVAILABLE and (
        not isinstance(manifest, str) or not _SHA256_RE.fullmatch(manifest)
    ):
        raise ValueError(
            'goldenManifestHash must be a lowercase SHA-256 hex digest or '
            f'{GOLDEN_MANIFEST_UNAVAILABLE!r}'
        )
    digest = record['inputFileSha256']
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise ValueError('inputFileSha256 must be a lowercase SHA-256 hex digest')
    if not isinstance(record['clauseReference'], str):
        raise ValueError('clauseReference must be a string')
    for field in _AUDIT_NONEMPTY:
        if not isinstance(record[field], str) or not record[field].strip():
            raise ValueError(f'{field} must be a non-empty string')
    if type(record['score']) is not int or not 0 <= record['score'] <= 10:
        raise ValueError('score must be an integer from 0 to 10')
    rule = record['rule']
    if not isinstance(rule, dict):
        raise ValueError('rule must be an object with id and evidence')
    if not isinstance(rule.get('id'), str) or not rule['id'].strip():
        raise ValueError('rule.id must be a non-empty string')
    evidence = rule.get('evidence')
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError('rule.evidence must be a non-empty string')
    if record['event'] == 'score':
        _require_matrix_score(record)


class AuditLog:
    """Append-only list of score records and later override records.

    ``append`` copies the record, assigns the next record id, and stores it.
    There is no update or delete. ``records`` returns copies, so changing a
    returned dict does not change the log. ``append_human_override`` adds one
    override record that points at the original score record.
    """

    def __init__(self) -> None:
        self._records: list[dict] = []

    def records(self) -> list[dict]:
        return [_copy_json(record) for record in self._records]

    def append(self, record: dict) -> dict:
        return self._append(record, from_replay=False)

    def _append(self, record: dict, *, from_replay: bool) -> dict:
        if not isinstance(record, dict):
            raise TypeError('audit record must be a dict')
        if 'recordId' in record:
            raise ValueError('recordId is assigned by the log and cannot be set by the caller')
        stored = _copy_json(record)
        legacy = from_replay and _is_legacy_override(stored)
        _validate_audit_body(stored, from_replay=from_replay)
        same_risk = [item for item in self._records if item['riskId'] == stored['riskId']]
        if stored['event'] == 'score' and any(item['event'] == 'score' for item in same_risk):
            raise ValueError(
                f"score record for {stored['riskId']} already exists; append an override instead"
            )
        if stored['event'] == 'override' and not any(item['event'] == 'score' for item in same_risk):
            raise ValueError(f"override requires a score record for {stored['riskId']}")
        if stored['event'] == 'override':
            _require_matching_provenance(stored, same_risk)
            # An older override line has no original-record link. Leave it as
            # stored. A new override must still point at the score record.
            if not legacy:
                _require_original_link(stored, self._records)
        stored['recordId'] = f'AR-{len(self._records) + 1:03d}'
        fields = AUDIT_FIELDS if legacy else _fields_for(stored['event'])
        ordered = {key: stored[key] for key in fields}
        self._records.append(ordered)
        return _copy_json(ordered)

    def append_human_override(
        self,
        *,
        original_record_id: str,
        reviewer: str,
        new_band: str,
        rationale: str,
        timestamp: str,
        clause_reference: str | None = None,
        input_file_sha256: str | None = None,
    ) -> dict:
        """Append one human override. The original score record is not edited.

        ``oldBand`` is taken from the original score. ``new_band`` must be a
        different action band. A different clause or contract hash is rejected
        by the same provenance check as any other override.
        """
        original = _score_record_by_id(self._records, original_record_id)
        evidence = rationale if isinstance(rationale, str) and rationale.strip() else 'unset'
        body = {
            'event': 'override',
            'riskId': original['riskId'],
            'clauseReference': (
                original['clauseReference'] if clause_reference is None else clause_reference
            ),
            'likelihood': original['likelihood'],
            'severity': original['severity'],
            'score': original['score'],
            'playbookVersion': original['playbookVersion'],
            'scorerVersion': original['scorerVersion'],
            'goldenManifestHash': original['goldenManifestHash'],
            'inputFileSha256': (
                original['inputFileSha256'] if input_file_sha256 is None else input_file_sha256
            ),
            'timestamp': timestamp,
            'reviewer': reviewer,
            'oldBand': _band_for(original['score']),
            'newBand': new_band,
            'rationale': rationale,
            'originalRecordId': original['recordId'],
            'rule': {'id': 'human_override', 'evidence': evidence},
        }
        return self.append(body)

    @classmethod
    def load(cls, path: str) -> "AuditLog":
        """Reopen an append-only log. Earlier lines are not rewritten."""
        raw = _read_regular_file(path)
        log = cls()
        if raw == b'':
            return log
        text = raw.decode('utf-8')
        if not text.endswith('\n'):
            raise ValueError('audit log must be newline-terminated JSON lines')
        # Split only on the newline this writer adds. Clause text may contain
        # U+2028 / U+2029 / U+0085, which str.splitlines() would treat as breaks.
        lines = text.split('\n')[:-1]
        for line in lines:
            if line == '':
                raise ValueError('audit log has a blank line')
            _replay_audit_line(log, line)
        return log

    def write_new(self, path: str, already: int) -> None:
        """Append records added after ``already``. Never rewrites earlier lines."""
        if already < 0 or already > len(self._records):
            raise ValueError('audit write position is outside the log')
        payload = b''.join(_audit_line(record) for record in self._records[already:])
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(path, flags, 0o600)
        try:
            start = os.lseek(fd, 0, os.SEEK_END)
            view = memoryview(payload)
            try:
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise OSError('audit log write made no progress')
                    view = view[written:]
            except OSError:
                os.ftruncate(fd, start)
                raise
        finally:
            os.close(fd)


def _audit_line(record: dict) -> bytes:
    """One JSON line. Line-separator characters stay inside the JSON string."""
    line = json.dumps(record, ensure_ascii=False)
    line = line.replace('\u2028', '\\u2028').replace('\u2029', '\\u2029').replace('\u0085', '\\u0085')
    return (line + '\n').encode('utf-8')


def _replay_audit_line(log: AuditLog, line: str) -> None:
    parsed = json.loads(line)
    if not isinstance(parsed, dict):
        raise ValueError('audit log line must be a JSON object')
    record_id = parsed.pop('recordId', None)
    if not isinstance(record_id, str):
        raise ValueError('audit log line is missing recordId')
    stored = log._append(parsed, from_replay=True)
    if stored['recordId'] != record_id:
        raise ValueError('audit log record ids are not sequential')


def _require_matching_provenance(stored: dict, same_risk: list[dict]) -> None:
    """Reject an override that names the same risk but a different contract or clause."""
    original = next(item for item in same_risk if item['event'] == 'score')
    mismatched = [field for field in _PROVENANCE_FIELDS if stored.get(field) != original.get(field)]
    if mismatched:
        raise ValueError(
            'override provenance does not match the score record for '
            f"{stored['riskId']}: " + ', '.join(mismatched)
        )


def _score_record_by_id(records: list[dict], original_record_id: object) -> dict:
    """Return the score record with this id, or reject an unknown id."""
    if not isinstance(original_record_id, str) or not original_record_id.strip():
        raise ValueError('unknown original record id')
    original = next(
        (
            item for item in records
            if item['recordId'] == original_record_id and item['event'] == 'score'
        ),
        None,
    )
    if original is None:
        raise ValueError(f'unknown original record id: {original_record_id}')
    return original


def _require_original_link(stored: dict, records: list[dict]) -> None:
    """The override must point at the score record for the same risk.

    ``oldBand`` has to be the band of that stored score, so a caller cannot
    rewrite what the original line said.
    """
    original = _score_record_by_id(records, stored.get('originalRecordId'))
    if original['riskId'] != stored['riskId']:
        raise ValueError(f"unknown original record id: {stored.get('originalRecordId')}")
    expected = _band_for(original['score'])
    if stored['oldBand'] != expected:
        raise ValueError(
            f"oldBand {stored['oldBand']} does not match the original score band {expected}"
        )


def _score_audit_body(index, risk, playbook_version, manifest_hash, input_sha256, timestamp):
    """One score record for a risk that has already been scored."""
    severity = risk.get('riskLevel', '')
    likelihood = risk.get('likelihood', '')
    score = risk.get('score')
    row = RISK_MATRIX.get(severity, {})
    expected = row.get(likelihood)
    if score != expected:
        raise ValueError(
            f'score {score} does not match RISK_MATRIX[{severity}][{likelihood}] = {expected}'
        )
    entry = (risk.get('playbookRef') or {}).get('id') or 'none'
    return {
        'event': 'score',
        'riskId': f'RK-{index:03d}',
        'clauseReference': str(risk.get('clauseReference', '')),
        'likelihood': likelihood,
        'severity': severity,
        'score': score,
        'playbookVersion': playbook_version,
        'scorerVersion': SCORER_VERSION,
        'goldenManifestHash': manifest_hash,
        'inputFileSha256': input_sha256,
        'timestamp': timestamp,
        'rule': {
            'id': 'RISK_MATRIX',
            'evidence': (
                f'RISK_MATRIX[{severity}][{likelihood}] = {score} '
                f'after canonical severity and likelihood. Playbook entry: {entry}.'
            ),
        },
    }


def build_score_audit(
    state,
    input_path: str,
    timestamp: str,
    cases_dir: str | None = None,
    input_sha256: str | None = None,
) -> AuditLog:
    """Append one score record per scored risk.

    ``input_path`` is the contract file for this ``state``. The caller pairs
    them. When ``input_sha256`` is omitted, this function hashes that file and
    does not write it. Pass ``input_sha256`` when those bytes were already read
    for analysis, so a later edit is not recorded in their place. ``timestamp``
    is required (``YYYY-MM-DDTHH:MM:SSZ``) so a test run does not depend on the
    clock.
    """
    _require_timestamp(timestamp)
    if input_sha256 is None:
        digest = file_sha256(input_path)
    elif isinstance(input_sha256, str) and _SHA256_RE.fullmatch(input_sha256):
        digest = input_sha256
    else:
        raise ValueError('input_sha256 must be a lowercase SHA-256 hex digest')
    manifest = golden_manifest_hash(cases_dir)
    data = build_contract_data(state)
    playbook_version = str(data.get('playbookVersion') or 'builtin')
    log = AuditLog()
    for index, risk in enumerate(data['riskAssessment'], start=1):
        log.append(_score_audit_body(
            index=index,
            risk=risk,
            playbook_version=playbook_version,
            manifest_hash=manifest,
            input_sha256=digest,
            timestamp=timestamp,
        ))
    return log


def audit_sidecar_path(output_path: str) -> str:
    """Score-audit file that sits next to a dashboard, outside the HTML."""
    root, _ext = os.path.splitext(output_path)
    return root + AUDIT_SUFFIX


def _utc_timestamp() -> str:
    return datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def _paths_are_same(left: str, right: str) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return os.path.realpath(left) == os.path.realpath(right)


def _hashable_contract_path(path: str) -> str:
    """Regular file whose bytes are hashed.

    A symlink is resolved once so a CLI or notebook path that already reads
    through a link can still be audited. ``file_sha256`` then refuses to follow
    any link that remains.
    """
    if not os.path.islink(path):
        return path
    candidate = os.path.realpath(path)
    if not os.path.isfile(candidate):
        raise OSError(f'contract path must be a regular file: {path}')
    return candidate


def _contract_path_for_audit(state, input_path: str | None) -> str | None:
    if input_path is not None:
        if not isinstance(input_path, str) or not input_path:
            raise ValueError('input_path must be the contract file path')
        return input_path
    if isinstance(state, dict):
        candidate = state.get('pdf_path')
        if isinstance(candidate, str) and candidate and os.path.isfile(candidate):
            return candidate
    return None


def _default_audit_path(contract_path: str) -> str:
    directory = os.path.dirname(os.path.abspath(contract_path))
    stem, _ext = os.path.splitext(os.path.basename(contract_path))
    return os.path.join(directory, stem + AUDIT_SUFFIX)


def _prepare_lock_byte(fd: int) -> None:
    """Give a Windows byte-range lock one byte to hold. POSIX locks need none.

    Two processes can both see an empty file and each append a byte. The lock
    is always the first byte, so the extra byte does not weaken it.
    """
    if os.lseek(fd, 0, os.SEEK_END) == 0:
        os.write(fd, b'\0')
    os.lseek(fd, 0, os.SEEK_SET)


def _win_lock(fd: int, mode_name: str) -> None:
    """Byte-range lock. ``mode_name`` is ``LK_LOCK`` or ``LK_UNLCK``."""
    if _msvcrt is None:
        raise OSError('Windows file locking is unavailable')
    # Attribute access fails type-checking here: the Windows msvcrt stub is not
    # available on the Linux type-check run.
    locking = getattr(_msvcrt, 'locking')  # noqa: B009
    locking(fd, getattr(_msvcrt, mode_name), 1)


def _acquire_exclusive_lock(fd: int) -> None:
    """Block until this process holds the lock on ``fd``.

    The Windows lock waits about ten seconds, then raises. The score file is
    left unchanged, so a timed-out build does not reuse a record id.
    """
    if os.name == 'nt':
        _prepare_lock_byte(fd)
        _win_lock(fd, 'LK_LOCK')
        return
    if _fcntl is None:
        raise OSError('POSIX file locking is unavailable')
    _fcntl.flock(fd, _fcntl.LOCK_EX)


def _release_exclusive_lock(fd: int) -> None:
    """Release a lock taken by ``_acquire_exclusive_lock``."""
    if os.name == 'nt':
        os.lseek(fd, 0, os.SEEK_SET)
        _win_lock(fd, 'LK_UNLCK')
        return
    if _fcntl is None:
        raise OSError('POSIX file locking is unavailable')
    _fcntl.flock(fd, _fcntl.LOCK_UN)


class _InterprocessLock:
    """Exclusive lock across processes for one audit sidecar.

    The lock file is kept after the write. Removing it would let a new file
    race with a process that still holds the old one. Closing the descriptor
    releases the lock, including when the process crashes. ``O_NOFOLLOW``
    refuses a symlink at the lock path on systems that support that flag.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._fd: int | None = None

    def __enter__(self) -> "_InterprocessLock":
        flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(self._path, flags, 0o600)
        try:
            _acquire_exclusive_lock(fd)
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd
        return self

    def __exit__(self, *_exc: object) -> None:
        fd = self._fd
        self._fd = None
        if fd is None:
            return
        try:
            _release_exclusive_lock(fd)
        finally:
            os.close(fd)


def _snapshot_sha256(state, input_sha256: str | None) -> str | None:
    """Digest of the bytes already analyzed, when the caller captured one.

    An explicit digest wins. Otherwise a local-model run stores the digest on
    ``state['local_llm']['input_file_sha256']`` from the single read that fed
    the model. A value that is present but not a lowercase SHA-256 digest is
    refused. Callers that did not snapshot still hash the path.
    """
    if input_sha256 is not None:
        return input_sha256
    if not isinstance(state, dict):
        return None
    info = state.get('local_llm')
    if not isinstance(info, dict) or 'input_file_sha256' not in info:
        return None
    snapshot = info.get('input_file_sha256')
    if isinstance(snapshot, str) and _SHA256_RE.fullmatch(snapshot):
        return snapshot
    raise ValueError('input_sha256 must be a lowercase SHA-256 hex digest')


def _record_dashboard_audit(
    state, input_path, timestamp, audit_path, cases_dir, input_sha256=None,
) -> None:
    """Append score records for this analysis. Does not touch the HTML."""
    contract_path = _contract_path_for_audit(state, input_path)
    if contract_path is None:
        return
    when = timestamp if timestamp is not None else _utc_timestamp()
    destination = audit_path if audit_path is not None else _default_audit_path(contract_path)
    hashed = _hashable_contract_path(contract_path)
    if _paths_are_same(hashed, destination) or _paths_are_same(contract_path, destination):
        raise ValueError('audit log path must be different from the contract file')
    fresh = build_score_audit(
        state,
        hashed,
        when,
        cases_dir=cases_dir,
        input_sha256=_snapshot_sha256(state, input_sha256),
    )
    # Load, check, and append under one lock. O_APPEND only places the write;
    # without this, two builds can read the same history and reuse record ids.
    with _InterprocessLock(destination + '.lock'):
        if os.path.exists(destination):
            log = AuditLog.load(destination)
        else:
            log = AuditLog()
        already = len(log.records())
        seen = {record['riskId']: record for record in log.records() if record['event'] == 'score'}
        for record in fresh.records():
            prior = seen.get(record['riskId'])
            body = {key: value for key, value in record.items() if key != 'recordId'}
            if prior is None:
                log.append(body)
                continue
            # Keep the original score. The same contract and clause with the same
            # score is a re-run. A different contract, clause, or score is refused
            # before any new line is written, so the page is not returned either.
            mismatched = [
                field for field in _PROVENANCE_FIELDS if prior.get(field) != record.get(field)
            ]
            if mismatched:
                raise ValueError(
                    f"score record for {record['riskId']} already exists with different provenance: "
                    + ', '.join(mismatched)
                )
            changed = [
                field for field in ('likelihood', 'severity', 'score', 'rule')
                if prior.get(field) != record.get(field)
            ]
            if changed:
                raise ValueError(
                    f"score record for {record['riskId']} already exists with a different score: "
                    + ', '.join(changed)
                )
        log.write_new(destination, already)


def write_human_override(
    audit_path: str,
    *,
    original_record_id: str,
    reviewer: str,
    new_band: str,
    rationale: str,
    timestamp: str,
    clause_reference: str | None = None,
    input_file_sha256: str | None = None,
) -> dict:
    """Append one human override to an existing score history file.

    Uses the same lock and append-only writer as scoring. A rejected override
    (missing rationale, unknown original id, unchanged band, or a different
    contract or clause) does not add a line. The original score line is never
    rewritten.
    """
    if not os.path.isfile(audit_path):
        raise ValueError(f'unknown original record id: {original_record_id}')
    with _InterprocessLock(audit_path + '.lock'):
        log = AuditLog.load(audit_path)
        already = len(log.records())
        stored = log.append_human_override(
            original_record_id=original_record_id,
            reviewer=reviewer,
            new_band=new_band,
            rationale=rationale,
            timestamp=timestamp,
            clause_reference=clause_reference,
            input_file_sha256=input_file_sha256,
        )
        log.write_new(audit_path, already)
        return stored


if __name__ == '__main__':
    # Demo: generate dashboard with sample data (reads existing HTML template)
    print("generate_dashboard.py - Contract Visualization Generator")
    print("Import and call generate_dashboard_html(state) with pipeline output.")
    print("See module docstring for usage examples.")
