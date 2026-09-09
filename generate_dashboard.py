"""
generate_dashboard.py

Converts the output of risk_graph.invoke() into an interactive HTML dashboard.

Usage:
    from generate_dashboard import generate_dashboard_html

    output = risk_graph.invoke(input={'pdf_path': 'contract.pdf'})
    html = generate_dashboard_html(output)

    # Save to file:
    with open('dashboard.html', 'w') as f:
        f.write(html)

    # Or render in Streamlit:
    import streamlit.components.v1 as components
    components.html(html, height=2400, scrolling=True)
"""

import json
import os
import re
from datetime import datetime, timedelta

# --- Tolerant parsing helpers -------------------------------------------------
# Real Mixtral/Mistral output deviates from the prompt's bullet+**bold** template:
# it emits numbered lists, drops bold markers, and adds prose qualifiers. These
# helpers extract fields tolerantly so a single format slip doesn't collapse the
# whole parse into one fallback blob.

def _split_blocks(text, anchor_label):
    """Split text before each occurrence of an anchor field label.

    Tolerates an optional leading bullet ('-'/'*'), numbered prefix ('1.'/'2)'),
    and optional '**bold**' around the label, followed by ':' or '-'.
    """
    lab = anchor_label.replace(' ', r'\s+')
    return re.split(
        r'\n(?=\s*(?:[-*]|\d+[.)])?\s*(?:\*\*)?\s*' + lab + r'\s*(?:\*\*)?\s*[:\-])',
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
    head = r'(?:[-*]|\d+[.)])?\s*(?:\*\*)?\s*' + lab + r'\s*(?:\*\*)?\s*[:\-]\s*\[?'
    if multiline:
        stops = '|'.join(
            r'(?:[-*]|\d+[.)])?\s*(?:\*\*)?\s*' + s.replace(' ', r'\s+') + r'\s*(?:\*\*)?\s*[:\-]'
            for s in _STOP_LABELS
        )
        m = re.search(head + r'(.*?)(?=\n\s*(?:' + stops + r')|\Z)', block, re.S)
    else:
        m = re.search(head + r'(.*?)\]?\s*$', block, re.M)
    if not m:
        return None
    return m.group(1).strip().strip('[]').rstrip('-* \n').strip()


def _canon_level(value, default='Medium'):
    """Normalize a risk level / impact to one of High / Medium / Low."""
    if not value:
        return default
    s = value.lower()
    if any(w in s for w in ('critical', 'severe', 'very high', 'extreme')):
        return 'High'
    if 'high' in s:
        return 'High'
    if any(w in s for w in ('low', 'minor', 'negligible', 'minimal')):
        return 'Low'
    if any(w in s for w in ('medium', 'moderate')):
        return 'Medium'
    return default


def _canon_likelihood(value, default='Possible'):
    """Normalize a likelihood to one of Unlikely / Possible / Likely."""
    if not value:
        return default
    s = value.lower()
    # 'unlikely' contains 'likely' — check the negatives first.
    if any(w in s for w in ('unlikely', 'rare', 'improbable', 'seldom')):
        return 'Unlikely'
    if any(w in s for w in ('likely', 'probable', 'certain', 'frequent', 'expected')):
        return 'Likely'
    if any(w in s for w in ('possible', 'occasional')):
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
        # Require a leading digit so the field label "Clause Type" isn't matched.
        sec = re.search(r'\b(?:Section|Article|Clause)\s+\d[\dA-Za-z.]*', block)
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


def _find_source_clause(risk, clauses):
    """Best-effort match of a risk to the clause it references (by section, then type)."""
    ref = str(risk.get('clauseReference', '')).lower()
    if not ref:
        return None
    for c in clauses:
        sec = str(c.get('section', '')).lower()
        if sec and sec in ref:
            return c
    for c in clauses:
        ct = str(c.get('clauseType', '')).lower()
        if ct and ct in ref:
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


def generate_dashboard_html(state):
    """
    Main entry point: takes pipeline State dict, returns complete HTML string.

    Args:
        state: dict with keys 'key_clauses' (str), 'risk_assessment_report' (str),
               'recommended_actions' (str). All are markdown/text from LLM output.

    Returns:
        Complete HTML string for the visualization dashboard.
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

    # Apply the configurable playbook (action band + escalation) and attach the
    # deterministic reasoning + verified source citation to each risk.
    playbook = load_playbook(state.get('contract_type', 'default'))
    _apply_playbook(risks, playbook)
    _attach_reasoning(risks, clauses)

    overall_score = compute_risk_score(risks)
    metadata = _build_metadata(state)
    timeline = _build_timeline(metadata)

    # Read the HTML template
    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  'contract_visualization.html')
    with open(template_path) as f:
        template = f.read()

    # Build the data object matching CONTRACT_DATA structure
    data = {
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
    }

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

    return result


if __name__ == '__main__':
    # Demo: generate dashboard with sample data (reads existing HTML template)
    print("generate_dashboard.py - Contract Visualization Generator")
    print("Import and call generate_dashboard_html(state) with pipeline output.")
    print("See module docstring for usage examples.")
