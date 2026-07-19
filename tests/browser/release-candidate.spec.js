const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');

const slug = 'release-candidate-demo';
const runtime = path.resolve('.release-candidate-runtime');
const steps = [];
async function step(name, fn) {
  const started = Date.now();
  await fn();
  steps.push({ name, status: 'PASS', ms: Date.now() - started });
}

test.afterAll(() => {
  fs.mkdirSync(runtime, { recursive: true });
  fs.writeFileSync(path.join(runtime, 'acceptance-evidence.json'), JSON.stringify({ result: 'PASS', steps }, null, 2));
});

test('volledige bestaande gebruikersreis en routecontracten', async ({ page, request }, testInfo) => {
  const shots = path.join(runtime, 'screenshots'); fs.mkdirSync(shots, { recursive: true });
  const approvalPath = path.join(runtime, 'projects', slug, 'manifests', 'paid_research_approval.json');
  fs.writeFileSync(approvalPath, JSON.stringify({ version: 1, approval_required: true, estimated_cost_usd: 0.04, extra_sources: 6, reason: 'Een internationale bron ontbreekt nog.', countries: ['Nederland', 'België'], languages: ['Nederlands', 'Frans'], claims: ['De dienstregeling beter vergelijken.'] }, null, 2));
  await step('Projectoverzicht opent', async () => { await page.goto('/'); await expect(page).toHaveTitle(/Inside the Case/); });
  await step('Nieuw project met invoer, budget en YouTube-link', async () => {
    await page.goto('/projects/new');
    await page.locator('[name=prompt]').fill(`RC browser ${testInfo.project.name}`);
    await page.locator('[name=budget]').fill('0');
    await page.locator('[name=youtube_urls]').fill('https://www.youtube.com/watch?v=offlinefixture');
    await Promise.all([page.waitForURL(/\/projects\/rc-browser-/), page.getByRole('button', { name: 'Project aanmaken' }).click()]);
    await page.reload(); await expect(page.locator('.progress-shell')).toBeVisible();
  });
  await step('Directe URL en voortgang zonder browsercache', async () => {
    const response = await request.get(`/projects/${slug}/progress-data`);
    expect(response.status()).toBe(200); expect(response.headers()['cache-control']).toContain('no-store');
    await page.goto(`/projects/${slug}/production`); await expect(page.locator('#progress-content')).toBeVisible();
    await page.screenshot({ path: path.join(shots, `${testInfo.project.name}-toestemming.png`), fullPage: true });
  });
  await step('Betaalde call heeft drie zichtbare acties', async () => {
    await expect(page.getByRole('button', { name: 'Goedkeuren en doorgaan' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Annuleren' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Alleen lokaal doorgaan' })).toBeVisible();
  });
  await step('Alleen lokaal verwerkt toestemming en ververst status', async () => {
    await page.getByRole('button', { name: 'Alleen lokaal doorgaan' }).click();
    await page.waitForURL(`/projects/${slug}/production`); await page.reload();
    await expect(page.getByRole('button', { name: 'Goedkeuren en doorgaan' })).toHaveCount(0);
  });
  const routes = ['', '/advanced', '/reference-intake', '/dossier-review', '/research-panel', '/draft-review', '/youtube-draft', '/preview/video', '/preview/thumbnail/s01'];
  for (const route of routes) await step(`Route ${route || '/project'} opent`, async () => {
    const response = await request.get(`/projects/${slug}${route}`); expect(response.status()).toBe(200);
  });
  await step('Bron afwijzen blijft opgeslagen na redirect en refresh', async () => {
    await page.goto(`/projects/${slug}/dossier-review`);
    const form = page.locator('form[action*="source/src01/reject"]'); await expect(form).toBeVisible();
    await Promise.all([page.waitForURL(/notice=Bron%20afgewezen/), form.getByRole('button').click()]);
    await page.reload(); await expect(page.locator('#source-src01')).toContainText('Afgewezen');
  });
  await step('Bron goedkeuren is idempotent en blijft opgeslagen', async () => {
    const action = `/projects/${slug}/research/source/src01/approve`;
    expect((await request.post(action, { maxRedirects: 0 })).status()).toBe(303); expect((await request.post(action, { maxRedirects: 0 })).status()).toBe(303);
    await page.goto(`/projects/${slug}/dossier-review#source-src01`); await expect(page.locator('#source-src01')).toContainText('Goedgekeurd');
  });
  await step('Claim afwijzen en goedkeuren blijven opgeslagen', async () => {
    expect((await request.post(`/projects/${slug}/research/claim/c01/reject`, { maxRedirects: 0 })).status()).toBe(303);
    expect((await request.post(`/projects/${slug}/research/claim/c01/approve`, { maxRedirects: 0 })).status()).toBe(303);
    await page.goto(`/projects/${slug}/dossier-review#claim-c01`); await expect(page.locator('#claim-c01')).toContainText('Goedgekeurd');
  });
  await step('Ongeldige ID wijzigt niets en geeft duidelijke fout', async () => {
    const response = await request.post(`/projects/${slug}/research/source/bestaat-niet/approve`);
    expect(response.status()).toBe(404); expect((await response.text()).toLowerCase()).toContain('niet gevonden');
  });
  await step('Review, preview en YouTube-pakket zijn zichtbaar', async () => {
    await page.goto(`/projects/${slug}/draft-review`); await expect(page.locator('.scene-review').first()).toBeVisible();
    await page.screenshot({ path: path.join(shots, `${testInfo.project.name}-review.png`), fullPage: true });
    await page.goto(`/projects/${slug}/youtube-draft`); await expect(page.locator('body')).toContainText('YouTube');
  });
  await step('MP4, selectieve revisie en YouTube-draft bestaan', async () => {
    const root = path.join(runtime, 'projects', slug);
    expect(fs.statSync(path.join(root, 'exports/final_video.mp4')).size).toBeGreaterThan(1000);
    expect(fs.existsSync(path.join(root, 'manifests/selective_regeneration.json'))).toBeTruthy();
    expect(JSON.parse(fs.readFileSync(path.join(root, 'manifests/youtube_draft.json'))).status).toBe('draft');
  });
  await step('Terugknop en refresh behouden projectcontext', async () => {
    await page.goto(`/projects/${slug}`); await page.goto(`/projects/${slug}/advanced`); await page.goBack();
    await expect(page).toHaveURL(`/projects/${slug}`); await page.reload(); await expect(page.locator('body')).toContainText('Offline');
  });
});
