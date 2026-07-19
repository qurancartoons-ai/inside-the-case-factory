const { defineConfig, devices } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests/browser',
  timeout: 120000,
  workers: 1,
  reporter: [['line'], ['html', { outputFolder: 'playwright-report', open: 'never' }]],
  use: { baseURL: 'http://127.0.0.1:8766', trace: 'retain-on-failure' },
  webServer: {
    command: 'python3 tools/release_candidate_acceptance.py prepare && python3 -m inside_case_factory --root .release-candidate-runtime dashboard --port 8766',
    url: 'http://127.0.0.1:8766/', timeout: 120000, reuseExistingServer: false,
  },
  projects: [
    { name: 'desktop-chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobiel-chromium', use: { ...devices['Pixel 7'] } },
  ],
});
