// Run in the existing sasha-account-manager-agent Docker image with this repository at /repo.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');

async function main() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  const page = await browser.newPage({ timezoneId: 'America/Los_Angeles' });
  await page.clock.install({ time: new Date('2026-10-13T02:00:00Z') });
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
    if (path === '/api/deals/900001/notes') {
      data = { deal_id: '900001', notes: 'Stage 1 follow-up generated: offer_art_team' };
    } else if (path === '/api/deals/900001') {
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
    await page.locator('#deal-notes-content').getByText('Stage 1 follow-up generated: offer_art_team').waitFor();
    await page.getByRole('button', { name: 'Follow-up check', exact: true }).click();
    assert.equal(await page.locator('#as-of-date').inputValue(), '2026-10-12');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.setViewportSize({ width: 1280, height: 900 });
    assert.equal(await page.locator('#followup-stage').count(), 0);
    await page.locator('#days-since-reply').fill('99');
    run = { ...run, turn_type: 'follow_up', as_of_date: '2026-10-12', days_since_client_reply: 99 };
    await page.getByRole('button', { name: 'Run follow-up', exact: true }).click();
    await page.locator('#additional-message-button:not([disabled])').waitFor();
    assert.deepEqual(submitted, { deal_id: '900001', turn_type: 'follow_up', as_of_date: '2026-10-12', days_since_client_reply: 99 });
    assert.match(await page.locator('#followup-meta').textContent(), /^Follow-up check · As-of 2026-10-12 · 99 day\(s\) since client reply$/);
    await page.locator('#run-notes-content').getByText('Stage 1 follow-up generated: offer_art_team').waitFor();
    await page.locator('#additional-message-text').fill('Does the sample include printing?');
    await page.getByRole('button', { name: 'Send Additional Message', exact: true }).click();
    await page.getByText('Does the sample include printing?\nAccepted', { exact: true }).waitFor();
    run = { ...run, turn_type: null, as_of_date: null, days_since_client_reply: null, messages: [] };
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

    const bubbles = [{ category: 'core_response', text: 'Your mockup is ready.' },
      { category: 'cta', text: '<img src=x onerror=alert(1)> What do you think?' }];
    run.result = { status: 'completed', message_html: '', sms_messages: bubbles };
    await page.reload();
    await page.locator('#sms-preview-section').waitFor();
    assert.deepEqual(await page.locator('.sms-bubble').allTextContents(), bubbles.map(bubble => bubble.text));
    assert.equal(await page.locator('#sms-preview img').count(), 0);
    assert.equal(await page.locator('#email-preview-section').isVisible(), false);
    assert.equal(await page.locator('#no-reply-needed').isVisible(), false);
    run.result.message_html = '<p>Hello Alex.</p>';
    await page.reload();
    await page.frameLocator('#message-preview').getByText('Hello Alex.', { exact: true }).waitFor();
    assert.equal(await page.locator('#sms-preview-section').isVisible(), true);

    run = { ...run, status: 'failed', error_message: 'Fixture browser failure', result: { status: 'failed', message_html: '' } };
    await page.reload();
    await page.getByText('Fixture browser failure', { exact: true }).waitFor();
    assert.equal(await page.locator('#no-reply-needed').isVisible(), false);
    console.log('PASS: follow-up form, local date, metadata, notes, active follow-up messages, optional input, removed selector, additional messages, no reply, draft preview, and failure rendering');
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
