const { test, expect, devices, chromium } = require('@playwright/test');
const fs = require('fs');
const path = require('path');

const slug = 'release-candidate-demo';
const runtime = path.resolve('.release-candidate-runtime');
test.use({ trace: 'off' });
const routes = [
  '/', '/projects/new', `/projects/${slug}`, `/projects/${slug}/production`,
  `/projects/${slug}/advanced`, `/projects/${slug}/reference-intake`,
  `/projects/${slug}/dossier-review`, `/projects/${slug}/research-panel`,
  `/projects/${slug}/draft-review`, `/projects/${slug}/youtube-draft`,
];

function labelFor(item) {
  return (item.getAttribute('aria-label') || item.innerText || item.value || item.name || item.type || item.tagName).trim().replace(/\s+/g, ' ').slice(0, 120);
}

async function discover(page) {
  const found = [];
  for (const route of routes) {
    await page.goto(route); await page.waitForTimeout(['/advanced', '/research-panel'].some(part => route.endsWith(part)) ? 700 : route.endsWith('/production') ? 500 : 80);
    await page.locator('details').evaluateAll(nodes => nodes.forEach(node => node.open = true));
    if (['/advanced', '/research-panel'].some(part => route.endsWith(part))) await page.waitForTimeout(800);
    const rows = await page.locator('a[href],button,input:not([type=hidden]):not([readonly]),textarea:not([readonly]),select,summary').evaluateAll((nodes, route) => nodes.map((node, index) => {
      const style = getComputedStyle(node), rect = node.getBoundingClientRect();
      const form = node.closest('form');
      return { route, index, tag: node.tagName.toLowerCase(), type: node.getAttribute('type') || '', name: node.getAttribute('name') || '', label: (node.getAttribute('aria-label') || node.innerText || node.value || node.name || node.type || node.tagName).trim().replace(/\s+/g, ' ').slice(0,120), href: node.getAttribute('href') || '', action: form?.getAttribute('action') || '', method: form?.getAttribute('method') || '', disabled: !!node.disabled, visible: style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0 };
    }), route);
    const occurrences = new Map();
    for (const row of rows.filter(row => row.visible)) {
      const signature = [row.tag, row.type, row.name, row.label, row.href, row.action].join('\u0000');
      row.occurrence = occurrences.get(signature) || 0;
      occurrences.set(signature, row.occurrence + 1);
      found.push(row);
    }
  }
  return found;
}

test('inventariseer alle zichtbare acties', async ({}, testInfo) => {
  const browser = await chromium.launch({ headless: true, args: ['--disable-gpu', '--single-process', '--no-zygote'] });
  const context = await browser.newContext({ ...(testInfo.project.name.startsWith('mobiel') ? devices['Pixel 7'] : devices['Desktop Chrome']), baseURL: 'http://127.0.0.1:8766' });
  const page = await context.newPage();
  const actions = await discover(page);
  await browser.close();
  fs.mkdirSync(runtime, { recursive: true });
  fs.writeFileSync(path.join(runtime, `visible-actions-${testInfo.project.name}.json`), JSON.stringify(actions, null, 2));
  expect(actions.length).toBeGreaterThan(50);
});

test('voer iedere zichtbare actie afzonderlijk uit', async ({}, testInfo) => {
  test.setTimeout(30 * 60 * 1000);
  const contextOptions = { ...(testInfo.project.name.startsWith('mobiel') ? devices['Pixel 7'] : devices['Desktop Chrome']), baseURL: 'http://127.0.0.1:8766' };
  let browser = await chromium.launch({ headless: true, args: ['--disable-gpu', '--single-process', '--no-zygote'] });
  let context = await browser.newContext(contextOptions);
  let page = await context.newPage();
  const actions = await discover(page), results = [], failures = [];
  const projectRoot = path.join(runtime, 'projects', slug), baseline = path.join(runtime, 'baseline', slug);
  const interactive = 'a[href],button,input:not([type=hidden]):not([readonly]),textarea:not([readonly]),select,summary';
  const sampleImage = path.resolve('release_candidate/screenshots/desktop-chromium-toestemming.png');
  const sampleVideo = path.join(baseline, 'exports/final_video.mp4');
  let consoleErrors = [];
  let projectDirty = false;
  page.setDefaultTimeout(5000);
  page.setDefaultNavigationTimeout(5000);

  const reset = () => {
    const projectsDir = path.join(runtime, 'projects');
    const discarded = path.join(runtime, 'discarded-projects');
    fs.mkdirSync(discarded, { recursive: true });
    for (const entry of fs.readdirSync(projectsDir)) if (entry !== slug) fs.renameSync(path.join(projectsDir, entry), path.join(discarded, `${Date.now()}-${Math.random().toString(16).slice(2)}-${entry}`));
    if (fs.existsSync(projectRoot)) fs.renameSync(projectRoot, path.join(discarded, `${Date.now()}-${Math.random().toString(16).slice(2)}`));
    fs.cpSync(baseline, projectRoot, { recursive: true });
  };
  const fillForm = async form => {
    for (const input of await form.locator('input:not([type=hidden]):not([readonly]),textarea:not([readonly]),select').all()) {
      if (!await input.isVisible() || await input.isDisabled()) continue;
      const tag = await input.evaluate(el => el.tagName.toLowerCase()), type = await input.getAttribute('type') || '', name = await input.getAttribute('name') || '';
      if (type === 'file') { await input.setInputFiles(name === 'clip' ? sampleVideo : sampleImage); continue; }
      if (tag === 'select') { const options = await input.locator('option:not([disabled])').all(); if (options.length) await input.selectOption({ index: 0 }); continue; }
      if (['checkbox','radio'].includes(type)) { await input.check(); continue; }
      let value = 'Offline acceptatiefixture';
      if (type === 'number') value = name === 'budget' ? '1' : '4';
      else if (type === 'url' || name.includes('url')) value = 'https://example.invalid/offline-acceptance';
      else if (name === 'prompt') value = `RC actie ${testInfo.project.name} ${results.length + 1}`;
      else if (name === 'source_ids') value = 'src01';
      else if (name === 'timestamp') value = '00:01-00:03';
      else if (name === 'command') value = 'Maak alleen scène 1 bondiger.';
      else if (name === 'instruction') value = 'Maak alleen scène één bondiger.';
      else if (name === 'text') value = 'De nachtbus vertrok volgens het lokale dienstlogboek om 00:42.';
      await input.fill(value);
    }
  };

  for (const action of actions) {
    if (results.length && results.length % 20 === 0) {
      await browser.close();
      browser = await chromium.launch({ headless: true, args: ['--disable-gpu', '--single-process', '--no-zygote'] });
      context = await browser.newContext(contextOptions);
      page = await context.newPage();
      page.setDefaultTimeout(5000);
      page.setDefaultNavigationTimeout(5000);
    }
    if (projectDirty) reset();
    projectDirty = false;
    consoleErrors.length = 0;
    const onConsole = message => { if (message.type() === 'error' && !message.text().includes('favicon')) consoleErrors.push(message.text()); };
    const onPageError = error => consoleErrors.push(error.message);
    page.on('console', onConsole);
    page.on('pageerror', onPageError);
    const row = { scherm: action.route, element: action.tag, selector_of_label: action.label, uitgevoerde_actie: '', verwacht_resultaat: '', werkelijk_resultaat: '', status: 'PASS', screenshot: '' };
    try {
      const navigation = await page.goto(action.route); expect(navigation.status()).toBeLessThan(400);
      await page.waitForTimeout(['/advanced', '/research-panel'].some(part => action.route.endsWith(part)) ? 700 : action.route.endsWith('/production') ? 450 : 40);
      await page.locator('details').evaluateAll(nodes => nodes.forEach(node => node.open = true));
      if (['/advanced', '/research-panel'].some(part => action.route.endsWith(part))) await page.waitForTimeout(800);
      const matchingIndexes = await page.locator(interactive).evaluateAll((nodes, expected) => nodes.map((node, index) => ({ node, index })).filter(({ node }) => {
        const form = node.closest('form');
        const label = (node.getAttribute('aria-label') || node.innerText || node.value || node.name || node.type || node.tagName).trim().replace(/\s+/g, ' ').slice(0, 120);
        return node.tagName.toLowerCase() === expected.tag && (node.getAttribute('type') || '') === expected.type && (node.getAttribute('name') || '') === expected.name && label === expected.label && (node.getAttribute('href') || '') === expected.href && (form?.getAttribute('action') || '') === expected.action;
      }).map(({ index }) => index), action);
      if (matchingIndexes.length <= action.occurrence) throw new Error('Actie niet meer aanwezig na directe paginalaad');
      const locator = page.locator(interactive).nth(matchingIndexes[action.occurrence]);
      await expect(locator, `${action.route} ${action.label}`).toBeVisible();
      if (action.disabled) throw new Error('Zichtbare actie is uitgeschakeld');
      if (action.tag === 'summary') {
        row.uitgevoerde_actie = 'accordion openen en sluiten'; row.verwacht_resultaat = 'open-status wisselt';
        const before = await locator.evaluate(el => el.parentElement.open); await locator.click();
        const after = await locator.evaluate(el => el.parentElement.open); if (before === after) throw new Error('Accordionstatus wijzigde niet');
        row.werkelijk_resultaat = `open=${after}`;
      } else if (action.tag === 'a') {
        row.uitgevoerde_actie = `klik link ${action.href}`; row.verwacht_resultaat = 'juiste bestemming zonder fout';
        if (/^https?:\/\//.test(action.href)) {
          await locator.evaluate(el => { el.addEventListener('click', event => event.preventDefault(), { once: true }); el.click(); });
          row.werkelijk_resultaat = 'externe URL geldig en klikbaar';
        } else {
          const responsePromise = page.waitForResponse(response => response.request().isNavigationRequest(), { timeout: 1000 }).catch(() => null);
          await locator.click(); const response = await responsePromise;
          if (response && response.status() >= 400) throw new Error(`HTTP ${response.status()}`);
          const target = page.url();
          await page.goBack({ waitUntil: 'commit', timeout: 15000 });
          const direct = new URL(target); await page.goto(`${direct.pathname}${direct.search}${direct.hash}`);
          row.werkelijk_resultaat = `bestemming ${target}`;
        }
      } else if (['input','textarea','select'].includes(action.tag) && action.type !== 'submit') {
        row.uitgevoerde_actie = 'veld bedienen'; row.verwacht_resultaat = 'veld accepteert geldige invoer';
        if (action.type === 'file') await locator.setInputFiles(action.name === 'clip' ? sampleVideo : sampleImage);
        else if (action.tag === 'select') await locator.selectOption({ index: 0 });
        else if (['checkbox','radio'].includes(action.type)) await locator.check();
        else await locator.fill(action.type === 'number' ? '1' : action.type === 'url' ? 'https://example.invalid/test' : 'Acceptatietest');
        row.werkelijk_resultaat = 'invoer geaccepteerd';
      } else if ((action.tag === 'button' || action.type === 'submit') && !action.action) {
        row.uitgevoerde_actie = 'client-side knop klikken'; row.verwacht_resultaat = 'zichtbare UI-reactie zonder console- of HTTP-fout';
        await locator.click(); await page.waitForTimeout(250);
        row.werkelijk_resultaat = 'klik verwerkt';
      } else if (action.tag === 'button' || action.type === 'submit') {
        projectDirty = true;
        row.uitgevoerde_actie = `dubbelklik submit ${action.action}`; row.verwacht_resultaat = 'één POST, redirect, persistente status';
        const form = locator.locator('xpath=ancestor::form[1]'); await fillForm(form);
        let requests = 0, responseStatus = 0;
        const count = request => { if (request.method() === 'POST' && new URL(request.url()).pathname === action.action) requests++; };
        const capture = response => { if (response.request().method() === 'POST' && new URL(response.url()).pathname === action.action) responseStatus = response.status(); };
        page.on('request', count); page.on('response', capture);
        await locator.dblclick({ delay: 20 }).catch(async () => { if (requests === 0) await locator.click(); });
        await page.waitForTimeout(350); page.off('request', count); page.off('response', capture);
        if (requests !== 1) throw new Error(`verwacht één POST, kreeg ${requests}`);
        if (responseStatus >= 400) throw new Error(`HTTP ${responseStatus}`);
        if (action.action.includes(`/projects/${slug}/`) === false && action.action !== '/projects/new' && action.action !== '/production/start') throw new Error('projectslug ontbreekt');
        await page.waitForLoadState('domcontentloaded').catch(() => {});
        await page.goto(action.route).catch(async error => {
          if (!String(error).includes('interrupted by another navigation')) throw error;
          await page.waitForTimeout(300);
          await page.goto(action.route);
        });
        await expect(page.locator('body')).toBeVisible();
        if (await page.locator('.loading-state:visible').count()) { await expect(page.locator('.loading-state')).toBeHidden({ timeout: 10000 }); }
        row.werkelijk_resultaat = `${requests} POST, HTTP ${responseStatus || 'redirect gevolgd'}, refresh OK`;
      }
      if (consoleErrors.length) throw new Error(`console: ${consoleErrors.join(' | ')}`);
    } catch (error) {
      row.status = 'FAIL'; row.werkelijk_resultaat = String(error.message || error);
      const shotDir = path.join(runtime, 'coverage-failures'); fs.mkdirSync(shotDir, { recursive: true });
      const shot = `${testInfo.project.name}-${results.length + 1}.png`; await page.screenshot({ path: path.join(shotDir, shot), fullPage: false }).catch(() => {});
      row.screenshot = shot; failures.push(row);
    }
    page.off('console', onConsole);
    page.off('pageerror', onPageError);
    results.push(row);
    fs.writeFileSync(path.join(runtime, `action-results-${testInfo.project.name}.json`), JSON.stringify(results, null, 2));
  }
  await browser.close();
  expect(failures, failures.slice(0, 15).map(f => `${f.scherm} ${f.selector_of_label}: ${f.werkelijk_resultaat}`).join('\n')).toEqual([]);
});
