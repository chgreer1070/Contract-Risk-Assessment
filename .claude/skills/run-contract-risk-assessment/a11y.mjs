#!/usr/bin/env node
/**
 * a11y.mjs - Automated accessibility scan of contract_visualization.html.
 *
 * Serves the dashboard locally (Chart.js from node_modules, as smoke.mjs does),
 * renders it in headless Chromium, and runs axe-core against WCAG 2.0/2.1/2.2
 * A and AA rules. Fails (exit 1) on any serious or critical violation.
 *
 *   node .claude/skills/run-contract-risk-assessment/a11y.mjs
 */
import { chromium } from 'playwright';
import axePkg from '@axe-core/playwright';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import { createServer } from 'http';
import { readFile } from 'fs/promises';
import { extname } from 'path';

const AxeBuilder = axePkg.default || axePkg;
const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..', '..', '..');
const PORT = 8789;
const MIME = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json' };

function startServer() {
  return new Promise((res) => {
    const srv = createServer(async (req, resp) => {
      let filePath;
      if (req.url === '/') filePath = resolve(ROOT, 'contract_visualization.html');
      else if (req.url.includes('chart')) filePath = resolve(ROOT, 'node_modules/chart.js/dist/chart.umd.js');
      else filePath = resolve(ROOT, req.url.slice(1));
      try {
        let data = await readFile(filePath, 'utf8');
        if (filePath.endsWith('.html')) {
          data = data.replace(
            /<script src="https:\/\/cdn\.jsdelivr\.net\/npm\/chart\.js@[^"]*"[^>]*><\/script>/,
            '<script src="/chart.umd.js"></script>'
          );
        }
        resp.writeHead(200, { 'Content-Type': MIME[extname(filePath)] || 'text/plain' });
        resp.end(data);
      } catch { resp.writeHead(404); resp.end('Not found'); }
    });
    srv.listen(PORT, () => res(srv));
  });
}

async function main() {
  const server = await startServer();
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-gpu'] });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1200 } });
  const page = await context.newPage();
  await page.goto(`http://localhost:${PORT}/`, { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForSelector('.clause-card', { timeout: 15000 });
  await page.waitForTimeout(1000);

  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze();

  await browser.close();
  server.close();

  const order = { critical: 0, serious: 1, moderate: 2, minor: 3 };
  const violations = results.violations.slice().sort((a, b) => (order[a.impact] ?? 9) - (order[b.impact] ?? 9));
  if (violations.length === 0) {
    console.log('axe-core: no WCAG 2.0/2.1/2.2 A/AA violations found.');
  } else {
    for (const v of violations) {
      console.log(`[${(v.impact || 'n/a').toUpperCase()}] ${v.id}: ${v.help} (${v.nodes.length} node(s))`);
      console.log(`   ${v.helpUrl}`);
      v.nodes.slice(0, 3).forEach(n => console.log(`   - ${n.target.join(' ')}`));
    }
  }
  const blocking = violations.filter(v => v.impact === 'serious' || v.impact === 'critical');
  console.log(`\nSummary: ${violations.length} total violation type(s), ${blocking.length} serious/critical.`);
  console.log(blocking.length === 0 ? 'ACCESSIBILITY CHECK PASSED' : 'ACCESSIBILITY CHECK FAILED');
  process.exit(blocking.length === 0 ? 0 : 1);
}

main().catch(err => { console.error('a11y scan failed:', err.message); process.exit(1); });
