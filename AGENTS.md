# AGENTS.md — guide for AI coding agents

Read this before changing code. It reflects the repository as it is now; if you
change how something is built, tested, or run, update this file in the same PR.

## What this repository is

An AI contract-risk assessment tool with two halves:

- **Model / notebook half (GPU, Colab):** `Contract_Risk_Assessment.ipynb` runs a
  LangGraph pipeline (clause extraction → risk assessment → recommended actions)
  plus a Streamlit UI; `Finetuned_Model_for_Legal_Chatbot.ipynb` fine-tunes the
  chatbot. These need a GPU and model weights and are **out of scope for the CPU
  test loop** (lint excludes `*.ipynb`).
- **Dashboard half (CPU, pure Python + static HTML):** `generate_dashboard.py`
  parses the pipeline's markdown output, scores and enriches risks (ISO 31000
  matrix, playbooks, reasoning, citations, deterministic confidence + human-review
  flags), and injects JSON into `contract_visualization.html` (Chart.js, no build
  step). The `calibration/` package evaluates/corrects confidence trustworthiness.

Important coupling: the notebook `wget`s `generate_dashboard.py`,
`contract_visualization.html`, and `playbooks/` **standalone** from GitHub `main`.
Therefore `generate_dashboard.py` must stay importable on its own — never make it
import `calibration/` or `tests/`.

## Layout

| path | purpose |
|---|---|
| `generate_dashboard.py` | the Python bridge (parsers, scoring, playbooks, confidence, HTML injection) |
| `contract_visualization.html` | dashboard template; embeds a `contract-data` JSON island + a CSP with a hashed inline script |
| `playbooks/` | JSON negotiation playbooks (`default`, contract-type variants) |
| `calibration/` | confidence-calibration harness (metrics, calibrator, dataset, `evaluate` + `export_labels` CLIs) |
| `golden/` | answer-key regression fixtures |
| `tests/` | pytest suite (parsers, scoring, playbooks, confidence, calibration, CSP hash, a11y markup) |
| `docs/` | architecture + calibration design docs |
| `.claude/skills/run-contract-risk-assessment/` | Playwright `smoke.mjs` (render + assertions) and `a11y.mjs` (axe-core WCAG scan) |
| `.github/workflows/ci.yml` | CI: ruff, mypy, pytest + coverage gate, smoke, a11y |

## Setup

```bash
pip install -r requirements-dev.txt     # pytest, pytest-cov, ruff, mypy
npm install                             # playwright, chart.js, @axe-core/playwright
npx playwright install chromium         # browser for smoke + a11y
```

`requirements.txt` is the GPU/Colab runtime stack; you do **not** need it for the
CPU loop.

## Verify (run all of these before you call a change done)

```bash
ruff check .                                                     # lint
mypy generate_dashboard.py calibration                           # types
pytest --cov=generate_dashboard --cov-report=term-missing --cov-fail-under=90
node .claude/skills/run-contract-risk-assessment/smoke.mjs       # renders + asserts the dashboard
node .claude/skills/run-contract-risk-assessment/a11y.mjs        # 0 serious/critical axe violations
```

These are exactly the CI jobs. Everything runs on CPU with no network or model.

## Conventions that bite

- **CSP hash:** any edit to the inline `<script>` in `contract_visualization.html`
  changes its SHA-384. Recompute it and update the `Content-Security-Policy` meta
  tag; `tests/test_csp_hash.py` fails otherwise.
- **Escaping:** all data rendered into HTML goes through `esc()` / `escAttr()`;
  JSON is embedded via the data island, never string-concatenated into JS.
- **Sample data in the HTML:** the template ships with an embedded demo dataset.
  If you add fields the UI reads, regenerate that island by running the demo data
  through `generate_dashboard_html` so the static page still renders them.
- **Determinism:** scoring and confidence are pure functions of the input; do not
  add randomness or network calls. New behaviour needs tests in `tests/`.
- **Accessibility:** interactive elements need labels/roles/keyboard support;
  status changes should use the existing `aria-live` `announce()`.
- **Playbooks** are data, not code: add clause types in `playbooks/*.json` and
  cover them with a golden case.
- **Calibration:** `generate_dashboard.py` applies a calibrator only if
  `calibration/calibrator.json` (or `$CONTRACT_RISK_CALIBRATOR`) exists. Never
  commit a calibrator fitted on the synthetic seed dataset.

## Git workflow

Small, single-purpose branches (`cursor/<name>-...`), a clear commit per logical
change, PR to `main`, merge only when the five checks above are green. Do not
amend or force-push shared branches.

## Cursor Cloud specific instructions

- The environment has Python 3.12, Node 22, and Chromium already installed;
  run `npm install` and `npx playwright install chromium` if `node_modules` or
  the browser is missing (a Playwright upgrade can invalidate the browser).
- The smoke and a11y scripts start their own local HTTP server (they serve
  Chart.js from `node_modules` instead of the CDN), so no dev server is needed.
- For UI changes, verify in the browser and capture screenshots/video as
  evidence; run the a11y scan after any markup change.
- Run the Python suite from the repo root so `tests/` can import
  `generate_dashboard` and `calibration`.
