// Self-contained game: no HA connection, services, intake or stock mutations.
const LABELS = { morning: 'Morgens', noon: 'Mittags', evening: 'Abends', night: 'Zur Nacht' };
const GREETINGS = { morning: 'Guten Morgen!', noon: 'Guten Mittag!', evening: 'Guten Abend!', night: 'Gute Nacht!' };
const ENDINGS = { morning: 'Schon Mittag!', noon: 'Schon Abend!', evening: 'Schon Nacht!', night: 'Der nächste Morgen ist da.' };
const html = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const SKY_LABELS = { morning: 'Sonnenaufgang', noon: 'Sonne', evening: 'Sonnenuntergang', night: 'Mond und Sterne' };
function skyIcon(slot) {
  const sun = `<circle cx="32" cy="30" r="12" fill="${slot === 'evening' ? '#ffae78' : '#ffd66d'}"/><path d="M32 7v5m0 36v5M9 30h5m36 0h5M16 14l4 4m24 24 4 4M16 46l4-4m24-24 4-4" stroke="#ffdc7d" stroke-width="3" stroke-linecap="round"/>`;
  const moon = '<path d="M43 9a23 23 0 1 0 12 34A24 24 0 0 1 43 9Z" fill="#e5d8ff"/><path d="m15 10 1.5 4.5L21 16l-4.5 1.5L15 22l-1.5-4.5L9 16l4.5-1.5ZM53 20l1 3 3 1-3 1-1 3-1-3-3-1 3-1Z" fill="#ffdf89"/>';
  const horizon = ['morning','evening'].includes(slot);
  return `<svg viewBox="0 0 64 64" aria-hidden="true">${slot === 'night' ? moon : horizon ? `<svg width="64" height="40" viewBox="0 0 64 40" overflow="hidden">${sun}</svg>` : sun}${horizon ? '<path d="M8 40h48m-40 7h32" stroke="#ffd188" stroke-width="3" stroke-linecap="round"/>' : ''}</svg>`;
}
function pillArt(med, extra = '', style = '') {
  const index = Math.max(0, Number(String(med.code).replace('M','')) - 1) || 0;
  return `<i aria-hidden="true" class="pill-art ${index % 3 === 0 ? 'tablet' : 'capsule'} palette-${index % 5} ${extra}" style="${style}"><span class="face"></span></i>`;
}
const GLASS = `<svg class="glass-art" viewBox="0 0 140 156" preserveAspectRatio="none" aria-hidden="true">
  <defs><linearGradient id="catch-glass" x1="0" x2="1"><stop stop-color="#b5e8ff" stop-opacity=".28"/><stop offset=".2" stop-color="#dcf6ff" stop-opacity=".05"/><stop offset=".75" stop-color="#dcf6ff" stop-opacity=".02"/><stop offset="1" stop-color="#b5e8ff" stop-opacity=".28"/></linearGradient><linearGradient id="catch-base" x2="0" y2="1"><stop stop-color="#d8f6ff" stop-opacity=".04"/><stop offset="1" stop-color="#a2dbff" stop-opacity=".55"/></linearGradient></defs>
  <path d="M7 14 19 132Q20 147 70 148T121 132L133 14" fill="url(#catch-glass)" stroke="#aee4ff" stroke-width="2"/>
  <path d="m19 129 1 10q50 25 100 0l1-10q-51 22-102 0Z" fill="url(#catch-base)" stroke="#bcf0ff" stroke-width="1.5"/>
  <path d="m16 26 10 98M122 27l-9 83" fill="none" stroke="#e2f8ff" stroke-opacity=".55" stroke-width="3" stroke-linecap="round"/>
  <path d="m23 27 5 45" stroke="#fff" stroke-opacity=".65" stroke-width="5" stroke-linecap="round"/>
  <ellipse cx="70" cy="14" rx="63" ry="10" fill="#8ddcff" fill-opacity=".06" stroke="#def7ff" stroke-width="2.5"/>
  <ellipse cx="70" cy="14" rx="58" ry="6" fill="none" stroke="#8bd2ff" stroke-opacity=".65"/>
</svg>`;

export function snapshotPlan(profile, date = new Date()) {
  // Current PillPal model has daily doses, no date-dependent medication rules.
  // Time plans affect slot times only. Never use runtime intake completion here.
  const seen = new Set();
  const meds = (profile?.medications || []).filter(m => {
    if (!m.id || seen.has(m.id) || m.archived || m.pure_as_needed || !Object.keys(LABELS).some(s => Number(m.doses?.[s]) > 0)) return false;
    seen.add(m.id); return true;
  }).map((m, i) => Object.freeze({ id: String(m.id), name: String(m.name), code: `M${i + 1}`, slots: Object.freeze(Object.keys(LABELS).filter(s => Number(m.doses?.[s]) > 0)) }));
  return Object.freeze({ personId: profile?.person_id || 'demo', name: String(profile?.name || ''), date: date.toLocaleDateString('de-DE'), meds: Object.freeze(meds), slots: Object.freeze(Object.keys(LABELS).filter(s => meds.some(m => m.slots.includes(s)))) });
}
const demoPlan = () => snapshotPlan({ person_id: 'demo', name: 'Demomodus', medications: [
  {id:'demo-a', name:'Beispiel Vitamin D', doses:{morning:1}},
  {id:'demo-b', name:'Beispiel Magnesium', doses:{morning:1, evening:1}},
  {id:'demo-c', name:'Beispiel Vitamin B12', doses:{noon:1}},
] });

export class CatchRound {
  constructor(plan, day = 0, random = Math.random) {
    this.plan = plan; this.day = day; this.random = random;
    this.score = 0; this.catches = 0; this.completed = 0; this.index = 0; this.lane = 1;
    this.startSlot();
  }
  startSlot() {
    this.slot = this.plan.slots[this.index];
    this.targets = this.plan.meds.filter(m => m.slots.includes(this.slot));
    this.collected = new Set(); this.objects = []; this.offered = new Set();
    this.elapsed = 0; this.budget = 8 + this.targets.length * Math.max(4, 6 - .25 * this.day);
    this.fallTime = Math.max(2.5, 4 - this.day * .15);
    this.nextSpawn = 0; this.serial = 0; this.outcome = null;
  }
  get missing() { return this.targets.filter(m => !this.collected.has(m.id)); }
  spawn() {
    if (this.objects.length >= (this.day ? 3 : 2)) return;
    const lanes = [0,1,2].filter(l => !this.objects.some(o => o.lane === l));
    if (!lanes.length) return;
    const pool = this.missing.filter(m => !this.objects.some(o => o.med.id === m.id));
    const first = pool.find(m => !this.offered.has(m.id));
    const distractions = this.plan.meds.filter(m => !m.slots.includes(this.slot));
    let med = first || pool[0];
    if (!first && this.offered.size >= this.targets.length && this.random() < .25) {
      med = distractions[Math.floor(this.random() * distractions.length)] || {id:'obstacle', code:'×', name:'Hindernis'};
    }
    if (!med) return;
    this.offered.add(med.id);
    this.objects.push({ med, lane: lanes[Math.floor(this.random() * lanes.length)], born: this.elapsed, land: this.elapsed + this.fallTime, serial: ++this.serial });
  }
  catch(med) {
    if (this.collected.has(med.id)) return;
    if (!this.targets.some(m => m.id === med.id)) {
      this.outcome = 'wrong'; this.reason = `${med.code} · ${med.name} statt eines noch benötigten ${LABELS[this.slot]}-Medikaments gefangen.`; return;
    }
    this.collected.add(med.id); this.catches++; this.score += 100;
    this.objects = this.objects.filter(o => o.med.id !== med.id);
    if (!this.missing.length) {
      this.score += Math.floor(Math.max(0, this.budget - this.elapsed)) * 10;
      this.completed++; this.outcome = 'slot';
    }
  }
  advance(seconds) {
    if (this.outcome) return;
    const end = Math.min(this.budget, this.elapsed + Math.max(0, seconds));
    // Process landings chronologically, including the deadline, before timeout.
    while (!this.outcome) {
      const next = Math.min(this.nextSpawn, ...this.objects.map(o => o.land));
      if (next > end) break;
      this.elapsed = next;
      const landed = this.objects.filter(o => o.land <= next);
      this.objects = this.objects.filter(o => o.land > next);
      for (const o of landed) {
        if (!this.outcome && o.lane === this.lane) this.catch(o.med);
      }
      if (!this.outcome && this.nextSpawn <= next) { this.spawn(); this.nextSpawn = next + 1.65; }
    }
    if (this.outcome) return;
    this.elapsed = end;
    if (end >= this.budget) { this.outcome = 'timeout'; this.reason = `${ENDINGS[this.slot]} Dein ${LABELS[this.slot]}-Set war noch nicht vollständig.`; }
  }
}

export class PillCatch extends HTMLElement {
  constructor() {
    super(); this.attachShadow({mode:'open'}); this.mode = 'intro';
    this._pop = () => { if (this.dialog.open) { this.pause(); this.dialog.close(); this.restoreFocus?.(); } this.inHistory = false; };
    this._visibility = () => { if (document.hidden) this.pause(); };
    this._resize = () => { this.checkSize(); this.pause(); if (this.dialog.open) this.render(); };
  }
  connectedCallback() {
    this.shadowRoot.innerHTML = `<link rel="stylesheet" href="/pillpal_static_523b7/pillpal-catch.css?v=2"><dialog aria-label="Pill★Catch" tabindex="-1"><div class="shell"></div></dialog>`;
    this.dialog = this.shadowRoot.querySelector('dialog');
    this.shell = this.shadowRoot.querySelector('.shell');
    this.dialog.addEventListener('cancel', e => { e.preventDefault(); this.pause(); });
    this.shadowRoot.addEventListener('click', e => {
      const button = e.target.closest('button'); if (!button) return;
      const action = button.dataset.do;
      if (action === 'start') this.start();
      if (action === 'resume') this.prepare();
      if (action === 'pause') this.pause();
      if (action === 'list') { this.pause(); this.mode = 'list'; this.render(); }
      if (action === 'back') { this.mode = 'paused'; this.render(); }
      if (action === 'close') this.dispose();
      if (action === 'next') {
        if (this.round.index + 1 < this.plan.slots.length) { this.round.index++; this.round.startSlot(); this.prepare(); }
        else { const old = this.round; this.round = new CatchRound(this.plan, old.day + 1); this.round.score = old.score; this.round.catches = old.catches; this.round.completed = old.completed; this.prepare(); }
      }
    });
    this.dialog.addEventListener('keydown', e => {
      if (['ArrowLeft','ArrowRight','1','2','3',' '].includes(e.key) && !e.target.matches('input')) {
        if (e.key === ' ' && e.target.matches('button') && !['running','paused'].includes(this.mode)) return;
        e.preventDefault();
        if (e.key === ' ') { if (this.mode === 'running') this.pause(); else if (this.mode === 'paused') this.prepare(); }
        else if (this.mode === 'running') this.move(e.key === 'ArrowLeft' ? this.round.lane - 1 : e.key === 'ArrowRight' ? this.round.lane + 1 : Number(e.key) - 1);
      }
    });
    window.addEventListener('popstate', this._pop);
    document.addEventListener('visibilitychange', this._visibility);
    window.addEventListener('orientationchange', this._resize);
    window.addEventListener('resize', this._resize);
  }
  open(profile) {
    this.profile = profile;
    if (!this.dialog.open) {
      history.pushState({...history.state, pillCatch:true}, '', location.href); this.inHistory = true;
      this.dialog.showModal();
    }
    this.render();
  }
  profileChanged(profile) {
    this.profile = profile;
    if (this.plan && this.profileId !== profile?.person_id) {
      this.pause(); this.changedProfile = true;
      if (this.dialog.open) this.render();
    }
  }
  storage(key, value) {
    try { if (value !== undefined) localStorage.setItem(key, String(value)); return localStorage.getItem(key); } catch { return null; }
  }
  start() {
    this.plan = snapshotPlan(this.profile); this.profileId = this.profile?.person_id;
    this.demo = !this.plan.slots.length; if (this.demo) this.plan = demoPlan();
    this.changedProfile = false; this.round = new CatchRound(this.plan);
    this.recordKey = `pillpal.catch.v1.${encodeURIComponent(this.profileId || 'demo')}.${this.demo ? 'demo' : 'normal'}`;
    this.record = Number(this.storage(this.recordKey)) || 0;
    this.prepare();
  }
  checkSize() { this.tooSmall = innerHeight < 480; }
  prepare() {
    this.checkSize();
    if (this.changedProfile || this.tooSmall || document.hidden) { this.mode = 'paused'; this.render(); return; }
    cancelAnimationFrame(this.frame); this.mode = 'ready'; this.readyAt = performance.now() + 1000; this.render();
    const fallHeight = this.landingY() - 34;
    if (fallHeight < 100) { this.tooSmall = true; this.mode = 'paused'; this.render(); return; }
    if (!this.round.elapsed && fallHeight < innerHeight * .4) this.round.fallTime = Math.max(5, this.round.fallTime);
    this.last = performance.now(); this.frame = requestAnimationFrame(t => this.tick(t));
  }
  tick(now) {
    if (!this.isConnected || !this.dialog.open) return;
    if (this.mode === 'ready' && now >= this.readyAt) { this.mode = 'running'; this.render(); this.last = now; }
    if (this.mode === 'running') {
      // Treat long frame gaps as an interruption, never charge hidden time.
      if (now - this.last > 1000) { this.pause(); return; }
      const count = this.round.catches;
      this.round.advance((now - this.last) / 1000);
      if (this.round.outcome) {
        this.record = Math.max(this.record, this.round.score); this.storage(this.recordKey, this.record);
        this.mode = this.round.outcome === 'slot' ? 'success' : 'result'; this.render(); return;
      }
      if (this.round.catches !== count) this.updateCatch();
      this.paint();
    }
    this.last = now;
    if (['ready','running'].includes(this.mode)) this.frame = requestAnimationFrame(t => this.tick(t));
  }
  pause() {
    cancelAnimationFrame(this.frame);
    if (['ready','running'].includes(this.mode)) { this.mode = 'paused'; this.render(); }
  }
  move(lane) {
    if (this.mode !== 'running') return;
    this.round.lane = Math.max(0, Math.min(2, lane)); this.paint();
  }
  list(items = this.round.targets) {
    return `<ul class="med-list">${[...items].sort((a,b) => Number(this.round.collected.has(a.id)) - Number(this.round.collected.has(b.id))).map(m => `<li><strong>${html(m.code)} · ${html(m.name)}</strong><span>${this.round.collected.has(m.id) ? '✓ Gesammelt' : 'Noch gesucht'}</span></li>`).join('')}</ul>`;
  }
  button(action, label) { return `<button data-do="${action}">${label}</button>`; }
  contents() {
    const collected = [...this.round.collected];
    return collected.slice(0,8).map((id,i) => pillArt(this.plan.meds.find(m => m.id === id), 'collected-pill', `--i:${i}`)).join('') + (collected.length > 8 ? `<b>+${collected.length - 8}</b>` : '');
  }
  needs() {
    const r = this.round;
    // Reserve space for names, even when the full set contains many targets.
    const preview = r.missing.slice(0,2);
    return preview.map(m => `<span class="need-chip ${r.collected.has(m.id) ? 'done' : ''}"><b>${r.collected.has(m.id) ? '✓' : html(m.code)}</b><span>${html(m.name)}</span></span>`).join('');
  }
  updateCatch() {
    // Preserve the control DOM and its pointer capture throughout a drag.
    const r = this.round;
    this.shell.querySelector('.score').textContent = `${r.score} Punkte`;
    this.shell.querySelector('.collection-status').textContent = `${LABELS[r.slot]} · ${r.collected.size} von ${r.targets.length} gesammelt`;
    this.shell.querySelector('.need-preview').innerHTML = this.needs();
    this.shell.querySelector('.contents').innerHTML = this.contents();
    this.shell.querySelector('.glass-count').textContent = `${r.collected.size} / ${r.targets.length}`;
    this.shell.querySelector('.catch-feedback').innerHTML = '<span>+100 ✦</span>';
  }
  render() {
    const focused = this.shadowRoot.activeElement;
    const focusAction = focused?.dataset.do;
    const fieldFocused = focused?.classList.contains('field');
    const r = this.round;
    this.shell.dataset.slot = r?.slot || 'night';
    let content;
    if (this.mode === 'intro') {
      const plan = snapshotPlan(this.profile);
      content = `<section class="menu intro"><div class="intro-art" aria-hidden="true">${pillArt({code:'M2'})}${pillArt({code:'M1'})}<span>✦</span></div><div class="eyebrow">DEIN KLEINES PLAN-ABENTEUER</div><h1>Pill<span class="brand-star">★</span>Catch entdeckt!</h1><p>Dein Medikamentenplan als Mini-Spiel. Bewege das Glas und sammle alle Medikamente eines Tagesabschnitts.</p><p>${plan.slots.length ? `Profil: ${html(plan.name)} · ${html(plan.date)}` : 'Kein regulärer Plan vorhanden. Du kannst im Demomodus mit Beispieldaten spielen.'}</p><div class="tutorial">${pillArt({code:'M3'})}<p>Fange die gesuchten Kennungen in deinem Glas. <strong>× Hindernis</strong> und Medikamente anderer Abschnitte lässt du vorbeifallen.</p></div><p>Tippe direkt ins Spielfeld oder ziehe unten nach links oder rechts. Auch ← → / 1 2 3 bewegen das Glas. Leertaste pausiert. „Alle Medikamente“ zeigt die vollständigen Namen bei angehaltener Uhr.</p><p>Die Spielzeit ist fiktiv. Farben und Pillenformen sind Spielgrafiken; Kennungen sind keine Dosierungen.</p><strong class="game-notice">Nur ein Spiel · keine Einnahmebestätigung</strong><div class="actions">${this.button('start', plan.slots.length ? 'Spiel starten' : 'Demomodus starten')}${this.button('close','Zurück')}</div></section>`;
    } else if (['ready','running'].includes(this.mode)) {
      content = `<header><strong>Pill<span class="brand-star">★</span>Catch</strong><span class="score">${r.score} Punkte</span>${this.button('pause','Pause')}</header>
        <section class="task"><div class="task-heading"><div class="sky-icon" role="img" aria-label="${SKY_LABELS[r.slot]}">${skyIcon(r.slot)}</div><div><strong>${GREETINGS[r.slot]}</strong><span>Sammle dein ${LABELS[r.slot]}-Set.</span></div></div><div class="task-status"><span class="collection-status" aria-live="polite">${LABELS[r.slot]} · ${r.collected.size} von ${r.targets.length} gesammelt</span><span class="time"></span></div><div class="time-track"><progress max="${r.budget}" value="${r.budget-r.elapsed}" aria-label="Verbleibende Spielzeit"></progress><span class="time-spark" aria-hidden="true">✦</span></div></section>
        <div class="needs"><div class="need-preview">${this.needs()}</div>${this.button('list',`Alle Medikamente (${r.targets.length})`)}</div>
        <div class="field" tabindex="0" role="group" aria-label="Spielfeld mit drei Fangspuren. Tippen oder ziehen bewegt das Glas. Tastatur: Pfeile oder 1, 2, 3."><div class="scene-sky" aria-hidden="true">${skyIcon(r.slot)}</div><div class="lanes" aria-hidden="true"></div><div class="falling" aria-hidden="true"></div><div class="glass" aria-label="Glas"><span class="glass-count">${r.collected.size} / ${r.targets.length}</span><span class="rim" aria-hidden="true"></span>${GLASS}<div class="contents">${this.contents()}</div><div class="catch-feedback" aria-hidden="true"></div></div><div class="controls" aria-hidden="true"><span>‹</span><span class="drag-hint">Glas hier bewegen<span class="drag-line"></span></span><span>›</span></div>${this.mode === 'ready' ? '<div class="ready"><span>✦</span>Bereit?</div>' : ''}</div>
        <footer>${this.demo ? 'Demomodus · Beispieldaten' : 'Spielmodus'} · Spieltag ${r.day+1} · Tippen oder ziehen</footer>`;
    } else if (this.mode === 'list') {
      content = `<section class="menu"><h1>Alle Medikamente</h1><p>${LABELS[r.slot]} · ${r.collected.size} von ${r.targets.length} gesammelt · Spiel pausiert</p>${this.list()}${this.button('back','Zurück zur Pause')}</section>`;
    } else if (this.mode === 'paused') {
      content = `<section class="menu"><h1>Pill★Catch pausiert</h1><p>${this.changedProfile ? 'Das Profil wurde gewechselt. Starte eine neue Runde mit dem ausgewählten Profil.' : this.tooSmall ? 'Zu wenig Platz: Bitte ins Hochformat wechseln oder die Ansicht vergrößern.' : 'Glas, Objekte und Uhr stehen still. Dein Fortschritt bleibt erhalten.'}</p><div class="actions">${!this.changedProfile && !this.tooSmall ? this.button('resume','Weiter') : ''}${this.button('start','Neu starten')}${this.button('close','Spiel verlassen')}</div></section>`;
    } else {
      const success = this.mode === 'success';
      const dayDone = success && r.index + 1 === this.plan.slots.length;
      content = `<section class="menu"><div class="result-art" aria-hidden="true">${success ? '✦' : '☆'}</div><h1>${dayDone ? 'Tagesplan komplett gesammelt!' : success ? `${LABELS[r.slot]}-Set vollständig!` : 'Runde vorbei'}</h1><p>${success ? 'Gut gefangen! Dein Glas ist vollständig.' : html(r.reason)}</p><div class="result">${r.score} Punkte</div><p>${r.catches} richtige Fänge · ${r.completed} geschaffte Abschnitte · Rekord ${this.record}</p>${r.outcome === 'timeout' ? `<h2>Noch fehlend</h2>${this.list(r.missing)}` : ''}<p>Dein Medikamentenplan bleibt unverändert.</p><div class="actions">${success ? this.button('next',dayDone ? 'Nächster Spieltag' : 'Nächster Abschnitt') : ''}${this.button('start','Noch einmal')}${this.button('close','Zurück zu Log & Info')}</div></section>`;
    }
    this.shell.innerHTML = content;
    if (['ready','running'].includes(this.mode)) { this.bindPointer(); this.paint(); }
    const nextFocus = fieldFocused ? this.shell.querySelector('.field') : focusAction ? this.shell.querySelector(`[data-do="${focusAction}"]`) : null;
    (nextFocus || this.shell.querySelector('button'))?.focus({preventScroll:true});
  }
  bindPointer() {
    const controls = this.shell.querySelector('.field');
    const select = e => { const rect = controls.getBoundingClientRect(); this.move(Math.floor((e.clientX - rect.left) / rect.width * 3)); };
    controls.onpointerdown = e => { if (!e.isPrimary || e.button !== 0) return; this.pointer = e.pointerId; controls.setPointerCapture(e.pointerId); controls.focus({preventScroll:true}); select(e); };
    controls.onpointermove = e => { if (this.pointer === e.pointerId) select(e); };
    const end = e => { if (this.pointer === e.pointerId) { this.pointer = null; if (controls.hasPointerCapture(e.pointerId)) controls.releasePointerCapture(e.pointerId); } };
    controls.onpointerup = end; controls.onpointercancel = end; controls.onlostpointercapture = () => { this.pointer = null; };
  }
  landingY() {
    const field = this.shell.querySelector('.field');
    return this.shell.querySelector('.rim').getBoundingClientRect().top - field.getBoundingClientRect().top - field.clientTop;
  }
  paint() {
    const r = this.round; const field = this.shell.querySelector('.field'); if (!field) return;
    // Read the responsive glass opening: the pill's center reaches that exact
    // plane at o.land. Labels are separate; side/bottom contacts never count.
    const travel = Math.max(1, this.landingY() - 34);
    this.shell.querySelector('.falling').innerHTML = r.objects.map(o => {
      const phase = (r.elapsed-o.born)/r.fallTime;
      return `<div class="object ${o.med.id === 'obstacle' ? 'obstacle' : ''}" style="left:${(o.lane+.5)/3*100}%;top:${34+phase*travel}px;--label-opacity:${Math.max(0,Math.min(1,(.98-phase)/.14))}">${o.med.id === 'obstacle' ? '<i class="obstacle-art">×</i>' : pillArt(o.med)}<div class="object-label"><b>${html(o.med.code)}</b><span>${html(o.med.name)}</span></div></div>`;
    }).join('');
    this.shell.querySelector('.glass').style.left = `${(r.lane + .5) / 3 * 100}%`;
    this.shell.querySelector('.time').textContent = `${Math.ceil(r.budget - r.elapsed)} s`;
    this.shell.querySelector('progress').value = r.budget - r.elapsed;
    this.shell.querySelector('.time-spark').style.left = `${Math.max(0,1-r.elapsed/r.budget)*100}%`;
    field.style.setProperty('--active-lane', r.lane);
  }
  dispose() {
    const back = this.inHistory; this.inHistory = false;
    this.dialog?.close(); this.remove(); this.restoreFocus?.();
    if (back) history.back();
  }
  disconnectedCallback() {
    cancelAnimationFrame(this.frame); this.pointer = null;
    window.removeEventListener('popstate', this._pop);
    document.removeEventListener('visibilitychange', this._visibility);
    window.removeEventListener('orientationchange', this._resize);
    window.removeEventListener('resize', this._resize);
  }
}
customElements.define('pillpal-catch', PillCatch);
