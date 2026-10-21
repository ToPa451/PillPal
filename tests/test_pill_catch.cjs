/* Production game, real browser, simulated read-only HA bootstrap. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require('playwright');
const frontend = path.resolve(__dirname, '../custom_components/pillpal/frontend');
const component = fs.readFileSync(path.join(frontend,'pillpal-panel.js'),'utf8').match(/customElements.define\("([^"]+)"/)[1];
const profile = {person_id:'person.test',name:'Test',medications:[],archived_medications:[],as_needed_medications:[],doctors:[],settings:{},runtime:{},schedule:{},log:[]};
const meds = n => Array.from({length:n},(_,i) => ({id:`med-${i}`,name:i < 2 ? 'Identischer Name' : 'SehrLangerMedikamentenname'.repeat(5),doses:{morning:1,evening:i===0?2:0}}));
(async () => {
 const server = http.createServer((req,res) => {
   const file = path.join(frontend,path.basename(req.url.split('?')[0]));
   res.setHeader('Content-Type',req.url === '/' ? 'text/html; charset=utf-8' : file.endsWith('.css') ? 'text/css' : 'text/javascript');
   if (req.url === '/') res.end('<!doctype html><meta charset="utf-8"><body style="margin:0"><script src="/pillpal-panel.js"></script>');
   else if (fs.existsSync(file)) res.end(fs.readFileSync(file)); else {res.writeHead(404);res.end();}
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 let browser;
 try {
  browser = await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHANNEL ? {channel:process.env.PLAYWRIGHT_CHANNEL} : {})});
  const page = await browser.newPage({viewport:{width:390,height:844},hasTouch:true});
  const errors=[]; page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.evaluate(({profile,component})=>{
   window.saved=profile;window.writes=[];
   window.panel=document.createElement(component);panel._page='log';panel._panel={};
   panel._hass={connection:{subscribeEvents:async()=>()=>{},sendMessagePromise:async message=>{
    if(message.type!=='pillpal/bootstrap') writes.push(message);
    return structuredClone({profile:saved,selected_person_id:saved.person_id,version:'test',options:{}});
   }}};
   document.body.append(panel);
  },{profile:{...profile,medications:meds(20)},component});
  await page.locator('[data-action="pill-catch"]').click();
  await page.locator('pillpal-catch h1').waitFor();
  assert.match(await page.locator('pillpal-catch .shell').innerText(),/Pill★Catch entdeckt/);
  // Pure engine acceptance tests in the same production ES module.
  const result = await page.evaluate(async()=>{
   const {snapshotPlan,CatchRound}=await import('/pillpal_static_523b6/pillpal-catch.js?v=1');
   const p=snapshotPlan({...saved,medications:[...saved.medications,{id:'archive',name:'Archived',archived:true,doses:{morning:1}},{id:'prn',name:'PRN',pure_as_needed:true,doses:{}},saved.medications[0]]});
   if(p.meds.length!==20 || p.slots.length!==2) throw Error('adapter exclusions / duplicate ID');
   saved.medications[0].name='CHANGED'; if(p.meds[0].name==='CHANGED') throw Error('snapshot changed');
   const round=new CatchRound(p,0,()=>.9);
   const first=round.targets[0];
   round.objects=[{med:first,lane:0,born:0,land:4},{med:first,lane:1,born:1,land:5}];
   round.catch(first);round.catch(first);
   if(round.score!==100 || round.catches!==1) throw Error('duplicate scored');
   if(round.objects.length) throw Error('duplicate objects remain');
   const atDeadline=new CatchRound(snapshotPlan({medications:[{id:'a',name:'A',doses:{morning:1}}]}));
   atDeadline.nextSpawn=Infinity;atDeadline.objects=[{med:atDeadline.targets[0],lane:1,born:10,land:14}];atDeadline.advance(14);
   if(atDeadline.outcome!=='slot' || atDeadline.score!==100) throw Error('deadline collision');
   const late=new CatchRound(atDeadline.plan);late.nextSpawn=Infinity;late.objects=[{med:late.targets[0],lane:1,born:10,land:14.001}];late.advance(15);
   if(late.outcome!=='timeout') throw Error('late catch counted');
   const wrong=new CatchRound(p);wrong.catch({id:'obstacle',code:'×',name:'Hindernis'});if(wrong.outcome!=='wrong') throw Error('wrong catch');
   const complete=new CatchRound(p);
   complete.targets.forEach(m=>complete.catch(m));
   if(complete.completed!==1 || complete.score!==20*100+complete.budget*10) throw Error('slot bonus');
   complete.index++;complete.startSlot();
   if(complete.collected.size || complete.objects.length || complete.targets.length!==1) throw Error('slot reset');
   const day2=new CatchRound(p,1);
   if(day2.budget>=new CatchRound(p).budget || day2.fallTime>=4) throw Error('difficulty');
   for(const n of [1,3,4,8,12,20]) {
    const plan=snapshotPlan({medications:p.meds.slice(0,n).map(m=>({id:m.id,name:m.name,doses:{morning:1}}))});
    const r=new CatchRound(plan,0,()=>.9);let guard=0;
    while(!r.outcome && guard++<2000) {
     r.lane=r.objects.slice().sort((a,b)=>a.land-b.land)[0]?.lane??1;r.advance(.05);
     if(r.objects.length>2) throw Error('spawn limit');
    }
    if(r.outcome!=='slot' || r.catches!==n) throw Error(`unfair first pass ${n}`);
    if(r.objects.some(o=>r.collected.has(o.med.id))) throw Error('caught duplicate remains');
   }
   return {targets:p.meds.length,budget:round.budget};
  });
  assert.equal(result.targets,20);
  await page.locator('pillpal-catch [data-do="start"]').click();
  await page.waitForFunction(()=>document.querySelector('pillpal-catch').mode==='running');
  await page.locator('pillpal-catch [data-do="list"]').click();
  assert.equal(await page.locator('pillpal-catch .med-list li').count(),20);
  const time=await page.evaluate(()=>document.querySelector('pillpal-catch').round.elapsed);
  await page.waitForTimeout(150);
  assert.equal(await page.evaluate(()=>document.querySelector('pillpal-catch').round.elapsed),time);
  await page.locator('pillpal-catch [data-do="back"]').click();
  await page.locator('pillpal-catch [data-do="resume"]').click();
  await page.waitForFunction(()=>document.querySelector('pillpal-catch').mode==='running');
  await page.keyboard.press('1');
  assert.equal(await page.evaluate(()=>document.querySelector('pillpal-catch').round.lane),0);
  await page.keyboard.press('3');
  assert.equal(await page.evaluate(()=>document.querySelector('pillpal-catch').round.lane),2);
  await page.keyboard.press(' ');
  assert.equal(await page.evaluate(()=>document.querySelector('pillpal-catch').mode),'paused');
  await page.keyboard.press(' ');
  await page.waitForFunction(()=>document.querySelector('pillpal-catch').mode==='running');
  // Drag must retain the release lane, rather than the original pressed button.
  const control=await page.locator('pillpal-catch .controls').boundingBox();
  await page.mouse.move(control.x+15,control.y+20);await page.mouse.down();
  await page.evaluate(()=>{
    const g=document.querySelector('pillpal-catch');window.originalControls=g.shell.querySelector('.controls');
    const target=g.round.missing[0];
    g.round.nextSpawn=Infinity;g.round.objects=[{med:target,lane:g.round.lane,born:g.round.elapsed-3.99,land:g.round.elapsed+.01}];
  });
  await page.waitForFunction(()=>document.querySelector('pillpal-catch').round.catches===1);
  assert.equal(await page.evaluate(()=>document.querySelector('pillpal-catch').shell.querySelector('.controls')===originalControls),true);
  await page.mouse.move(control.x+control.width-15,control.y+20);await page.mouse.up();
  assert.equal(await page.evaluate(()=>document.querySelector('pillpal-catch').round.lane),2);
  await page.keyboard.press('Escape');
  assert.equal(await page.evaluate(()=>document.querySelector('pillpal-catch').mode),'paused');
  await page.evaluate(()=>history.back());
  await page.waitForFunction(()=>!document.querySelector('pillpal-catch').dialog.open);
  await page.locator('[data-action="pill-catch"]').click();
  assert.equal(await page.evaluate(()=>document.querySelector('pillpal-catch').mode),'paused');
  await page.evaluate(()=>{saved.person_id='person.other';panel._data.profile=saved;panel._render();});
  assert.equal(await page.locator('pillpal-catch [data-do="resume"]').count(),0);
  await page.locator('pillpal-catch [data-do="start"]').click();
  await page.waitForFunction(()=>document.querySelector('pillpal-catch').mode==='running');
  await page.evaluate(()=>{const g=document.querySelector('pillpal-catch');g.round.targets.forEach(m=>g.round.catch(m));});
  await page.locator('pillpal-catch [data-do="next"]').waitFor();
  await page.locator('pillpal-catch [data-do="next"]').click();
  await page.waitForFunction(()=>document.querySelector('pillpal-catch').mode==='running');
  assert.equal(await page.evaluate(()=>document.querySelector('pillpal-catch').round.collected.size),0);
  await page.evaluate(()=>{const g=document.querySelector('pillpal-catch');g.round.targets.forEach(m=>g.round.catch(m));});
  await page.locator('pillpal-catch [data-do="next"]').waitFor();
  assert.match(await page.locator('pillpal-catch h1').innerText(),/Tagesplan komplett/);
  await page.locator('pillpal-catch [data-do="next"]').click();
  assert.equal(await page.evaluate(()=>document.querySelector('pillpal-catch').round.day),1);
  await page.locator('pillpal-catch [data-do="pause"]').click();
  // Layout, many names, glass overflow, and both themes.
  for(const width of [320,360,390,430,1000]) {
   await page.setViewportSize({width,height:740});
   await page.waitForTimeout(100);
   await page.evaluate(()=>{const g=document.querySelector('pillpal-catch');g.checkSize();g.prepare();});
   await page.waitForFunction(()=>document.querySelector('pillpal-catch').mode==='running');
   const layout=await page.evaluate(()=>{
    const g=document.querySelector('pillpal-catch');g.pause();
    for(const m of g.round.targets.slice(0,12))g.round.catch(m);
    g.round.objects=[];g.round.spawn();g.round.elapsed=1;
    g.mode='running';g.render();
    const root=g.shadowRoot;const field=root.querySelector('.field').getBoundingClientRect();
    return {overflow:root.querySelector('.shell').scrollWidth>innerWidth,field:field.height,
     right:root.querySelector('.controls').getBoundingClientRect().right,
     bottom:root.querySelector('.controls').getBoundingClientRect().bottom,
     pills:root.querySelectorAll('.contents i').length,badge:root.querySelector('.contents b')?.textContent};
   });
   assert.equal(layout.overflow,false);assert.ok(layout.field>=200);assert.ok(layout.right<=width);assert.ok(layout.bottom<=740);assert.equal(layout.pills,8);assert.equal(layout.badge,'+4');
   if(process.env.PILLPAL_SCREENSHOTS) {
    fs.mkdirSync(process.env.PILLPAL_SCREENSHOTS,{recursive:true});
    for(const dark of [false,true]) {
     await page.evaluate(dark=>{document.querySelector('pillpal-catch').style.cssText=dark?'--card-background-color:#252b36;--primary-text-color:#eff2f8;--primary-color:#b49bdd;--secondary-text-color:#c1c9d5;--divider-color:#687187':'';},dark);
     await page.screenshot({path:path.join(process.env.PILLPAL_SCREENSHOTS,`catch-${width}-${dark?'dark':'light'}.png`)});
    }
   }
   await page.evaluate(()=>document.querySelector('pillpal-catch').pause());
  }
  await page.setViewportSize({width:740,height:390});
  await page.waitForTimeout(100);
  assert.match(await page.locator('pillpal-catch .shell').innerText(),/Hochformat/);
  // Visibility pause, no background clock, and storage denial keep game usable.
  await page.setViewportSize({width:390,height:844});
  await page.waitForTimeout(100);
  await page.evaluate(()=>{const g=document.querySelector('pillpal-catch');g.checkSize();g.prepare();});
  await page.waitForFunction(()=>document.querySelector('pillpal-catch').mode==='running');
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));});
  assert.equal(await page.evaluate(()=>document.querySelector('pillpal-catch').mode),'paused');
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:false});Object.defineProperty(window,'localStorage',{configurable:true,get(){throw Error('denied');}});});
  await page.locator('pillpal-catch [data-do="start"]').click();
  await page.locator('pillpal-catch [data-do="pause"]').click();
  await page.locator('pillpal-catch [data-do="close"]').click();
  await page.waitForFunction(()=>!document.querySelector('pillpal-catch'));
  assert.equal(await page.evaluate(()=>panel.shadowRoot.activeElement?.dataset.action),'pill-catch');
  // Empty plan offers explicit demo mode.
  await page.evaluate(()=>{saved.medications=[];panel._render();});
  await page.locator('[data-action="pill-catch"]').click();
  assert.match(await page.locator('pillpal-catch [data-do="start"]').innerText(),/Demomodus/);
  assert.deepEqual(await page.evaluate(()=>writes),[]);
  assert.deepEqual(errors,[]);
  console.log('PASS Pill★Catch: snapshot, 1–20 targets, duplicates, deadline, wrong catch, fair spawning, keyboard, drag, pause, back, profile change, 320–1000px, glass, visibility, storage denial, demo, zero writes');
 } finally {if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});

