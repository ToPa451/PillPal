/* Production UI with simulated HA responses. Requires playwright. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {chromium} = require('playwright');
const frontend = path.resolve(__dirname, '../custom_components/pillpal/frontend');
const component = fs.readFileSync(path.join(frontend, 'pillpal-panel.js'), 'utf8').match(/customElements.define\("([^"]+)"/)[1];
const normal = {wake_time:'08:00', morning_delay_minutes:15, lunch_window_from:'12:00', lunch_window_to:'14:00', evening:'20:00', night:'23:00'};
const standard = {id:'standard', name:'Standard', enabled:true, weekdays:[0,1,2,3,4,5,6], normal, holiday:{...normal}, holiday_enabled:false, holiday_initialized:false};
const profile = {person_id:'person.test', name:'Testperson', medications:[], archived_medications:[], doctors:[],
  as_needed_medications:[], settings:{time_plans:[standard], early_minutes:30, snooze_minutes:15, repeat_minutes:5,
    order_warning_days:10, practice_lead_days:5, low_stock_window_days:7, expiry_warning_days:14,
    alarm_window_from:'04:00', alarm_window_to:'11:00', bedtime_offset_hours:8.25, evening_before_bedtime_hours:2},
  runtime:{cycle_state:'active', cycle_started_at:'2026-10-24T09:30:00+02:00', cycle_started_by:'awake_helper',
    schedule_source:'Smartphone-Wecker', time_plan_snapshot:{plan_name:'Wochenende', holiday_applied:true}}, schedule:{}};

(async () => {
  const server = http.createServer((req,res) => {
    const file = path.join(frontend, path.basename(req.url.split('?')[0]));
    if (req.url === '/') {
      res.setHeader('Content-Type','text/html; charset=utf-8');
      res.end('<!doctype html><meta charset="utf-8"><body style="margin:0"><script src="/pillpal-panel.js"></script>');
    } else if (fs.existsSync(file)) {
      res.setHeader('Content-Type',file.endsWith('.css') ? 'text/css' : 'text/javascript');
      res.end(fs.readFileSync(file));
    } else {res.writeHead(404);res.end();}
  });
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
  let browser;
  try {
    browser = await chromium.launch({headless:true, ...(process.env.PLAYWRIGHT_CHANNEL ? {channel:process.env.PLAYWRIGHT_CHANNEL} : {})});
    for (const width of [360,390,768,1440]) {
      const page = await browser.newPage({viewport:{width,height:1000}});
      const errors = [];
      page.on('pageerror',error => errors.push(error.message));
      await page.goto(`http://127.0.0.1:${server.address().port}`);
      await page.evaluate(({profile,component}) => {
        window.saved = structuredClone(profile);
        window.payloads = [];
        window.panel = document.createElement(component);
        panel.style.cssText = '--primary-background-color:#202124;--card-background-color:#303336;--primary-text-color:#f1f3f4;--secondary-text-color:#bdc1c6;--primary-color:#8057ad;--divider-color:#665479';
        panel._page = 'zeiten'; panel._panel = {};
        panel._hass = {connection:{subscribeEvents:async callback => {window.refresh=callback;return ()=>{};},
          sendMessagePromise:async message => {
            if (message.type === 'pillpal/action' && message.action === 'update_settings') {
              if (window.rejectSave) throw new Error('Zeitpläne überschneiden sich (simulierter Backendfehler).');
              payloads.push(message.data.settings);
              Object.assign(saved.settings,structuredClone(message.data.settings));
              return {};
            }
            return structuredClone({profile:saved,selected_person_id:saved.person_id,options:{}});
          }}};
        document.body.append(panel);
      }, {profile,component});
      let card = page.locator('.time-plan');
      await card.waitFor();
      await card.locator('summary').click();
      assert.equal(await card.locator('[data-weekday]').count(),0);
      await page.locator('[name="repeat_minutes"]').fill('17');
      await card.locator('[data-plan-set="normal"] [data-value="wake_time"]').fill('08:45');
      await card.locator('[data-plan-field="holiday_enabled"]').check();
      assert.equal(await card.locator('[data-plan-set="holiday"] [data-value="wake_time"]').inputValue(),'08:45');
      await card.locator('[data-plan-set="holiday"] [data-value="wake_time"]').fill('10:15');
      await card.locator('[data-plan-field="holiday_enabled"]').uncheck();
      await card.locator('[data-plan-set="normal"] [data-value="wake_time"]').fill('07:45');
      await card.locator('[data-plan-field="holiday_enabled"]').check();
      assert.equal(await card.locator('[data-plan-set="holiday"] [data-value="wake_time"]').inputValue(),'10:15');
      await page.locator('[data-action="add-time-plan"]').click();
      const extra = page.locator('.time-plan').nth(1);
      await extra.locator('[data-plan-field="name"]').fill('Wochenende');
      await extra.locator('[data-plan-field="enabled"]').check();
      await extra.locator('[data-plan-set="normal"] [data-value="night"]').fill('00:30');
      assert.equal(await page.locator('[name="repeat_minutes"]').inputValue(),'17');
      await page.evaluate(() => {refresh({data:{person_id:saved.person_id}});});
      assert.equal(await extra.locator('[data-plan-field="name"]').inputValue(),'Wochenende');
      await page.locator('#settings-form [type="submit"]').click();
      await page.waitForFunction(() => payloads.length === 1);
      const payload = await page.evaluate(() => payloads[0]);
      assert.equal(payload.repeat_minutes,17);
      assert.equal(payload.time_plans.length,2);
      assert.deepEqual(payload.time_plans[1].weekdays,[5,6]);
      assert.equal(payload.time_plans[1].normal.night,'00:30');
      assert.equal(payload.time_plans[0].holiday.wake_time,'10:15');
      assert.ok(!Object.keys(payload).some(key => key.startsWith('time_') && key !== 'time_plans'));
      await page.waitForFunction(() => !panel._formDirty);
      // Remove is a draft operation; discard restores the stored entry.
      await extra.locator('summary').click();
      await extra.locator('[data-action="remove-time-plan"]').click();
      assert.equal(await page.locator('.time-plan').count(),1);
      await page.locator('[data-action="discard-changes"]').click();
      await page.waitForFunction(() => !panel._formDirty && panel.shadowRoot.querySelectorAll('.time-plan').length === 2);
      await page.locator('.time-plan').nth(1).locator('summary').click();
      await page.locator('.time-plan').nth(1).locator('[data-plan-field="enabled"]').uncheck();
      await page.locator('#settings-form [type="submit"]').click();
      await page.waitForFunction(() => payloads.length === 2 && !panel._formDirty);
      assert.equal(await page.evaluate(() => saved.settings.time_plans[1].enabled),false);
      // Stored holiday values survive switching off and normal-value edits.
      card = page.locator('.time-plan').first();
      await card.locator('summary').click();
      await card.locator('[data-plan-field="holiday_enabled"]').uncheck();
      await card.locator('[data-plan-set="normal"] [data-value="wake_time"]').fill('08:10');
      await page.locator('#settings-form [type="submit"]').click();
      await page.waitForFunction(() => payloads.length === 3 && !panel._formDirty);
      await card.locator('summary').click();
      await card.locator('[data-plan-field="holiday_enabled"]').check();
      assert.equal(await card.locator('[data-plan-set="holiday"] [data-value="wake_time"]').inputValue(),'10:15');
      // All controls fit the page and have usable widths at every viewport.
      const overflow = await page.evaluate(() => [...panel.shadowRoot.querySelectorAll('#settings-form input, #settings-form button, .time-plan')]
        .filter(el => el.getClientRects().length && (el.getBoundingClientRect().right > innerWidth + 1 || el.getBoundingClientRect().left < -1))
        .map(el => el.outerHTML.slice(0,120)));
      assert.deepEqual(overflow,[]);
      await card.scrollIntoViewIfNeeded();
      if (process.env.PILLPAL_SCREENSHOTS) {
        fs.mkdirSync(process.env.PILLPAL_SCREENSHOTS,{recursive:true});
        await card.evaluate(el => el.scrollIntoView({block:'start'}));
        await page.screenshot({path:path.join(process.env.PILLPAL_SCREENSHOTS,`time-plans-${width}.png`)});
      }
      await page.locator('[data-action="discard-changes"]').click();
      await page.waitForFunction(() => !panel._formDirty);
      await page.locator('.time-plan').nth(1).locator('summary').click();
      await page.locator('.time-plan').nth(1).locator('[data-action="remove-time-plan"]').click();
      await page.evaluate(() => {window.rejectSave=true;});
      await page.locator('#settings-form [type="submit"]').click();
      await page.waitForFunction(() => !panel._actionBusy && panel._feedback.type === 'error');
      assert.equal(await page.locator('.time-plan').count(),1);
      assert.equal(await page.evaluate(() => panel._formDirty),true);
      assert.equal(await page.evaluate(() => saved.settings.time_plans.length),2);
      await page.evaluate(() => {window.rejectSave=false;});
      await page.locator('#settings-form [type="submit"]').click();
      await page.waitForFunction(() => payloads.length === 4 && !panel._formDirty);
      assert.equal(await page.evaluate(() => saved.settings.time_plans.length),1);
      await page.evaluate(() => panel._navigate('overview',false));
      const dashboard = await page.evaluate(() => panel._overview(saved));
      assert.ok(dashboard.includes('Zeitplan: Wochenende'));
      assert.ok(dashboard.includes('Feiertagszeiten'));
      assert.ok(dashboard.includes('Start: Aufgestanden-Helfer'));
      assert.ok(dashboard.includes('Abend/Nacht: Smartphone-Wecker'));
      assert.deepEqual(errors,[]);
      console.log(`PASS ${width}px: plan CRUD, holiday retention, discard, refresh, layout and dashboard`);
      await page.close();
    }
  } finally {if (browser) await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
