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
import re


def parse_key_clauses(raw_markdown):
    """Parse LLM markdown output for key clauses into structured dicts."""
    clauses = []
    blocks = re.split(r'\n(?=[-*]\s*\*\*Clause Type\*\*)', raw_markdown)
    for i, block in enumerate(blocks):
        clause = {
            'id': f'KC-{i+1:03d}',
            'clauseType': 'General',
            'section': '',
            'extractedClause': '',
            'summary': '',
            'riskImpact': 'Medium'
        }
        type_match = re.search(r'\*\*Clause Type\*\*\s*:\s*\[?(.*?)\]?\s*$', block, re.M)
        if type_match:
            clause['clauseType'] = type_match.group(1).strip().strip('[]')

        extract_match = re.search(r'\*\*Extracted Clause\*\*\s*:\s*\[?(.*?)(?=\*\*Summary\*\*|\*\*Clause Type\*\*|\Z)', block, re.S)
        if extract_match:
            clause['extractedClause'] = extract_match.group(1).strip().strip('[]').rstrip('-* \n')

        summary_match = re.search(r'\*\*Summary\*\*\s*:\s*\[?(.*?)(?=\*\*Clause Type\*\*|\*\*Extracted Clause\*\*|\Z)', block, re.S)
        if summary_match:
            clause['summary'] = summary_match.group(1).strip().strip('[]').rstrip('-* \n')

        # Infer risk impact from keywords
        text = (clause['extractedClause'] + ' ' + clause['summary']).lower()
        if any(w in text for w in ['liability', 'indemnif', 'data breach', 'gdpr', 'penalty']):
            clause['riskImpact'] = 'High'
        elif any(w in text for w in ['confidential', 'force majeure', 'minor']):
            clause['riskImpact'] = 'Low'

        if clause['extractedClause'] or clause['summary']:
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
    blocks = re.split(r'\n(?=[-*]\s*\*\*Risk Type\*\*)', raw_text)
    for block in blocks:
        risk = {
            'riskType': 'General',
            'clauseReference': '',
            'riskLevel': 'Medium',
            'likelihood': 'Possible',
            'potentialConsequence': '',
            'score': 5
        }
        type_match = re.search(r'\*\*Risk Type\*\*\s*:\s*\[?(.*?)\]?\s*$', block, re.M)
        if type_match:
            risk['riskType'] = type_match.group(1).strip().strip('[]')

        clause_match = re.search(r'\*\*Clause Reference\*\*\s*:\s*\[?(.*?)\]?\s*$', block, re.M)
        if clause_match:
            risk['clauseReference'] = clause_match.group(1).strip().strip('[]')

        level_match = re.search(r'\*\*Risk Level\*\*\s*:\s*\[?(.*?)\]?\s*$', block, re.M)
        if level_match:
            risk['riskLevel'] = level_match.group(1).strip().strip('[]')

        like_match = re.search(r'\*\*Likelihood\*\*\s*:\s*\[?(.*?)\]?\s*$', block, re.M)
        if like_match:
            risk['likelihood'] = like_match.group(1).strip().strip('[]')

        conseq_match = re.search(r'\*\*Potential Consequence\*\*\s*:\s*\[?(.*?)(?=\*\*Risk Type\*\*|\*\*Clause Reference\*\*|\Z)', block, re.S)
        if conseq_match:
            risk['potentialConsequence'] = conseq_match.group(1).strip().strip('[]').rstrip('-* \n')

        # Compute numeric score
        level_scores = {'High': 8, 'Medium': 5, 'Low': 3}
        like_mult = {'Likely': 1.1, 'Possible': 1.0, 'Unlikely': 0.8}
        base = level_scores.get(risk['riskLevel'], 5)
        mult = like_mult.get(risk['likelihood'], 1.0)
        risk['score'] = min(10, round(base * mult))

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
    blocks = re.split(r'\n(?=[-*]\s*\*\*Clause\*\*|\*\*Clause\*\*)', raw_text)
    for block in blocks:
        action = {
            'clause': '',
            'riskLevel': 'Medium',
            'action': '',
            'priority': 'Medium',
            'category': 'Legal Review'
        }
        clause_match = re.search(r'\*\*Clause\*\*\s*:\s*\[?(.*?)\]?\s*$', block, re.M)
        if clause_match:
            action['clause'] = clause_match.group(1).strip().strip('[]')

        level_match = re.search(r'\*\*Risk Level\*\*\s*:\s*\[?(.*?)\]?\s*$', block, re.M)
        if level_match:
            action['riskLevel'] = level_match.group(1).strip().strip('[]')

        act_match = re.search(r'\*\*Recommended Action\*\*\s*:\s*\[?(.*?)(?=\*\*Clause\*\*|\*\*Risk Level\*\*|\Z)', block, re.S)
        if act_match:
            action['action'] = act_match.group(1).strip().strip('[]').rstrip('-* \n')

        # Infer priority from risk level
        priority_map = {'High': 'Urgent', 'Medium': 'High', 'Low': 'Medium'}
        action['priority'] = priority_map.get(action['riskLevel'], 'Medium')

        # Infer category from action text
        text = action['action'].lower()
        if any(w in text for w in ['negotiate', 'increase', 'reduce', 'remove cap']):
            action['category'] = 'Negotiation'
        elif any(w in text for w in ['compliance', 'audit', 'gdpr', 'insurance']):
            action['category'] = 'Compliance'
        elif any(w in text for w in ['monitor', 'track', 'review period']):
            action['category'] = 'Monitoring'

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


def compute_risk_score(risks):
    """Compute overall 0-10 risk score from parsed risks."""
    if not risks:
        return 0
    return round(sum(r['score'] for r in risks) / len(risks), 1)


def generate_dashboard_html(state):
    """
    Main entry point: takes pipeline State dict, returns complete HTML string.

    Args:
        state: dict with keys 'key_clauses' (str), 'risk_assessment_report' (str),
               'recommended_actions' (str). All are markdown/text from LLM output.

    Returns:
        Complete HTML string for the visualization dashboard.
    """
    clauses = parse_key_clauses(state.get('key_clauses', ''))
    risks = parse_risk_assessment(state.get('risk_assessment_report', ''))
    actions = parse_recommended_actions(state.get('recommended_actions', ''))
    overall_score = compute_risk_score(risks)

    # Read the HTML template
    import os
    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  'contract_visualization.html')
    with open(template_path, 'r') as f:
        template = f.read()

    # Build the data object matching CONTRACT_DATA structure
    data = {
        'metadata': {
            'title': 'Contract Analysis Results',
            'parties': ['Party A', 'Party B'],
            'effectiveDate': state.get('effective_date', ''),
            'expirationDate': state.get('expiration_date', ''),
            'contractValue': 'N/A',
            'jurisdiction': 'N/A'
        },
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
        'timeline': [],
        'overallRiskScore': overall_score
    }

    data_json = json.dumps(data, indent=2, ensure_ascii=False)

    # Replace the embedded CONTRACT_DATA in the template
    pattern = r'const CONTRACT_DATA = \{.*?\n\};'
    replacement = f'const CONTRACT_DATA = {data_json};'
    result = re.sub(pattern, replacement, template, flags=re.DOTALL)

    return result


if __name__ == '__main__':
    # Demo: generate dashboard with sample data (reads existing HTML template)
    print("generate_dashboard.py - Contract Visualization Generator")
    print("Import and call generate_dashboard_html(state) with pipeline output.")
    print("See module docstring for usage examples.")
