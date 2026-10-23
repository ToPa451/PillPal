/* Clipboard regression test. Requires playwright; optionally set PLAYWRIGHT_CHANNEL. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {chromium} = require('playwright');

const frontend = path.resolve(__dirname, '../custom_components/pillpal/frontend');
const panelSource = fs.readFileSync(path.join(frontend, 'pillpal-panel.js'), 'utf8');
const component = panelSource.match(/customElements.define\("([^"]+)"/)[1];
const orderText = 'Praxis Beispiel\n- Medikament A: 2 Packungen';

(async () => {
  const server = http.createServer((req, res) => {
    const file = path.join(frontend, path.basename(req.url.split('?')[0]));
    if (req.url === '/') {
      res.setHeader('Content-Type', 'text/html; charset=utf-8');
      res.end('<!doctype html><meta charset="utf-8"><body><script src="/pillpal-panel.js"></script>');
    } else if (fs.existsSync(file)) {
      res.setHeader('Content-Type', file.endsWith('.css') ? 'text/css' : 'text/javascript');
      res.end(fs.readFileSync(file));
    } else { res.writeHead(404); res.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({headless: true,
      ...(process.env.PLAYWRIGHT_CHANNEL ? {channel: process.env.PLAYWRIGHT_CHANNEL} : {})});
    for (const insecure of [false, true]) {
      const page = await browser.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      if (insecure) {
        await page.route('http://pillpal.test/**', async route => {
          const url = new URL(route.request().url());
          if (url.pathname === '/') return route.fulfill({contentType: 'text/html; charset=utf-8',
            body: '<!doctype html><meta charset="utf-8"><body><script src="/pillpal-panel.js"></script>'});
          const file = path.join(frontend, path.basename(url.pathname));
          if (!fs.existsSync(file)) return route.fulfill({status: 404, body: ''});
          return route.fulfill({contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript',
            body: fs.readFileSync(file)});
        });
      }
      await page.goto(insecure ? 'http://pillpal.test/' : `http://127.0.0.1:${server.address().port}`);
      assert.equal(await page.evaluate(() => window.isSecureContext), !insecure);
      await page.evaluate(({component, orderText, insecure}) => {
        window.panel = document.createElement(component);
        document.body.append(panel);
        panel._inventoryClipboardText = orderText;
        panel._data = {profile: {order_plan: {clipboard_text: 'stale text'}}};
        panel._render = () => {};
        window.copyEvents = [];
        document.addEventListener('copy', () => {
          const field = document.activeElement;
          copyEvents.push({value: field.value,
            start: field.selectionStart, end: field.selectionEnd});
        });
        if (!insecure) {
          Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {
            writeText: async text => { window.modernCopy = text; },
          }});
        }
      }, {component, orderText, insecure});
      await page.evaluate(async () => panel._handleAction('copy-order', document.body));
      assert.equal(await page.evaluate(() => panel._feedback.type), 'success');
      if (insecure) {
        assert.equal(await page.evaluate(() => typeof navigator.clipboard), 'undefined');
        assert.deepEqual(await page.evaluate(() => copyEvents), [{
          value: orderText, start: 0, end: orderText.length,
        }]);
      } else {
        assert.equal(await page.evaluate(() => modernCopy), orderText);
        assert.deepEqual(await page.evaluate(() => copyEvents), []);
      }
      assert.equal(await page.locator('body > textarea').count(), 0);
      assert.deepEqual(errors, []);
      console.log(`PASS ${insecure ? 'LAN HTTP fallback' : 'secure Clipboard API'}: order text copied`);
      await page.close();
    }
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
