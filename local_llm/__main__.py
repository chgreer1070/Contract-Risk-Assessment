"""CLI: analyze a contract with a local LM Studio / Ollama model.

    python -m local_llm ping
    python -m local_llm analyze contract.pdf -o dashboard.html
    python -m local_llm analyze contract.txt --provider ollama --model qwen2.5:32b
"""

from __future__ import annotations

import argparse
import sys

from local_llm.client import LocalLLMError, list_models, resolve_endpoint
from local_llm.pipeline import analyze_contract


def _add_endpoint_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument('--provider', default='lmstudio', help='lmstudio (default) or ollama')
    p.add_argument('--base-url', default=None, help='Override API root (default depends on --provider)')
    p.add_argument('--model', default=None, help='Model id (LM Studio often ignores this if one model is loaded)')
    p.add_argument('--api-key', default=None, help='Optional bearer token (usually unused locally)')
    p.add_argument('--timeout', type=float, default=180.0, help='Seconds to wait per model call')


def _cmd_ping(args: argparse.Namespace) -> int:
    ep = resolve_endpoint(args.provider, args.base_url, args.model, args.api_key)
    print(f'Checking {ep["base_url"]} ...')
    names = list_models(ep['base_url'], ep['api_key'], timeout=args.timeout)
    if names:
        print('Reachable. Models advertised:')
        for name in names:
            print(f'  - {name}')
    else:
        print('Reachable, but no model list was returned. Load a model in LM Studio / Ollama and retry.')
    return 0


def _cmd_analyze(args: argparse.Namespace) -> int:
    from generate_dashboard import generate_dashboard_html

    state = analyze_contract(
        args.input,
        provider=args.provider,
        base_url=args.base_url,
        model=args.model,
        api_key=args.api_key,
        timeout=args.timeout,
        max_chars=args.max_chars,
    )
    info = state.get('local_llm') or {}
    if info.get('truncated'):
        print('Note: the contract was longer than --max-chars and was truncated before sending.')
    n_clauses = len(state.get('key_clauses_data') or [])
    n_risks = len(state.get('risk_assessment_data') or [])
    n_actions = len(state.get('recommended_actions_data') or [])
    print(f'Local model ({info.get("model")} @ {info.get("base_url")}): '
          f'{n_clauses} clauses, {n_risks} risks, {n_actions} actions.')
    if n_clauses == 0 and n_risks == 0:
        print('The model replied, but no structured clauses/risks were parsed. '
              'Try a stronger instruct model or check LM Studio logs.', file=sys.stderr)
        return 2
    html = generate_dashboard_html(state)
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'Wrote dashboard to {args.output}')
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description='Analyze a contract with a local LM Studio or Ollama model.',
    )
    sub = ap.add_subparsers(dest='command', required=True)

    ping = sub.add_parser('ping', help='Check that the local model server is reachable')
    _add_endpoint_flags(ping)
    ping.set_defaults(func=_cmd_ping)

    ana = sub.add_parser('analyze', help='Read a contract and write a dashboard HTML file')
    _add_endpoint_flags(ana)
    ana.add_argument('input', help='Path to a .pdf, .txt, or .md contract')
    ana.add_argument('-o', '--output', default='dashboard.html', help='Where to write the dashboard')
    ana.add_argument('--max-chars', type=int, default=100_000,
                     help='Truncate long contracts to this many characters')
    ana.set_defaults(func=_cmd_analyze)

    args = ap.parse_args(argv)
    try:
        return int(args.func(args))
    except LocalLLMError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
