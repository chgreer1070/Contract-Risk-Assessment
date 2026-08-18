#!/usr/bin/env bash
# Idempotent Cloud Agent bootstrap for the Contract Risk Assessment repo.
#
# Scope: the CPU-only development/test loop that CI runs
# (.github/workflows/ci.yml). The GPU model/fine-tuning stack in
# requirements.txt (torch, unsloth, bitsandbytes, milvus, ...) targets a
# Google Colab GPU runtime and is intentionally NOT installed here — it is not
# runnable on a CPU Cloud Agent VM and is not needed for the dashboard/parser
# development loop.
set -euo pipefail

cd "$(dirname "$0")/.."

# Python parser tests: generate_dashboard.py is pure stdlib, so only pytest is
# required (matches the "pip install pytest" step in CI and the README).
python3 -m pip install --user --upgrade pytest

# Node dependencies for the dashboard smoke test (Playwright + Chart.js).
npm install

# Chromium browser plus system libraries for headless dashboard rendering.
npx --yes playwright install --with-deps chromium

echo "Cloud Agent environment ready: pytest + Playwright/Chromium installed."
