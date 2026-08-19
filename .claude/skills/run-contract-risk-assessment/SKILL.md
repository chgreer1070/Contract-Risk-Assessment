---
name: run-contract-risk-assessment
description: Build, run, and drive the Contract Risk Assessment dashboard. Use when asked to start the app, take a screenshot of the dashboard, run its smoke test, or verify visualizations render correctly.
---

Self-contained HTML dashboard (`contract_visualization.html`) with 12 interactive Chart.js visualizations. Drive it via `.claude/skills/run-contract-risk-assessment/smoke.mjs` — a Playwright-based smoke script that serves the HTML locally, launches headless Chromium, takes screenshots, and validates all components rendered.

## Prerequisites

```bash
npm install playwright chart.js
npx playwright install chromium --with-deps
```

## Run (agent path)

The smoke script starts a local HTTP server (port 8787), rewrites the Chart.js CDN URL to serve from `node_modules/` (CDN not available in headless environments), opens the dashboard in headless Chromium, and runs validation checks.

```bash
node .claude/skills/run-contract-risk-assessment/smoke.mjs
```

Screenshots land in `/tmp/shots/`:

| File | Contents |
|---|---|
| `dashboard-dark.png` | Full dashboard, dark theme |
| `dashboard-light.png` | Full dashboard, light theme |
| `dashboard-filtered.png` | Dashboard with High Risk filter active |

Expected output:
```
Server on http://localhost:8787
Launching headless Chromium...
Opening dashboard...
Screenshot: /tmp/shots/dashboard-dark.png
Stat cards rendered: 6
Clause cards rendered: 8
Chart canvases: 5
Pipeline nodes: 5
Clause card expand: PASS
Search filter (Liability): 1 cards visible
Theme toggle: now light
Screenshot: /tmp/shots/dashboard-light.png
High Risk filter: 3 cards visible
Screenshot: /tmp/shots/dashboard-filtered.png
Risk table rows: 12 (expected 12)
Risk table score sort (desc): PASS (first=9)
Risk table High filter: 4 visible (expected 4)
Risk table checks: PASS
Console errors: none

ALL CHECKS PASSED
```

## Run (human path)

Open `contract_visualization.html` directly in any browser. Requires internet for Chart.js CDN.

```bash
open contract_visualization.html   # macOS
xdg-open contract_visualization.html  # Linux
```

## Direct invocation — generate from pipeline output

```python
from generate_dashboard import generate_dashboard_html

state = {
    'key_clauses': '...',           # LLM markdown output
    'risk_assessment_report': '...', # LLM risk report
    'recommended_actions': '...',    # LLM actions
}
html = generate_dashboard_html(state)
with open('output.html', 'w') as f:
    f.write(html)
```

## Gotchas

- **Chart.js CDN fails in headless/offline containers.** The smoke script works around this by serving `node_modules/chart.js/dist/chart.umd.js` locally and rewriting the CDN URL in the HTML response. When opening directly in a browser, you need internet for the CDN.
- **Port 8787 must be free.** The smoke script's HTTP server binds to 8787. If another process holds it, the script fails silently. Kill it with `lsof -ti:8787 | xargs kill` before re-running.
- **`file://` protocol won't work for smoke tests.** Browser security blocks CDN script tags from `file://` origins. Always use the HTTP server path for headless testing.
- **Tooltip binding happens once.** If you re-render components (e.g., after theme toggle recreates charts), the heatmap/treemap/timeline tooltips remain bound to the original DOM elements. Stat card tooltips survive because those elements persist across theme changes.

## Troubleshooting

- **`ERR_MODULE_NOT_FOUND: Cannot find package 'playwright'`**: Run `npm install playwright` in the project root.
- **`page.waitForSelector: Timeout exceeded` on `.stat-card`**: Chart.js didn't load. Ensure `npm install chart.js` was run and `node_modules/chart.js/dist/chart.umd.js` exists.
- **`page.waitForSelector: Timeout exceeded` on `.clause-card`**: The DOMContentLoaded listener crashed partway through — likely a Chart.js rendering error. Check `console --errors` output or add `page.on('pageerror', ...)` before navigation.
- **Charts render but are blank/white**: Theme mismatch. The chart colors are computed from `data-theme` attribute at render time. If the attribute is missing, all colors default to dark-theme values on a light background.
