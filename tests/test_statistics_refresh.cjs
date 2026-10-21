/* Browser regression test. Requires playwright; optionally set PLAYWRIGHT_CHANNEL. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require('playwright');

const frontend = path.resolve(__dirname, '../custom_components/pillpal/frontend');
const panelSource = fs.readFileSync(path.join(frontend, 'pillpal-panel.js'), 'utf8');
const component = panelSource.match(/customElements.define\("([^"]+)"/)[1];
const medication = { medication_id: 'med', name: 'Example medication', quantity: 1,
  unit_singular: 'Tablette', unit_plural: 'Tabletten' };
const booking = { slot_id: 'cycle:morning', status: 'taken', original_status: 'taken',
  type: 'regular_taken', slot: 'morning', timestamp: '2026-10-21T08:00:00+02:00',
  medications: [medication], all_medications: [medication], correction_id: '' };
const statistics = { period_start: '2026-10-15', period_end: '2026-10-21', selected_day: '2026-10-21',
  planned: 1, taken: 1, skipped: 0, missed: 0, pending: 0, adherence: 100,
  as_needed_bookings: 0, as_needed_quantity: 0, available_medications: [], available_doctors: [],
  heatmap: [{date: '2026-10-21', heatmap_status: 'complete', planned: 1, taken: 1}],
  day_details: { events: [booking] } };
const profile = { person_id: 'person.test', name: 'Example', settings: {}, medications: [], doctors: [],
  archived_medications: [], as_needed_medications: [], statistics };

(async () => {
  const server = http.createServer((req, res) => {
    const file = path.join(frontend, path.basename(req.url.split('?')[0]));
    if (req.url === '/') {
      res.setHeader('Content-Type', 'text/html; charset=utf-8');
      res.end('<!doctype html><meta charset="utf-8"><body style="margin:0"><script src="/pillpal-panel.js"></script>');
    } else if (fs.existsSync(file)) {
      res.setHeader('Content-Type', file.endsWith('.css') ? 'text/css; charset=utf-8' : 'text/javascript; charset=utf-8');
      res.end(fs.readFileSync(file));
    } else { res.writeHead(404); res.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true,
      ...(process.env.PLAYWRIGHT_CHANNEL ? {channel: process.env.PLAYWRIGHT_CHANNEL} : {}) });
    for (const width of [360, 1440]) {
      const page = await browser.newPage({ viewport: {width, height: 900} });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(`http://127.0.0.1:${server.address().port}`);
      await page.evaluate(({profile, component}) => {
        window.nextStatistics = profile.statistics;
        window.requests = 0;
        const shell = document.createElement('div');
        shell.attachShadow({mode: 'open'});
        const scroller = document.createElement('div');
        scroller.style.cssText = 'height:650px;overflow:auto';
        shell.shadowRoot.append(scroller);
        document.body.append(shell);
        window.scroller = scroller;
        window.panel = document.createElement(component);
        panel._page = 'statistik';
        panel._panel = {};
        panel._hass = { connection: {
          subscribeEvents: async callback => { window.update = callback; return () => {}; },
          sendMessagePromise: async message => {
            if (message.type === 'pillpal/bootstrap') return structuredClone({profile, selected_person_id: profile.person_id});
            requests++;
            if (window.failStatistics) throw new Error('Simulated refresh failure');
            return structuredClone(nextStatistics);
          },
        } };
        scroller.append(panel);
      }, {profile, component});
      const entry = page.locator('details.intake-entry');
      await entry.waitFor();
      await entry.locator('summary').click();
      await entry.scrollIntoViewIfNeeded();
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      await page.evaluate(() => {
        window.previousEntry = panel.shadowRoot.querySelector('details.intake-entry');
        window.previousTop = scroller.scrollTop;
      });
      async function refresh() {
        const before = await page.evaluate(() => requests);
        await page.evaluate(() => update({data: {person_id: 'person.test'}}));
        await page.waitForFunction(count => requests > count, before);
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      }
      for (let i = 0; i < 3; i++) await refresh();
      assert.equal(await entry.getAttribute('open'), '');
      assert.equal(await page.evaluate(() => previousEntry === panel.shadowRoot.querySelector('details.intake-entry')), true);
      assert.ok(await page.evaluate(() => Math.abs(scroller.scrollTop - previousTop) <= 1),
        JSON.stringify(await page.evaluate(() => ({previousTop, currentTop: scroller.scrollTop, height:scroller.scrollHeight, viewport:scroller.clientHeight}))));

      // A real update must appear without closing the editor or moving the viewport.
      await page.evaluate(() => {
        nextStatistics.taken = 0; nextStatistics.skipped = 1;
        const event = nextStatistics.day_details.events[0];
        Object.assign(event, {status: 'skipped', type: 'regular_skipped', corrected: true,
          correction_id: 'revision-1', corrected_at: '2026-10-21T09:00:00+02:00'});
      });
      await refresh();
      assert.equal(await entry.getAttribute('open'), '');
      assert.match(await entry.locator('summary').innerText(), /Übersprungen/);
      assert.equal(await entry.locator('[data-status="taken"]').getAttribute('data-revision'), 'revision-1');
      assert.ok(await page.evaluate(() => Math.abs(scroller.scrollTop - previousTop) <= 1),
        JSON.stringify(await page.evaluate(() => ({previousTop, currentTop: scroller.scrollTop, height:scroller.scrollHeight, viewport:scroller.clientHeight}))));

      // Failures preserve the expanded entry; recovery clears the error.
      await page.evaluate(() => { window.failStatistics = true; });
      await refresh();
      assert.equal(await entry.getAttribute('open'), '');
      await page.evaluate(() => { window.failStatistics = false; });
      await refresh();
      assert.equal(await entry.getAttribute('open'), '');

      // User collapse and page/profile context changes must not resurrect an editor.
      await entry.locator('summary').click();
      await refresh();
      assert.equal(await entry.getAttribute('open'), null);
      await entry.locator('summary').click();
      await page.evaluate(() => { nextStatistics.selected_day = '2026-10-20'; });
      await refresh();
      assert.equal(await entry.getAttribute('open'), null);
      await entry.locator('summary').click();
      await page.evaluate(() => { panel._data.selected_person_id = 'person.other'; panel._render(); });
      assert.equal(await entry.getAttribute('open'), null);

      // PRN bookings expose discard only, including after background refreshes.
      await page.evaluate(() => {
        panel._data.selected_person_id = 'person.test';
        Object.assign(nextStatistics.day_details.events[0], {
          slot_id: 'as_needed:prn-1', intake_kind: 'as_needed', slot: null,
          status: 'taken', type: 'as_needed', original_status: 'taken',
          corrected: false, correction_id: '', corrected_at: null,
        });
      });
      await refresh();
      await entry.locator('summary').click();
      assert.equal(await entry.locator('.intake-actions button').count(), 1);
      assert.equal(await entry.locator('.intake-actions button').innerText(), 'Verwerfen');
      await page.evaluate(() => { panel._call = async (action, data) => { window.prnAction = {action, data}; return {status:'discarded'}; }; });
      await entry.locator('.intake-actions button').click();
      assert.deepEqual(await page.evaluate(() => prnAction), {
        action: 'correct_intake', data: {intake_id:'as_needed:prn-1', status:'discarded', expected_revision:''},
      });
      await page.evaluate(() => Object.assign(nextStatistics.day_details.events[0], {
        status: 'discarded', type: 'as_needed_discarded', corrected: true, correction_id: 'prn-revision',
      }));
      await refresh();
      assert.equal(await entry.getAttribute('open'), '');
      assert.equal(await entry.locator('.intake-actions button').count(), 0);
      assert.match(await entry.locator('summary').innerText(), /Bedarf · Verworfen \*/);
      assert.equal(await entry.locator('.intake-summary').evaluate(node => getComputedStyle(node).textDecorationLine), 'line-through');
      assert.deepEqual(errors, []);
      console.log(`PASS ${width}px: statistics refresh state and PRN discard-only actions, payload, audit and strikethrough`);
      await page.close();
    }
  } finally {
    if (browser) await browser.close();
    server.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
