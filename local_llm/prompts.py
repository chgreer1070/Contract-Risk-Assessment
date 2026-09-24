"""Prompts for the four analysis steps (match the Colab notebook's JSON contract)."""

CLAUSES = """You are an AI legal assistant with expertise in contract analysis. Extract and summarize key clauses from the following contract.
Do not include irrelevant information except clauses from the document.

Instructions:
- Identify all relevant clauses related to liabilities, risks, obligations, and rights.
- Provide a concise yet comprehensive summary of each clause.
- Retain legal accuracy while simplifying complex legal language.

Output Format: Return ONLY a JSON array. Each element is an object with keys:
"clauseType" (e.g. Liability, Termination, Confidentiality), "section" (e.g. "Section 8.1" or ""), "extractedClause" (verbatim or closely paraphrased), "summary" (plain-language explanation).
Output the JSON array and nothing else.

Contract Text:
{context}

JSON:
"""

RISKS = """You are an expert in legal risk assessment. Analyze the contract below and identify potential risks.

Instructions:
- Identify and classify risks (e.g., financial, legal, compliance, reputational, operational).
- Assess severity (Low, Medium, High) and likelihood (Unlikely, Possible, Likely).
- Explain potential consequences of each risk.

Output Format: Return ONLY a JSON array. Each element is an object with keys:
"riskType", "clauseReference" (e.g. "Section 8.1" or ""), "riskLevel" (Low, Medium, High), "likelihood" (Unlikely, Possible, Likely), "potentialConsequence" (brief impact).
Output the JSON array and nothing else.

Contract Text:
{context}

JSON:
"""

ACTIONS = """You are a legal expert. Analyze the following risk assessment report and suggest specific actions for each clause:

Risk Assessment Report:
{report}

Return ONLY a JSON array. Each element is an object with keys:
"clause" (clause text or reference), "riskLevel" (Low, Medium, High), "action" (specific recommended action).
Output the JSON array and nothing else.
"""

METADATA = """You are a contract analyst. From the contract text below, extract metadata as STRICT JSON with exactly these keys:
"title" (short agreement name), "parties" (array of party names), "effectiveDate" (YYYY-MM-DD or ""), "expirationDate" (YYYY-MM-DD or ""), "contractValue" (e.g. "$480,000" or ""), "jurisdiction" (governing law / state or "").
Use "" (or [] for parties) when a value is not stated. Output ONLY the JSON object, nothing else.

Contract Text:
{context}

JSON:
"""
