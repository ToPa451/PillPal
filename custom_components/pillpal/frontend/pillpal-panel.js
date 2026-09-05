const PAGES = [
  ["overview", "mdi:clipboard-clock-outline", "Übersicht"],
  ["bedarf", "mdi:flask-plus-outline", "Bedarf"],
  ["statistik", "mdi:chart-box-outline", "Statistik"],
  ["bestand", "mdi:clipboard-list-outline", "Bestand"],
  ["praxis", "mdi:doctor", "Praxis"],
  ["verwalten", "mdi:medical-bag", "Verwalten"],
  ["zeiten", "mdi:clock-outline", "Zeiten"],
  ["benachrichtigungen", "mdi:bell-cog-outline", "Benachrichtigungen"],
  ["schnittstellen", "mdi:cog-transfer-outline", "Schnittstellen"],
  ["log", "mdi:text-box-search-outline", "Log & Info"],
];

const SLOT_LABELS = { morning: "Morgens", noon: "Mittags", evening: "Abends", night: "Zur Nacht" };
const SLOT_ICONS = { morning: "mdi:weather-sunset-up", noon: "mdi:white-balance-sunny", evening: "mdi:weather-sunset-down", night: "mdi:weather-night" };
const STATUS_LABELS = {
  not_planned: "Nicht geplant", planned: "Geplant", pending: "Ausstehend",
  notified: "Benachrichtigt", snoozed: "Zurückgestellt", taken: "Eingenommen",
  skipped: "Übersprungen", missed: "Verpasst", already_taken: "Bereits eingenommen",
  already_skipped: "Bereits übersprungen", active: "Aktiv", ended: "Beendet",
};
const EVENT_LABELS = {
  regular_planned: "Ausstehend", regular_taken: "Eingenommen",
  regular_snoozed: "Zurückgestellt", regular_skipped: "Übersprungen",
  regular_missed: "Verpasst", as_needed: "Bedarf",
  as_needed_max_override: "Bedarf · bestätigte Höchstdosis-Ausnahme",
};
const UNIT_OPTIONS = [
  ["Einheit", "Einheiten"], ["Tablette", "Tabletten"], ["Kapsel", "Kapseln"],
  ["Tropfen", "Tropfen"], ["Zäpfchen", "Zäpfchen"], ["Beutel", "Beutel"],
  ["Sprühstoß", "Sprühstöße"], ["Hub", "Hübe"], ["Pflaster", "Pflaster"],
  ["mg", "mg"], ["ml", "ml"], ["Löffel", "Löffel"], ["Anwendung", "Anwendungen"],
  ["Stück", "Stück"], ["Tube", "Tuben"], ["Strang", "Stränge"],
];
const PAGE_META = {
  overview: ["Übersicht", "Aktueller Status, heutige Einnahmen und Schnellaktionen", "banner_uebersicht.png", "#c65d72"],
  bedarf: ["Bedarfseinnahme", "Einnahme schnell buchen mit Bestands- und Maximaldosis-Prüfung", "banner_bedarf.png", "#9b63bd"],
  statistik: ["Statistik", "Personenbezogene Auswertung ohne vermischte Buchungen", "banner_statistik.png", "#378ce0"],
  bestand: ["Medikamentenplan", "Aktive Präparate, Reichweiten, Bestellungen und MHD", "banner_bestand.png", "#18aab7"],
  praxis: ["Praxistage", "Status zur Praxisöffnung und zukünftige Schließzeiten", "banner_praxis.png", "#78b94f"],
  verwalten: ["Medikamente verwalten", "Stammdaten, Dosen, Bestand und Archiv", "banner_verwalten.png", "#d49a21"],
  zeiten: ["Zeiten & Fristen", "Einnahmezeiten, Intervalle und Warnfristen", "banner_zeiten.png", "#ef7d25"],
  benachrichtigungen: ["Benachrichtigungen", "Texte und Verhalten der Erinnerungen", "banner_benachrichtigungen.png", "#9a5eae"],
  schnittstellen: ["Schnittstellen", "Externe Entitäten für ein- und ausgehende Informationen", "banner_system.png", "#c75f5f"],
  log: ["Log & Info", "Diagnoseereignisse der letzten 48 Stunden und Versions-Info", "banner_log.png", "#6887aa"],
};

const esc = (value) => String(value ?? "")
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
const num = (value) => Number(value || 0).toLocaleString("de-DE", { maximumFractionDigits: 3 });
const days = (value) => `${num(value)} ${Number(value) === 1 ? "Tag" : "Tage"}`;
const brand = (small = false) => `Pill<span class="star${small ? " small" : ""}">★</span>Pal`;
const dateTime = (value) => value ? new Intl.DateTimeFormat("de-DE", { dateStyle: "short", timeStyle: "short" }).format(new Date(value)) : "–";
const timeOnly = (value) => value ? new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit" }).format(new Date(value)) : "–";
const dateOnly = (value) => value ? new Intl.DateTimeFormat("de-DE").format(new Date(`${value}T12:00:00`)) : "–";

class PillPalPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._page = "overview";
    this._data = null;
    this._feedback = null;
    this._prnId = "";
    this._prnQuantity = 1;
    this._prnConfirmation = null;
    this._medId = "";
    this._adminMode = false;
    this._statsPeriod = "7";
    this._statsMedication = "";
    this._statsSlot = "";
    this._statsFrom = "";
    this._statsTo = "";
    this._statsSelectedDate = "";
    this._statistics = null;
    this._statisticsLoading = false;
    this._statisticsError = "";
    this._statisticsRequest = 0;
    this._statisticsPersonId = "";
    this._loadRequest = 0;
    this._personGeneration = 0;
    this._connected = false;
    this._clockTimer = null;
    this._touchStart = null;
    this._onPopState = () => {
      const page = this._pageFromLocation();
      if (page && page !== this._page) {
        if (!this._allowDiscard()) {
          const panel = this._adminMode ? "pillpal-admin" : "pillpal";
          history.pushState({}, "", `/${panel}/${this._page}`);
          return;
        }
        this._page = page;
        this._feedback = null;
        this._render();
        if (page === "overview") this._scheduleRefresh();
        if (page === "statistik") this._loadStatistics();
      }
    };
  }

  set hass(value) {
    this._hass = value;
    this._start();
  }

  set panel(value) {
    this._panel = value;
    this._adminMode = Boolean(value?.config?.admin_mode) || location.pathname.includes("pillpal-admin");
    this._start();
  }

  get hass() { return this._hass; }

  connectedCallback() {
    this._connected = true;
    this._bind();
    this._page = this._pageFromLocation() || this._page;
    window.addEventListener("popstate", this._onPopState);
    clearInterval(this._clockTimer);
    this._clockTimer = setInterval(() => {
      if (this._connected && this._page === "overview") this._scheduleRefresh();
    }, 5000);
    this._start();
  }

  disconnectedCallback() {
    this._connected = false;
    this._unsubscribe?.();
    this._unsubscribe = null;
    clearTimeout(this._refreshTimer);
    clearInterval(this._clockTimer);
    this._clockTimer = null;
    window.removeEventListener("popstate", this._onPopState);
    this._started = false;
  }

  async _start() {
    if (!this._connected || !this._hass || !this._panel || this._started) return;
    this._started = true;
    this._renderLoading();
    try {
      this._unsubscribe = await this._hass.connection.subscribeEvents(
        (event) => {
          if (!this._data?.selected_person_id || event.data.person_id === this._data.selected_person_id) {
            this._scheduleRefresh();
          }
        },
        "pillpal_updated",
      );
      await this._load();
    } catch (err) {
      this._renderFatal(err);
    }
  }

  _scheduleRefresh() {
    if (this._actionBusy || this._formDirty) return;
    const active = this.shadowRoot.activeElement;
    if (active?.matches?.("select,input,textarea")) {
      this._refreshPending = true;
      return;
    }
    this._refreshPending = false;
    clearTimeout(this._refreshTimer);
    this._refreshTimer = setTimeout(() => this._load(this._data?.selected_person_id, true), 80);
  }

  async _load(personId = null, quiet = false) {
    const request = ++this._loadRequest;
    if (!quiet) this._busy = true;
    try {
      const data = await this._hass.connection.sendMessagePromise({
        type: "pillpal/bootstrap",
        admin_mode: this._adminMode,
        ...(personId ? { person_id: personId } : {}),
      });
      if (request !== this._loadRequest) return false;
      this._data = data;
      if (this._statisticsPersonId && this._statisticsPersonId !== this._data?.selected_person_id) {
        this._statistics = null;
        this._statsMedication = "";
        this._statsSelectedDate = "";
      }
      const meds = this._data?.profile?.as_needed_medications || [];
      if (!meds.some((item) => item.id === this._prnId)) this._prnId = meds[0]?.id || "";
      const selected = meds.find((item) => item.id === this._prnId);
      if (selected && (!this._prnQuantity || this._prnQuantity < selected.step)) this._prnQuantity = selected.step;
      const activeMedications = this._data?.profile?.medications || [];
      const manageable = this._data?.profile?.settings?.show_archived
        ? [...activeMedications, ...(this._data?.profile?.archived_medications || [])]
        : activeMedications;
      if (!manageable.some((item) => item.id === this._medId)) this._medId = manageable[0]?.id || "";
      this._formDirty = false;
      this._refreshPending = false;
      return true;
    } finally {
      if (request !== this._loadRequest) return;
      this._busy = false;
      this._render();
      if (this._page === "statistik") this._loadStatistics();
    }
  }

  _bind() {
    this.shadowRoot.addEventListener("click", (event) => this._click(event));
    this.shadowRoot.addEventListener("change", (event) => this._change(event));
    this.shadowRoot.addEventListener("input", (event) => {
      if (event.target.closest?.("#settings-form,#med-form,#closure-form")) this._formDirty = true;
    });
    this.shadowRoot.addEventListener("submit", (event) => this._submit(event));
    this.shadowRoot.addEventListener("focusout", () => {
      if (!this._refreshPending) return;
      setTimeout(() => this._scheduleRefresh(), 0);
    });
    this.shadowRoot.addEventListener("touchstart", (event) => this._touchBegin(event), { passive: true });
    this.shadowRoot.addEventListener("touchmove", (event) => this._touchMove(event), { passive: false });
    this.shadowRoot.addEventListener("touchend", (event) => this._touchEnd(event), { passive: true });
  }

  _pageFromLocation() {
    const candidate = location.pathname.split("/").filter(Boolean).at(-1);
    return PAGES.some(([id]) => id === candidate) ? candidate : null;
  }

  _navigate(page, push = true) {
    if (!PAGES.some(([id]) => id === page) || page === this._page) return;
    if (!this._allowDiscard()) return;
    this._page = page;
    this._feedback = null;
    if (push) {
      const panel = this._adminMode ? "pillpal-admin" : "pillpal";
      history.pushState({}, "", `/${panel}/${page}`);
    }
    this._render();
    if (page === "overview") this._scheduleRefresh();
    if (page === "statistik") this._loadStatistics();
  }

  _allowDiscard() {
    if (!this._formDirty) return true;
    if (!window.confirm("Ungespeicherte Änderungen verwerfen?")) return false;
    this._formDirty = false;
    this._feedback = null;
    return true;
  }

  _touchIgnored(target) {
    return Boolean(target?.closest?.("nav,select,input,textarea,button,a,.number-field,.stepper,.table-wrap,.log-scroll,[data-no-swipe]"));
  }

  _touchBegin(event) {
    if (event.touches.length !== 1 || this._touchIgnored(event.target)) {
      this._touchStart = null;
      return;
    }
    const touch = event.touches[0];
    this._touchStart = { x: touch.clientX, y: touch.clientY, dx: 0, dy: 0 };
  }

  _touchMove(event) {
    if (!this._touchStart || event.touches.length !== 1) return;
    const touch = event.touches[0];
    this._touchStart.dx = touch.clientX - this._touchStart.x;
    this._touchStart.dy = touch.clientY - this._touchStart.y;
    if (Math.abs(this._touchStart.dx) > 18 && Math.abs(this._touchStart.dx) > Math.abs(this._touchStart.dy) * 1.25) {
      event.preventDefault();
    }
  }

  _touchEnd() {
    const gesture = this._touchStart;
    this._touchStart = null;
    if (!gesture || Math.abs(gesture.dx) < Math.max(54, innerWidth * 0.15) || Math.abs(gesture.dx) <= Math.abs(gesture.dy) * 1.25) return;
    const index = PAGES.findIndex(([id]) => id === this._page);
    const direction = gesture.dx < 0 ? 1 : -1;
    const next = (index + direction + PAGES.length) % PAGES.length;
    this._navigate(PAGES[next][0]);
  }

  async _click(event) {
    const target = event.target.closest("[data-page],[data-action],[data-step],[data-adjust],[data-med],[data-stats-date],[data-closure-remove]");
    if (!target) return;
    if (target.dataset.page) {
      this._navigate(target.dataset.page);
      return;
    }
    if (target.dataset.med) {
      this._medId = target.dataset.med;
      this._render();
      return;
    }
    if (target.dataset.statsDate) {
      this._statsSelectedDate = target.dataset.statsDate;
      await this._loadStatistics();
      return;
    }
    if (target.dataset.closureRemove !== undefined) {
      const index = Number(target.dataset.closureRemove);
      const closures = (this._data.practice_closures || []).filter((_, itemIndex) => itemIndex !== index);
      if (!Number.isInteger(index) || index < 0 || index >= (this._data.practice_closures || []).length) return;
      if (!window.confirm("Diese Praxisschließung wirklich entfernen?")) return;
      await this._call("update_practice_closures", { closures }, "Praxisschließung wird entfernt …", "Praxisschließung wurde entfernt.", "closure-form");
      return;
    }
    if (target.dataset.step) {
      const medication = this._selectedPrn();
      if (!medication) return;
      const direction = Number(target.dataset.step);
      const step = Number(medication.step || 1);
      const next = Math.round((Number(this._prnQuantity) + direction * step) * 1000) / 1000;
      this._prnQuantity = Math.max(step, next);
      this._prnConfirmation = null;
      this._render();
      return;
    }
    if (target.dataset.adjust) {
      const input = this.shadowRoot.querySelector(`[name="${CSS.escape(target.dataset.adjust)}"]`);
      if (!input) return;
      const step = Number(target.dataset.amount || input.step || 1);
      const min = input.min === "" ? -Infinity : Number(input.min);
      const max = input.max === "" ? Infinity : Number(input.max);
      input.value = String(Math.min(max, Math.max(min, Math.round((Number(input.value || 0) + Number(target.dataset.direction) * step) * 1000) / 1000)));
      input.dispatchEvent(new Event("input", { bubbles: true }));
      return;
    }
    if (target.dataset.action) await this._handleAction(target.dataset.action, target);
  }

  async _change(event) {
    const el = event.target;
    if (el.name === "as_needed_allowed") {
      const settings = this.shadowRoot.querySelector(".prn-settings");
      if (settings) settings.hidden = !el.checked;
      return;
    } else if (el.name === "expiry_enabled") {
      const settings = this.shadowRoot.querySelector(".expiry-settings");
      if (settings) settings.hidden = !el.checked;
      return;
    } else if (el.id === "person-select") {
      if (!this._allowDiscard()) { this._render(); return; }
      this._personGeneration += 1;
      this._feedback = null;
      await this._load(el.value);
    } else if (el.id === "prn-select") {
      this._prnId = el.value;
      const med = this._selectedPrn();
      this._prnQuantity = Number(med?.step || 1);
      this._prnConfirmation = null;
      this._render();
    } else if (el.id === "med-select") {
      if (!this._allowDiscard()) { this._render(); return; }
      this._medId = el.value;
      this._feedback = null;
      this._render();
    } else if (el.id === "stats-period") {
      this._statsPeriod = el.value;
      this._statsSelectedDate = "";
      await this._loadStatistics();
    } else if (el.id === "stats-medication") {
      this._statsMedication = el.value;
      await this._loadStatistics();
    } else if (el.id === "stats-slot") {
      this._statsSlot = el.value;
      await this._loadStatistics();
    } else if (el.id === "stats-from") {
      this._statsFrom = el.value;
      this._statsSelectedDate = "";
      await this._loadStatistics();
    } else if (el.id === "stats-to") {
      this._statsTo = el.value;
      this._statsSelectedDate = "";
      await this._loadStatistics();
    } else if (el.id === "stats-show-archived") {
      if (!el.checked && this._data?.profile?.archived_medications?.some((med) => med.id === this._statsMedication)) {
        this._statsMedication = "";
      }
      await this._call("update_settings", { settings: { statistics_show_archived: el.checked } }, "Statistik wird aktualisiert …", "Statistikfilter wurde aktualisiert.");
    } else if (el.id === "show-archived") {
      if (!this._allowDiscard()) { this._render(); return; }
      if (!el.checked && this._selectedMedication()?.archived) {
        this._medId = this._data?.profile?.medications?.[0]?.id || "";
      }
      await this._call("update_settings", { settings: { show_archived: el.checked } }, "Ansicht wird aktualisiert …", "Archivansicht wurde aktualisiert.", "med-actions");
    }
  }

  async _submit(event) {
    event.preventDefault();
    const form = event.target;
    if (form.id === "settings-form") await this._saveSettings(form);
    if (form.id === "med-form") await this._saveMedication(form);
    if (form.id === "closure-form") await this._saveClosure(form);
  }

  async _call(action, data, pending, success, scope = "page") {
    if (!this._data?.selected_person_id) {
      this._setFeedback("error", "Für diese Aktion ist keine betreute Person ausgewählt.", scope);
      return null;
    }
    if (this._actionBusy) return null;
    const personId = this._data.selected_person_id;
    const personGeneration = this._personGeneration;
    this._actionBusy = true;
    this._setFeedback("pending", pending, scope);
    try {
      const result = await this._hass.connection.sendMessagePromise({
        type: "pillpal/action",
        person_id: personId,
        admin_mode: this._adminMode,
        action,
        data,
      });
      this._feedback = result?.status === "confirmation_required"
        ? { type: "error", text: result.confirmation?.warning || "Eine ausdrückliche zweite Bestätigung ist erforderlich.", scope }
        : { type: "success", text: success, scope };
      if (personGeneration === this._personGeneration && this._data?.selected_person_id === personId) {
        await this._load(personId, true);
      } else {
        this._feedback = null;
        const slot = this.shadowRoot.querySelector(`[data-feedback-scope="${CSS.escape(scope)}"]`);
        if (slot) slot.innerHTML = "";
      }
      return result;
    } catch (err) {
      if (personGeneration === this._personGeneration && this._data?.selected_person_id === personId) {
        this._setFeedback("error", err?.message || String(err), scope);
      }
      return null;
    } finally {
      this._actionBusy = false;
    }
  }

  _setFeedback(type, text, scope = "page") {
    this._feedback = { type, text, scope };
    const slot = this.shadowRoot.querySelector(`[data-feedback-scope="${CSS.escape(scope)}"]`);
    if (slot) slot.innerHTML = this._feedbackMarkup(scope);
  }

  async _discardChanges(scope) {
    if (this._actionBusy || !this._data?.selected_person_id) return;
    this._formDirty = false;
    this._setFeedback("pending", "Gespeicherter Stand wird geladen …", scope);
    try {
      await this._load(this._data.selected_person_id, true);
      this._setFeedback("success", "Ungespeicherte Änderungen wurden verworfen.", scope);
    } catch (err) {
      this._setFeedback("error", `Gespeicherter Stand konnte nicht geladen werden: ${err?.message || err}`, scope);
    }
  }

  _statisticsMessage() {
    const message = {
      type: "pillpal/statistics",
      person_id: this._data.selected_person_id,
      admin_mode: this._adminMode,
      days: Math.max(1, Math.min(3660, Number(this._statsPeriod) || 7)),
      ...(this._statsMedication ? { medication_id: this._statsMedication } : {}),
      ...(this._statsSlot ? { slot: this._statsSlot } : {}),
      ...(this._statsSelectedDate ? { selected_day: this._statsSelectedDate } : {}),
    };
    if (this._statsPeriod === "custom") {
      const today = new Date();
      const fallbackTo = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
      const fallbackFromDate = new Date(today); fallbackFromDate.setDate(today.getDate() - 6);
      const fallbackFrom = `${fallbackFromDate.getFullYear()}-${String(fallbackFromDate.getMonth() + 1).padStart(2, "0")}-${String(fallbackFromDate.getDate()).padStart(2, "0")}`;
      this._statsFrom ||= fallbackFrom;
      this._statsTo ||= fallbackTo;
      message.start_date = this._statsFrom;
      message.end_date = this._statsTo;
    }
    return message;
  }

  async _loadStatistics() {
    if (!this._data?.selected_person_id || this._page !== "statistik") return;
    const request = ++this._statisticsRequest;
    this._statisticsLoading = true;
    this._statisticsError = "";
    this._render();
    try {
      const result = await this._hass.connection.sendMessagePromise(this._statisticsMessage());
      if (request !== this._statisticsRequest) return;
      this._statistics = result;
      this._statisticsPersonId = this._data.selected_person_id;
      this._statsSelectedDate = result.selected_day || "";
    } catch (err) {
      if (request !== this._statisticsRequest) return;
      this._statisticsError = err?.message || String(err);
    } finally {
      if (request === this._statisticsRequest) {
        this._statisticsLoading = false;
        this._render();
      }
    }
  }

  async _handleAction(action, target) {
    if (action === "discard-changes") {
      await this._discardChanges(target.dataset.scope || "page");
    } else if (action === "confirm" || action === "snooze" || action === "skip") {
      const service = action === "confirm" ? "confirm_slot" : action === "snooze" ? "snooze_slot" : "skip_slot";
      await this._call(service, { slot: target.dataset.slot || null, source: this._adminMode ? "Admin-Dashboard" : "Dashboard" }, "Einnahme wird verarbeitet …", "Einnahme wurde aktualisiert.");
    } else if (action === "book-prn") {
      const med = this._selectedPrn();
      if (!med) return;
      const result = await this._call("book_as_needed", { medication_id: med.id, quantity: this._prnQuantity, source: this._adminMode ? "Admin-Dashboard" : "Dashboard" }, "Bedarfseinnahme wird geprüft …", `${med.name}: ${num(this._prnQuantity)} ${this._unit(med, this._prnQuantity)} gebucht.`);
      this._prnConfirmation = result?.status === "confirmation_required" ? result.confirmation : null;
      this._render();
    } else if (action === "confirm-prn-override") {
      const confirmation = this._prnConfirmation;
      const med = this._selectedPrn();
      if (!confirmation || !med || confirmation.medication_id !== med.id || Number(confirmation.quantity) !== Number(this._prnQuantity)) {
        this._prnConfirmation = null;
        this._setFeedback("error", "Die Buchungsdaten haben sich geändert. Bitte erneut prüfen.");
        return;
      }
      const result = await this._call("book_as_needed", { medication_id: med.id, quantity: this._prnQuantity, confirmation_token: confirmation.token, source: this._adminMode ? "Admin-Dashboard" : "Dashboard" }, "Ausnahmebestätigung wird geprüft …", `${med.name}: Höchstdosis-Ausnahme wurde protokolliert und gebucht.`);
      if (result?.status !== "confirmation_required") this._prnConfirmation = null;
    } else if (["archive_medication", "reactivate_medication"].includes(action)) {
      const med = this._selectedMedication();
      if (!med) return;
      if (this._formDirty) {
        this._setFeedback("error", "Bitte Änderungen zuerst speichern oder verwerfen.", "med-actions");
        return;
      }
      await this._call(action, { medication_id: med.id }, "Medikament wird aktualisiert …", action.startsWith("archive") ? "Medikament archiviert." : "Medikament reaktiviert.", "med-actions");
    } else if (action === "refill") {
      const med = this._selectedMedication();
      if (!med) return;
      if (this._formDirty) {
        this._setFeedback("error", "Bitte Änderungen zuerst speichern oder verwerfen.", "med-actions");
        return;
      }
      const expiry = this.shadowRoot.querySelector("#refill-expiry")?.value || med.expiry_date || "";
      await this._call("refill", { medication_id: med.id, quantity: med.pack_size, expiry_date: expiry }, "Bestand wird aufgefüllt …", "Bestand wurde aufgefüllt.", "med-actions");
    } else if (action === "acknowledge_errors") {
      await this._call("acknowledge_errors", {}, "Fehlerhinweise werden bestätigt …", "Fehlerhinweise wurden als gelesen markiert.");
    } else if (action === "copy-order") {
      try {
        await navigator.clipboard.writeText(this._data?.profile?.order_plan?.clipboard_text || "");
        this._feedback = { type: "success", text: "Bestelltext wurde in die Zwischenablage kopiert.", scope: "page" };
      } catch (err) {
        this._feedback = { type: "error", text: `Bestelltext konnte nicht kopiert werden: ${err?.message || err}`, scope: "page" };
      }
      this._render();
    }
  }

  async _saveSettings(form) {
    const raw = Object.fromEntries(new FormData(form).entries());
    const times = {};
    for (const slot of Object.keys(SLOT_LABELS)) {
      if (`time_${slot}` in raw) { times[slot] = raw[`time_${slot}`]; delete raw[`time_${slot}`]; }
    }
    for (const key of ["early_minutes", "morning_delay_minutes", "snooze_minutes", "repeat_minutes", "order_warning_days", "practice_lead_days", "low_stock_window_days", "expiry_warning_days", "bedtime_offset_hours", "evening_before_bedtime_hours", "notification_ttl", "notification_timeout", "ios_volume", "ios_badge"]) {
      if (key in raw) raw[key] = Number(raw[key]);
    }
    for (const key of ["notification_sticky", "notification_persistent", "notification_alert_once", "notification_critical", "show_archived", "statistics_show_archived"]) {
      if (form.querySelector(`[name="${key}"]`)) raw[key] = form.querySelector(`[name="${key}"]`).checked;
    }
    if (Object.keys(times).length) raw.times = times;
    await this._call("update_settings", { settings: raw }, "Einstellungen werden gespeichert …", "Einstellungen wurden gespeichert.", "settings-form");
  }

  async _saveMedication(form) {
    const raw = Object.fromEntries(new FormData(form).entries());
    const med = this._selectedMedication();
    const [unitSingular, unitPlural] = String(raw.unit_pair || "Einheit|Einheiten").split("|");
    const medication = {
      ...(med?.id && this._medId !== "__new__" ? { id: med.id } : {}),
      ...raw,
      unit_singular: unitSingular, unit_plural: unitPlural || unitSingular,
      step: Number(raw.step), pack_size: Number(raw.pack_size), stock: Number(raw.stock), cost: Number(raw.cost),
      single_max: Number(raw.single_max), daily_max: Number(raw.daily_max),
      button_amount: Number(raw.button_amount || raw.step),
      as_needed_allowed: raw.as_needed_allowed === "on", expiry_enabled: raw.expiry_enabled === "on",
      expiry_date: raw.expiry_enabled === "on" ? raw.expiry_date : "",
      doses: { morning: Number(raw.dose_morning), noon: Number(raw.dose_noon), evening: Number(raw.dose_evening), night: Number(raw.dose_night) },
    };
    for (const key of ["dose_morning", "dose_noon", "dose_evening", "dose_night", "unit_pair"]) delete medication[key];
    const result = await this._call("save_medication", { medication }, "Medikament wird gespeichert …", "Medikament wurde gespeichert.", "med-form");
    if (result?.id) {
      this._medId = result.id;
      this._render();
    }
  }

  async _saveClosure(form) {
    const raw = Object.fromEntries(new FormData(form).entries());
    const closures = [...(this._data.practice_closures || []), { start: raw.start, end: raw.end || raw.start }];
    await this._call("update_practice_closures", { closures }, "Praxisschließung wird gespeichert …", "Praxisschließung wurde gespeichert.", "closure-form");
  }

  _selectedPrn() { return this._data?.profile?.as_needed_medications?.find((item) => item.id === this._prnId); }
  _allMedications() { return [...(this._data?.profile?.medications || []), ...(this._data?.profile?.archived_medications || [])]; }
  _selectedMedication() { return this._allMedications().find((item) => item.id === this._medId); }
  _unit(med, quantity) { return Number(quantity) === 1 ? med.unit_singular : med.unit_plural; }

  _eventMedicationIds(event) {
    return [event.medication_id, ...(event.medications || []).map((item) => item.medication_id || item.id)].filter(Boolean);
  }

  _eventQuantity(event, medicationId = "") {
    if (Array.isArray(event.medications) && event.medications.length) {
      return event.medications.filter((item) => !medicationId || (item.medication_id || item.id) === medicationId).map((item) => {
        const itemId = item.medication_id || item.id;
        const med = this._allMedications().find((candidate) => candidate.id === itemId);
        const quantity = Number(item.quantity || 0);
        const unit = quantity === 1
          ? item.unit_singular || med?.unit_singular
          : item.unit_plural || med?.unit_plural;
        return `${num(quantity)}${unit ? ` ${esc(unit)}` : ""}`;
      }).join(", ");
    }
    if (event.quantity === undefined || event.quantity === null || event.quantity === "") return "";
    const quantity = Number(event.quantity || 0);
    const med = this._allMedications().find((candidate) => candidate.id === event.medication_id);
    const unit = quantity === 1
      ? event.unit_singular || med?.unit_singular
      : event.unit_plural || med?.unit_plural;
    return `${num(quantity)}${unit ? ` ${esc(unit)}` : ""}`;
  }

  _eventDetails(event, medicationId = "") {
    if (Array.isArray(event.medications) && event.medications.length) {
      return event.medications
        .filter((item) => !medicationId || (item.medication_id || item.id) === medicationId)
        .map((item) => item.name || this._allMedications().find((candidate) => candidate.id === (item.medication_id || item.id))?.name || item.medication_id || item.id)
        .filter(Boolean)
        .join(", ");
    }
    return event.medication_name
      || this._allMedications().find((candidate) => candidate.id === event.medication_id)?.name
      || (event.slot ? SLOT_LABELS[event.slot] : "")
      || "Keine Medikamentendetails gespeichert";
  }

  _eventMedicationList(event, medicationId = "") {
    if (Array.isArray(event.medications) && event.medications.length) {
      const items = event.medications
        .filter((item) => !medicationId || (item.medication_id || item.id) === medicationId)
        .map((item) => {
          const itemId = item.medication_id || item.id;
          const med = this._allMedications().find((candidate) => candidate.id === itemId);
          const name = item.name || med?.name || itemId || "Unbekanntes Medikament";
          const quantity = Number(item.quantity || 0);
          const unit = quantity === 1
            ? item.unit_singular || med?.unit_singular
            : item.unit_plural || med?.unit_plural;
          return `${esc(name)} – ${num(quantity)}${unit ? ` ${esc(unit)}` : ""}`;
        });
      if (items.length) return `<ul class="medication-list stats-medications">${items.map((item) => `<li>${item}</li>`).join("")}</ul>`;
    }
    const details = esc(this._eventDetails(event, medicationId));
    const quantity = this._eventQuantity(event, medicationId);
    return `<ul class="medication-list stats-medications"><li>${details}${quantity ? ` – ${quantity}` : ""}</li></ul>`;
  }

  _localizedText(value) {
    let text = String(value ?? "");
    text = text.replace(/\b\d{4}-\d{2}-\d{2}\b/g, (date) => dateOnly(date));
    for (const [key, label] of Object.entries({ fallback: "Fallback-Zeiten", helper: "Helfer", notification: "Benachrichtigung", schedule: "Zeitplan", cycle_end: "Zyklusende", dashboard: "Dashboard", ...SLOT_LABELS, ...STATUS_LABELS, ...EVENT_LABELS })) {
      text = text.replace(new RegExp(`\\b${key}\\b`, "gi"), label);
    }
    return text;
  }

  _statusLabel(value, fallback = "Unbekannter Zustand") {
    return STATUS_LABELS[String(value || "")] || fallback;
  }

  _feedbackMarkup(scope = "page") {
    if (!this._feedback || (this._feedback.scope || "page") !== scope) return "";
    const icon = this._feedback.type === "error" ? "mdi:alert-circle" : this._feedback.type === "success" ? "mdi:check-circle" : "mdi:progress-clock";
    return `<div class="feedback ${this._feedback.type}"><ha-icon icon="${icon}"></ha-icon>${esc(this._feedback.text)}</div>`;
  }

  _feedbackSlot(scope, extraClass = "") {
    return `<div class="feedback-slot ${extraClass}" data-feedback-scope="${scope}" aria-live="polite">${this._feedbackMarkup(scope)}</div>`;
  }

  _number(name, value, step = 1, min = 0, max = "") {
    return `<div class="number-field"><input name="${name}" type="number" value="${esc(value)}" step="${step}" min="${min}" ${max !== "" ? `max="${max}"` : ""} inputmode="decimal"><button type="button" data-adjust="${name}" data-direction="-1" data-amount="${step}" aria-label="Verringern">−</button><button type="button" data-adjust="${name}" data-direction="1" data-amount="${step}" aria-label="Erhöhen">+</button></div>`;
  }

  _renderLoading() {
    this.shadowRoot.innerHTML = `<link rel="stylesheet" href="/pillpal_static_5100_21/pillpal.css?v=5100-21"><div class="loading"><ha-circular-progress active></ha-circular-progress><p>Pill★Pal wird geladen …</p></div>`;
  }

  _renderFatal(err) {
    this.shadowRoot.innerHTML = `<link rel="stylesheet" href="/pillpal_static_5100_21/pillpal.css?v=5100-21"><div class="empty error"><ha-icon icon="mdi:alert-circle-outline"></ha-icon><h2>Pill★Pal konnte nicht geladen werden</h2><p>${esc(err?.message || err)}</p></div>`;
  }

  _render() {
    if (!this._data) return;
    const previousNav = this.shadowRoot.querySelector("nav");
    const navLeft = previousNav?.scrollLeft || 0;
    const navTop = previousNav?.scrollTop || 0;
    const profile = this._data.profile;
    if (!profile) {
      const text = this._adminMode
        ? "Es gibt keine Person, deren Profil du als Administrator betreuen darfst."
        : "Dein Home-Assistant-Benutzer ist keiner aufgenommenen Person zugeordnet.";
      this.shadowRoot.innerHTML = `<link rel="stylesheet" href="/pillpal_static_5100_21/pillpal.css?v=5100-21"><main class="no-profile"><section class="empty"><ha-icon icon="mdi:account-alert-outline"></ha-icon><h1>Pill★Pal</h1><p>${text}</p><small>Öffne Einstellungen → Geräte & Dienste → Pill★Pal, um Personen hinzuzufügen oder die Assistenz zu konfigurieren.</small></section></main>`;
      return;
    }
    const meta = PAGE_META[this._page];
    this.shadowRoot.innerHTML = `
      <link rel="stylesheet" href="/pillpal_static_5100_21/pillpal.css?v=5100-21">
      <style>:host{--accent:${meta[3]}}</style>
      <main class="app page-${this._page}">
        <header class="mobile-toolbar"><ha-menu-button></ha-menu-button><strong>Pill★Pal · ${meta[0]}</strong></header>
        <nav>${PAGES.map(([id, icon, label]) => `<button class="nav ${this._page === id ? "active" : ""}" data-page="${id}" title="${label}" aria-label="${label}"><ha-icon icon="${icon}"></ha-icon></button>`).join("")}</nav>
        <div class="content">
          ${this._personPicker()}
          ${this._feedbackSlot("page")}
          ${this._renderPage(profile)}
        </div>
      </main>`;
    const menuButton = this.shadowRoot.querySelector("ha-menu-button");
    if (menuButton) {
      menuButton.hass = this._hass;
      menuButton.narrow = true;
    }
    if (this._page === "verwalten") {
      this.shadowRoot.querySelector(".manage-top")?.insertAdjacentHTML(
        "beforebegin",
        `<label class="archive-toggle"><input id="show-archived" type="checkbox" ${profile.settings?.show_archived ? "checked" : ""}>Archivierte Medikamente einblenden</label>`,
      );
    }
    requestAnimationFrame(() => {
      const nav = this.shadowRoot.querySelector("nav");
      if (!nav) return;
      nav.scrollLeft = navLeft;
      nav.scrollTop = navTop;
      this.shadowRoot.querySelector(".nav.active")?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
    });
  }

  _personPicker() {
    if (!this._adminMode) return "";
    return `<div class="person-picker"><ha-icon icon="mdi:account-convert"></ha-icon><select id="person-select" aria-label="Betreute Person">${this._data.people.map((person) => `<option value="${esc(person.person_id)}" ${person.person_id === this._data.selected_person_id ? "selected" : ""}>${esc(person.name)}</option>`).join("")}</select></div>`;
  }

  _renderPage(profile) {
    if (this._page === "overview") return this._overview(profile);
    if (this._page === "bedarf") return this._bedarf(profile);
    if (this._page === "statistik") return this._statistik(profile);
    if (this._page === "bestand") return this._bestand(profile);
    if (this._page === "praxis") return this._praxis(profile);
    if (this._page === "verwalten") return this._verwalten(profile);
    if (["zeiten", "benachrichtigungen", "schnittstellen"].includes(this._page)) return this._settings(profile);
    return this._log(profile);
  }

  _section(title, icon, body, extra = "") {
    return `<section class="section ${extra}"><h2><ha-icon icon="${icon}"></ha-icon>${title}</h2>${body}</section>`;
  }

  _pageBody(body, extra = "") {
    return `<div class="page-body ${extra}">${body}</div>`;
  }

  _subheading(title, icon) {
    return `<h3 class="subheading"><ha-icon icon="${icon}"></ha-icon>${title}</h3>`;
  }

  _slotCard(item, mode) {
    const slot = item.slot;
    const detailItems = (item.items || []).map((med) => {
      const source = med.unit_singular ? med : this._allMedications().find((item) => item.id === med.medication_id) || med;
      const unit = Number(med.quantity) === 1 ? source.unit_singular : source.unit_plural;
      return `${esc(med.name)} – ${num(med.quantity)}${unit ? ` ${esc(unit)}` : ""}`;
    });
    const snoozedUntil = item.snoozed_until ? new Date(item.snoozed_until) : null;
    const isSnoozed = snoozedUntil && !Number.isNaN(snoozedUntil.getTime()) && snoozedUntil > new Date();
    const terminal = ["taken", "skipped", "missed"].includes(item.status);
    const isDue = !terminal && !isSnoozed && (item.is_due === true || (item.is_due === undefined && mode === "due"));
    const isBookable = !terminal && !isSnoozed && (item.is_bookable === true || (item.is_bookable === undefined && mode === "early"));
    const buttons = isDue
      ? `<div class="actions compact"><button data-action="confirm" data-slot="${slot}"><ha-icon icon="mdi:check"></ha-icon>Bestätigen</button><button class="secondary" data-action="snooze" data-slot="${slot}" aria-label="Zurückstellen"><ha-icon icon="mdi:alarm-snooze"></ha-icon></button><button class="secondary" data-action="skip" data-slot="${slot}" aria-label="Überspringen"><ha-icon icon="mdi:skip-next"></ha-icon></button></div>`
      : isBookable
        ? `<div class="actions compact"><button data-action="confirm" data-slot="${slot}"><ha-icon icon="mdi:check"></ha-icon>Vorzeitig bestätigen</button><button class="secondary" data-action="skip" data-slot="${slot}" aria-label="Überspringen"><ha-icon icon="mdi:skip-next"></ha-icon>Überspringen</button></div>`
        : "";
    const statusText = terminal
      ? esc(item.status_label || this._statusLabel(item.status))
      : isSnoozed
        ? `Zurückgestellt bis ${timeOnly(item.snoozed_until)}`
        : isDue
        ? "Jetzt fällig"
        : isBookable
          ? `Vorzeitig buchbar · regulär fällig um ${timeOnly(item.due_at)}`
          : `Buchbar ab ${timeOnly(item.bookable_at)}`;
    const details = ["upcoming", "past"].includes(mode)
      ? `<p>${statusText}</p><ul class="medication-list">${detailItems.map((detail) => `<li>${detail}</li>`).join("")}</ul>`
      : `<p>${statusText}${detailItems.length ? ` · ${detailItems.join(", ")}` : ""}</p>`;
    return `<article class="inner slot"><ha-icon class="slot-icon" icon="${SLOT_ICONS[slot]}"></ha-icon><div><strong>${SLOT_LABELS[slot]} · ${timeOnly(item.due_at)}</strong>${details}${buttons}</div></article>`;
  }

  _overview(profile) {
    const schedule = profile.schedule;
    const alerts = [];
    if (profile.storage_warning) alerts.push(`<article class="alert error"><ha-icon icon="mdi:database-alert-outline"></ha-icon><span>${esc(profile.storage_warning)}</span><button type="button" class="secondary" data-action="acknowledge_errors">Hinweis schließen</button></article>`);
    if (profile.warning) alerts.push(`<article class="alert"><ha-icon icon="mdi:bell-alert-outline"></ha-icon><span>${esc(profile.warning)}</span></article>`);
    const low = profile.order_plan?.items || [];
    if (profile.order_plan?.active) alerts.push(`<article class="alert"><ha-icon icon="mdi:cart-arrow-down"></ha-icon><span>Nachbestellung: ${low.map((med) => esc(med.name)).join(", ")}</span></article>`);
    const expiring = profile.expiry_plan?.items || [];
    if (profile.expiry_plan?.active) alerts.push(`<article class="alert"><ha-icon icon="mdi:calendar-alert"></ha-icon><span>MHD prüfen: ${expiring.map((med) => `${esc(med.name)} (${dateOnly(med.expiry_date)})`).join(", ")}</span></article>`);
    const acknowledgedAt = profile.runtime?.diagnostic_errors_acknowledged_at || "";
    const currentErrors = (profile.log || []).filter((item) => item.level === "error" && (!acknowledgedAt || item.timestamp > acknowledgedAt));
    if (currentErrors.length) alerts.push(`<article class="alert error"><ha-icon icon="mdi:alert-octagon-outline"></ha-icon><span>Fehler erkannt · Details im Log</span><button type="button" class="secondary" data-action="acknowledge_errors">Als gelesen markieren</button></article>`);
    const cycleActive = profile.runtime?.cycle_state === "active";
    const cycleState = cycleActive ? "Tages-Zyklus aktiv" : profile.runtime?.cycle_state === "ended" ? "Tages-Zyklus beendet" : "Tages-Zyklus noch nicht gestartet";
    const startedBy = profile.runtime?.cycle_started_by === "awake_helper" ? "Aufgestanden-Helfer" : profile.runtime?.cycle_started_by === "fallback_time" ? "Fallback-Aufstehzeit" : "unbekannter Auslöser";
    const scheduleSource = esc(this._localizedText(profile.runtime?.schedule_source || "Fallback-Zeiten"));
    const cycleDetail = cycleActive ? `Gestartet: ${dateTime(profile.runtime.cycle_started_at)} · Auslöser: ${startedBy} · Einnahmezeiten: ${scheduleSource}` : profile.runtime?.cycle_ended_at ? `Beendet: ${dateTime(profile.runtime.cycle_ended_at)}` : "Start durch Aufgestanden-Helfer oder Fallback-Aufstehzeit";
    const cycle = `<article class="inner"><ha-icon icon="${cycleActive ? "mdi:weather-sunset-up" : "mdi:weather-sunset-down"}"></ha-icon><div><strong>${cycleState}</strong><p>${cycleDetail}</p></div></article>`;
    const lastActivity = profile.runtime?.last_activity || "";
    const status = lastActivity ? `<article class="inner"><ha-icon icon="mdi:message-text-outline"></ha-icon><div><strong>Letzte Aktivität</strong><p>${esc(this._localizedText(lastActivity))}</p></div></article>` : "";
    const past = (schedule.past || []).slice(-2).map((item) => this._slotCard(item, "past")).join("");
    const due = (schedule.due || []).map((item) => this._slotCard(item, "due")).join("");
    const early = (schedule.early || []).map((item) => this._slotCard(item, "early")).join("");
    const upcoming = (schedule.upcoming || []).slice(0, 3).map((item) => this._slotCard(item, "upcoming")).join("");
    const statusFields = `<div class="grid two status-fields">${cycle}${status}</div>`;
    return `${alerts.length ? `<div class="alerts">${alerts.join("")}</div>` : ""}<div class="grid overview-grid">${due ? this._section("Fällige Einnahmen", "mdi:alarm-light-outline", due, "due") : ""}${early ? this._section("Vorzeitig buchbare Einnahmen", "mdi:clock-fast", early, "early") : ""}${this._section("Status", "mdi:information-outline", statusFields, "status-overview")}${upcoming ? this._section("Anstehende Einnahmen", "mdi:clock-outline", upcoming) : ""}${past ? this._section("Vergangene Einnahmen", "mdi:history", past) : ""}</div>`;
  }

  _bedarf(profile) {
    const meds = profile.as_needed_medications || [];
    if (!meds.length) return this._pageBody(`<div class="inner empty-inline"><ha-icon icon="mdi:flask-empty-outline"></ha-icon><div><strong>Kein Bedarfsmedikament vorhanden</strong><p>Hier erscheinen aktive Medikamente, bei denen die Bedarfseinnahme ausdrücklich aktiviert ist.</p></div></div>`, "page-body-bedarf");
    const med = this._selectedPrn() || meds[0];
    const todayKey = new Intl.DateTimeFormat("en-CA", {
      timeZone: this.hass?.config?.time_zone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(new Date());
    const today = (profile.events || []).filter((event) => event.type === "as_needed" && event.medication_id === med.id && event.date === todayKey).reduce((sum, event) => sum + Number(event.quantity || 0), 0);
    const pending = this._prnConfirmation?.medication_id === med.id && Number(this._prnConfirmation?.quantity) === Number(this._prnQuantity) ? this._prnConfirmation : null;
    const prnKind = med.regular ? "Regulär und bei Bedarf" : "Reines Bedarfsmedikament";
    const override = pending ? `<article class="alert error"><ha-icon icon="mdi:shield-alert-outline"></ha-icon><span><strong>Höchstdosis wird überschritten.</strong> Angefragt: ${num(pending.quantity)}, Tagesstand danach: ${num(pending.projected_total)} ${esc(this._unit(med, pending.projected_total))}. Diese Ausnahme wird als Warnereignis protokolliert.</span><button type="button" class="primary" data-action="confirm-prn-override">Höchstdosis ausnahmsweise überschreiten</button></article>` : "";
    return this._pageBody(`${override}<div class="grid two prn-grid"><article class="inner fieldset"><h3><ha-icon icon="mdi:medical-bag"></ha-icon>Medikament wählen</h3><select id="prn-select">${meds.map((item) => `<option value="${esc(item.id)}" ${item.id === med.id ? "selected" : ""}>${esc(item.name)}</option>`).join("")}</select></article><article class="inner medication-info"><ha-icon icon="mdi:flask-plus-outline"></ha-icon><div><strong>${esc(med.name)}</strong><p>${esc(med.description || "Ohne Beschreibung")} · Bestand ${num(med.stock)} ${this._unit(med, med.stock)} · Kleinste Buchungsmenge ${num(med.step)} ${this._unit(med, med.step)} · Einzeldosis max. ${num(med.single_max)} ${this._unit(med, med.single_max)} · Tagesdosis max. ${num(med.daily_max)} ${this._unit(med, med.daily_max)} · Heute bereits ${num(today)} ${this._unit(med, today)} als Bedarf gebucht · ${prnKind}</p></div></article><article class="inner fieldset"><h3><ha-icon icon="mdi:pill-multiple"></ha-icon>Einnahme buchen</h3><div class="stepper"><output>${num(this._prnQuantity)}</output><button type="button" data-step="-1" aria-label="Verringern">−</button><button type="button" data-step="1" aria-label="Erhöhen">+</button></div><button class="primary wide" data-action="book-prn"><ha-icon icon="mdi:playlist-plus"></ha-icon>Bedarfseinnahme buchen</button></article></div>`, "page-body-bedarf");
  }

  _statistik(profile) {
    const showArchived = Boolean(profile.settings?.statistics_show_archived);
    const stats = this._statistics;
    const options = stats?.available_medications || [
      ...(profile.medications || []).map((med) => ({ medication_id: med.id, name: med.name, archived: false })),
      ...(showArchived ? (profile.archived_medications || []).map((med) => ({ medication_id: med.id, name: med.name, archived: true })) : []),
    ];
    const customDates = this._statsPeriod === "custom" ? `<label>Von<input id="stats-from" type="date" value="${esc(this._statsFrom)}"></label><label>Bis<input id="stats-to" type="date" value="${esc(this._statsTo)}"></label>` : "";
    const filters = `<div class="stats-filters"><label>Zeitraum<select id="stats-period"><option value="7" ${this._statsPeriod === "7" ? "selected" : ""}>7 Tage</option><option value="30" ${this._statsPeriod === "30" ? "selected" : ""}>30 Tage</option><option value="90" ${this._statsPeriod === "90" ? "selected" : ""}>90 Tage</option><option value="365" ${this._statsPeriod === "365" ? "selected" : ""}>1 Jahr</option><option value="custom" ${this._statsPeriod === "custom" ? "selected" : ""}>Benutzerdefiniert</option></select></label>${customDates}<label>Medikament<select id="stats-medication"><option value="">Alle Medikamente</option>${options.map((med) => `<option value="${esc(med.medication_id)}" ${med.medication_id === this._statsMedication ? "selected" : ""}>${med.archived ? "[Archiv] " : ""}${esc(med.name)}</option>`).join("")}</select></label><label>Einnahmezeit<select id="stats-slot"><option value="">Alle Einnahmezeiten</option>${Object.entries(SLOT_LABELS).map(([slot, label]) => `<option value="${slot}" ${slot === this._statsSlot ? "selected" : ""}>${label}</option>`).join("")}<option value="as_needed" ${this._statsSlot === "as_needed" ? "selected" : ""}>Bedarf</option></select></label><label class="archive-toggle"><input id="stats-show-archived" type="checkbox" ${showArchived ? "checked" : ""}><span>Archivierte auch auswerten</span></label></div>`;
    const filterSection = this._section("Auswertung filtern", "mdi:filter-variant", filters);
    if (!stats) {
      const message = this._statisticsError || (this._statisticsLoading ? "Statistik wird geladen …" : "Statistikdaten werden vorbereitet …");
      return `${filterSection}${this._section("Statistik", this._statisticsError ? "mdi:alert-circle-outline" : "mdi:progress-clock", `<div class="inner centered">${esc(message)}</div>`)}`;
    }
    const cards = [["Geplant", stats.planned, "mdi:calendar-clock"], ["Eingenommen", stats.taken, "mdi:check-circle-outline"], ["Übersprungen", stats.skipped, "mdi:skip-next-circle-outline"], ["Verpasst", stats.missed, "mdi:close-circle-outline"], ["Ausstehend", stats.pending, "mdi:clock-alert-outline"], ["Bedarfsbuchungen", stats.as_needed_bookings, "mdi:flask-plus-outline"], ["Bedarfsmenge", num(stats.as_needed_quantity), "mdi:counter"], ["Einnahmetreue", `${num(stats.adherence)} %`, "mdi:percent-circle-outline"]];
    const heat = (stats.heatmap || []).map((day) => {
      const date = new Date(`${day.date}T12:00:00`);
      let state = { complete: "complete", partial: "partial", not_occurred: "not-occurred", manual_only: "prn", no_data: "empty", current: "current" }[day.heatmap_status] || "empty";
      if (state === "complete" && day.additional_as_needed) state = "complete-prn";
      const title = `${new Intl.DateTimeFormat("de-DE").format(date)} · geplant ${day.planned}, eingenommen ${day.taken}, übersprungen ${day.skipped}, verpasst ${day.missed}, ausstehend ${day.pending}, Bedarf ${day.as_needed_bookings}`;
      return `<button type="button" class="stat-day-cell heat-${state} ${day.heatmap_status === "current" ? "current" : ""} ${day.date === stats.selected_day ? "selected" : ""}" data-stats-date="${day.date}" title="${esc(title)}" aria-label="${esc(title)} auswählen"><span>${date.getDate()}</span></button>`;
    }).join("");
    const events = (stats.day_details?.events || []).slice(-100).reverse().map((event) => {
      const slotLabel = event.slot ? (SLOT_LABELS[event.slot] || "Unbekannte Einnahmezeit") : "";
      const eventLabel = EVENT_LABELS[event.type] || "Unbekanntes Ereignis";
      const type = `${slotLabel ? `${slotLabel} · ` : ""}${eventLabel}`;
      return `<tr><td>${dateTime(event.timestamp)}</td><td>${esc(type)}</td><td>${this._eventMedicationList(event, this._statsMedication)}</td></tr>`;
    }).join("");
    const selectedDateLabel = dateOnly(stats.selected_day);
    const periodLabel = `${dateOnly(stats.period_start)} bis ${dateOnly(stats.period_end)}`;
    const loading = this._statisticsLoading ? `<div class="feedback pending"><ha-icon icon="mdi:progress-clock"></ha-icon>Statistik wird aktualisiert …</div>` : this._statisticsError ? `<div class="feedback error"><ha-icon icon="mdi:alert-circle"></ha-icon>${esc(this._statisticsError)}</div>` : "";
    return `${filterSection}${loading}${this._section(`Gesamtstatistik · ${periodLabel}`, "mdi:counter", `<div class="stats">${cards.map(([label, value, icon]) => `<article class="inner"><ha-icon icon="${icon}"></ha-icon><div><strong>${label}</strong><p>${value}</p></div></article>`).join("")}</div>`)}${this._section("Tagesstatistik im Zeitraum", "mdi:calendar-blank-multiple", `<div class="stat-day-grid">${heat}</div><div class="heat-legend"><span><i class="heat-current"></i>laufender Tag mit offenen Einnahmen</span><span><i class="heat-complete"></i>vollständig</span><span><i class="heat-complete-prn"></i>vollständig + Bedarf</span><span><i class="heat-partial"></i>unvollständig</span><span><i class="heat-not-occurred"></i>nicht erfolgt</span><span><i class="heat-prn"></i>nur Bedarf</span><span><i class="heat-empty"></i>keine Planung/Buchung</span></div>`)}${this._section(`Buchungen und Planung · ${selectedDateLabel}`, "mdi:table", `<div class="table-wrap"><table><thead><tr><th>Zeit</th><th>Typ</th><th>Details und Menge</th></tr></thead><tbody>${events || `<tr><td colspan="3">Keine Planung oder Buchung am ausgewählten Tag.</td></tr>`}</tbody></table></div>`)}`;
  }

  _medLine(med, regular, projection = null) {
    const unit = this._unit(med, med.stock);
    const doses = Object.values(med.doses || {}).map(num).join("-");
    const orderDetails = regular && projection ? ` · Reichweite <b>${days(projection.days_remaining)}</b> · voraussichtlich leer <b>${dateOnly(projection.projected_empty_date)}</b> · Bestelltag <b>${dateOnly(projection.effective_order_date)}</b>` : regular ? ` · Reichweite <b>${med.days_remaining == null ? "–" : days(med.days_remaining)}</b>` : "";
    return `<article class="inner medline"><strong>${esc(med.name)}</strong><p>${esc(med.unit_plural)} · ${esc(med.description || "Ohne Beschreibung")} · Bestand <b>${num(med.stock)} ${esc(unit)}</b>${orderDetails}${regular ? ` · Schema <b>${doses}</b>` : ` · Einzeldosis max. <b>${num(med.single_max)} ${esc(unit)}</b> · Tagesdosis max. <b>${num(med.daily_max)} ${esc(unit)}</b>`}${med.expiry_enabled && med.expiry_date ? ` · MHD <b>${dateOnly(med.expiry_date)}</b>` : ""}</p></article>`;
  }

  _bestand(profile) {
    const regular = profile.regular_medications || [];
    const prn = profile.as_needed_medications || [];
    const plan = profile.order_plan || { items: [], projections: [] };
    const projections = new Map((plan.projections || []).map((item) => [item.medication_id, item]));
    const orderItems = (plan.items || []).map((item) => {
      const closure = ["practice_closure_advanced", "practice_closure_noted"].includes(item.reason) ? ` · Schließblock ${dateOnly(item.closed_from)} bis ${dateOnly(item.closed_to)}${item.reason === "practice_closure_advanced" ? " · Erinnerung vorgezogen" : ""}` : "";
      return `<article class="inner order-line"><ha-icon icon="${item.status === "order_now" ? "mdi:cart-arrow-down" : "mdi:cart-plus"}"></ha-icon><div><strong>${esc(item.status_label)} · ${esc(item.name)}</strong><p>Bestand ${num(item.current_stock)} · Tagesdosis ${num(item.daily_dose)} · leer am ${dateOnly(item.projected_empty_date)} · normaler Bestelltag ${dateOnly(item.normal_order_date)} · wirksamer Bestelltag ${dateOnly(item.effective_order_date)} · Packung ${num(item.pack_size)} · Kosten/Zuzahlung ${Number(item.pack_cost) > 0 ? `${num(item.pack_cost)} ${esc(plan.currency)}` : "nicht gepflegt"}${closure}</p></div></article>`;
    }).join("") || `<div class="inner centered">Aktuell ist keine Bestellung fällig.</div>`;
    const costText = plan.cost_status === "complete" ? `Gesamtkosten bzw. Zuzahlung: ca. ${num(plan.cost_total)} ${esc(plan.currency)}` : plan.cost_status === "incomplete" ? "Kosten bzw. Zuzahlung konnten nicht vollständig ermittelt werden." : "Keine Kosten bzw. Zuzahlungen gepflegt.";
    const orderActions = plan.items?.length ? `<article class="inner order-summary"><ha-icon icon="mdi:clipboard-text-outline"></ha-icon><div><strong>${costText}</strong><pre>${esc(plan.clipboard_text)}</pre><button class="secondary" type="button" data-action="copy-order"><ha-icon icon="mdi:content-copy"></ha-icon>Bestelltext in Zwischenablage kopieren</button></div></article>` : "";
    const expiryItems = (profile.expiry_plan?.items || []).map((item) => `<article class="inner order-line"><ha-icon icon="mdi:calendar-alert"></ha-icon><div><strong>${esc(item.name)} · ${dateOnly(item.expiry_date)}</strong><p>${item.days_until_expiry < 0 ? `Seit ${days(Math.abs(item.days_until_expiry))} abgelaufen.` : item.days_until_expiry === 0 ? "Läuft heute ab." : `Noch ${days(item.days_until_expiry)}.`}</p></div></article>`).join("");
    const notices = `${plan.items?.length ? this._section("Bestellvorschlag", "mdi:cart-arrow-down", `${orderItems}${orderActions}`, "inventory-notice") : ""}${expiryItems ? this._section("MHD-Hinweise", "mdi:calendar-alert", expiryItems, "inventory-notice") : ""}`;
    return `<div class="grid two inventory">${notices}${this._section("Regelmäßige Medikation", "mdi:medical-bag", regular.map((med) => this._medLine(med, true, projections.get(med.id))).join("") || `<div class="inner centered">Keine aktiven regelmäßigen Medikamente vorhanden.</div>`)}${this._section("Bedarfsmedikation", "mdi:flask-plus-outline", prn.map((med) => this._medLine(med, false)).join("") || `<div class="inner centered">Keine aktiven Bedarfsmedikamente vorhanden.</div>`)}</div>`;
  }

  _praxis() {
    const closures = this._data.practice_closures || [];
    const status = this._data.profile?.practice_status || { open: true, title: "Praxisstatus wird ermittelt", detail: "" };
    const list = closures.map((item, index) => `<article class="inner closure"><ha-icon icon="mdi:office-building-marker-outline"></ha-icon><div><strong>${dateOnly(item.start)} bis ${dateOnly(item.end)}</strong><p>Diese laufende oder zukünftige Schließung fließt in alle Bestelltermine ein.</p></div><button class="secondary closure-remove" type="button" data-closure-remove="${index}"><ha-icon icon="mdi:delete-outline"></ha-icon>Entfernen</button></article>`).join("") || `<div class="inner centered">Keine laufende oder zukünftige Praxisschließung hinterlegt.</div>`;
    return `<div class="grid two">${this._section("Status", "mdi:information-outline", `<article class="inner"><ha-icon icon="${status.open ? "mdi:doctor" : "mdi:office-building-marker-outline"}"></ha-icon><div><strong>${esc(status.title)}</strong><p>Nächster Öffnungstag: ${esc(status.next_open_weekday || "–")}, ${dateOnly(status.next_open_date)}.</p></div></article>`)}${this._section("Laufende und zukünftige Praxisschließungen", "mdi:office-building-marker-outline", `${list}<form id="closure-form" class="inner form-inline"><label>Von<input name="start" type="date" required></label><label>Bis<input name="end" type="date"></label><button class="primary" type="submit"><ha-icon icon="mdi:content-save-check"></ha-icon>Weitere Schließung hinzufügen</button>${this._feedbackSlot("closure-form", "form-feedback")}</form>`)}</div>`;
  }

  _verwalten(profile) {
    const showArchived = Boolean(profile.settings?.show_archived);
    const all = this._allMedications().filter((item) => !item.archived || showArchived);
    const med = all.find((item) => item.id === this._medId);
    const data = med || { name: "", description: "", unit_singular: "Einheit", unit_plural: "Einheiten", step: 1, pack_size: 0, stock: 0, cost: 0, single_max: 0, daily_max: 0, button_amount: 1, button_helper: "", doses: {}, expiry_date: "" };
    const options = `<option value="__new__" ${!med ? "selected" : ""}>+ Neues Medikament</option>${all.map((item) => `<option value="${esc(item.id)}" ${item.id === this._medId ? "selected" : ""}>${item.archived ? "[Archiv] " : ""}${esc(item.name)}</option>`).join("")}`;
    const unitValue = `${data.unit_singular}|${data.unit_plural}`;
    const unitChoices = [...UNIT_OPTIONS];
    if (!unitChoices.some(([one, many]) => `${one}|${many}` === unitValue)) unitChoices.push([data.unit_singular, data.unit_plural]);
    const helperOptions = `<option value="">Kein Taster-Helfer</option>${(this._data.options?.medication_button_helpers || []).map((item) => `<option value="${esc(item)}" ${item === data.button_helper ? "selected" : ""}>${esc(item)}</option>`).join("")}`;
    const currentYear = new Date().getFullYear();
    const expiryMin = `${currentYear - 1}-01-01`;
    const expiryMax = `${currentYear + 5}-12-31`;
    return this._pageBody(`<div class="manage-top"><select id="med-select">${options}</select>${med ? `<div class="actions"><button class="secondary" data-action="refill"><ha-icon icon="mdi:package-up"></ha-icon>Auffüllen</button><button class="secondary" data-action="${med.archived ? "reactivate_medication" : "archive_medication"}"><ha-icon icon="mdi:archive-arrow-${med.archived ? "up" : "down"}-outline"></ha-icon>${med.archived ? "Reaktivieren" : "Archivieren"}</button></div>` : ""}</div>${this._feedbackSlot("med-actions", "action-feedback")}<form id="med-form" class="form-grid"><label>Name<input name="name" value="${esc(data.name)}" required></label><label>Beschreibung<input name="description" value="${esc(data.description)}"></label><label>Einheit<select name="unit_pair">${unitChoices.map(([one, many]) => `<option value="${esc(`${one}|${many}`)}" ${`${one}|${many}` === unitValue ? "selected" : ""}>${esc(`${one}/${many}`)}</option>`).join("")}</select></label><label>Kleinste Teilung<select name="step"><option value="0.25" ${Number(data.step) === .25 ? "selected" : ""}>Viertel (0,25)</option><option value="0.5" ${Number(data.step) === .5 ? "selected" : ""}>Halb (0,5)</option><option value="1" ${Number(data.step) === 1 ? "selected" : ""}>Ganz (1)</option></select></label><label>Packungsgröße${this._number("pack_size", data.pack_size, data.step || 1)}</label><label>Aktueller Bestand${this._number("stock", data.stock, data.step || 1)}</label><label>Kosten/Zuzahlung pro Packung (${esc(profile.settings.currency || "€")})${this._number("cost", data.cost, .01)}</label>${Object.entries(SLOT_LABELS).map(([slot, label]) => `<label>Dosis ${label}${this._number(`dose_${slot}`, data.doses?.[slot] || 0, data.step || 1)}</label>`).join("")}<label class="check"><input type="checkbox" name="as_needed_allowed" ${data.as_needed_allowed ? "checked" : ""}>Bedarfseinnahme erlauben</label><div class="prn-settings" ${data.as_needed_allowed ? "" : "hidden"}><label>Einzeldosis max.${this._number("single_max", data.single_max, data.step || 1)}</label><label>Tagesdosis max.${this._number("daily_max", data.daily_max, data.step || 1)}</label><label>Taster für Bedarfseinnahme<select name="button_helper">${helperOptions}</select></label><label>Menge je Tastendruck${this._number("button_amount", data.button_amount || data.step, data.step || 1, data.step || .001)}</label></div><label class="check"><input type="checkbox" name="expiry_enabled" ${data.expiry_enabled ? "checked" : ""}>MHD-Prüfung aktiv</label><label class="expiry-settings" ${data.expiry_enabled ? "" : "hidden"}>Frühestes MHD<input type="date" id="refill-expiry" name="expiry_date" min="${expiryMin}" max="${expiryMax}" value="${esc(data.expiry_date)}"></label><div class="form-actions"><button class="primary save" type="submit"><ha-icon icon="mdi:content-save-check"></ha-icon>Medikament speichern</button><button class="secondary" type="button" data-action="discard-changes" data-scope="med-form"><ha-icon icon="mdi:restore"></ha-icon>Änderungen verwerfen</button></div>${this._feedbackSlot("med-form", "form-feedback")}</form>`, "page-body-verwalten");
  }

  _settings(profile) {
    const settings = profile.settings || {};
    const o = this._data.options || {};
    const opts = (items, current, empty = "Nicht gesetzt") => `<option value="">${empty}</option>${(items || []).map((item) => `<option value="${esc(item)}" ${item === current ? "selected" : ""}>${esc(item)}</option>`).join("")}`;
    let fields = "";
    if (this._page === "zeiten") {
      fields = `
        <div class="inner info"><ha-icon icon="mdi:information-outline"></ha-icon><p>Ist ein Smartphone-Wecker eingerichtet, werden Morgen-, Abend- und Nachtzeit daraus abgeleitet. Die festen Zeiten bleiben als verlässliche Rückfallwerte erhalten.</p></div>
        ${this._subheading("Erinnerungsintervalle", "mdi:alarm")}
        <label>Einnahme vor Fälligkeit erlauben (Min.)${this._number("early_minutes", settings.early_minutes, 1)}</label>
        <label>Verzögerung nach dem Aufstehen (Min.)${this._number("morning_delay_minutes", settings.morning_delay_minutes, 1)}</label>
        <label>Snooze-Dauer (Min.)${this._number("snooze_minutes", settings.snooze_minutes, 1, 1)}</label>
        <label>Wiederholungsintervall (Min.)${this._number("repeat_minutes", settings.repeat_minutes, 1, 1)}</label>
        ${this._subheading("Bestellungs- und MHD-Fristen", "mdi:calendar-clock")}
        <label>Erinnerung vor Bestandsverbrauch (Tage)${this._number("order_warning_days", settings.order_warning_days, 1)}</label>
        <label>Zusätzlicher Vorlauf vor Praxisschließung (Tage)${this._number("practice_lead_days", settings.practice_lead_days, 1)}</label>
        <label>Mitbestellfenster bei niedrigem Bestand (Tage)${this._number("low_stock_window_days", settings.low_stock_window_days, 1)}</label>
        <label>MHD-Warnfrist (Tage)${this._number("expiry_warning_days", settings.expiry_warning_days, 1)}</label>
        ${this._subheading("Parameter für dynamische Erinnerung", "mdi:alarm-plus")}
        <label>Wecker gültig von<input type="time" name="alarm_window_from" value="${esc(settings.alarm_window_from)}"></label>
        <label>Wecker gültig bis<input type="time" name="alarm_window_to" value="${esc(settings.alarm_window_to)}"></label>
        <label>Mittagsfenster von<input type="time" name="lunch_window_from" value="${esc(settings.lunch_window_from)}"></label>
        <label>Mittagsfenster bis<input type="time" name="lunch_window_to" value="${esc(settings.lunch_window_to)}"></label>
        <label>Nachtruhe vor nächstem Wecker (Std.)${this._number("bedtime_offset_hours", settings.bedtime_offset_hours, .25)}</label>
        <label>Abends vor Nachtruhe (Std.)${this._number("evening_before_bedtime_hours", settings.evening_before_bedtime_hours, .25)}</label>
        ${this._subheading("Fallback-Einnahmezeiten", "mdi:clock-outline")}
        <label>Fallback-Aufstehzeit<input type="time" name="fallback_wake_time" value="${esc(settings.fallback_wake_time || "08:00")}"></label>
        ${Object.entries(SLOT_LABELS).map(([slot, label]) => `<label>${label}<input type="time" name="time_${slot}" value="${esc(settings.times?.[slot])}"></label>`).join("")}`;
    } else if (this._page === "benachrichtigungen") {
      fields = `
        <div class="inner info"><ha-icon icon="mdi:information-outline"></ha-icon><p>Ist ein Notify-Ziel eingerichtet, werden personenbezogen Push-Nachrichten versendet. Deren Verhalten kann hier konfiguriert werden.</p></div>
        ${this._subheading("Allgemeine Benachrichtigungsparameter", "mdi:bell-cog-outline")}
        <label>Titel Einnahmeerinnerung<input name="notification_title" value="${esc(settings.notification_title)}"></label>
        <label>Einleitung Einnahmeerinnerung<input name="notification_intro" value="${esc(settings.notification_intro)}"></label>
        <label>Text Sammeleinnahme Einnahmeerinnerung<input name="action_take" value="${esc(settings.action_take)}"></label>
        <label>Text Snooze Einnahmeerinnerung<input name="action_snooze" value="${esc(settings.action_snooze)}"></label>
        <label>Text Überspringen Einnahmeerinnerung<input name="action_skip" value="${esc(settings.action_skip)}"></label>
        <label>Titel Nachbestellung<input name="order_notification_title" value="${esc(settings.order_notification_title)}"></label>
        <label>Titel MHD-Hinweis<input name="expiry_notification_title" value="${esc(settings.expiry_notification_title)}"></label>
        <label>Symbol für alle Meldungen<input name="notification_icon" value="${esc(settings.notification_icon)}"></label>
        ${this._subheading("Android", "mdi:android")}
        <label>Kanal<input name="notification_channel" value="${esc(settings.notification_channel)}"></label>
        <label>Gruppe<input name="notification_group" value="${esc(settings.notification_group)}"></label>
        <label>Farbe<input name="notification_color" value="${esc(settings.notification_color)}"></label>
        <label>Vibrationsmuster<input name="notification_vibration_pattern" value="${esc(settings.notification_vibration_pattern)}"></label>
        <label>LED-Farbe<input name="notification_led_color" value="${esc(settings.notification_led_color)}"></label>
        <label>Wichtigkeit<select name="notification_importance">${["min","low","default","high","max"].map((v) => `<option ${settings.notification_importance === v ? "selected" : ""}>${v}</option>`).join("")}</select></label>
        <label>Priorität<select name="notification_priority">${["normal","high"].map((v) => `<option ${settings.notification_priority === v ? "selected" : ""}>${v}</option>`).join("")}</select></label>
        <label>Sichtbarkeit<select name="notification_visibility">${["public","private","secret"].map((v) => `<option ${settings.notification_visibility === v ? "selected" : ""}>${v}</option>`).join("")}</select></label>
        <label>TTL (Sek.)${this._number("notification_ttl", settings.notification_ttl, 1)}</label>
        <label>Timeout (Sek.)${this._number("notification_timeout", settings.notification_timeout, 1)}</label>
        <label class="check"><input type="checkbox" name="notification_sticky" ${settings.notification_sticky ? "checked" : ""}>Sticky</label>
        <label class="check"><input type="checkbox" name="notification_persistent" ${settings.notification_persistent ? "checked" : ""}>Persistent</label>
        <label class="check"><input type="checkbox" name="notification_alert_once" ${settings.notification_alert_once ? "checked" : ""}>Nur einmal alarmieren</label>
        ${this._subheading("iOS", "mdi:apple-ios")}
        <label>Ton<input name="notification_sound" value="${esc(settings.notification_sound)}"></label>
        <label>Unterbrechungsstufe<select name="ios_interruption_level">${["passive","active","time-sensitive","critical"].map((v) => `<option ${settings.ios_interruption_level === v ? "selected" : ""}>${v}</option>`).join("")}</select></label>
        <label>Darstellung im Vordergrund<input name="ios_presentation_options" value="${esc(settings.ios_presentation_options)}"></label>
        <label>Lautstärke${this._number("ios_volume", settings.ios_volume, .1, 0, 1)}</label>
        <label>Badge${this._number("ios_badge", settings.ios_badge, 1)}</label>
        <label class="check"><input type="checkbox" name="notification_critical" ${settings.notification_critical ? "checked" : ""}>Kritischer Ton</label>`;
    } else {
      fields = `
        ${this._subheading("Eingehende Informationen", "mdi:download")}
        <label>Zu überwachender Wecker<select name="next_alarm_entity">${opts(o.sensors, settings.next_alarm_entity)}</select></label>
        <label>Aufgestanden-Helfer<select name="awake_helper">${opts(o.input_booleans, settings.awake_helper)}</select></label>
        <label>Taster-Helfer Sammeleinnahme<select name="confirm_helper">${opts(o.confirm_helpers, settings.confirm_helper)}</select></label>
        <label>Feiertagskalender<select name="holiday_calendar">${opts(o.calendars, settings.holiday_calendar)}</select></label>
        ${this._subheading("Ausgehende Informationen", "mdi:upload")}
        <label>Notify-Ziel<select name="notify_target">${opts(o.notify_targets, settings.notify_target)}</select></label>
        <label>Einnahmekalender<select name="intake_calendar">${opts(o.calendars, settings.intake_calendar)}</select></label>
        <div class="inner info"><ha-icon icon="mdi:information-outline"></ha-icon><p>Des Weiteren stellt Pill★Pal unter anderem die personenbezogenen Sensoren „Einnahme fällig“, „Einnahme möglich“, „MHD-Hinweise“ und „Tages-Zyklus vollständig“ bereit. Statistik-Sensoren sind standardmäßig deaktiviert und können bei Bedarf in Home Assistant aktiviert werden.</p></div>`;
    }
    return this._pageBody(`<form id="settings-form" class="form-grid">${fields}<div class="form-actions"><button class="primary save" type="submit"><ha-icon icon="mdi:content-save-check"></ha-icon>Änderungen speichern</button><button class="secondary" type="button" data-action="discard-changes" data-scope="settings-form"><ha-icon icon="mdi:restore"></ha-icon>Änderungen verwerfen</button></div>${this._feedbackSlot("settings-form", "form-feedback")}</form>`, `page-body-${this._page}`);
  }

  _log(profile) {
    const rows = [...(profile.log || [])].reverse().map((item) => `<article class="log-row ${esc(item.level)}"><span class="dot"></span><div><p>${esc(this._localizedText(item.message))}</p><small>${item.actor ? `${esc(item.actor)} · ` : ""}${dateTime(item.timestamp)}</small></div></article>`).join("");
    return `<div class="grid two log-grid">${this._section("Systeminformation", "mdi:folder-information-outline", `<article class="inner"><ha-icon icon="mdi:folder-information-outline"></ha-icon><div><strong>${brand(true)}-Version</strong><p>Revision ${esc(this._data.version)} · Datenschema ${esc(profile.schema || 1)}</p><small>Profil-ID: ${esc(profile.person_id)}</small></div></article>`)}${this._section("Diagnoseereignisse", "mdi:text-box-search-outline", `<div class="log-scroll">${rows || `<p class="muted">Noch keine Einträge.</p>`}</div>`)}</div>`;
  }
}

if (!customElements.get("pillpal-panel-5100-21")) customElements.define("pillpal-panel-5100-21", PillPalPanel);
