// Isolated real-browser component test. No Hub, login fixture, server or live AI.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const root = fileURLToPath(new URL('../', import.meta.url));
const bundle = await build({
  absWorkingDir: root,
  stdin: {
    contents: `
      import '@angular/compiler';
      import 'zone.js';
      import { bootstrapApplication } from '@angular/platform-browser';
      import { provideZoneChangeDetection } from '@angular/core';
      import { StrategyGameComponent } from './src/app/features/strategy-game/strategy-game.component';
      bootstrapApplication(StrategyGameComponent, { providers: [provideZoneChangeDetection()] });
    `,
    resolveDir: root, loader: 'ts',
  },
  bundle: true, write: false, platform: 'browser', format: 'iife', target: 'es2022',
  tsconfig: `${root}tsconfig.json`,
});

async function withGame(width, check) {
  const browser = await chromium.launch({ headless: true,
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined });
  try {
    const context = await browser.newContext({ viewport: { width, height: 1000 } });
    const page = await context.newPage();
    page.setDefaultTimeout(5000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => route.abort());
    await page.setContent(`<!doctype html><html lang="de"><head><meta charset="utf-8">
      <title>Ananta Strategiespiel – Browsertest</title>
      <style>body { margin: 0; padding: 16px; font-family: system-ui, sans-serif; color: #1e293b; background: #fff; }</style>
      </head><body><main><app-strategy-game></app-strategy-game></main></body></html>`);
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    await expect(page.getByRole('heading', { name: 'Ananta', exact: true })).toBeVisible();
    await check(page);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
}

test('desktop: complete match through browser controls, export, restore and reject an invalid replay', { timeout: 45000 }, async () => {
  await withGame(1200, async page => {
    await expect(page.locator('.hex')).toHaveCount(19);
    await expect(page.locator('.hex.selected, .hex.legal')).toHaveCount(0);
    if (process.env.STRATEGY_GAME_SCREENSHOT) {
      await page.screenshot({ path: process.env.STRATEGY_GAME_SCREENSHOT, fullPage: true });
    }
    for (const id of ['H05', 'H10', 'H14', 'H18']) await page.locator(`[data-cell="${id}"]`).click();
    await page.getByTestId('end-turn').click();
    await page.getByTestId('end-turn').click();
    await page.locator('[data-cell="H18"]').click();
    await page.locator('[data-cell="H19"]').click();
    await expect(page.getByTestId('winner')).toHaveText('Sonne gewinnt');
    await page.locator('summary').filter({ hasText: 'Zugprotokoll' }).click();
    await page.getByRole('button', { name: 'Protokoll exportieren', exact: true }).click();
    const replay = await page.locator('#game-replay').inputValue();
    await page.getByTestId('new-game').click();
    await expect(page.getByTestId('winner')).toHaveCount(0);
    await page.locator('#game-replay').fill(replay);
    await page.getByRole('button', { name: 'Partie aus Protokoll laden' }).click();
    await expect(page.getByTestId('winner')).toHaveText('Sonne gewinnt');
    await page.locator('#game-replay').fill('{"version":99}');
    await page.getByRole('button', { name: 'Partie aus Protokoll laden' }).click();
    await expect(page.getByRole('status')).toContainText('ungültiges Replay');
    await expect(page.getByTestId('winner')).toHaveText('Sonne gewinnt');
  });
});

test('mobile: keyboard movement, visible controls, no horizontal overflow and accessible semantics', { timeout: 45000 }, async () => {
  await withGame(390, async page => {
    await page.locator('[data-cell="H04"]').focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('[data-cell="H04"]')).toHaveAttribute('aria-pressed', 'true');
    await page.locator('[data-cell="H09"]').focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('[data-cell="H09"]')).toContainText('Naga');
    await expect(page.getByTestId('end-turn')).toBeVisible();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    const accessibility = await new AxeBuilder({ page }).analyze();
    assert.deepEqual(accessibility.violations.map(({ id, impact, nodes }) => ({ id, impact, targets: nodes.map(node => node.target) })), []);
  });
});
