// Run in the existing sasha-openai-managed Docker image with this repository at /repo.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');

async function main() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  const page = await browser.newPage();
  const runId = 'a'.repeat(32);
  let submitted;
  let run = {
    run_id: runId, deal_id: '900001', client_message: null, file_urls: [],
    workflow: null, status: 'running', accepting_messages: true,
    progress: [], messages: [], artifacts: [], result: null, error_message: '',
  };
  await page.route('http://account-manager.test/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    let data;
    if (path === '/static/app.js' || path === '/static/styles.css') {
      await route.fulfill({ path: `/repo/webapp${path}` });
      return;
    }
    if (path === '/api/deals/900001') {
      data = { deal_id: '900001', runs: [], run_count: 0 };
    } else if (path === '/api/runs' && request.method() === 'POST') {
      submitted = request.postDataJSON();
      data = run;
    } else if (path.endsWith('/messages')) {
      const message = { ...request.postDataJSON(), status: 'accepted' };
      run.messages.push(message);
      data = message;
    } else if (path.startsWith('/api/runs/')) {
      data = run;
    } else {
      await route.fulfill({ contentType: 'text/html', body: fs.readFileSync('/repo/webapp/static/index.html', 'utf8') });
      return;
    }
    await route.fulfill({ json: data });
  });
  try {
    await page.goto('http://account-manager.test/deals/900001');
    await page.getByRole('button', { name: 'Start new run', exact: true }).click();
    assert.equal(await page.locator('#workflow').count(), 0);
    assert.equal(await page.locator('#run-type').count(), 0);
    assert.equal(await page.locator('#client-response-fields').isVisible(), true);
    await page.getByRole('button', { name: 'Run Account Manager', exact: true }).click();
    await page.locator('#additional-message-button:not([disabled])').waitFor();
    assert.equal(submitted.deal_id, '900001');
    assert.equal('workflow' in submitted, false);
    assert.equal(submitted.client_message, null);
    assert.deepEqual(submitted.file_urls, []);
    await page.locator('#additional-message-text').fill('Are samples free?');
    await page.getByRole('button', { name: 'Send Additional Message', exact: true }).click();
    await page.getByText('Are samples free?\nAccepted', { exact: true }).waitFor();

    run = { ...run, status: 'completed', accepting_messages: false, result: { status: 'completed', message_html: '' } };
    await page.reload();
    await page.getByText('No reply needed.', { exact: true }).waitFor();
    assert.equal(await page.locator('#message-preview').isVisible(), false);
    assert.equal(await page.locator('#additional-message-button').isDisabled(), true);

    run.result = { status: 'completed', message_html: '<p>Hello Alex.</p>' };
    await page.reload();
    await page.frameLocator('#message-preview').getByText('Hello Alex.', { exact: true }).waitFor();
    assert.equal(await page.locator('#no-reply-needed').isVisible(), false);

    run = { ...run, status: 'failed', error_message: 'Fixture browser failure', result: { status: 'failed', message_html: '' } };
    await page.reload();
    await page.getByText('Fixture browser failure', { exact: true }).waitFor();
    assert.equal(await page.locator('#no-reply-needed').isVisible(), false);
    console.log('PASS: no run type, optional message, no workflow, additional messages, no reply, draft preview, and failure rendering');
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
