"""Runtime manager for the Pill★Pal integration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ADMIN_ASSISTANCE,
    CONF_CREATE_EXAMPLE,
    CONF_PERSON_ENTITY_ID,
    CONF_PERSON_ID,
    CONF_PERSON_NAME,
    CONF_USER_ID,
    DATA_SCHEMA_VERSION,
    DOMAIN,
    PERSON_SUBENTRY_TYPE,
    PANEL_URL,
    SCHEDULER_INTERVAL,
    SIGNAL_DATA_UPDATED,
    SIGNAL_PROFILE_UPDATED,
    SLOT_LABELS,
    SLOTS,
    STORAGE_KEY,
    STORAGE_VERSION,
    VERSION,
)
from .model import (
    DuplicateHelperError,
    PersistenceError,
    PillPalError,
    acknowledge_errors,
    adjust_stock,
    append_log,
    archive_medication,
    archive_removed_profile,
    book_as_needed,
    cycle_is_active,
    confirm_slot,
    end_cycle,
    ensure_profile,
    ensure_today_schedule,
    expiry_plan,
    get_profile,
    helper_owners,
    medication_button_owners,
    medication_is_regular,
    medication_summary,
    mark_slot_missed,
    new_store,
    next_regular_slot_for_medication,
    normalize_practice_closures,
    order_plan,
    reactivate_medication,
    rebuild_schedule,
    refill,
    reminder_configuration_warning,
    regular_medications,
    save_medication,
    select_actionable_slot,
    skip_slot,
    slot_is_due,
    snapshot,
    snooze_slot,
    start_cycle,
    statistics,
    update_settings,
    validate_helper_assignment,
    validate_button_helper_assignment,
    validate_store_payload,
)

_LOGGER = logging.getLogger(__name__)

_ACTION_LABELS = {
    "confirm": "Einnahme bestätigen",
    "confirm_slot": "Einnahme bestätigen",
    "snooze": "Einnahme zurückstellen",
    "snooze_slot": "Einnahme zurückstellen",
    "skip": "Einnahme überspringen",
    "skip_slot": "Einnahme überspringen",
    "book_as_needed": "Bedarfseinnahme buchen",
    "save_medication": "Medikament speichern",
    "archive_medication": "Medikament archivieren",
    "reactivate_medication": "Medikament reaktivieren",
    "refill": "Bestand auffüllen",
    "adjust_stock": "Bestand korrigieren",
    "update_settings": "Einstellungen speichern",
    "update_practice_closures": "Praxisschließzeiten speichern",
    "acknowledge_errors": "Fehlerhinweise bestätigen",
    "recalculate": "Neu berechnen",
    "statistics": "Statistik abrufen",
    "notification_action": "Benachrichtigungsaktion",
}


def _empty_holiday_forecast() -> dict[str, Any]:
    return {
        "entity_id": "",
        "range_start": None,
        "range_end": None,
        "fetched_on": None,
        "closed_dates": [],
        "event_count": 0,
        "last_error": None,
    }


def _format_german_date(value: Any) -> str:
    try:
        parsed = value if isinstance(value, date) else date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return "–"
    return parsed.strftime("%d.%m.%Y")


def _format_german_number(value: Any) -> str:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{parsed:g}".replace(".", ",")


def notification_cleanup_endpoints(
    profile: Mapping[str, Any],
) -> list[tuple[str, str]]:
    """Return every exact or deterministically known notification endpoint."""

    endpoints: set[tuple[str, str]] = set()

    def add(target: Any, tag: Any) -> None:
        exact_target = str(target or "").strip()
        exact_tag = str(tag or "").strip()
        if exact_target.startswith("notify.") and exact_tag:
            endpoints.add((exact_target, exact_tag))

    person_id = str(profile.get("person_id", "")).strip()
    raw_settings = profile.get("settings", {})
    settings = raw_settings if isinstance(raw_settings, Mapping) else {}
    raw_runtime = profile.get("runtime", {})
    runtime = raw_runtime if isinstance(raw_runtime, Mapping) else {}
    configured_target = str(settings.get("notify_target", ""))
    base_tag = str(settings.get("notification_tag", "Medikation"))

    raw_slots = runtime.get("slots", {})
    slots = raw_slots if isinstance(raw_slots, Mapping) else {}
    for item in slots.values():
        if isinstance(item, Mapping):
            add(item.get("notification_target") or configured_target, item.get("notification_tag"))
    raw_deliveries = runtime.get("inventory_notification_deliveries", {})
    deliveries = raw_deliveries if isinstance(raw_deliveries, Mapping) else {}
    for item in deliveries.values():
        if isinstance(item, Mapping):
            add(item.get("target"), item.get("tag"))
    raw_feedback = runtime.get("pending_notification_feedback", {})
    feedback = raw_feedback if isinstance(raw_feedback, Mapping) else {}
    for item in feedback.values():
        if not isinstance(item, Mapping):
            continue
        payload = item.get("payload", {})
        data = payload.get("data", {}) if isinstance(payload, Mapping) else {}
        add(item.get("target"), data.get("tag") if isinstance(data, Mapping) else None)
    raw_cleanup = runtime.get("pending_notification_cleanup", {})
    cleanup = raw_cleanup if isinstance(raw_cleanup, Mapping) else {}
    for item in cleanup.values():
        if isinstance(item, Mapping):
            add(item.get("target"), item.get("tag"))

    if person_id and configured_target.startswith("notify."):
        add(
            configured_target,
            f"{settings.get('order_notification_tag', 'Medikamentenbestellung')}-{person_id}",
        )
        add(
            configured_target,
            f"{settings.get('expiry_notification_tag', 'MedikamentenMHD')}-{person_id}",
        )
        cycle_id = str(runtime.get("cycle_id") or "").strip()
        for slot in SLOTS:
            if cycle_id:
                add(configured_target, f"{base_tag}-{person_id}-{cycle_id}-{slot}")
            add(configured_target, f"{base_tag}-{person_id}-{slot}-feedback")
            add(configured_target, f"{base_tag}-{person_id}-{slot}-action-error")
        raw_medications = profile.get("medications", {})
        medications = raw_medications if isinstance(raw_medications, Mapping) else {}
        for medication_id in medications:
            add(
                configured_target,
                f"{base_tag}-{person_id}-prn-{medication_id}-feedback",
            )
            add(
                configured_target,
                f"{base_tag}-{person_id}-helper-{medication_id}-helper-error",
            )
    return sorted(endpoints)


def queue_profile_notification_cleanup(
    profile: dict[str, Any], now: datetime
) -> int:
    """Durably queue idempotent notification cleanup before profile archival."""

    pending = profile.setdefault("runtime", {}).setdefault(
        "pending_notification_cleanup", {}
    )
    queued = 0
    timestamp = now.isoformat()
    for target, tag in notification_cleanup_endpoints(profile):
        cleanup_id = hashlib.sha256(f"{target}\0{tag}".encode("utf-8")).hexdigest()
        previous = pending.get(cleanup_id, {})
        pending[cleanup_id] = {
            "target": target,
            "tag": tag,
            "created_at": str(previous.get("created_at") or timestamp),
            "updated_at": timestamp,
            "next_retry_at": timestamp,
            "attempts": max(0, int(previous.get("attempts", 0))),
            "last_error": str(previous.get("last_error") or "Bereinigung vorgemerkt."),
        }
        queued += 1
    profile.setdefault("runtime", {})[
        "removed_notification_cleanup_queued_at"
    ] = timestamp
    return queued


async def async_cleanup_stored_notifications(
    hass: HomeAssistant, data: Mapping[str, Any]
) -> tuple[int, list[tuple[str, str, str]]]:
    """Best-effort clear all persisted notification tags during uninstall."""

    raw_profiles = data.get("profiles", {})
    profiles = raw_profiles if isinstance(raw_profiles, Mapping) else {}
    endpoints = {
        endpoint
        for profile in profiles.values()
        if isinstance(profile, Mapping)
        for endpoint in notification_cleanup_endpoints(profile)
    }
    cleared = 0
    failed: list[tuple[str, str, str]] = []
    for target, tag in sorted(endpoints):
        service = target.split(".", 1)[1] if target.startswith("notify.") else ""
        try:
            if not service or not hass.services.has_service("notify", service):
                raise PillPalError("Notify-Dienst ist nicht registriert.")
            await hass.services.async_call(
                "notify",
                service,
                {"message": "clear_notification", "data": {"tag": tag}},
                blocking=True,
            )
            cleared += 1
        except Exception as err:  # Deinstallation must remain possible.
            failed.append((target, tag, str(err)))
    return cleared, failed

_R4_UNITS = {
    "unit": ("Einheit", "Einheiten"),
    "tablet": ("Tablette", "Tabletten"),
    "capsule": ("Kapsel", "Kapseln"),
    "drops": ("Tropfen", "Tropfen"),
    "suppository": ("Zäpfchen", "Zäpfchen"),
    "sachet": ("Beutel", "Beutel"),
    "spray": ("Sprühstoß", "Sprühstöße"),
    "hub": ("Hub", "Hübe"),
    "patch": ("Pflaster", "Pflaster"),
    "mg": ("mg", "mg"),
    "ml": ("ml", "ml"),
    "spoon": ("Löffel", "Löffel"),
    "application": ("Anwendung", "Anwendungen"),
    "piece": ("Stück", "Stück"),
    "tube": ("Tube", "Tuben"),
    "strand": ("Strang", "Stränge"),
}
_R4_SLOTS = {
    "morning": "morgens",
    "noon": "mittags",
    "evening": "abends",
    "night": "zur_nacht",
}


def _convert_r4_medication(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Convert one normalized R4 record into the explicit R5 medication schema."""

    schedule = raw.get("schedule") if isinstance(raw.get("schedule"), Mapping) else {}
    doses: dict[str, Any] = {}
    for target_slot, source_slot in _R4_SLOTS.items():
        source = schedule.get(source_slot, {})
        if not isinstance(source, Mapping):
            source = {}
        doses[target_slot] = source.get("dose", 0) if source.get("enabled", False) else 0
    as_needed = raw.get("as_needed") if isinstance(raw.get("as_needed"), Mapping) else {}
    button = as_needed.get("button") if isinstance(as_needed.get("button"), Mapping) else {}
    singular, plural = _R4_UNITS.get(str(raw.get("unit", "unit")), _R4_UNITS["unit"])
    return {
        "id": str(raw.get("id", "")),
        "name": str(raw.get("name", "")),
        "description": str(raw.get("description", "")),
        "unit_singular": singular,
        "unit_plural": plural,
        "step": raw.get("smallest_unit", 0.5),
        "pack_size": raw.get("pack_size", 0),
        "cost": raw.get("pack_cost", 0),
        "stock": raw.get("current_stock", 0),
        "doses": doses,
        # Keep the R4 mapping so model normalization performs and records the
        # one-time deterministic migration to the explicit R5 flag.
        "as_needed": dict(as_needed),
        "single_max": as_needed.get("max_single_dose", 0),
        "daily_max": as_needed.get("max_daily_dose", 0),
        "button_helper": str(button.get("helper_entity_id") or ""),
        "button_amount": button.get("amount", raw.get("smallest_unit", 0.5)),
        "expiry_enabled": bool(raw.get("expiry_date")),
        "expiry_date": str(raw.get("expiry_date") or ""),
        "archived": not bool(raw.get("active", True)),
    }


class PillPalManager:
    """Own persistent data and serialize person-explicit operations."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._store_key = f"{STORAGE_KEY}.{entry.entry_id}"
        self._store = Store(hass, STORAGE_VERSION, self._store_key)
        self._quarantine_store = Store(
            hass, STORAGE_VERSION, f"{self._store_key}.quarantine"
        )
        self.data: dict[str, Any] = new_store()
        self._lock = asyncio.Lock()
        self._save_lock = asyncio.Lock()
        self._save_revision = 0
        self._persisted_revision = 0
        self._unsubscribers: list[Callable[[], None]] = []
        self._helper_unsubscribers: list[Callable[[], None]] = []
        self._interface_unsubscribers: list[Callable[[], None]] = []
        self._helper_owner_by_entity: dict[str, tuple[str, str, str | None]] = {}
        self._helper_last_state_by_entity: dict[str, str] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._task_person_ids: dict[asyncio.Task[Any], str] = {}
        self._feedback_retrying: set[str] = set()
        self._cleanup_retrying: set[str] = set()
        self._generation = 0
        self._running = False

    def _create_task(
        self, coroutine: Any, name: str, person_id: str | None = None
    ) -> asyncio.Task[Any]:
        """Create and own a background task until it finishes or shutdown cancels it."""

        task = self.hass.async_create_background_task(coroutine, name)
        self._tasks.add(task)
        if person_id:
            self._task_person_ids[task] = person_id
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        person_id = self._task_person_ids.pop(task, None)
        if task.cancelled():
            return
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            _LOGGER.error(
                "Pill★Pal background task failed: %s",
                error,
                exc_info=(type(error), error, error.__traceback__),
            )
            if person_id and self._running:
                reporter = self.hass.async_create_background_task(
                    self._async_record_runtime_error(person_id, error),
                    f"Pill★Pal diagnostic error {person_id}",
                )
                self._tasks.add(reporter)
                reporter.add_done_callback(self._diagnostic_task_done)

    def _diagnostic_task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            _LOGGER.error("Pill★Pal runtime error could not be persisted: %s", error)

    async def _async_record_runtime_error(
        self, person_id: str, error: BaseException
    ) -> None:
        async with self._lock:
            try:
                profile = self.profile(person_id)
            except PillPalError:
                return
            append_log(
                profile,
                f"{profile['name']}: Interner Laufzeitfehler in einer "
                f"Hintergrundaufgabe ({type(error).__name__}): {str(error)[:500]}",
                level="error",
                source="runtime",
                now=dt_util.now(),
            )
            await self._async_save()
        self._dispatch(person_id)

    async def async_initialize(self) -> None:
        """Load storage and reconcile it with person subentries."""

        loaded = await self._store.async_load()
        validation_report: dict[str, Any] | None = None
        repair_report: dict[str, Any] | None = None
        if loaded is not None:
            safe, report = validate_store_payload(loaded, dt_util.now())
            validation_report = report
            if report["quarantine_required"]:
                quarantine_id = await self._async_quarantine(loaded, report)
                if report.get("unsupported_schema"):
                    raise PersistenceError(
                        "Der Pill★Pal-Speicher verwendet ein neueres, nicht "
                        f"unterstütztes Datenschema. Die Originaldaten wurden als "
                        f"{quarantine_id} gesichert und nicht überschrieben."
                    )
                safe["storage_health"] = {
                    "status": "repaired_from_quarantine",
                    "quarantine_id": quarantine_id,
                    "detected_at": report["detected_at"],
                    "reason_count": len(report["reasons"]),
                    "repaired_profiles": deepcopy(report["repaired_profiles"]),
                    "dropped_profiles": deepcopy(report["dropped_profiles"]),
                }
                repair_report = report
                _LOGGER.error(
                    "Pill★Pal storage %s was quarantined as %s and repaired (%s issue(s))",
                    self._store_key,
                    quarantine_id,
                    len(report["reasons"]),
                )
            self.data = safe
        self.data.setdefault("profiles", {})
        self.data.setdefault("migration", {})
        self.data["schema"] = DATA_SCHEMA_VERSION
        stored_revision = self.data.get("persistence", {}).get("revision", 0)
        self._save_revision = (
            stored_revision
            if isinstance(stored_revision, int)
            and not isinstance(stored_revision, bool)
            and stored_revision >= 0
            else 0
        )
        self._persisted_revision = self._save_revision

        active_ids: set[str] = set()
        active_subentry_ids: set[str] = set()
        for subentry in self.entry.get_subentries_of_type(PERSON_SUBENTRY_TYPE):
            person_id = str(subentry.data[CONF_PERSON_ID])
            active_ids.add(person_id)
            active_subentry_ids.add(str(subentry.subentry_id))
            entity_id = str(subentry.data.get(CONF_PERSON_ENTITY_ID, ""))
            person_state = self.hass.states.get(entity_id) if entity_id else None
            person_available = self._state_available(person_state)
            person_attributes = (
                person_state.attributes
                if person_available and isinstance(person_state.attributes, Mapping)
                else {}
            )
            if person_available:
                name = str(
                    person_attributes.get("friendly_name")
                    or person_state.name
                    or subentry.data.get(CONF_PERSON_NAME, subentry.title)
                )
                user_id = (
                    person_attributes.get("user_id")
                    if "user_id" in person_attributes
                    else subentry.data.get(CONF_USER_ID)
                )
            else:
                name = str(subentry.data.get(CONF_PERSON_NAME, subentry.title))
                user_id = subentry.data.get(CONF_USER_ID)
            ensure_profile(
                self.data,
                person_id=person_id,
                name=name,
                person_entity_id=entity_id,
                user_id=user_id,
                admin_assistance=bool(
                    subentry.data.get(CONF_ADMIN_ASSISTANCE, False)
                ),
                # A configured subentry is authoritative. Temporary state
                # unavailability must never archive medication/history.
                person_exists=True,
                create_example=bool(self.entry.data.get(CONF_CREATE_EXAMPLE, True)),
            )

        removal_time = dt_util.now()
        for person_id, profile in self.data["profiles"].items():
            if person_id not in active_ids:
                runtime = profile.setdefault("runtime", {})
                if not profile.get("archived") or not runtime.get(
                    "removed_notification_cleanup_queued_at"
                ):
                    queue_profile_notification_cleanup(profile, removal_time)
                archive_removed_profile(profile, removal_time)
        self._cleanup_removed_subentry_registries(
            active_ids=active_ids,
            active_subentry_ids=active_subentry_ids,
        )

        legacy_closures = normalize_practice_closures(
            self.data.pop("practice_closures", []), dt_util.now().date()
        )
        for profile in self.active_profiles:
            if legacy_closures and not profile.get("practice_closures"):
                profile["practice_closures"] = deepcopy(legacy_closures)
            profile["practice_closures"] = normalize_practice_closures(
                profile.get("practice_closures", []), dt_util.now().date()
            )
            current = dt_util.now()
            self._apply_dynamic_context(profile, current)
            self._reconcile_cycle_lifecycle(
                profile, current, allow_helper_recovery=True
            )
            runtime = profile.setdefault("runtime", {})
            interrupted = False
            for item in runtime.get("slots", {}).values():
                if item.get("notification_state") == "reserved" or item.get(
                    "notification_reservation_id"
                ):
                    item["notification_state"] = "idle"
                    item["notification_reservation_id"] = None
                    item["notification_reserved_at"] = None
                    interrupted = True
            if runtime.get("inventory_notification_reservations"):
                runtime["inventory_notification_reservations"] = {}
                interrupted = True
            if interrupted:
                append_log(
                    profile,
                    f"{profile['name']}: Beim Start unterbrochene "
                    "Benachrichtigungsaufträge werden anhand der Outbox erneut "
                    "abgeglichen.",
                    source="startup",
                    now=current,
                )
            slots_before_repair = deepcopy(profile.get("runtime", {}).get("slots", {}))
            ensure_today_schedule(profile, current)
            if slots_before_repair != profile.get("runtime", {}).get("slots", {}):
                append_log(
                    profile,
                    f"{profile['name']}: Aktiver Tages-Zyklus wurde beim Start "
                    "mit Medikamentenplan und Fälligkeiten abgeglichen.",
                    source="startup",
                    now=current,
                )
            self._log_reminder_warning_if_needed(profile, reason="startup")
            if validation_report and validation_report.get("schema_migrated"):
                append_log(
                    profile,
                    f"{profile['name']}: Datenschema "
                    f"{validation_report.get('source_schema')} wurde kontrolliert "
                    f"auf Schema {DATA_SCHEMA_VERSION} migriert.",
                    source="migration",
                    now=current,
                )
            if repair_report is not None:
                health = self.data["storage_health"]
                append_log(
                    profile,
                    f"{profile['name']}: Beschädigte Speicherdaten wurden vor der "
                    f"Reparatur unverändert quarantänisiert ({health['quarantine_id']}). "
                    "Bitte den Pill★Pal-Log prüfen.",
                    level="error",
                    source="storage",
                    now=dt_util.now(),
                )
        await self._async_save()
        self._running = True
        self._register_runtime_listeners()
        for profile in self.active_profiles:
            await self._async_refresh_holiday_forecast(
                profile["person_id"], force=True
            )
            self._create_task(
                self._async_sync_inventory_notifications(profile["person_id"]),
                f"Pill★Pal inventory alerts {profile['person_id']}",
                profile["person_id"],
            )
        # Do not expose up to one scheduler interval of stale state after a
        # restart. This pass consumes the persisted reminder/feedback/cleanup
        # outboxes and reserves due work before setup returns.
        await self._async_scheduler_tick(dt_util.now())

    def _cleanup_removed_subentry_registries(
        self, *, active_ids: set[str], active_subentry_ids: set[str]
    ) -> None:
        """Remove only entities/devices belonging to deleted person subentries."""

        archived_ids = set(self.data.get("profiles", {})) - active_ids
        try:
            from homeassistant.helpers import entity_registry as er

            registry = er.async_get(self.hass)
            for entry in list(
                er.async_entries_for_config_entry(registry, self.entry.entry_id)
            ):
                subentry_id = str(getattr(entry, "config_subentry_id", "") or "")
                unique_id = str(getattr(entry, "unique_id", "") or "")
                belongs_to_removed_profile = any(
                    unique_id.startswith(f"{person_id}_")
                    for person_id in archived_ids
                )
                if (
                    (subentry_id and subentry_id not in active_subentry_ids)
                    or belongs_to_removed_profile
                ) and hasattr(registry, "async_remove"):
                    registry.async_remove(entry.entity_id)
        except (AttributeError, ImportError, TypeError):
            _LOGGER.debug("Entity registry cleanup is not available during setup")
        try:
            from homeassistant.helpers import device_registry as dr

            registry = dr.async_get(self.hass)
            entries = dr.async_entries_for_config_entry(
                registry, self.entry.entry_id
            )
            for device in list(entries):
                subentry_id = str(
                    getattr(device, "config_subentry_id", "") or ""
                )
                identifiers = set(getattr(device, "identifiers", set()) or set())
                belongs_to_removed_profile = any(
                    (DOMAIN, person_id) in identifiers for person_id in archived_ids
                )
                if (
                    (subentry_id and subentry_id not in active_subentry_ids)
                    or belongs_to_removed_profile
                ):
                    registry.async_remove_device(device.id)
        except (AttributeError, ImportError, TypeError):
            _LOGGER.debug("Device registry cleanup is not available during setup")

    @property
    def active_profiles(self) -> list[dict[str, Any]]:
        """Return profiles currently backed by an existing HA Person."""

        return [
            profile
            for profile in self.data.get("profiles", {}).values()
            if not profile.get("archived")
        ]

    def profile(self, person_id: str) -> dict[str, Any]:
        """Return one profile."""

        return get_profile(self.data, person_id)

    def person_id_for_user(self, user_id: str | None) -> str | None:
        """Resolve an HA user to exactly one included Person."""

        if not user_id:
            return None
        for profile in self.active_profiles:
            if profile.get("user_id") == user_id:
                return profile["person_id"]
        return None

    def manageable_profiles(
        self, user_id: str | None, is_admin: bool
    ) -> list[dict[str, Any]]:
        """Return profiles an admin may assist, excluding their own profile."""

        if not is_admin:
            return []
        own = self.person_id_for_user(user_id)
        return sorted(
            [
                profile
                for profile in self.active_profiles
                if profile["person_id"] != own
                and bool(profile.get("admin_assistance"))
            ],
            key=lambda item: item.get("name", "").casefold(),
        )

    def is_authorized(
        self,
        *,
        user_id: str | None,
        is_admin: bool,
        person_id: str,
        admin_mode: bool = False,
    ) -> bool:
        """Check access without relying on a global selection."""

        own = self.person_id_for_user(user_id)
        if not admin_mode:
            return own == person_id
        return any(
            profile["person_id"] == person_id
            for profile in self.manageable_profiles(user_id, is_admin)
        )

    def frontend_bootstrap(
        self,
        *,
        user_id: str | None,
        is_admin: bool,
        admin_mode: bool,
        requested_person_id: str | None = None,
    ) -> dict[str, Any]:
        """Return authorized panel data."""

        own = self.person_id_for_user(user_id)
        manageable = self.manageable_profiles(user_id, is_admin)
        if admin_mode:
            allowed = [profile["person_id"] for profile in manageable]
            person_id = (
                requested_person_id
                if requested_person_id in allowed
                else (allowed[0] if allowed else None)
            )
        else:
            person_id = own

        selected = snapshot(self.profile(person_id), dt_util.now()) if person_id else None
        if selected is not None:
            selected["storage_warning"] = self._storage_warning(
                self.profile(person_id)
            )
            selected["warning"] = self._visible_reminder_warning(
                self.profile(person_id)
            )
        return {
            "version": VERSION,
            "admin_mode": admin_mode,
            "own_person_id": own,
            "selected_person_id": person_id,
            "people": [
                {
                    "person_id": profile["person_id"],
                    "name": profile["name"],
                    "person_entity_id": profile.get("person_entity_id", ""),
                }
                for profile in manageable
            ],
            "profile": selected,
            "practice_closures": deepcopy(
                selected.get("practice_closures", []) if selected else []
            ),
        }

    async def async_snapshot(self, person_id: str) -> dict[str, Any]:
        """Return a snapshot after ensuring today's schedule."""

        async with self._lock:
            profile = self.profile(person_id)
            ensure_today_schedule(profile, dt_util.now())
            result = snapshot(profile, dt_util.now())
            result["storage_warning"] = self._storage_warning(profile)
            result["warning"] = self._visible_reminder_warning(profile)
            return result

    def _visible_reminder_warning(
        self, profile: Mapping[str, Any]
    ) -> str | None:
        """Describe a missing reminder path without rejecting valid alternatives."""

        target = str(profile.get("settings", {}).get("notify_target", ""))
        return reminder_configuration_warning(
            profile,
            notify_target_valid=self.valid_notify_target(target),
            due_output_available=self.due_output_available(profile),
        )

    def _storage_warning(self, profile: Mapping[str, Any]) -> str | None:
        """Return a safe user-visible warning for the latest store repair."""

        health = self.data.get("storage_health", {})
        if not isinstance(health, Mapping) or health.get("status") != "repaired_from_quarantine":
            return None
        acknowledged_at = profile.get("runtime", {}).get(
            "diagnostic_errors_acknowledged_at"
        )
        detected_at = health.get("detected_at")
        if acknowledged_at and detected_at:
            acknowledged = dt_util.parse_datetime(str(acknowledged_at))
            detected = dt_util.parse_datetime(str(detected_at))
            if acknowledged is not None and detected is not None:
                if acknowledged.astimezone(timezone.utc) >= detected.astimezone(
                    timezone.utc
                ):
                    return None
        quarantine_id = str(health.get("quarantine_id", "unbekannt"))
        count = int(health.get("reason_count", 0) or 0)
        return (
            "Beschädigte Speicherdaten wurden gesichert und kontrolliert repariert "
            f"(Quarantäne {quarantine_id}, {count} Befund(e)). Bitte den Log prüfen."
        )

    async def _async_quarantine(
        self, raw: Any, report: Mapping[str, Any]
    ) -> str:
        """Persist the untouched unsafe payload before the live store is repaired."""

        quarantine_id = f"quarantine_{uuid4().hex}"
        existing = await self._quarantine_store.async_load()
        if existing is None:
            records: list[dict[str, Any]] = []
        elif (
            not isinstance(existing, Mapping)
            or not isinstance(existing.get("records"), list)
            or not all(isinstance(item, Mapping) for item in existing["records"])
        ):
            raise PersistenceError(
                "Der Pill★Pal-Quarantänespeicher ist selbst beschädigt. "
                "Der Live-Store wurde nicht verändert."
            )
        else:
            records = [deepcopy(dict(item)) for item in existing["records"]]
        records.append(
            {
                "id": quarantine_id,
                "detected_at": str(report.get("detected_at", dt_util.now().isoformat())),
                "reasons": deepcopy(list(report.get("reasons", []))),
                "raw": deepcopy(raw),
            }
        )
        await self._quarantine_store.async_save({"schema": 1, "records": records})
        return quarantine_id

    async def _async_save(self) -> None:
        """Persist immutable revisions in invocation order."""

        self._save_revision += 1
        revision = self._save_revision
        self.data.setdefault("persistence", {})["revision"] = revision
        frozen = deepcopy(self.data)
        async with self._save_lock:
            if revision <= self._persisted_revision:
                return
            try:
                await self._store.async_save(frozen)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                raise PersistenceError(
                    "Pill★Pal konnte die Änderung nicht dauerhaft speichern. "
                    "Die Aktion wurde nicht als erfolgreich bestätigt."
                ) from err
            self._persisted_revision = revision

    @callback
    def _dispatch(self, person_id: str) -> None:
        async_dispatcher_send(self.hass, SIGNAL_PROFILE_UPDATED.format(person_id))
        async_dispatcher_send(self.hass, SIGNAL_DATA_UPDATED)
        self.hass.bus.async_fire(
            "pillpal_updated",
            {"person_id": person_id, "entry_id": self.entry.entry_id},
        )

    @staticmethod
    def _safe_action_payload(value: Any) -> Any:
        """Redact credentials and bound results published as entity attributes."""

        if isinstance(value, Mapping):
            blocked = {
                "token",
                "confirmation_token",
                "action_token",
                # These are returned directly to an opted-in service/WebSocket caller,
                # but are too large and volatile for a restored sensor attribute.
                "heatmap",
                "day_details",
                "available_medications",
                "available_slots",
            }
            safe: dict[str, Any] = {}
            visible = [(key, item) for key, item in value.items() if str(key) not in blocked]
            for key, item in visible[:50]:
                safe[str(key)] = PillPalManager._safe_action_payload(item)
            if len(visible) > 50:
                safe["omitted_items"] = len(visible) - 50
            return safe
        if isinstance(value, (list, tuple)):
            safe = [PillPalManager._safe_action_payload(item) for item in value[:50]]
            if len(value) > 50:
                safe.append({"omitted_items": len(value) - 50})
            return safe
        if isinstance(value, str):
            return value[:2000]
        return deepcopy(value)

    async def async_record_action_result(
        self,
        person_id: str,
        action: str,
        status: str,
        message: str,
        *,
        actor: str | None = None,
        result: Any = None,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        """Persist and fire the same machine-readable result for every action path."""

        item = {
            "status": status,
            "action": action,
            "message": str(message)[:1000],
            "timestamp": dt_util.now().isoformat(),
            "actor": actor,
            "result": self._safe_action_payload(result),
            "error_code": error_code,
        }
        async with self._lock:
            profile = self.profile(person_id)
            profile.setdefault("runtime", {})["last_action_result"] = deepcopy(item)
            if status == "error":
                append_log(
                    profile,
                    f"{profile['name']}: {_ACTION_LABELS.get(action, action)} "
                    f"fehlgeschlagen: {item['message']}",
                    level="error",
                    source="action",
                    actor=actor,
                    now=dt_util.now(),
                )
            await self._async_save()
        self._dispatch(person_id)
        self.hass.bus.async_fire(
            "pillpal_action_result",
            {"person_id": person_id, **deepcopy(item)},
        )
        return item

    async def async_statistics(
        self,
        person_id: str,
        *,
        days: int = 30,
        medication_id: str | None = None,
        slot: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        selected_day: date | None = None,
    ) -> dict[str, Any]:
        """Return a filterable native action response without shared UI helpers."""

        async with self._lock:
            return statistics(
                self.profile(person_id),
                days,
                dt_util.now(),
                medication_id=medication_id,
                slot=slot,
                start_date=start_date,
                end_date=end_date,
                selected_day=selected_day,
            )

    async def _async_sync_intake_calendar_event(
        self, person_id: str, event: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Create one idempotently tracked intake-history calendar event."""

        event_id = str(event.get("id", ""))
        if not event_id:
            return {"status": "ignored", "reason": "missing_event_id"}
        calendar_entity = ""
        delivery_key = ""
        profile_snapshot: dict[str, Any]
        async with self._lock:
            profile = self.profile(person_id)
            calendar_entity = str(profile.get("settings", {}).get("intake_calendar", ""))
            if not calendar_entity:
                return {"status": "not_configured"}
            delivery_key = f"{calendar_entity}|{event_id}"
            deliveries = profile.setdefault("runtime", {}).setdefault(
                "intake_calendar_deliveries", {}
            )
            previous = deliveries.get(delivery_key, {})
            if previous.get("status") == "delivered":
                return deepcopy(previous)
            if previous.get("status") == "pending":
                try:
                    reserved = datetime.fromisoformat(str(previous.get("updated_at")))
                except (TypeError, ValueError):
                    reserved = dt_util.now() - timedelta(minutes=6)
                if dt_util.now() - reserved < timedelta(minutes=5):
                    return deepcopy(previous)
            deliveries[delivery_key] = {
                "status": "pending",
                "event_id": event_id,
                "calendar_entity": calendar_entity,
                "updated_at": dt_util.now().isoformat(),
                "error": None,
            }
            profile_snapshot = deepcopy(profile)
            await self._async_save()
        if not self.hass.services.has_service("calendar", "create_event"):
            error: Exception = PillPalError(
                "Der ausgewählte Einnahmekalender unterstützt keine Ereigniserstellung."
            )
        else:
            error = None  # type: ignore[assignment]
        event_type = str(event.get("type", ""))
        status_data = {
            "regular_taken": ("✅", "Eingenommen"),
            "regular_skipped": ("⏭️", "Übersprungen"),
            "regular_missed": ("❌", "Verpasst"),
            "as_needed": ("➕", "Bedarfseinnahme"),
        }
        symbol, status_label = status_data.get(event_type, ("💊", "Einnahme"))
        slot = str(event.get("slot", ""))
        slot_label = SLOT_LABELS.get(slot, "Bedarf")
        medications = list(event.get("medications", []))
        if event_type == "as_needed":
            medications = [
                {
                    "medication_id": event.get("medication_id"),
                    "name": event.get("medication_name"),
                    "quantity": event.get("quantity"),
                }
            ]
        medication_lines = []
        for medication in medications:
            medication_id = str(medication.get("medication_id", ""))
            stored = profile_snapshot.get("medications", {}).get(medication_id, {})
            quantity = medication.get("quantity", 0)
            unit = (
                stored.get("unit_singular", "Einheit")
                if float(quantity or 0) == 1
                else stored.get("unit_plural", "Einheiten")
            )
            medication_lines.append(
                f"• {medication.get('name') or stored.get('name') or medication_id}: "
                f"{_format_german_number(quantity)} {unit}"
            )
        try:
            started = datetime.fromisoformat(str(event.get("timestamp")))
        except (TypeError, ValueError):
            started = dt_util.now()
        payload = {
            "summary": f"{symbol} {slot_label} – {profile_snapshot.get('name', person_id)}",
            "description": "\n".join(
                [
                    f"Status: {status_label}",
                    f"Person: {profile_snapshot.get('name', person_id)}",
                    f"Zyklus: {event.get('cycle_id') or '–'}",
                    *medication_lines,
                ]
            ),
            "start_date_time": started.isoformat(),
            "end_date_time": (started + timedelta(minutes=1)).isoformat(),
        }
        try:
            if error is not None:
                raise error
            await self.hass.services.async_call(
                "calendar",
                "create_event",
                payload,
                blocking=True,
                target={"entity_id": calendar_entity},
            )
            delivery = {
                "status": "delivered",
                "event_id": event_id,
                "calendar_entity": calendar_entity,
                "updated_at": dt_util.now().isoformat(),
                "error": None,
            }
        except Exception as err:  # calendar providers raise integration-specific errors
            delivery = {
                "status": "error",
                "event_id": event_id,
                "calendar_entity": calendar_entity,
                "updated_at": dt_util.now().isoformat(),
                "error": str(err),
            }
        async with self._lock:
            profile = self.profile(person_id)
            profile.setdefault("runtime", {}).setdefault(
                "intake_calendar_deliveries", {}
            )[delivery_key] = deepcopy(delivery)
            if delivery["status"] == "error":
                append_log(
                    profile,
                    f"{profile['name']}: Einnahmekalender {calendar_entity} konnte "
                    f"nicht aktualisiert werden: {delivery['error']}",
                    level="warning",
                    source="calendar",
                    now=dt_util.now(),
                )
            await self._async_save()
        self._dispatch(person_id)
        return delivery

    def _latest_event(
        self, person_id: str, event_type: str, slot: str | None = None
    ) -> dict[str, Any] | None:
        for event in reversed(self.profile(person_id).get("events", [])):
            if event.get("type") != event_type:
                continue
            if slot is not None and event.get("slot") != slot:
                continue
            return deepcopy(event)
        return None

    async def _mutate(
        self,
        person_id: str,
        operation: Callable[[], Any],
        *,
        settings_changed: bool = False,
    ) -> Any:
        """Execute one serialized mutation and persist before side effects."""

        stale_plan_notifications: list[tuple[str, str, str]] = []
        refresh_due_notifications = False
        async with self._lock:
            before = deepcopy(self.data)
            try:
                previous_slots = (
                    deepcopy(
                        self.profile(person_id).get("runtime", {}).get("slots", {})
                    )
                    if settings_changed
                    else {}
                )
                result = operation()
                if settings_changed:
                    current = dt_util.now()
                    profile = self.profile(person_id)
                    self._apply_dynamic_context(profile, current)
                    self._reconcile_cycle_lifecycle(
                        profile, current, allow_helper_recovery=True
                    )
                    rebuild_schedule(profile, current)
                    live_slots = profile.get("runtime", {}).get("slots", {})
                    for slot, previous in previous_slots.items():
                        was_visible = bool(
                            previous.get("notification_target")
                            and previous.get("notification_tag")
                            and (
                                previous.get("notification_state") == "sent"
                                or previous.get("last_notification_at")
                            )
                        )
                        if not was_visible:
                            continue
                        live = live_slots.get(slot)
                        previous_signature = (
                            previous.get("cycle_id"),
                            previous.get("due_at"),
                            previous.get("status"),
                            previous.get("items"),
                        )
                        live_signature = (
                            live.get("cycle_id"),
                            live.get("due_at"),
                            live.get("status"),
                            live.get("items"),
                        ) if isinstance(live, Mapping) else None
                        if previous_signature == live_signature:
                            continue
                        stale_plan_notifications.append(
                            (
                                slot,
                                str(previous["notification_tag"]),
                                str(previous["notification_target"]),
                            )
                        )
                        if isinstance(live, dict) and live.get("status") in {
                            "planned",
                            "snoozed",
                        }:
                            live["last_notification_at"] = None
                            live["notification_sent_at"] = None
                            live["next_reminder_at"] = None
                            live["notification_state"] = "idle"
                            live["notification_reservation_id"] = None
                            live["notification_reserved_at"] = None
                            live["notification_error"] = None
                            live["notification_target"] = None
                            live["notification_tag"] = None
                            live["action_token"] = uuid4().hex
                            live["notification_action_target"] = None
                            refresh_due_notifications = True
                    self._log_reminder_warning_if_needed(
                        profile, reason="configuration"
                    )
                    self._rebuild_helper_listeners()
                    self._rebuild_interface_listeners()
                await self._async_save()
            except Exception:
                self.data = before
                if settings_changed:
                    self._rebuild_helper_listeners()
                    self._rebuild_interface_listeners()
                raise
        self._dispatch(person_id)
        for slot, tag, target in stale_plan_notifications:
            try:
                await self._async_clear_notification(
                    person_id, slot, tag=tag, target=target
                )
            except Exception as err:
                _LOGGER.warning(
                    "Outdated Pill★Pal plan notification %s at %s could not be "
                    "cleared: %s",
                    tag,
                    target,
                    err,
                )
        if refresh_due_notifications and self._running:
            await self._async_scheduler_tick(dt_util.now())
        return result

    async def async_confirm_slot(
        self,
        person_id: str,
        slot: str | None,
        *,
        actor: str | None,
        source: str,
        allow_early: bool = False,
        expected_cycle_id: str | None = None,
        action_token: str | None = None,
    ) -> dict[str, Any]:
        """Confirm a slot for exactly one person."""

        result = await self._mutate(
            person_id,
            lambda: confirm_slot(
                self.data,
                person_id,
                slot,
                actor=actor,
                source=source,
                now=dt_util.now(),
                allow_early=allow_early,
                expected_cycle_id=expected_cycle_id,
                action_token=action_token,
            ),
        )
        clear_slot = result.get("slot")
        if clear_slot and result.get("status") == "already_taken":
            try:
                await self._async_clear_notification(person_id, clear_slot)
                result["notification_cleanup"] = {"status": "cleaned"}
            except Exception as err:
                result["notification_cleanup"] = {
                    "status": "failed",
                    "error": str(err),
                }
                _LOGGER.warning(
                    "Duplicate Pill★Pal confirmation could not clear its stale "
                    "notification: %s",
                    err,
                )
            return result
        if clear_slot and result.get("status") == "taken":
            event = self._latest_event(person_id, "regular_taken", clear_slot)
            if event is not None:
                result["calendar"] = await self._async_sync_intake_calendar_event(
                    person_id, event
                )
            notification_task = self._async_handle_confirmation_notification(
                person_id, clear_slot, source
            )
            if source in {"notification", "helper", "Button-Entität"}:
                # External actions must not finish before their alarm was
                # cleared and acknowledgement delivery was attempted. A
                # failed post-commit Notify call is persisted for retry and
                # must never turn the already committed intake into an error.
                result["notification_feedback"] = await notification_task
            else:
                self._create_task(
                    notification_task,
                    f"Pill★Pal confirmation notification {person_id} {clear_slot}",
                    person_id,
                )
            self._create_task(
                self._async_sync_inventory_notifications(person_id),
                f"Pill★Pal inventory alerts {person_id}",
                person_id,
            )
        return result

    async def async_snooze_slot(
        self,
        person_id: str,
        slot: str | None,
        minutes: int | None,
        *,
        actor: str | None,
        source: str = "Dashboard",
        expected_cycle_id: str | None = None,
        action_token: str | None = None,
    ) -> dict[str, Any]:
        result = await self._mutate(
            person_id,
            lambda: snooze_slot(
                self.data,
                person_id,
                slot,
                minutes,
                actor=actor,
                source=source,
                now=dt_util.now(),
                expected_cycle_id=expected_cycle_id,
                action_token=action_token,
            ),
        )
        clear_slot = result.get("slot")
        if clear_slot:
            if result.get("status") == "skipped":
                event = self._latest_event(person_id, "regular_skipped", clear_slot)
                if event is not None:
                    result["calendar"] = await self._async_sync_intake_calendar_event(
                        person_id, event
                    )
            if source == "notification":
                result["notification_feedback"] = await self._async_handle_slot_action_notification(
                    person_id,
                    clear_slot,
                    "SNOOZE",
                    source,
                    snoozed_until=result.get("snoozed_until"),
                )
            else:
                self._create_task(
                    self._async_clear_notification(person_id, clear_slot),
                    f"Pill★Pal clear notification {person_id} {clear_slot}",
                    person_id,
                )
        return result

    async def async_skip_slot(
        self,
        person_id: str,
        slot: str | None,
        *,
        actor: str | None,
        source: str = "dashboard",
        expected_cycle_id: str | None = None,
        action_token: str | None = None,
    ) -> dict[str, Any]:
        result = await self._mutate(
            person_id,
            lambda: skip_slot(
                self.data,
                person_id,
                slot,
                actor=actor,
                source=source,
                now=dt_util.now(),
                expected_cycle_id=expected_cycle_id,
                action_token=action_token,
            ),
        )
        clear_slot = result.get("slot")
        if clear_slot:
            if source == "notification":
                result["notification_feedback"] = await self._async_handle_slot_action_notification(
                    person_id, clear_slot, "SKIP", source
                )
            else:
                self._create_task(
                    self._async_clear_notification(person_id, clear_slot),
                    f"Pill★Pal clear notification {person_id} {clear_slot}",
                    person_id,
                )
        return result

    async def async_book_as_needed(
        self,
        person_id: str,
        medication_id: str,
        quantity: Any,
        *,
        actor: str | None,
        actor_user_id: str | None = None,
        source: str,
        confirmation_token: str | None = None,
    ) -> dict[str, Any]:
        result = await self._mutate(
            person_id,
            lambda: book_as_needed(
                self.data,
                person_id,
                medication_id,
                quantity,
                actor=actor,
                actor_user_id=actor_user_id,
                source=source,
                confirmation_token=confirmation_token,
                now=dt_util.now(),
            ),
        )
        if result.get("status") != "confirmation_required":
            result["calendar"] = await self._async_sync_intake_calendar_event(
                person_id, result
            )
            if source in {"helper", "Button-Entität"}:
                await self._async_handle_as_needed_notification(
                    person_id, result, source
                )
            self._create_task(
                self._async_sync_inventory_notifications(person_id),
                f"Pill★Pal inventory alerts {person_id}",
                person_id,
            )
        return result

    async def async_acknowledge_errors(
        self, person_id: str, *, actor: str | None
    ) -> dict[str, Any]:
        """Acknowledge current diagnostic errors for one profile."""

        return await self._mutate(
            person_id,
            lambda: acknowledge_errors(
                self.data,
                person_id,
                actor=actor,
                now=dt_util.now(),
            ),
        )

    async def async_save_medication(
        self,
        person_id: str,
        medication: Mapping[str, Any],
        *,
        actor: str | None,
    ) -> dict[str, Any]:
        button_helper = str(medication.get("button_helper", "")).strip()
        if button_helper:
            validate_button_helper_assignment(self.data, person_id, button_helper)
            profile = self.profile(person_id)
            current_id = str(medication.get("id", ""))
            for med_id, existing in profile.get("medications", {}).items():
                if med_id != current_id and existing.get("button_helper") == button_helper:
                    raise DuplicateHelperError(
                        f"{button_helper} wird bereits für {existing.get('name', med_id)} verwendet."
                    )
            if profile.get("settings", {}).get("confirm_helper") == button_helper:
                raise DuplicateHelperError(
                    f"{button_helper} wird bereits für die Sammeleinnahme verwendet."
                )
        result = await self._mutate(
            person_id,
            lambda: save_medication(
                self.data,
                person_id,
                medication,
                actor=actor,
                now=dt_util.now(),
            ),
            settings_changed=True,
        )
        self._create_task(
            self._async_sync_inventory_notifications(person_id),
            f"Pill★Pal inventory alerts {person_id}",
            person_id,
        )
        return result

    async def async_archive_medication(
        self, person_id: str, medication_id: str, *, actor: str | None
    ) -> dict[str, Any]:
        result = await self._mutate(
            person_id,
            lambda: archive_medication(
                self.data,
                person_id,
                medication_id,
                actor=actor,
                now=dt_util.now(),
            ),
            settings_changed=True,
        )
        self._create_task(
            self._async_sync_inventory_notifications(person_id),
            f"Pill★Pal inventory alerts {person_id}",
            person_id,
        )
        return result

    async def async_reactivate_medication(
        self, person_id: str, medication_id: str, *, actor: str | None
    ) -> dict[str, Any]:
        result = await self._mutate(
            person_id,
            lambda: reactivate_medication(
                self.data,
                person_id,
                medication_id,
                actor=actor,
                now=dt_util.now(),
            ),
            settings_changed=True,
        )
        self._create_task(
            self._async_sync_inventory_notifications(person_id),
            f"Pill★Pal inventory alerts {person_id}",
            person_id,
        )
        return result

    async def async_refill(
        self,
        person_id: str,
        medication_id: str,
        quantity: Any | None,
        expiry_date: str | None,
        *,
        actor: str | None,
    ) -> dict[str, Any]:
        result = await self._mutate(
            person_id,
            lambda: refill(
                self.data,
                person_id,
                medication_id,
                quantity,
                expiry_date,
                actor=actor,
                now=dt_util.now(),
            ),
        )
        self._create_task(
            self._async_sync_inventory_notifications(person_id),
            f"Pill★Pal inventory alerts {person_id}",
            person_id,
        )
        return result

    async def async_adjust_stock(
        self,
        person_id: str,
        medication_id: str,
        delta: Any,
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Apply and durably commit a relative stock correction."""

        result = await self._mutate(
            person_id,
            lambda: adjust_stock(
                self.data,
                person_id,
                medication_id,
                delta,
                actor=actor,
                source=source,
                now=dt_util.now(),
            ),
        )
        self._create_task(
            self._async_sync_inventory_notifications(person_id),
            f"Pill★Pal inventory alerts {person_id}",
            person_id,
        )
        return result

    async def async_update_settings(
        self,
        person_id: str,
        updates: Mapping[str, Any],
        *,
        actor: str | None,
    ) -> dict[str, Any]:
        for key in ("confirm_helper",):
            if key in updates:
                validate_helper_assignment(
                    self.data, person_id, key, str(updates.get(key, ""))
                )
        if updates.get("confirm_helper"):
            entity_id = str(updates["confirm_helper"])
            medication_owner = medication_button_owners(self.data).get(entity_id)
            if medication_owner is not None:
                raise DuplicateHelperError(
                    f"{entity_id} wird bereits für eine Bedarfseinnahme verwendet."
                )
        profile_before = self.profile(person_id)
        settings_before = profile_before.get("settings", {})
        old_target = str(settings_before.get("notify_target", ""))
        old_base_tag = str(settings_before.get("notification_tag", "Medikation"))
        old_order_tag = str(
            settings_before.get("order_notification_tag", "Medikamentenbestellung")
        )
        old_expiry_tag = str(
            settings_before.get("expiry_notification_tag", "MedikamentenMHD")
        )
        old_holiday_calendar = str(settings_before.get("holiday_calendar", ""))
        old_slot_endpoints = {
            (str(item.get("notification_target", "")), str(item.get("notification_tag", "")))
            for item in profile_before.get("runtime", {}).get("slots", {}).values()
            if item.get("notification_target") and item.get("notification_tag")
        }
        old_inventory_endpoints: dict[str, tuple[str, str]] = {}
        for kind, delivery in profile_before.get("runtime", {}).get(
            "inventory_notification_deliveries", {}
        ).items():
            if isinstance(delivery, Mapping) and delivery.get("target") and delivery.get("tag"):
                old_inventory_endpoints[str(kind)] = (
                    str(delivery["target"]),
                    str(delivery["tag"]),
                )

        intake_endpoint_change_requested = (
            "notify_target" in updates
            and str(updates.get("notify_target", "")) != old_target
        ) or (
            "notification_tag" in updates
            and str(updates.get("notification_tag", "")) != old_base_tag
        )
        holiday_change_requested = (
            "holiday_calendar" in updates
            and str(updates.get("holiday_calendar", "")) != old_holiday_calendar
        )

        def operation() -> dict[str, Any]:
            updated = update_settings(
                self.data,
                person_id,
                updates,
                actor=actor,
                now=dt_util.now(),
            )
            if intake_endpoint_change_requested:
                runtime = self.profile(person_id).setdefault("runtime", {})
                for item in runtime.get("slots", {}).values():
                    if item.get("status") in {"taken", "skipped", "missed"}:
                        continue
                    item["last_notification_at"] = None
                    item["next_reminder_at"] = None
                    item["notification_reservation_id"] = None
                    item["notification_state"] = "idle"
            return updated

        result = await self._mutate(
            person_id,
            operation,
            settings_changed=True,
        )
        target_changed = old_target != str(result.get("notify_target", ""))
        intake_tag_changed = old_base_tag != str(
            result.get("notification_tag", "Medikation")
        )
        order_tag_changed = old_order_tag != str(
            result.get("order_notification_tag", "Medikamentenbestellung")
        )
        expiry_tag_changed = old_expiry_tag != str(
            result.get("expiry_notification_tag", "MedikamentenMHD")
        )
        cleanup_endpoints: set[tuple[str, str]] = set()
        if target_changed:
            cleanup_endpoints.update(old_slot_endpoints)
            cleanup_endpoints.update(old_inventory_endpoints.values())
        else:
            if intake_tag_changed:
                cleanup_endpoints.update(old_slot_endpoints)
            if order_tag_changed and "stock" in old_inventory_endpoints:
                cleanup_endpoints.add(old_inventory_endpoints["stock"])
            if expiry_tag_changed and "expiry" in old_inventory_endpoints:
                cleanup_endpoints.add(old_inventory_endpoints["expiry"])
        endpoint_changed = bool(
            target_changed
            or intake_tag_changed
            or order_tag_changed
            or expiry_tag_changed
        )
        if holiday_change_requested:
            await self._async_refresh_holiday_forecast(person_id, force=True)
        if endpoint_changed:
            for target, tag in sorted(cleanup_endpoints):
                try:
                    await self._async_call_notify(
                        target,
                        {"message": "clear_notification", "data": {"tag": tag}},
                        blocking=True,
                    )
                except Exception as err:
                    _LOGGER.warning(
                        "Old Pill★Pal notification %s at %s could not be cleared: %s",
                        tag,
                        target,
                        err,
                    )
            await self._async_sync_inventory_notifications(person_id)
            if self._running and (target_changed or intake_tag_changed):
                await self._async_scheduler_tick(dt_util.now())
        else:
            self._create_task(
                self._async_sync_inventory_notifications(person_id),
                f"Pill★Pal inventory alerts {person_id}",
                person_id,
            )
        return result

    async def async_update_practice_closures(
        self, person_id: str, closures: list[Mapping[str, Any]], *, actor: str | None
    ) -> list[dict[str, str]]:
        def operation() -> list[dict[str, str]]:
            normalized = normalize_practice_closures(
                closures, dt_util.now().date()
            )
            profile = self.profile(person_id)
            profile["practice_closures"] = normalized
            append_log(
                profile,
                f"{profile['name']}: Praxisschließzeiten aktualisiert.",
                source="practice",
                actor=actor,
                now=dt_util.now(),
            )
            return deepcopy(normalized)

        result = await self._mutate(person_id, operation)
        self._create_task(
            self._async_sync_inventory_notifications(person_id),
            f"Pill★Pal inventory alerts {person_id}",
            person_id,
        )
        return result

    async def async_recalculate(
        self, person_id: str, *, actor: str | None
    ) -> dict[str, Any]:
        """Rebuild every derived value and retry failed calendar output."""

        def operation() -> dict[str, Any]:
            profile = self.profile(person_id)
            current = dt_util.now()
            self._apply_dynamic_context(profile, current)
            self._reconcile_cycle_lifecycle(
                profile, current, allow_helper_recovery=True
            )
            rebuild_schedule(profile, current)
            append_log(
                profile,
                f"{profile['name']}: Manuelle Neuberechnung ausgeführt.",
                source="recalculate",
                actor=actor,
                now=current,
            )
            return {
                "status": "recalculated",
                "person_id": person_id,
                "cycle_id": profile.get("runtime", {}).get("cycle_id"),
                "cycle_date": profile.get("runtime", {}).get("cycle_date"),
            }

        result = await self._mutate(person_id, operation, settings_changed=True)
        await self._async_refresh_holiday_forecast(person_id, force=True)
        retry_ids = {
            str(item.get("event_id"))
            for item in self.profile(person_id)
            .get("runtime", {})
            .get("intake_calendar_deliveries", {})
            .values()
            if item.get("status") in {"error", "pending"} and item.get("event_id")
        }
        retried = []
        for event in self.profile(person_id).get("events", []):
            if str(event.get("id")) not in retry_ids:
                continue
            retried.append(
                await self._async_sync_intake_calendar_event(person_id, event)
            )
        await self._async_sync_inventory_notifications(person_id)
        result["calendar_retries"] = retried
        return result

    def available_helpers(self, person_id: str, key: str) -> list[str]:
        """List compatible helpers excluding values owned by another person."""

        domain = "input_button" if key in {"confirm_helper", "button_helper"} else "input_boolean"
        owners = helper_owners(self.data, key)
        if key in {"confirm_helper", "button_helper"}:
            owners.update(helper_owners(self.data, "confirm_helper"))
            owners.update(medication_button_owners(self.data))
        return sorted(
            state.entity_id
            for state in self.hass.states.async_all(domain)
            if owners.get(state.entity_id) in (None, person_id)
        )

    def available_notify_targets(self) -> list[str]:
        """Return the callable notify services that exist at this moment."""

        services = self.hass.services.async_services().get("notify", {})
        return sorted(
            f"notify.{service}"
            for service in services
            if service not in {"send_message", "persistent_notification"}
        )

    def _lifecycle_listener(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        """Ignore already queued callbacks after reload or shutdown."""

        generation = self._generation

        async def guarded(*args: Any) -> None:
            if not self._running or generation != self._generation:
                return
            await handler(*args)

        return guarded

    def _register_runtime_listeners(self) -> None:
        replacements: list[Callable[[], None]] = []
        replacements.append(
            async_track_time_interval(
                self.hass,
                self._lifecycle_listener(self._async_scheduler_tick),
                SCHEDULER_INTERVAL,
            )
        )
        configured_ids = {
            str(subentry.data[CONF_PERSON_ID])
            for subentry in self.entry.get_subentries_of_type(PERSON_SUBENTRY_TYPE)
        }
        person_entities = [
            str(profile.get("person_entity_id", ""))
            for person_id, profile in self.data.get("profiles", {}).items()
            if person_id in configured_ids and profile.get("person_entity_id")
        ]
        if person_entities:
            replacements.append(
                async_track_state_change_event(
                    self.hass,
                    person_entities,
                    self._lifecycle_listener(self._async_person_changed),
                )
            )
        replacements.append(
            self.hass.bus.async_listen(
                "mobile_app_notification_action",
                self._lifecycle_listener(self._async_notification_action),
            )
        )
        replacements.append(
            self.hass.bus.async_listen(
                "service_registered",
                self._lifecycle_listener(self._async_notify_services_changed),
            )
        )
        replacements.append(
            self.hass.bus.async_listen(
                "service_removed",
                self._lifecycle_listener(self._async_notify_services_changed),
            )
        )
        previous = self._unsubscribers
        self._unsubscribers = replacements
        for unsubscribe in previous:
            try:
                unsubscribe()
            except (RuntimeError, ValueError):
                pass
        self._rebuild_helper_listeners()
        self._rebuild_interface_listeners()

    def due_output_available(self, profile: Mapping[str, Any]) -> bool:
        """Return whether the native per-person due output can be consumed."""

        try:
            from homeassistant.helpers import entity_registry as er

            registry = er.async_get(self.hass)
            unique_id = f"{profile['person_id']}_intake_due"
            matches = [
                item
                for item in er.async_entries_for_config_entry(
                    registry, self.entry.entry_id
                )
                if item.unique_id == unique_id
            ]
        except (AttributeError, ImportError, KeyError, TypeError):
            # On first setup the entity does not exist in the registry yet, but
            # the binary-sensor platform will create it immediately afterwards.
            return True
        return not matches or any(item.disabled_by is None for item in matches)

    async def _async_notify_services_changed(self, event: Event) -> None:
        """Refresh validation, entities and open dashboards on notify changes."""

        if str(event.data.get("domain", "")) != "notify":
            return
        changed = False
        async with self._lock:
            for profile in self.active_profiles:
                before = deepcopy(profile.get("warning_fingerprints", {}))
                before_log_size = len(profile.get("log", []))
                self._log_reminder_warning_if_needed(
                    profile, reason="notify_registry"
                )
                changed = changed or before != profile.get("warning_fingerprints", {})
                changed = changed or before_log_size != len(profile.get("log", []))
            if changed:
                await self._async_save()
        for profile in self.active_profiles:
            self._dispatch(profile["person_id"])
            if self.valid_notify_target(
                str(profile.get("settings", {}).get("notify_target", ""))
            ):
                self._create_task(
                    self._async_sync_inventory_notifications(profile["person_id"]),
                    f"Pill★Pal inventory alerts {profile['person_id']}",
                    profile["person_id"],
                )
        for profile in self.data.get("profiles", {}).values():
            if profile.get("runtime", {}).get("pending_notification_cleanup"):
                await self._async_retry_pending_notification_cleanup(
                    str(profile.get("person_id", "")), force=True
                )
        if self._running:
            await self._async_scheduler_tick(dt_util.now())

    def _apply_dynamic_context(self, profile: dict[str, Any], now: datetime) -> None:
        """Resolve configured HA entities into person-local effective times/status."""

        settings = profile.get("settings", {})
        alarm_entity = str(settings.get("next_alarm_entity", ""))
        alarm_state = self.hass.states.get(alarm_entity) if alarm_entity else None
        alarm = None
        if alarm_state is not None and alarm_state.state not in {"", "unknown", "unavailable"}:
            alarm = dt_util.parse_datetime(str(alarm_state.state))
            if alarm is None:
                for key in ("timestamp", "next_alarm"):
                    alarm = dt_util.parse_datetime(str(alarm_state.attributes.get(key, "")))
                    if alarm is not None:
                        break
        if alarm is not None:
            alarm = dt_util.as_local(alarm)
        calendar_entity = str(settings.get("holiday_calendar", ""))
        calendar_state = self.hass.states.get(calendar_entity) if calendar_entity else None
        runtime = profile.setdefault("runtime", {})
        runtime["next_alarm_at"] = alarm.isoformat() if alarm is not None else None
        runtime["holiday_calendar_active"] = bool(calendar_state and calendar_state.state == "on")
        runtime["holiday_calendar_message"] = (
            str(calendar_state.attributes.get("message", "")) if calendar_state else ""
        )

    @staticmethod
    def _calendar_event_dates(event: Mapping[str, Any]) -> list[str]:
        """Expand one HA calendar event into affected local calendar dates."""

        raw_start = str(event.get("start", "")).strip()
        raw_end = str(event.get("end", "")).strip()
        if len(raw_start) < 10:
            return []
        try:
            start_date = date.fromisoformat(raw_start[:10])
            end_date = date.fromisoformat(raw_end[:10]) if len(raw_end) >= 10 else start_date
        except ValueError:
            return []
        all_day = len(raw_start) == 10
        last_date = end_date
        if end_date > start_date:
            end_at = dt_util.parse_datetime(raw_end) if not all_day else None
            if all_day or (
                end_at is not None and dt_util.as_local(end_at).time() == time.min
            ):
                last_date = end_date - timedelta(days=1)
        if last_date < start_date:
            last_date = start_date
        return [
            (start_date + timedelta(days=offset)).isoformat()
            for offset in range((last_date - start_date).days + 1)
        ]

    @staticmethod
    def _holiday_calendar_retry_pending(error: Any) -> bool:
        """Recognize Home Assistant calendar states that are normal during startup."""

        message = str(error).casefold()
        return any(
            marker in message
            for marker in (
                "sync from server has not completed",
                "service call requested response data but did not match any entities",
                "noch keine antwort. der abruf wird automatisch wiederholt",
            )
        )

    @classmethod
    def _remove_transient_calendar_log_entries(
        cls, profile: dict[str, Any], entity_id: str
    ) -> bool:
        """Remove old startup-sync noise written by earlier Pill★Pal builds."""

        entries = profile.get("log", [])
        if not isinstance(entries, list):
            return False
        retained = [
            item
            for item in entries
            if not (
                isinstance(item, Mapping)
                and item.get("level") == "error"
                and item.get("source") == "calendar"
                and entity_id in str(item.get("message", ""))
                and cls._holiday_calendar_retry_pending(item.get("message", ""))
            )
        ]
        if len(retained) == len(entries):
            return False
        profile["log"] = retained
        return True

    async def _async_refresh_holiday_forecast(
        self, person_id: str, *, force: bool = False
    ) -> bool:
        """Refresh a person-local multi-year holiday forecast once per day."""

        current = dt_util.now()
        today = current.date()
        async with self._lock:
            profile = self.profile(person_id)
            entity_id = str(profile.get("settings", {}).get("holiday_calendar", ""))
            runtime = profile.setdefault("runtime", {})
            previous = deepcopy(
                runtime.get("holiday_calendar_forecast", _empty_holiday_forecast())
            )
            if not entity_id:
                cleared = previous != _empty_holiday_forecast()
                if cleared:
                    runtime["holiday_calendar_forecast"] = _empty_holiday_forecast()
                    await self._async_save()
                else:
                    runtime.setdefault(
                        "holiday_calendar_forecast", _empty_holiday_forecast()
                    )
                if cleared:
                    self._dispatch(person_id)
                return cleared
            if (
                not force
                and previous.get("entity_id") == entity_id
                and previous.get("fetched_on") == today.isoformat()
                and not previous.get("last_error")
            ):
                return False

        range_start = today - timedelta(days=370)
        range_end = today + timedelta(days=1096)
        error: Exception | None = None
        retry_pending = False
        refreshed: dict[str, Any]
        try:
            response = await self.hass.services.async_call(
                "calendar",
                "get_events",
                {
                    "start_date_time": f"{range_start.isoformat()} 00:00:00",
                    "end_date_time": f"{range_end.isoformat()} 00:00:00",
                },
                blocking=True,
                target={"entity_id": entity_id},
                return_response=True,
            )
            if not isinstance(response, Mapping):
                raise PillPalError("calendar.get_events lieferte keine gültige Antwort.")
            if entity_id not in response:
                raise PillPalError(
                    "calendar.get_events lieferte für den gewählten Kalender "
                    "noch keine Antwort. Der Abruf wird automatisch wiederholt."
                )
            calendar_result = response.get(entity_id, {})
            if not isinstance(calendar_result, Mapping) or not isinstance(
                calendar_result.get("events", []), list
            ):
                raise PillPalError("calendar.get_events lieferte keine Ereignisliste.")
            closed_dates: set[str] = set()
            event_count = 0
            for event in calendar_result.get("events", []):
                if not isinstance(event, Mapping):
                    continue
                event_dates = self._calendar_event_dates(event)
                if event_dates:
                    event_count += 1
                    closed_dates.update(event_dates)
            refreshed = {
                "entity_id": entity_id,
                "range_start": range_start.isoformat(),
                "range_end": range_end.isoformat(),
                "fetched_on": today.isoformat(),
                "closed_dates": sorted(closed_dates),
                "event_count": event_count,
                "last_error": None,
            }
        except Exception as err:  # HA integrations may fail independently.
            error = err
            retry_pending = self._holiday_calendar_retry_pending(err)
            refreshed = (
                deepcopy(previous)
                if previous.get("entity_id") == entity_id
                else _empty_holiday_forecast()
            )
            refreshed.update(
                {
                    "entity_id": entity_id,
                    "range_start": range_start.isoformat(),
                    "range_end": range_end.isoformat(),
                    # A not-yet-synchronized calendar is a normal HA startup
                    # state. Keep it retryable without exposing a false error.
                    "fetched_on": None if retry_pending else today.isoformat(),
                    "last_error": None if retry_pending else str(err),
                }
            )

        changed = False
        async with self._lock:
            profile = self.profile(person_id)
            if str(profile.get("settings", {}).get("holiday_calendar", "")) != entity_id:
                return False
            runtime = profile.setdefault("runtime", {})
            live = runtime.get("holiday_calendar_forecast", _empty_holiday_forecast())
            changed = live != refreshed
            removed_transient_log = (
                self._remove_transient_calendar_log_entries(profile, entity_id)
                if error is None
                else False
            )
            if changed:
                runtime["holiday_calendar_forecast"] = refreshed
                if (
                    error is not None
                    and not retry_pending
                    and live.get("last_error") != str(error)
                ):
                    append_log(
                        profile,
                        f"{profile['name']}: Feiertagskalender {entity_id} konnte nicht "
                        f"gelesen werden: {error}",
                        level="error",
                        source="calendar",
                        now=current,
                    )
                elif error is None:
                    event_count = int(refreshed["event_count"])
                    event_label = "Ereignis" if event_count == 1 else "Ereignisse"
                    append_log(
                        profile,
                        f"{profile['name']}: Feiertagskalender {entity_id} gelesen "
                        f"({event_count} {event_label}, "
                        f"Vorschau {_format_german_date(refreshed['range_start'])} bis "
                        f"{_format_german_date(refreshed['range_end'])}).",
                        source="calendar",
                        now=current,
                    )
            if changed or removed_transient_log:
                await self._async_save()
        if changed or removed_transient_log:
            self._dispatch(person_id)
        return changed or removed_transient_log

    @staticmethod
    def _state_available(state: Any) -> bool:
        return state is not None and state.state not in {"", "unknown", "unavailable"}

    def _reconcile_cycle_lifecycle(
        self,
        profile: dict[str, Any],
        now: datetime,
        *,
        allow_helper_recovery: bool = False,
    ) -> bool:
        """Reconcile helper/fallback lifecycle without using midnight as a boundary."""

        settings = profile.get("settings", {})
        helper_id = str(settings.get("awake_helper", "")).strip()
        helper_state = self.hass.states.get(helper_id) if helper_id else None
        if helper_id and self._state_available(helper_state):
            if helper_state.state == "off" and cycle_is_active(profile):
                end_cycle(profile, now, source="awake_helper")
                return True
            if (
                helper_state.state == "on"
                and not cycle_is_active(profile)
                and allow_helper_recovery
            ):
                awake_at = dt_util.as_local(helper_state.last_changed)
                if awake_at.date() == now.date():
                    start_cycle(profile, awake_at, started_by="awake_helper")
                    return True
            return False

        try:
            fallback_time = datetime.strptime(
                str(settings.get("fallback_wake_time", "08:00")), "%H:%M"
            ).time()
        except (TypeError, ValueError):
            fallback_time = datetime.strptime("08:00", "%H:%M").time()
        fallback_at = datetime.combine(now.date(), fallback_time, now.tzinfo)
        runtime = profile.setdefault("runtime", {})
        if (
            now >= fallback_at
            and runtime.get("fallback_wake_last_started_date") != now.date().isoformat()
        ):
            if cycle_is_active(profile):
                end_cycle(profile, fallback_at, source="fallback_time")
            start_cycle(profile, fallback_at, started_by="fallback_time")
            return True
        return False

    def _rebuild_interface_listeners(self) -> None:
        """Listen for alarm, awake and calendar changes selected by each profile."""

        entity_ids = {
            str(profile.get("settings", {}).get(key, ""))
            for profile in self.active_profiles
            for key in ("next_alarm_entity", "awake_helper", "holiday_calendar")
            if profile.get("settings", {}).get(key)
        }
        replacements: list[Callable[[], None]] = []
        if entity_ids:
            replacements.append(
                async_track_state_change_event(
                    self.hass,
                    sorted(entity_ids),
                    self._lifecycle_listener(self._async_interface_changed),
                )
            )
        previous = self._interface_unsubscribers
        self._interface_unsubscribers = replacements
        for unsubscribe in previous:
            try:
                unsubscribe()
            except (RuntimeError, ValueError):
                pass

    async def _async_interface_changed(self, event: Event) -> None:
        """Refresh every profile that references the changed HA entity."""

        entity_id = str(event.data.get("entity_id", ""))
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        changed: list[str] = []
        calendar_changed: list[str] = []
        async with self._lock:
            for profile in self.active_profiles:
                settings = profile.get("settings", {})
                if entity_id not in {
                    str(settings.get("next_alarm_entity", "")),
                    str(settings.get("awake_helper", "")),
                    str(settings.get("holiday_calendar", "")),
                }:
                    continue
                current = dt_util.now()
                awake_entity = str(settings.get("awake_helper", ""))
                if entity_id == awake_entity and self._state_available(new_state):
                    old_value = old_state.state if self._state_available(old_state) else None
                    if new_state.state == "on" and old_value != "on":
                        if not cycle_is_active(profile):
                            start_cycle(profile, current, started_by="awake_helper")
                    elif new_state.state == "off" and old_value != "off":
                        if cycle_is_active(profile):
                            end_cycle(profile, current, source="awake_helper")
                elif entity_id == awake_entity:
                    self._reconcile_cycle_lifecycle(profile, current)
                self._apply_dynamic_context(profile, current)
                rebuild_schedule(profile, current)
                changed.append(profile["person_id"])
                if entity_id == str(settings.get("holiday_calendar", "")):
                    calendar_changed.append(profile["person_id"])
            if changed:
                await self._async_save()
        for person_id in changed:
            self._dispatch(person_id)
        for person_id in calendar_changed:
            if await self._async_refresh_holiday_forecast(person_id, force=True):
                self._create_task(
                    self._async_sync_inventory_notifications(person_id),
                    f"Pill★Pal inventory alerts {person_id}",
                    person_id,
                )

    def _rebuild_helper_listeners(self) -> None:
        """Rebuild confirmation helper listeners with unambiguous ownership."""

        replacements_by_entity: dict[str, tuple[str, str, str | None]] = {}
        duplicates: set[str] = set()
        for profile in self.active_profiles:
            entity_id = str(profile.get("settings", {}).get("confirm_helper", ""))
            if entity_id:
                if entity_id in replacements_by_entity:
                    duplicates.add(entity_id)
                else:
                    replacements_by_entity[entity_id] = (
                        profile["person_id"], "confirm", None
                    )
            for medication in profile.get("medications", {}).values():
                med_helper = str(medication.get("button_helper", ""))
                if not med_helper or medication.get("archived"):
                    continue
                if med_helper in replacements_by_entity:
                    duplicates.add(med_helper)
                    continue
                replacements_by_entity[med_helper] = (
                    profile["person_id"], "medication", medication["id"]
                )
        for entity_id in duplicates:
            owners = [
                profile["person_id"]
                for profile in self.active_profiles
                if entity_id
                in {
                    profile.get("settings", {}).get("confirm_helper"),
                    *(
                        med.get("button_helper")
                        for med in profile.get("medications", {}).values()
                    ),
                }
            ]
            replacements_by_entity.pop(entity_id, None)
            _LOGGER.error(
                "Confirmation helper %s is assigned more than once (%s) and is ignored",
                entity_id,
                ", ".join(sorted(set(owners))),
            )
        replacements: list[Callable[[], None]] = []
        if replacements_by_entity:
            replacements.append(
                async_track_state_change_event(
                    self.hass,
                    list(replacements_by_entity),
                    self._lifecycle_listener(self._async_confirm_helper_changed),
                )
            )
        previous = self._helper_unsubscribers
        self._helper_owner_by_entity = replacements_by_entity
        self._helper_last_state_by_entity = {
            entity_id: value
            for entity_id, value in self._helper_last_state_by_entity.items()
            if entity_id in replacements_by_entity
        }
        self._helper_unsubscribers = replacements
        for unsubscribe in previous:
            try:
                unsubscribe()
            except (RuntimeError, ValueError):
                pass

    async def _async_person_changed(self, event: Event) -> None:
        """Refresh person metadata; transient state loss never means removal."""

        entity_id = str(event.data.get("entity_id", ""))
        profile = next(
            (
                item
                for item in self.data.get("profiles", {}).values()
                if item.get("person_entity_id") == entity_id
            ),
            None,
        )
        if profile is None:
            return
        new_state = event.data.get("new_state")
        if not self._state_available(new_state):
            return
        attributes = (
            new_state.attributes if isinstance(new_state.attributes, Mapping) else {}
        )
        was_archived = bool(profile.get("archived"))
        async with self._lock:
            current = dt_util.now()
            ensure_profile(
                self.data,
                person_id=profile["person_id"],
                name=str(
                    attributes.get("friendly_name")
                    or new_state.name
                    or profile.get("name")
                    or profile["person_id"]
                ),
                person_entity_id=entity_id,
                # Missing attributes can occur while HA restores states.  Preserve
                # the last known link; an explicitly present ``None`` still means
                # that the HA Person was deliberately unlinked from a user.
                user_id=(
                    attributes.get("user_id")
                    if "user_id" in attributes
                    else profile.get("user_id")
                ),
                admin_assistance=bool(profile.get("admin_assistance", False)),
                person_exists=True,
                now=current,
            )
            self._apply_dynamic_context(profile, current)
            self._reconcile_cycle_lifecycle(
                profile, current, allow_helper_recovery=True
            )
            ensure_today_schedule(profile, current)
            if was_archived:
                append_log(
                    profile,
                    f"{profile.get('name', 'Person')}: HA-Person wieder hinzugefügt; "
                    "Medikamente bleiben archiviert.",
                    now=current,
                )
            await self._async_save()
        self._rebuild_helper_listeners()
        self._rebuild_interface_listeners()
        self._dispatch(profile["person_id"])

    async def _async_confirm_helper_changed(self, event: Event) -> None:
        entity_id = event.data.get("entity_id")
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if not entity_id or new_state is None or old_state is None:
            return
        old_value = str(getattr(old_state, "state", "") or "").strip()
        new_value = str(getattr(new_state, "state", "") or "").strip()
        if (
            not new_value
            or new_value == old_value
            or new_value.lower() in {"unknown", "unavailable"}
            or self._helper_last_state_by_entity.get(str(entity_id)) == new_value
        ):
            return
        binding = self._helper_owner_by_entity.get(entity_id)
        if not binding:
            return
        self._helper_last_state_by_entity[str(entity_id)] = new_value
        person_id, action, medication_id = binding
        action_name = "book_as_needed" if action == "medication" else "confirm_slot"
        await self.async_record_action_result(
            person_id,
            action_name,
            "pending",
            "Taster-Aktion wird ausgeführt.",
            actor="Helfer",
        )
        try:
            if action == "medication" and medication_id:
                medication = self.profile(person_id)["medications"][medication_id]
                if medication_is_regular(medication):
                    slot = select_actionable_slot(
                        self.profile(person_id),
                        dt_util.now(),
                        medication_id=medication_id,
                    )
                    if slot is None:
                        raise PillPalError(
                            f"Für {medication['name']} ist aktuell kein passender "
                            "regulärer Slot buchbar."
                        )
                    result = await self.async_confirm_slot(
                        person_id,
                        slot,
                        actor="Helfer",
                        source="Button-Entität",
                    )
                else:
                    result = await self.async_book_as_needed(
                        person_id,
                        medication_id,
                        medication.get("button_amount", medication.get("step", 1)),
                        actor="Helfer",
                        source="Button-Entität",
                    )
            else:
                result = await self.async_confirm_slot(
                    person_id,
                    None,
                    actor="Helfer",
                    source="Button-Entität",
                )
            await self.async_record_action_result(
                person_id,
                action_name,
                "success",
                "Taster-Aktion erfolgreich abgeschlossen.",
                actor="Helfer",
                result=result,
            )
        except PillPalError as err:
            _LOGGER.debug("Pill★Pal helper confirmation rejected: %s", err)
            async with self._lock:
                profile = self.profile(person_id)
                append_log(
                    profile,
                    (
                        f"{profile['name']}: Taster-Helfer {entity_id} konnte "
                        f"nicht verarbeitet werden: {err}"
                    ),
                    level="warning",
                    source="helper",
                    actor="Helfer",
                    now=dt_util.now(),
                )
                await self._async_save()
            self._dispatch(person_id)
            await self.async_record_action_result(
                person_id,
                action_name,
                "error",
                str(err),
                actor="Helfer",
                error_code=err.code,
            )
            if medication_id:
                await self._async_handle_medication_helper_error(
                    person_id,
                    medication_id,
                    str(entity_id),
                    str(err),
                )
        except Exception:
            _LOGGER.exception("Unexpected Pill★Pal helper-action error")
            message = "Technischer Fehler bei der Ausführung der Pill★Pal-Aktion."
            try:
                await self.async_record_action_result(
                    person_id,
                    action_name,
                    "error",
                    message,
                    actor="Helfer",
                    error_code="technical_error",
                )
            except Exception:
                _LOGGER.exception("Could not persist a technical helper-action error")

    async def _async_notification_action(self, event: Event) -> None:
        action = str(event.data.get("action", ""))
        parts = action.split(":")
        if len(parts) != 6 or parts[0] != "PILLPAL":
            return
        command, person_id, cycle_id, slot, token = parts[1:]
        action_name = {
            "TAKE": "confirm_slot",
            "SNOOZE": "snooze_slot",
            "SKIP": "skip_slot",
        }.get(command, "notification_action")
        try:
            await self.async_record_action_result(
                person_id,
                action_name,
                "pending",
                "Benachrichtigungsaktion wird ausgeführt.",
                actor="Benachrichtigung",
            )
        except PillPalError:
            return
        except Exception:
            _LOGGER.exception("Could not start a Pill★Pal notification action")
            return
        try:
            profile = self.profile(person_id)
            slot_data = profile.get("runtime", {}).get("slots", {}).get(slot, {})
            bound_target = str(
                slot_data.get("notification_action_target") or ""
            )
            current_target = str(
                profile.get("settings", {}).get("notify_target", "")
            )
            if not bound_target or bound_target != current_target:
                raise PillPalError(
                    "Diese Benachrichtigungsaktion gehört zu einem anderen oder "
                    "nicht mehr gültigen Benachrichtigungsgerät."
                )
            if command == "TAKE":
                result = await self.async_confirm_slot(
                    person_id,
                    slot,
                    actor="Benachrichtigung",
                    source="notification",
                    expected_cycle_id=cycle_id,
                    action_token=token,
                )
            elif command == "SNOOZE":
                result = await self.async_snooze_slot(
                    person_id,
                    slot,
                    None,
                    actor="Benachrichtigung",
                    source="notification",
                    expected_cycle_id=cycle_id,
                    action_token=token,
                )
            elif command == "SKIP":
                result = await self.async_skip_slot(
                    person_id,
                    slot,
                    actor="Benachrichtigung",
                    source="notification",
                    expected_cycle_id=cycle_id,
                    action_token=token,
                )
            else:
                raise PillPalError("Die Benachrichtigungsaktion ist unbekannt.")
            await self.async_record_action_result(
                person_id,
                action_name,
                "success",
                "Benachrichtigungsaktion erfolgreich abgeschlossen.",
                actor="Benachrichtigung",
                result=result,
            )
        except PillPalError as err:
            _LOGGER.debug("Pill★Pal notification action rejected: %s", err)
            try:
                async with self._lock:
                    profile = self.profile(person_id)
                    append_log(
                        profile,
                        f"{profile['name']}: Benachrichtigungsaktion {command} für "
                        f"{SLOT_LABELS.get(slot, slot)} abgelehnt: {err}",
                        level="warning",
                        source="notification",
                        actor="Benachrichtigung",
                        now=dt_util.now(),
                    )
                    await self._async_save()
            except PillPalError as log_error:
                _LOGGER.error(
                    "Rejected Pill★Pal notification action could not be logged: %s",
                    log_error,
                )
            try:
                self.profile(person_id)
            except PillPalError:
                return
            await self._async_handle_action_error_notification(
                person_id, slot, command, str(err)
            )
            await self.async_record_action_result(
                person_id,
                action_name,
                "error",
                str(err),
                actor="Benachrichtigung",
                error_code=err.code,
            )
            self._dispatch(person_id)
        except Exception:
            _LOGGER.exception("Unexpected Pill★Pal notification-action error")
            message = "Technischer Fehler bei der Ausführung der Pill★Pal-Aktion."
            try:
                await self.async_record_action_result(
                    person_id,
                    action_name,
                    "error",
                    message,
                    actor="Benachrichtigung",
                    error_code="technical_error",
                )
                await self._async_handle_action_error_notification(
                    person_id, slot, command, message
                )
            except Exception:
                _LOGGER.exception(
                    "Could not publish a technical notification-action error"
                )

    async def _async_scheduler_tick(self, now: datetime) -> None:
        """Process every profile explicitly; never switch a shared context."""

        if not self._running:
            return
        now = dt_util.as_local(now)
        generation = self._generation
        forecast_changed_ids = [
            profile["person_id"]
            for profile in list(self.active_profiles)
            if await self._async_refresh_holiday_forecast(profile["person_id"])
        ]
        feedback_retry_ids = [
            profile["person_id"]
            for profile in list(self.active_profiles)
            if profile.get("runtime", {}).get("pending_notification_feedback")
        ]
        cleanup_retry_ids = [
            str(profile.get("person_id", ""))
            for profile in list(self.data.get("profiles", {}).values())
            if profile.get("runtime", {}).get("pending_notification_cleanup")
        ]
        reminders: list[tuple[str, str, str, str, int]] = []
        calendar_events: list[tuple[str, dict[str, Any]]] = []
        stale_notifications: list[tuple[str, str, str | None, str | None]] = []
        changed_ids: set[str] = set()
        async with self._lock:
            for profile in self.active_profiles:
                normalized_closures = normalize_practice_closures(
                    profile.get("practice_closures", []), now.date()
                )
                if normalized_closures != profile.get("practice_closures", []):
                    profile["practice_closures"] = normalized_closures
                    changed_ids.add(profile["person_id"])
                runtime = profile.get("runtime", {})
                before_cycle_id = runtime.get("cycle_id")
                before_notifications = [
                    (
                        slot,
                        item.get("notification_tag"),
                        item.get("notification_target"),
                    )
                    for slot, item in runtime.get("slots", {}).items()
                    if item.get("notification_tag")
                ]
                self._apply_dynamic_context(profile, now)
                lifecycle_changed = self._reconcile_cycle_lifecycle(profile, now)
                ensure_today_schedule(profile, now)
                runtime = profile.get("runtime", {})
                cycle_id = str(runtime.get("cycle_id") or "")
                if lifecycle_changed or before_cycle_id != runtime.get("cycle_id"):
                    changed_ids.add(profile["person_id"])
                    stale_notifications.extend(
                        (profile["person_id"], slot, tag, target)
                        for slot, tag, target in before_notifications
                    )
                if not cycle_is_active(profile):
                    continue
                slots = runtime.get("slots", {})
                ordered = [slot for slot in ("morning", "noon", "evening", "night") if slot in slots]
                for index, slot in enumerate(ordered):
                    item = slots[slot]
                    if item.get("status") in {"taken", "skipped", "missed"}:
                        continue
                    if index + 1 < len(ordered):
                        next_due = datetime.fromisoformat(slots[ordered[index + 1]]["due_at"])
                        if now >= next_due:
                            stale_notifications.append(
                                (
                                    profile["person_id"],
                                    slot,
                                    item.get("notification_tag"),
                                    item.get("notification_target"),
                                )
                            )
                            mark_slot_missed(profile, slot, now=now, source="schedule")
                            latest = profile.get("events", [])[-1]
                            if latest.get("type") == "regular_missed":
                                calendar_events.append(
                                    (profile["person_id"], deepcopy(latest))
                                )
                            changed_ids.add(profile["person_id"])
                            continue
                    snoozed_until = item.get("snoozed_until")
                    if snoozed_until and now >= datetime.fromisoformat(str(snoozed_until)):
                        item["status"] = "planned"
                        item["snoozed_until"] = None
                        changed_ids.add(profile["person_id"])
                    if not slot_is_due(profile, slot, now):
                        continue
                    next_reminder = item.get("next_reminder_at")
                    if next_reminder and now < datetime.fromisoformat(str(next_reminder)):
                        continue
                    repeat = max(1, int(profile["settings"].get("repeat_minutes", 5)))
                    last = item.get("last_notification_at")
                    if last and now - datetime.fromisoformat(last) < timedelta(minutes=repeat):
                        continue
                    if item.get("notification_state") == "reserved":
                        try:
                            reserved_at = datetime.fromisoformat(
                                str(item.get("notification_reserved_at"))
                            )
                        except (TypeError, ValueError):
                            reserved_at = now - timedelta(minutes=5)
                        if now - reserved_at < timedelta(minutes=2):
                            continue
                    reservation_id = uuid4().hex
                    item["notification_state"] = "reserved"
                    item["notification_reservation_id"] = reservation_id
                    item["notification_reserved_at"] = now.isoformat()
                    item["notification_error"] = None
                    reminders.append(
                        (
                            profile["person_id"],
                            slot,
                            cycle_id,
                            reservation_id,
                            generation,
                        )
                    )
                    changed_ids.add(profile["person_id"])
            if changed_ids:
                await self._async_save()
        for person_id in changed_ids:
            self._dispatch(person_id)
        for person_id, slot, tag, target in stale_notifications:
            self._create_task(
                self._async_clear_notification(
                    person_id, slot, tag=tag, target=target
                ),
                f"Pill★Pal clear stale reminder {person_id} {slot}",
                person_id,
            )
        for person_id, event in calendar_events:
            self._create_task(
                self._async_sync_intake_calendar_event(person_id, event),
                f"Pill★Pal intake calendar {person_id} {event.get('id')}",
                person_id,
            )
        for person_id, slot, cycle_id, reservation_id, task_generation in reminders:
            self._create_task(
                self._async_send_reminder(
                    person_id,
                    slot,
                    expected_cycle_id=cycle_id,
                    reservation_id=reservation_id,
                    generation=task_generation,
                ),
                f"Pill★Pal reminder {person_id} {slot}",
                person_id,
            )
        for person_id in forecast_changed_ids:
            self._create_task(
                self._async_sync_inventory_notifications(person_id),
                f"Pill★Pal inventory alerts {person_id}",
                person_id,
            )
        for person_id in feedback_retry_ids:
            self._create_task(
                self._async_retry_pending_notification_feedback(person_id),
                f"Pill★Pal retry action feedback {person_id}",
                person_id,
            )
        for person_id in cleanup_retry_ids:
            self._create_task(
                self._async_retry_pending_notification_cleanup(person_id),
                f"Pill★Pal retry notification cleanup {person_id}",
                person_id,
            )

    def valid_notify_target(self, target: str) -> bool:
        if not target or not target.startswith("notify."):
            return False
        service = target.split(".", 1)[1]
        # Pill★Pal calls the legacy notify service directly.  A notify entity with
        # a similar name is not interchangeable with that service and must not be
        # accepted here (for example notify.pixel_8_pro versus the callable
        # notify.mobile_app_pixel_8_pro service).
        return self.hass.services.has_service("notify", service)

    def _log_reminder_warning_if_needed(
        self, profile: dict[str, Any], *, reason: str
    ) -> None:
        """Log once per configuration state, never from the minute loop."""

        settings = profile.get("settings", {})
        has_regular = bool(regular_medications(profile))
        notify_target = str(settings.get("notify_target", ""))
        notify_valid = self.valid_notify_target(notify_target)
        due_output_valid = self.due_output_available(profile)
        fingerprint = (
            f"regular={has_regular};target={notify_target};notify={notify_valid};"
            f"due_output={due_output_valid}"
        )
        warning_fingerprints = profile.setdefault("warning_fingerprints", {})
        if not has_regular or notify_valid or due_output_valid:
            warning_fingerprints.pop("reminder_configuration", None)
            return
        if warning_fingerprints.get("reminder_configuration") == fingerprint:
            return
        detail = reminder_configuration_warning(
            profile,
            notify_target_valid=notify_valid,
            due_output_available=due_output_valid,
        )
        if detail is None:
            return
        append_log(
            profile,
            f"{profile['name']}: {detail}",
            level="warning",
            source=f"configuration:{reason}",
            now=dt_util.now(),
        )
        warning_fingerprints["reminder_configuration"] = fingerprint

    async def _async_call_notify(
        self, target: str, payload: dict[str, Any], *, blocking: bool = False
    ) -> bool:
        if not self.valid_notify_target(target):
            return False
        service = target.split(".", 1)[1]
        if self.hass.services.has_service("notify", service):
            await self.hass.services.async_call(
                "notify", service, payload, blocking=blocking
            )
            return True
        return False

    async def _async_send_reminder(
        self,
        person_id: str,
        slot: str,
        *,
        expected_cycle_id: str,
        reservation_id: str,
        generation: int,
    ) -> None:
        """Send one reserved reminder and commit its timestamp only after success."""

        if generation != self._generation or not self._running:
            return
        current = dt_util.now()
        async with self._lock:
            profile = self.profile(person_id)
            runtime = profile.get("runtime", {})
            slot_data = runtime.get("slots", {}).get(slot, {})
            if (
                generation != self._generation
                or not cycle_is_active(profile)
                or runtime.get("cycle_id") != expected_cycle_id
                or slot_data.get("cycle_id") != expected_cycle_id
                or slot_data.get("notification_reservation_id") != reservation_id
                or slot_data.get("status") in {"taken", "skipped", "missed"}
                or not slot_is_due(profile, slot, current)
            ):
                return
            settings = deepcopy(profile.get("settings", {}))
            target = str(settings.get("notify_target", ""))
            action_token = str(slot_data.get("action_token", ""))
            tag = (
                f"{settings.get('notification_tag', 'Medikation')}-{person_id}-"
                f"{expected_cycle_id}-{slot}"
            )
            slot_data["notification_target"] = target
            slot_data["notification_tag"] = tag
            medications_snapshot = deepcopy(slot_data.get("items", []))
            profile_name = str(profile.get("name", person_id))
            await self._async_save()
        medications = "\n".join(
            f"• {item['name']} – {item['quantity']} "
            f"{item.get('unit_singular') if float(item.get('quantity', 0)) == 1 else item.get('unit_plural', '')}"
            for item in medications_snapshot
        )
        actions = [
            {"action": f"PILLPAL:TAKE:{person_id}:{expected_cycle_id}:{slot}:{action_token}", "title": str(settings.get("action_take", "Eingenommen"))},
            {"action": f"PILLPAL:SNOOZE:{person_id}:{expected_cycle_id}:{slot}:{action_token}", "title": str(settings.get("action_snooze", "Später"))},
            {"action": f"PILLPAL:SKIP:{person_id}:{expected_cycle_id}:{slot}:{action_token}", "title": str(settings.get("action_skip", "Überspringen"))},
        ]
        vibration_pattern = str(settings.get("notification_vibration_pattern", "")).strip()
        led_color = str(settings.get("notification_led_color", "")).strip()
        presentation_options = [
            item.strip()
            for item in str(
                settings.get("ios_presentation_options", "alert, badge, sound")
            ).split(",")
            if item.strip() in {"alert", "badge", "sound"}
        ]
        push_data: dict[str, Any] = {
            "sound": {
                "name": str(settings.get("notification_sound", "alarm.caf")),
                "critical": 1 if settings.get("notification_critical", True) else 0,
                "volume": float(settings.get("ios_volume", 1)),
            },
            "interruption-level": str(
                settings.get("ios_interruption_level", "critical")
            ),
        }
        if int(settings.get("ios_badge", 0)) > 0:
            push_data["badge"] = int(settings["ios_badge"])
        notification_data: dict[str, Any] = {
            "tag": tag,
            "persistent": bool(settings.get("notification_persistent", False)),
            "sticky": bool(settings.get("notification_sticky", True)),
            "alert_once": bool(settings.get("notification_alert_once", False)),
            "channel": str(settings.get("notification_channel", "alarm_stream")),
            "group": str(settings.get("notification_group", "Medikation")),
            "importance": str(settings.get("notification_importance", "high")),
            "priority": str(settings.get("notification_priority", "high")),
            "visibility": str(settings.get("notification_visibility", "private")),
            "notification_icon": str(
                settings.get("notification_icon", "mdi:medication-outline")
            ),
            "ttl": int(settings.get("notification_ttl", 0)),
            "actions": actions,
            "url": f"/{PANEL_URL}/overview",
            "clickAction": f"/{PANEL_URL}/overview",
            "presentation_options": presentation_options or ["alert", "badge", "sound"],
            "push": push_data,
        }
        if vibration_pattern:
            notification_data["vibrationPattern"] = vibration_pattern
        if led_color:
            notification_data["ledColor"] = led_color
        color = str(settings.get("notification_color", "")).strip()
        if color:
            notification_data["color"] = color
        timeout = int(settings.get("notification_timeout", 0))
        if timeout > 0:
            notification_data["timeout"] = timeout
        error: Exception | None = None
        try:
            if not self.valid_notify_target(target):
                raise PillPalError("Das konfigurierte Notify-Ziel ist nicht aufrufbar.")
            intro = str(settings.get("notification_intro", "")).strip()
            medication_text = medications or "• Eine Einnahme ist fällig."
            schedule_text = f"{SLOT_LABELS.get(slot, slot)}:\n{medication_text}"
            message = f"{intro}\n\n{schedule_text}" if intro else schedule_text
            delivered = await self._async_call_notify(
                target,
                {
                    "title": f"Pill★Pal · {settings.get('notification_title', 'Einnahme fällig')} · {profile_name}",
                    "message": message,
                    "data": notification_data,
                },
                blocking=True,
            )
            if not delivered:
                raise PillPalError("Das konfigurierte Notify-Ziel ist nicht mehr verfügbar.")
        except Exception as err:  # Home Assistant service errors are retryable.
            error = err

        clear_after_send = False
        async with self._lock:
            if generation != self._generation:
                return
            profile = self.profile(person_id)
            runtime = profile.get("runtime", {})
            slot_data = runtime.get("slots", {}).get(slot, {})
            if (
                runtime.get("cycle_id") != expected_cycle_id
                or slot_data.get("notification_reservation_id") != reservation_id
            ):
                clear_after_send = error is None
            else:
                slot_data["notification_reservation_id"] = None
                if error is None:
                    sent_at = dt_util.now()
                    repeat = max(1, int(settings.get("repeat_minutes", 5)))
                    slot_data["notification_state"] = "sent"
                    slot_data["notification_sent_at"] = sent_at.isoformat()
                    slot_data["last_notification_at"] = sent_at.isoformat()
                    slot_data["next_reminder_at"] = (
                        sent_at + timedelta(minutes=repeat)
                    ).isoformat()
                    slot_data["notification_error"] = None
                    slot_data["notification_action_target"] = target
                    append_log(
                        profile,
                        f"{profile['name']}: Erinnerung "
                        f"{SLOT_LABELS.get(slot, slot)} an {target} übergeben.",
                        level="info",
                        source="notification",
                        now=sent_at,
                    )
                else:
                    slot_data["notification_state"] = "failed"
                    slot_data["notification_error"] = str(error)
                    append_log(
                        profile,
                        f"{profile['name']}: Erinnerung {SLOT_LABELS.get(slot, slot)} "
                        f"konnte nicht gesendet werden: {error}",
                        level="error",
                        source="notification",
                        now=dt_util.now(),
                    )
                await self._async_save()
        if clear_after_send:
            await self._async_clear_notification(
                person_id, slot, tag=tag, target=target
            )
        self._dispatch(person_id)

    async def _async_sync_inventory_notifications(self, person_id: str) -> None:
        """Synchronize content-complete stock/MHD notifications transactionally."""

        jobs: list[dict[str, Any]] = []
        current = dt_util.now()
        async with self._lock:
            profile = self.profile(person_id)
            settings = profile.get("settings", {})
            target = str(settings.get("notify_target", ""))
            if not self.valid_notify_target(target):
                return
            orders = order_plan(profile, current)
            expiries = expiry_plan(profile, current)
            order_tag = (
                f"{settings.get('order_notification_tag', 'Medikamentenbestellung')}"
                f"-{person_id}"
            )
            expiry_tag = (
                f"{settings.get('expiry_notification_tag', 'MedikamentenMHD')}"
                f"-{person_id}"
            )
            common_data = {
                # No timeout and no persistence: the notification remains until
                # the user dismisses or opens it, but can still be swiped away.
                "sticky": False,
                "persistent": False,
                "alert_once": True,
                "importance": str(settings.get("notification_importance", "high")),
                "priority": str(settings.get("notification_priority", "high")),
                "visibility": str(settings.get("notification_visibility", "private")),
                "group": str(settings.get("notification_group", "Medikation")),
                "url": "/pillpal/bestand",
                "clickAction": "/pillpal/bestand",
            }
            desired: dict[str, dict[str, Any] | None] = {"stock": None, "expiry": None}
            if orders["active"] and orders["items"]:
                stock_lines = []
                for item in orders["items"]:
                    remaining_days = int(item["days_remaining"])
                    day_label = "Tag" if remaining_days == 1 else "Tage"
                    detail = (
                        f"• {item['status_label']}: {item['name']} – Bestand "
                        f"{_format_german_number(item['current_stock'])}, Reichweite "
                        f"{remaining_days} {day_label}, "
                        f"voraussichtlich leer {_format_german_date(item['projected_empty_date'])}"
                    )
                    if item.get("reason") in {
                        "practice_closure_advanced",
                        "practice_closure_noted",
                    }:
                        detail += (
                            f" – Praxisschließung {_format_german_date(item.get('closed_from'))} "
                            f"bis {_format_german_date(item.get('closed_to'))}"
                        )
                    stock_lines.append(detail)
                if orders["cost_status"] == "complete":
                    stock_lines.extend(
                        [
                            "",
                            f"Kosten bzw. Zuzahlung: ca. "
                            f"{_format_german_number(orders['cost_total'])} "
                            f"{orders['currency']}",
                        ]
                    )
                elif orders["cost_status"] == "incomplete":
                    stock_lines.extend(
                        [
                            "",
                            "Kosten bzw. Zuzahlung konnten nicht vollständig ermittelt werden.",
                        ]
                    )
                desired["stock"] = {
                    "title": (
                        f"Pill★Pal · {settings.get('order_notification_title', 'Nachbestellung')}"
                        f" · {profile['name']}"
                    ),
                    "message": "\n".join(stock_lines),
                    "data": {
                        **common_data,
                        "tag": order_tag,
                        "notification_icon": str(
                            settings.get("notification_icon", "mdi:medication-outline")
                        ),
                    },
                }
            if expiries["active"]:
                expiry_lines = []
                for item in expiries["items"]:
                    days = item["days_until_expiry"]
                    day_label = "Tag" if abs(days) == 1 else "Tage"
                    suffix = (
                        f" – seit {abs(days)} {day_label} abgelaufen"
                        if days < 0
                        else " – läuft heute ab"
                        if days == 0
                        else f" – noch {days} {day_label}"
                    )
                    expiry_lines.append(
                        f"• {item['name']}: {_format_german_date(item['expiry_date'])}{suffix}"
                    )
                desired["expiry"] = {
                    "title": (
                        f"Pill★Pal · {settings.get('expiry_notification_title', 'Haltbarkeit prüfen')}"
                        f" · {profile['name']}"
                    ),
                    "message": "\n".join(expiry_lines),
                    "data": {
                        **common_data,
                        "tag": expiry_tag,
                        "notification_icon": str(
                            settings.get("notification_icon", "mdi:medication-outline")
                        ),
                    },
                }

            runtime = profile.setdefault("runtime", {})
            fingerprints = runtime.setdefault(
                "inventory_notification_fingerprints", {}
            )
            deliveries = runtime.setdefault("inventory_notification_deliveries", {})
            reservations = runtime.setdefault(
                "inventory_notification_reservations", {}
            )
            runtime_changed = False
            for kind in ("stock", "expiry"):
                payload = desired[kind]
                tag = (
                    str(payload["data"]["tag"])
                    if payload is not None
                    else order_tag if kind == "stock" else expiry_tag
                )
                fingerprint = (
                    hashlib.sha256(
                        json.dumps(
                            {"target": target, "payload": payload},
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                    if payload is not None
                    else ""
                )
                delivery = deliveries.get(kind)
                if not isinstance(delivery, Mapping):
                    legacy = str(fingerprints.get(kind, ""))
                    delivery = (
                        {"fingerprint": legacy, "target": target, "tag": tag}
                        if legacy
                        else {}
                    )
                existing_reservation = reservations.get(kind)
                if isinstance(existing_reservation, Mapping):
                    try:
                        reserved_at = datetime.fromisoformat(
                            str(existing_reservation.get("created_at"))
                        )
                    except (TypeError, ValueError):
                        reserved_at = current - timedelta(minutes=5)
                    if current - reserved_at < timedelta(minutes=2):
                        continue
                    reservations.pop(kind, None)
                    runtime_changed = True

                operation = None
                operation_target = target
                operation_tag = tag
                if payload is not None and delivery.get("fingerprint") != fingerprint:
                    operation = "send"
                elif payload is None and delivery.get("fingerprint"):
                    operation = "clear"
                    operation_target = str(delivery.get("target") or target)
                    operation_tag = str(delivery.get("tag") or tag)
                if operation is None:
                    continue
                reservation_id = uuid4().hex
                reservation = {
                    "id": reservation_id,
                    "operation": operation,
                    "fingerprint": fingerprint,
                    "target": operation_target,
                    "tag": operation_tag,
                    "created_at": current.isoformat(),
                }
                reservations[kind] = reservation
                runtime_changed = True
                jobs.append(
                    {
                        **reservation,
                        "kind": kind,
                        "payload": deepcopy(payload),
                        "previous": deepcopy(dict(delivery)),
                    }
                )
            if runtime_changed:
                await self._async_save()

        for job in jobs:
            error: Exception | None = None
            success = False
            try:
                if job["operation"] == "send":
                    success = await self._async_call_notify(
                        job["target"], job["payload"], blocking=True
                    )
                else:
                    success = await self._async_call_notify(
                        job["target"],
                        {
                            "message": "clear_notification",
                            "data": {"tag": job["tag"]},
                        },
                        blocking=True,
                    )
                if not success:
                    raise PillPalError("Das Notify-Ziel ist nicht mehr verfügbar.")
            except Exception as err:
                error = err

            clear_previous: tuple[str, str] | None = None
            clear_stale: tuple[str, str] | None = None
            async with self._lock:
                profile = self.profile(person_id)
                runtime = profile.setdefault("runtime", {})
                deliveries = runtime.setdefault(
                    "inventory_notification_deliveries", {}
                )
                reservations = runtime.setdefault(
                    "inventory_notification_reservations", {}
                )
                live = reservations.get(job["kind"])
                if not isinstance(live, Mapping) or live.get("id") != job["id"]:
                    if success and job["operation"] == "send":
                        live_endpoint = (
                            str(live.get("target", "")),
                            str(live.get("tag", "")),
                        ) if isinstance(live, Mapping) else ("", "")
                        if live_endpoint != (job["target"], job["tag"]):
                            clear_stale = (job["target"], job["tag"])
                else:
                    reservations.pop(job["kind"], None)
                    if error is None:
                        previous = job["previous"]
                        if job["operation"] == "send":
                            deliveries[job["kind"]] = {
                                "fingerprint": job["fingerprint"],
                                "target": job["target"],
                                "tag": job["tag"],
                            }
                            profile["runtime"]["inventory_notification_fingerprints"][
                                job["kind"]
                            ] = job["fingerprint"]
                            previous_endpoint = (
                                str(previous.get("target", "")),
                                str(previous.get("tag", "")),
                            )
                            if previous.get("fingerprint") and previous_endpoint != (
                                job["target"], job["tag"]
                            ):
                                clear_previous = previous_endpoint
                        else:
                            deliveries.pop(job["kind"], None)
                            profile["runtime"]["inventory_notification_fingerprints"].pop(
                                job["kind"], None
                            )
                    else:
                        append_log(
                            profile,
                            f"{profile['name']}: {job['kind']}-Hinweis konnte nicht "
                            f"synchronisiert werden: {error}",
                            level="error",
                            source="notification",
                            now=dt_util.now(),
                        )
                    await self._async_save()
            for endpoint in (clear_previous, clear_stale):
                if endpoint and endpoint[0] and endpoint[1]:
                    try:
                        await self._async_call_notify(
                            endpoint[0],
                            {
                                "message": "clear_notification",
                                "data": {"tag": endpoint[1]},
                            },
                            blocking=True,
                        )
                    except Exception as err:
                        _LOGGER.warning(
                            "Old Pill★Pal inventory notification could not be cleared: %s",
                            err,
                        )
        if jobs:
            self._dispatch(person_id)

    async def _async_clear_notification(
        self,
        person_id: str,
        slot: str,
        *,
        tag: str | None = None,
        target: str | None = None,
    ) -> None:
        profile = self.profile(person_id)
        slot_data = profile.get("runtime", {}).get("slots", {}).get(slot, {})
        exact_target = str(
            target
            or slot_data.get("notification_target")
            or profile.get("settings", {}).get("notify_target", "")
        )
        exact_tag = str(
            tag
            or slot_data.get("notification_tag")
            or (
                f"{profile.get('settings', {}).get('notification_tag', 'Medikation')}-"
                f"{person_id}-{slot}"
            )
        )
        cleared = await self._async_call_notify(
            exact_target,
            {
                "message": "clear_notification",
                "data": {"tag": exact_tag},
            },
            blocking=True,
        )
        if not cleared:
            raise PillPalError(
                "Die alte Einnahmebenachrichtigung konnte nicht gelöscht werden."
            )
        async with self._lock:
            live = self.profile(person_id).get("runtime", {}).get("slots", {}).get(slot)
            if live and live.get("notification_tag") == exact_tag:
                live["notification_state"] = "cleaned"
                live["notification_reservation_id"] = None
                await self._async_save()

    async def _async_handle_confirmation_notification(
        self, person_id: str, slot: str, source: str
    ) -> dict[str, Any]:
        """Clear the alarm and acknowledge physical/helper button confirmations."""

        return await self._async_handle_slot_action_notification(
            person_id, slot, "TAKE", source
        )

    def _feedback_endpoint_and_data(
        self,
        profile: Mapping[str, Any],
        slot: str,
        source: str,
        *,
        suffix: str = "feedback",
    ) -> tuple[str, dict[str, Any]]:
        """Build feedback without the long-running Android alarm stream."""

        settings = profile.get("settings", {})
        slot_data = profile.get("runtime", {}).get("slots", {}).get(slot, {})
        replaces_reminder = bool(
            source in {"notification", "helper", "Button-Entität"}
            and slot_data.get("notification_target")
            and slot_data.get("notification_tag")
        )
        target = str(
            slot_data.get("notification_target")
            if replaces_reminder
            else settings.get("notify_target", "")
        )
        tag = str(
            slot_data.get("notification_tag")
            if replaces_reminder
            else (
                f"{settings.get('notification_tag', 'Medikation')}-"
                f"{profile['person_id']}-{slot}-{suffix}"
            )
        )
        data: dict[str, Any] = {
            "tag": tag,
            "persistent": False,
            "sticky": False,
            # Replacing the alarm must alert again so Android plays the short
            # sound of its General notification channel.  This is deliberately
            # distinct from the omitted alarm_stream channel and alarm sound.
            "alert_once": False if replaces_reminder else True,
            # A feedback notification that replaces a currently sounding
            # reminder must retain high-priority FCM delivery.  Otherwise
            # Android may defer it while the screen is off.  Omitting the
            # alarm channel and alarm sound routes Android through its normal
            # General channel, whose short sound confirms the booking.
            "importance": str(
                settings.get("notification_importance", "high")
                if replaces_reminder
                else "low"
            ),
            "priority": str(
                settings.get("notification_priority", "high")
                if replaces_reminder
                else "normal"
            ),
            "visibility": str(settings.get("notification_visibility", "private")),
            "group": str(settings.get("notification_group", "Medikation")),
            "notification_icon": str(
                settings.get("notification_icon", "mdi:medication-outline")
            ),
            "url": f"/{PANEL_URL}/overview",
            "clickAction": f"/{PANEL_URL}/overview",
        }
        if replaces_reminder:
            data["ttl"] = int(settings.get("notification_ttl", 0))
        return target, data

    def _next_intake_orientation(
        self, profile: Mapping[str, Any], now: datetime
    ) -> tuple[str, list[dict[str, str]]]:
        """Describe the next backend slot and expose actions only when due now."""

        runtime = profile.get("runtime", {})
        slots = runtime.get("slots", {})
        due_slot = select_actionable_slot(profile, now)
        if due_slot and slot_is_due(profile, due_slot, now):
            actions = self._slot_notification_actions(profile, due_slot)
            return (
                f" {SLOT_LABELS.get(due_slot, due_slot)} ist bereits fällig und "
                "kann jetzt bestätigt, zurückgestellt oder übersprungen werden.",
                actions,
            )

        future: list[tuple[datetime, int, str]] = []
        for index, slot in enumerate(SLOTS):
            item = slots.get(slot)
            if not item or item.get("status") not in {"planned", "snoozed"}:
                continue
            try:
                due_at = datetime.fromisoformat(str(item.get("due_at")))
            except (TypeError, ValueError):
                continue
            future.append((due_at, index, slot))
        if future:
            due_at, _, slot = min(future, key=lambda value: (value[0], value[1]))
            return (
                f" Nächste Einnahme: {SLOT_LABELS.get(slot, slot)} um "
                f"{due_at.strftime('%H:%M Uhr')}.",
                [],
            )
        if runtime.get("cycle_completed"):
            return " Der Tages-Zyklus ist vollständig.", []
        return " In diesem Tages-Zyklus ist keine weitere Einnahme offen.", []

    @staticmethod
    def _slot_notification_actions(
        profile: Mapping[str, Any], slot: str
    ) -> list[dict[str, str]]:
        """Build actions bound to the slot's current one-time token."""

        runtime = profile.get("runtime", {})
        item = runtime.get("slots", {}).get(slot, {})
        cycle_id = str(runtime.get("cycle_id") or "")
        token = str(item.get("action_token") or "")
        if not cycle_id or not token:
            return []
        settings = profile.get("settings", {})
        return [
            {
                "action": f"PILLPAL:TAKE:{profile['person_id']}:{cycle_id}:{slot}:{token}",
                "title": str(settings.get("action_take", "Eingenommen")),
            },
            {
                "action": f"PILLPAL:SNOOZE:{profile['person_id']}:{cycle_id}:{slot}:{token}",
                "title": str(settings.get("action_snooze", "Später")),
            },
            {
                "action": f"PILLPAL:SKIP:{profile['person_id']}:{cycle_id}:{slot}:{token}",
                "title": str(settings.get("action_skip", "Überspringen")),
            },
        ]

    async def _async_deliver_or_queue_feedback(
        self,
        person_id: str,
        feedback_id: str,
        target: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Deliver post-commit feedback or persist that side effect for retry."""

        error: Exception | None = None
        try:
            delivered = await self._async_call_notify(
                target, payload, blocking=True
            )
            if not delivered:
                raise PillPalError("Das Notify-Ziel ist derzeit nicht aufrufbar.")
        except Exception as err:  # The fachlicher commit must remain successful.
            error = err

        if error is None:
            async with self._lock:
                profile = self.profile(person_id)
                pending = profile.setdefault("runtime", {}).setdefault(
                    "pending_notification_feedback", {}
                )
                if feedback_id in pending:
                    pending.pop(feedback_id, None)
                    await self._async_save()
            return {"status": "delivered"}

        current = dt_util.now()
        try:
            async with self._lock:
                profile = self.profile(person_id)
                pending = profile.setdefault("runtime", {}).setdefault(
                    "pending_notification_feedback", {}
                )
                previous = pending.get(feedback_id, {})
                pending[feedback_id] = {
                    "target": target,
                    "payload": deepcopy(payload),
                    "created_at": str(previous.get("created_at") or current.isoformat()),
                    "updated_at": current.isoformat(),
                    "next_retry_at": (current + timedelta(minutes=5)).isoformat(),
                    "attempts": max(1, int(previous.get("attempts", 0)) + 1),
                    "last_error": str(error),
                }
                append_log(
                    profile,
                    f"{profile['name']}: Fachaktion erfolgreich gespeichert; die "
                    f"Rückmeldung konnte nicht gesendet werden und wird erneut "
                    f"versucht: {error}",
                    level="warning",
                    source="notification_feedback",
                    now=current,
                )
                await self._async_save()
        except Exception:
            _LOGGER.exception(
                "Post-commit Pill★Pal feedback retry could not be persisted"
            )
        self._dispatch(person_id)
        return {"status": "pending_retry", "error": str(error)}

    async def _async_retry_pending_notification_feedback(
        self, person_id: str
    ) -> None:
        """Retry durable feedback side effects without replaying fachlicher actions."""

        if person_id in self._feedback_retrying:
            return
        self._feedback_retrying.add(person_id)
        try:
            current = dt_util.now()
            async with self._lock:
                profile = self.profile(person_id)
                pending = profile.get("runtime", {}).get(
                    "pending_notification_feedback", {}
                )
                jobs = []
                for feedback_id, item in pending.items():
                    try:
                        retry_at = datetime.fromisoformat(
                            str(item.get("next_retry_at"))
                        )
                    except (TypeError, ValueError):
                        retry_at = current
                    if current >= retry_at:
                        jobs.append((feedback_id, deepcopy(item)))

            for feedback_id, item in jobs:
                error: Exception | None = None
                try:
                    delivered = await self._async_call_notify(
                        str(item.get("target", "")),
                        deepcopy(item.get("payload", {})),
                        blocking=True,
                    )
                    if not delivered:
                        raise PillPalError(
                            "Das Notify-Ziel ist weiterhin nicht aufrufbar."
                        )
                except Exception as err:
                    error = err

                async with self._lock:
                    profile = self.profile(person_id)
                    pending = profile.setdefault("runtime", {}).setdefault(
                        "pending_notification_feedback", {}
                    )
                    live = pending.get(feedback_id)
                    if not isinstance(live, Mapping) or live.get(
                        "updated_at"
                    ) != item.get("updated_at"):
                        continue
                    retry_time = dt_util.now()
                    if error is None:
                        pending.pop(feedback_id, None)
                        append_log(
                            profile,
                            f"{profile['name']}: Ausstehende Aktionsrückmeldung "
                            "erfolgreich nachgesendet.",
                            level="info",
                            source="notification_feedback",
                            now=retry_time,
                        )
                    else:
                        live["attempts"] = int(live.get("attempts", 1)) + 1
                        live["updated_at"] = retry_time.isoformat()
                        live["next_retry_at"] = (
                            retry_time + timedelta(minutes=5)
                        ).isoformat()
                        live["last_error"] = str(error)
                    await self._async_save()
                self._dispatch(person_id)
        finally:
            self._feedback_retrying.discard(person_id)

    async def _async_retry_pending_notification_cleanup(
        self, person_id: str, *, force: bool = False
    ) -> None:
        """Retry exact notification clears, including for archived profiles."""

        if person_id in self._cleanup_retrying:
            return
        self._cleanup_retrying.add(person_id)
        try:
            current = dt_util.now()
            async with self._lock:
                profile = self.profile(person_id)
                pending = profile.setdefault("runtime", {}).setdefault(
                    "pending_notification_cleanup", {}
                )
                jobs: list[tuple[str, dict[str, Any]]] = []
                for cleanup_id, item in pending.items():
                    try:
                        retry_at = datetime.fromisoformat(
                            str(item.get("next_retry_at"))
                        )
                    except (TypeError, ValueError):
                        retry_at = current
                    if force or current >= retry_at:
                        jobs.append((cleanup_id, deepcopy(item)))

            for cleanup_id, item in jobs:
                target = str(item.get("target", ""))
                tag = str(item.get("tag", ""))
                error: Exception | None = None
                try:
                    delivered = await self._async_call_notify(
                        target,
                        {"message": "clear_notification", "data": {"tag": tag}},
                        blocking=True,
                    )
                    if not delivered:
                        raise PillPalError(
                            "Der gespeicherte Notify-Dienst ist derzeit nicht aufrufbar."
                        )
                except Exception as err:
                    error = err

                async with self._lock:
                    profile = self.profile(person_id)
                    pending = profile.setdefault("runtime", {}).setdefault(
                        "pending_notification_cleanup", {}
                    )
                    live = pending.get(cleanup_id)
                    if (
                        not isinstance(live, Mapping)
                        or live.get("updated_at") != item.get("updated_at")
                        or live.get("target") != target
                        or live.get("tag") != tag
                    ):
                        continue
                    retry_time = dt_util.now()
                    if error is None:
                        pending.pop(cleanup_id, None)
                    else:
                        live["attempts"] = int(live.get("attempts", 0)) + 1
                        live["updated_at"] = retry_time.isoformat()
                        live["next_retry_at"] = (
                            retry_time + timedelta(minutes=5)
                        ).isoformat()
                        live["last_error"] = str(error)
                        _LOGGER.warning(
                            "Pill★Pal notification cleanup %s at %s is pending: %s",
                            tag,
                            target,
                            error,
                        )
                    await self._async_save()
                self._dispatch(person_id)
        finally:
            self._cleanup_retrying.discard(person_id)

    async def _async_bind_notification_action_target(
        self, person_id: str, slot: str, target: str
    ) -> bool:
        """Persist which concrete notify endpoint received a bound action token."""

        try:
            async with self._lock:
                profile = self.profile(person_id)
                slot_data = profile.get("runtime", {}).get("slots", {}).get(slot)
                if not isinstance(slot_data, dict):
                    return False
                previous = slot_data.get("notification_action_target")
                slot_data["notification_action_target"] = target
                try:
                    await self._async_save()
                except Exception:
                    slot_data["notification_action_target"] = previous
                    raise
            return True
        except Exception:
            _LOGGER.exception(
                "Pill★Pal notification action target could not be persisted"
            )
            return False

    async def _async_handle_slot_action_notification(
        self,
        person_id: str,
        slot: str,
        action: str,
        source: str,
        *,
        snoozed_until: str | None = None,
    ) -> dict[str, Any]:
        """Clear the alarm and acknowledge one successful external slot action."""

        profile = self.profile(person_id)
        slot_data = profile.get("runtime", {}).get("slots", {}).get(slot, {})
        replaces_reminder = bool(
            source in {"notification", "helper", "Button-Entität"}
            and slot_data.get("notification_target")
            and slot_data.get("notification_tag")
        )
        if not replaces_reminder:
            try:
                await self._async_clear_notification(person_id, slot)
            except Exception as err:
                # Feedback remains useful even if no exact reminder endpoint
                # was persisted or a preliminary cleanup failed.
                _LOGGER.warning(
                    "Old Pill★Pal reminder could not be cleared before feedback: %s",
                    err,
                )
            profile = self.profile(person_id)
            slot_data = profile.get("runtime", {}).get("slots", {}).get(slot, {})
        if action == "TAKE":
            source_text = {
                "helper": "über den Taster",
                "Button-Entität": "über die Button-Entität",
                "notification": "über die Benachrichtigung",
            }.get(source)
            if source_text is None:
                return {"status": "not_requested"}
            confirmed_at = slot_data.get("confirmed_at")
            try:
                time_text = "um " + datetime.fromisoformat(
                    str(confirmed_at)
                ).strftime("%H:%M Uhr")
            except (TypeError, ValueError):
                time_text = "zum tatsächlichen Einnahmezeitpunkt"
            title = f"Pill★Pal ✅ Einnahme bestätigt · {profile['name']}"
            message = (
                f"{SLOT_LABELS.get(slot, slot)} wurde {time_text} "
                f"{source_text} bestätigt."
            )
        elif action == "SNOOZE":
            try:
                time_text = datetime.fromisoformat(str(snoozed_until)).strftime(
                    "%H:%M Uhr"
                )
            except (TypeError, ValueError):
                time_text = "den neuen Erinnerungszeitpunkt"
            title = f"Pill★Pal 💤 Erinnerung zurückgestellt · {profile['name']}"
            message = (
                f"{SLOT_LABELS.get(slot, slot)} wurde bis {time_text} "
                "zurückgestellt."
            )
        else:
            skipped_at = slot_data.get("action_consumed_at")
            try:
                time_text = "um " + datetime.fromisoformat(
                    str(skipped_at)
                ).strftime("%H:%M Uhr")
            except (TypeError, ValueError):
                time_text = "zum tatsächlichen Aktionszeitpunkt"
            title = f"Pill★Pal ⏭️ Einnahme übersprungen · {profile['name']}"
            message = (
                f"{SLOT_LABELS.get(slot, slot)} wurde {time_text} übersprungen. "
                "Der Bestand wurde nicht verändert."
            )
        target, data = self._feedback_endpoint_and_data(
            profile, slot, source
        )
        if action in {"TAKE", "SKIP"}:
            orientation, next_actions = self._next_intake_orientation(
                profile, dt_util.now()
            )
            message += orientation
            if next_actions:
                next_slot = select_actionable_slot(profile, dt_util.now())
                if next_slot and await self._async_bind_notification_action_target(
                    person_id, next_slot, target
                ):
                    data["actions"] = next_actions
        elif action == "SNOOZE":
            snooze_actions = self._slot_notification_actions(profile, slot)
            if snooze_actions and await self._async_bind_notification_action_target(
                person_id, slot, target
            ):
                data["actions"] = snooze_actions
        cycle_id = str(profile.get("runtime", {}).get("cycle_id") or "cycle")
        feedback_id = f"{cycle_id}:{slot}:{action}"
        feedback_result = await self._async_deliver_or_queue_feedback(
            person_id,
            feedback_id,
            target,
            {"title": title, "message": message, "data": data},
        )
        if replaces_reminder and feedback_result.get("status") != "delivered":
            # If the atomic replacement itself could not be handed to the
            # device, still attempt to stop the old alarm.  The durable
            # feedback outbox will retry the acknowledgement independently.
            try:
                await self._async_clear_notification(person_id, slot)
            except Exception as err:
                _LOGGER.warning(
                    "Old Pill★Pal reminder could not be cleared after failed "
                    "feedback replacement: %s",
                    err,
                )
        return feedback_result

    async def _async_handle_as_needed_notification(
        self, person_id: str, result: Mapping[str, Any], source: str
    ) -> None:
        """Send the short helper feedback required for a PRN booking."""

        profile = self.profile(person_id)
        medication_id = str(result.get("medication_id", ""))
        medication = profile.get("medications", {}).get(medication_id, {})
        target, data = self._feedback_endpoint_and_data(
            profile, f"prn-{medication_id}", source
        )
        if not self.valid_notify_target(target):
            return
        quantity = result.get("quantity", 0)
        quantity_unit = (
            medication.get("unit_singular", "Einheit")
            if float(quantity) == 1
            else medication.get("unit_plural", "Einheiten")
        )
        stock = medication.get("stock", "–")
        try:
            singular_stock = float(stock) == 1
        except (TypeError, ValueError):
            singular_stock = False
        stock_unit = (
            medication.get("unit_singular", "Einheit")
            if singular_stock
            else medication.get("unit_plural", "Einheiten")
        )
        await self._async_call_notify(
            target,
            {
                "title": f"Pill★Pal 💊 Bedarfseinnahme bestätigt · {profile['name']}",
                "message": (
                    f"{result.get('medication_name', medication.get('name', medication_id))}: "
                    f"{_format_german_number(quantity)} {quantity_unit} über den Taster gebucht. "
                    f"Aktueller Bestand: {_format_german_number(stock)} {stock_unit}."
                ),
                "data": data,
            },
            blocking=True,
        )

    async def _async_handle_medication_helper_error(
        self,
        person_id: str,
        medication_id: str,
        entity_id: str,
        error: str,
    ) -> None:
        """Send a short non-alarming reason for a rejected medication button."""

        profile = self.profile(person_id)
        medication = profile.get("medications", {}).get(medication_id, {})
        target, data = self._feedback_endpoint_and_data(
            profile,
            f"helper-{medication_id}",
            "Button-Entität",
            suffix="helper-error",
        )
        if not self.valid_notify_target(target):
            return
        message = (
            f"Der Taster {entity_id} für "
            f"{medication.get('name', medication_id)} wurde nicht gebucht: {error}"
        )
        next_slot = next_regular_slot_for_medication(
            profile, medication_id, dt_util.now()
        )
        if next_slot:
            due = datetime.fromisoformat(next_slot["due_at"])
            message += (
                " Nächste reguläre Einnahme: "
                f"{SLOT_LABELS.get(next_slot['slot'], next_slot['slot'])} am "
                f"{due.strftime('%d.%m. um %H:%M Uhr')}."
            )
        elif medication_is_regular(medication):
            message += (
                " In diesem Tages-Zyklus ist keine weitere reguläre Einnahme offen."
            )
        await self._async_call_notify(
            target,
            {
                "title": f"Pill★Pal ⚠️ Taster nicht gebucht · {profile['name']}",
                "message": message,
                "data": data,
            },
            blocking=True,
        )

    async def _async_handle_action_error_notification(
        self, person_id: str, slot: str, command: str, error: str
    ) -> None:
        """Make a rejected Companion action visibly and non-alarmingly explicit."""

        profile = self.profile(person_id)
        target, data = self._feedback_endpoint_and_data(
            profile, slot, "action_error", suffix="action-error"
        )
        slot_data = profile.get("runtime", {}).get("slots", {}).get(slot, {})
        target = str(slot_data.get("notification_target") or target)
        data["tag"] = str(
            slot_data.get("notification_tag")
            or (
                f"{profile.get('settings', {}).get('notification_tag', 'Medikation')}-"
                f"{person_id}-{slot}-action-error"
            )
        )
        if not self.valid_notify_target(target):
            return
        data["importance"] = "high"
        labels = {"TAKE": "Bestätigen", "SNOOZE": "Zurückstellen", "SKIP": "Überspringen"}
        await self._async_call_notify(
            target,
            {
                "title": f"Pill★Pal ⚠️ Aktion abgelehnt · {profile['name']}",
                "message": (
                    f"{labels.get(command, command)} für {SLOT_LABELS.get(slot, slot)} "
                    f"wurde nicht ausgeführt: {error}"
                ),
                "data": data,
            },
            blocking=True,
        )

    async def async_import_r410(
        self, source_path: str, mapping: Mapping[str, str]
    ) -> dict[str, Any]:
        """Import medications from an explicitly reviewed R4.1 JSON export.

        The method never guesses profile ownership.  ``mapping`` maps old profile
        ids to current person ids and unmapped records are reported but untouched.
        Settings, runtime, events, statistics and logs are deliberately ignored.
        """

        path = Path(source_path)
        if not path.is_absolute():
            path = Path(self.hass.config.path(source_path))
        raw = await self.hass.async_add_executor_job(path.read_text, "utf-8")
        payload = json.loads(raw)
        old_profiles = payload.get("profiles") or payload.get("people") or {}
        imported: list[str] = []
        skipped: list[str] = []
        async with self._lock:
            for old_id, old_profile in old_profiles.items():
                new_id = mapping.get(str(old_id))
                if not new_id or new_id not in self.data.get("profiles", {}):
                    skipped.append(str(old_id))
                    continue
                profile = self.profile(new_id)
                raw_medications = old_profile.get("medications", {})
                medication_items = (
                    raw_medications.values()
                    if isinstance(raw_medications, Mapping)
                    else raw_medications
                    if isinstance(raw_medications, list)
                    else []
                )
                for raw_med in medication_items:
                    if not isinstance(raw_med, Mapping):
                        continue
                    try:
                        medication = save_medication(
                            self.data,
                            new_id,
                            _convert_r4_medication(raw_med),
                            actor="R4.1-Migration",
                            now=dt_util.now(),
                        )
                        append_log(
                            profile,
                            f"{profile['name']}: Bedarfskennzeichnung für "
                            f"{medication['name']} aus R4 explizit migriert "
                            f"({'freigegeben' if medication.get('as_needed_allowed') else 'nicht freigegeben'}).",
                            source="migration",
                            actor="R4.1-Migration",
                            now=dt_util.now(),
                        )
                    except PillPalError as err:
                        _LOGGER.warning("Skipped R4.1 medication: %s", err)
                imported.append(str(old_id))
                append_log(
                    profile,
                    f"{profile['name']}: R4.1-Medikamente kontrolliert importiert; "
                    "Einstellungen, Zyklen, Buchungen, Statistik und Log wurden "
                    "nicht übernommen.",
                    source="migration",
                    now=dt_util.now(),
                )
            self.data["migration"] = {
                "r410_imported_at": dt_util.utcnow().isoformat(),
                "source": str(path),
            }
            await self._async_save()
        for new_id in mapping.values():
            if new_id in self.data.get("profiles", {}):
                self._dispatch(new_id)
        return {"imported": imported, "skipped": skipped}

    async def async_shutdown(self) -> None:
        """Stop runtime listeners and flush storage."""

        self._running = False
        self._generation += 1
        while self._helper_unsubscribers:
            unsubscribe = self._helper_unsubscribers.pop()
            try:
                unsubscribe()
            except (RuntimeError, ValueError):
                pass
        while self._interface_unsubscribers:
            unsubscribe = self._interface_unsubscribers.pop()
            try:
                unsubscribe()
            except (RuntimeError, ValueError):
                pass
        while self._unsubscribers:
            unsubscribe = self._unsubscribers.pop()
            try:
                unsubscribe()
            except (RuntimeError, ValueError):
                pass
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._async_save()
