"""
Guard against Content-Security-Policy hash drift.

The dashboard ships a strict CSP whose script-src pins the SHA-384 of the inline
code <script> block. If that block is edited without recomputing the hash, the
browser silently blocks the page's own script and the dashboard renders blank.
This test recomputes the hash from the code block and asserts it matches the
hash embedded in the CSP meta tag, so a stale hash fails CI instead of shipping.
"""

import base64
import hashlib
import os
import re

HTML_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'contract_visualization.html',
)


def _code_script_content(html):
    """Return the exact text of the bare code <script> element (what CSP hashes)."""
    open_tag = '<script>'
    i = html.find(open_tag)
    assert i != -1, "bare code <script> not found"
    start = i + len(open_tag)
    end = html.find('</script>', start)
    assert end != -1, "closing </script> not found"
    return html[start:end]


def test_csp_meta_hash_matches_code_script():
    with open(HTML_PATH, encoding='utf-8') as f:
        html = f.read()

    content = _code_script_content(html)
    digest = hashlib.sha384(content.encode('utf-8')).digest()
    expected = 'sha384-' + base64.b64encode(digest).decode()

    # The CSP is the only place a single-quoted sha384 token appears (the Chart.js
    # Subresource-Integrity hash uses double quotes), so this is unambiguous.
    hashes = re.findall(r"'(sha384-[A-Za-z0-9+/=]+)'", html)
    assert len(hashes) == 1, f"expected exactly one CSP script hash, found {len(hashes)}"
    assert hashes[0] == expected, (
        f"CSP hash is stale: meta has {hashes[0]}, code block hashes to {expected}. "
        "Recompute the CSP script-src hash after editing the inline <script>."
    )
