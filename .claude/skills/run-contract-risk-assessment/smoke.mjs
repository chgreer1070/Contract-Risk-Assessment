#!/usr/bin/env node
/**
 * smoke.mjs — Launch contract_visualization.html in headless Chromium,
 * take screenshots, and verify the dashboard rendered correctly.
 *
 * Usage:
 *   node .claude/skills/run-contract-risk-assessment/smoke.mjs
 *
 * Screenshots land in /tmp/shots/
 */

import { chromium } from 'playwright';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import { mkdirSync } from 'fs';
import { createServer } from 'http';
import { readFile } from 'fs/promises';
import { extname } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..', '..', '..');
const SHOTS_DIR = '/tmp/shots';
const PORT = 8787;

mkdirSync(SHOTS_DIR, { recursive: true });

const MIME = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json' };

function startServer() {
  return new Promise((res) => {
    const srv = createServer(async (req, resp) => {
      let filePath;
      if (req.url === '/') {
        filePath = resolve(ROOT, 'contract_visualization.html');
      } else if (req.url.includes('chart.js') || req.url.includes('chart.umd')) {
        filePath = resolve(ROOT, 'node_modules/chart.js/dist/chart.umd.js');
      } else {
        filePath = resolve(ROOT, req.url.slice(1));
      }
      try {
        let data = await readFile(filePath, 'utf8');
        if (filePath.endsWith('.html')) {
          // Serve Chart.js from node_modules locally (the CDN is unavailable in
          // headless/offline test envs). Replace the whole tag so the production
          // Subresource-Integrity hash (computed for the minified CDN build)
          // does not reject the local unminified copy served here.
          data = data.replace(
            /<script src="https:\/\/cdn\.jsdelivr\.net\/npm\/chart\.js@[^"]*"[^>]*><\/script>/,
            '<script src="/chart.umd.js"></script>'
          );
        }
        resp.writeHead(200, { 'Content-Type': MIME[extname(filePath)] || 'text/plain' });
        resp.end(data);
      } catch {
        resp.writeHead(404);
        resp.end('Not found');
      }
    });
    srv.listen(PORT, () => { console.log(`Server on http://localhost:${PORT}`); res(srv); });
  });
}

async function main() {
  const server = await startServer();
  console.log('Launching headless Chromium...');
  const browser = await chromium.launch({
    args: ['--no-sandbox', '--disable-gpu'],
  });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } });

  console.log('Opening dashboard...');
  await page.goto(`http://localhost:${PORT}/`, { waitUntil: 'networkidle', timeout: 30000 });

  const consoleErrors = [];
  page.on('pageerror', err => consoleErrors.push(err.message));

  // Wait for the dashboard to render
  await page.waitForSelector('.stat-card', { timeout: 15000 });
  await page.waitForSelector('#gaugeChart', { timeout: 10000 });
  await page.waitForSelector('.clause-card', { timeout: 10000 });

  // Screenshot 1: Full dashboard (dark theme)
  await page.screenshot({ path: `${SHOTS_DIR}/dashboard-dark.png`, fullPage: true });
  console.log(`Screenshot: ${SHOTS_DIR}/dashboard-dark.png`);

  // Verify key elements rendered
  const stats = await page.$$('.stat-card');
  console.log(`Stat cards rendered: ${stats.length}`);

  const clauses = await page.$$('.clause-card');
  console.log(`Clause cards rendered: ${clauses.length}`);

  const canvases = await page.$$('canvas');
  console.log(`Chart canvases: ${canvases.length}`);

  const pipelineNodes = await page.$$('.pipeline-node');
  console.log(`Pipeline nodes: ${pipelineNodes.length}`);

  // Test interactivity: click a clause card to expand
  if (clauses.length > 0) {
    await clauses[0].click();
    await page.waitForTimeout(400);
    const expanded = await clauses[0].evaluate(el => el.classList.contains('expanded'));
    console.log(`Clause card expand: ${expanded ? 'PASS' : 'FAIL'}`);
  }

  // Test search
  const searchInput = await page.$('#searchInput');
  if (searchInput) {
    await searchInput.fill('Liability');
    await page.waitForTimeout(300);
    const visible = await page.$$eval('.clause-card', cards =>
      cards.filter(c => !c.classList.contains('hidden')).length
    );
    console.log(`Search filter (Liability): ${visible} cards visible`);
    await searchInput.fill('');
  }

  // Test theme toggle
  await page.click('.theme-toggle');
  await page.waitForTimeout(500);
  const theme = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
  console.log(`Theme toggle: now ${theme}`);

  // Screenshot 2: Light theme
  await page.screenshot({ path: `${SHOTS_DIR}/dashboard-light.png`, fullPage: true });
  console.log(`Screenshot: ${SHOTS_DIR}/dashboard-light.png`);

  // Test filter buttons
  const filterBtns = await page.$$('.filter-btn');
  if (filterBtns.length > 1) {
    await filterBtns[1].click(); // "High Risk"
    await page.waitForTimeout(300);
    const highOnly = await page.$$eval('.clause-card', cards =>
      cards.filter(c => !c.classList.contains('hidden')).length
    );
    console.log(`High Risk filter: ${highOnly} cards visible`);
  }

  // Screenshot 3: Filtered view
  await page.screenshot({ path: `${SHOTS_DIR}/dashboard-filtered.png`, fullPage: true });
  console.log(`Screenshot: ${SHOTS_DIR}/dashboard-filtered.png`);

  // Full "All Identified Risks" table: row count, score sort, level filter
  const riskCount = await page.evaluate(() => (CONTRACT_DATA.riskAssessment || []).length);
  const riskRows = await page.$$eval('#riskTableBody tr', rows => rows.filter(r => r.cells.length > 1).length);
  console.log(`Risk table rows: ${riskRows} (expected ${riskCount})`);
  await page.click('#riskTable th button[data-col="0"]'); // sort by score, desc first
  await page.waitForTimeout(200);
  const scores = await page.$$eval('#riskTableBody tr', rows =>
    rows.filter(r => r.cells.length > 1).map(r => parseFloat(r.cells[0].dataset.sort || '0')));
  const sortedDesc = scores.length > 1 && scores[0] === Math.max(...scores);
  console.log(`Risk table score sort (desc): ${sortedDesc ? 'PASS' : 'FAIL'} (first=${scores[0]})`);
  await page.selectOption('#riskTableFilter', 'High');
  await page.waitForTimeout(200);
  const visibleRisk = await page.$$eval('#riskTableBody tr', rows =>
    rows.filter(r => r.cells.length > 1 && r.style.display !== 'none').length);
  const highRisk = await page.evaluate(() =>
    CONTRACT_DATA.riskAssessment.filter(r => r.riskLevel === 'High').length);
  console.log(`Risk table High filter: ${visibleRisk} visible (expected ${highRisk})`);
  await page.selectOption('#riskTableFilter', 'all');
  const riskTableOk = riskRows === riskCount && riskRows > 0 && sortedDesc && visibleRisk === highRisk;
  console.log(`Risk table checks: ${riskTableOk ? 'PASS' : 'FAIL'}`);

  // Check for console errors
  const errors = [];
  page.on('pageerror', err => errors.push(err.message));
  if (errors.length > 0) {
    console.log('Console errors:', errors);
  } else {
    console.log('Console errors: none');
  }

  await browser.close();
  server.close();

  // Summary
  const allGood = stats.length === 6 && clauses.length === 8 && canvases.length === 5 && pipelineNodes.length === 5 && riskTableOk;
  console.log(`\n${allGood ? 'ALL CHECKS PASSED' : 'SOME CHECKS FAILED'}`);
  process.exit(allGood ? 0 : 1);
}

main().catch(err => {
  console.error('Smoke test failed:', err.message);
  process.exit(1);
});
