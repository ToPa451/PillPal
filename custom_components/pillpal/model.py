"""Pure, person-isolated data model for Pill★Pal.

This module deliberately has no Home Assistant imports.  It is the single source
of truth for mutations and can therefore be tested without a running instance.
Every public mutation requires an explicit ``person_id``; there is no active or
selected person in the backend.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Iterable, Mapping
from uuid import uuid4

try:
    from .const import (
        DATA_SCHEMA_VERSION,
        DEFAULT_SETTINGS,
        DIAGNOSTIC_RETENTION_HOURS,
        SLOT_LABELS,
        SLOTS,
    )
except ImportError:  # pragma: no cover - permits direct execution in simple tests
    DATA_SCHEMA_VERSION = 9
    DIAGNOSTIC_RETENTION_HOURS = 48
    SLOTS = ("morning", "noon", "evening", "night")
    SLOT_LABELS = {
        "morning": "Morgens",
        "noon": "Mittags",
        "evening": "Abends",
        "night": "Zur Nacht",
    }
    DEFAULT_SETTINGS = {
        "notify_target": "",
        "confirm_helper": "",
        "awake_helper": "",
        "next_alarm_entity": "",
        "holiday_calendar": "",
        "intake_calendar": "",
        "early_minutes": 30,
        "morning_delay_minutes": 15,
        "fallback_wake_time": "08:00",
        "snooze_minutes": 15,
        "repeat_minutes": 5,
        "order_warning_days": 10,
        "practice_lead_days": 5,
        "low_stock_window_days": 7,
        "expiry_warning_days": 14,
        "alarm_window_from": "04:00",
        "alarm_window_to": "11:00",
        "lunch_window_from": "12:00",
        "lunch_window_to": "14:00",
        "bedtime_offset_hours": 8.25,
        "evening_before_bedtime_hours": 2,
        "notification_title": "Einnahme fällig",
        "notification_intro": "Folgende Medikamente müssen genommen werden.",
        "action_take": "Sammeleinnahme",
        "action_snooze": "Snooze",
        "action_skip": "Überspringen",
        "notification_sticky": True,
        "notification_persistent": False,
        "notification_alert_once": False,
        "notification_critical": True,
        "notification_channel": "alarm_stream",
        "notification_group": "Medikation",
        "notification_tag": "Medikation",
        "notification_icon": "mdi:medication-outline",
        "order_notification_title": "Nachbestellung",
        "order_notification_tag": "Medikamentenbestellung",
        "order_notification_icon": "mdi:medication-outline",
        "expiry_notification_title": "Haltbarkeit prüfen",
        "expiry_notification_tag": "MedikamentenMHD",
        "expiry_notification_icon": "mdi:medication-outline",
        "notification_color": "",
        "notification_vibration_pattern": "",
        "notification_led_color": "",
        "notification_sound": "alarm.caf",
        "notification_importance": "high",
        "notification_priority": "high",
        "notification_visibility": "private",
        "notification_ttl": 0,
        "notification_timeout": 0,
        "ios_interruption_level": "critical",
        "ios_volume": 1,
        "ios_badge": 0,
        "ios_presentation_options": "alert, badge, sound",
        "currency": "€",
        "show_archived": False,
        "statistics_show_archived": False,
        "times": {
            "morning": "08:00",
            "noon": "13:00",
            "evening": "20:00",
            "night": "23:00",
        },
    }


class PillPalError(ValueError):
    """Base error raised for rejected Pill★Pal operations."""

    code = "invalid"


class PersistenceError(PillPalError):
    """Raised when a mutation cannot be committed to durable storage."""

    code = "persistence_failed"


class ProfileNotFoundError(PillPalError):
    """Raised when a person profile is missing."""

    code = "profile_not_found"


class MedicationNotFoundError(PillPalError):
    """Raised when a medication is missing."""

    code = "medication_not_found"


class DuplicateHelperError(PillPalError):
    """Raised when one physical helper is assigned to multiple people."""

    code = "helper_already_assigned"


class InvalidStepError(PillPalError):
    """Raised when a quantity violates the medication step size."""

    code = "invalid_step"


class NoDueIntakeError(PillPalError):
    """Raised when a requested slot cannot be confirmed."""

    code = "no_due_intake"


class InactiveCycleError(NoDueIntakeError):
    """Raised when an intake action targets a profile without an active cycle."""

    code = "cycle_not_active"


TERMINAL_SLOT_STATUSES = frozenset({"taken", "skipped", "missed"})
OPEN_SLOT_STATUSES = frozenset({"planned", "snoozed"})
MAX_DOSE_CONFIRMATION_TTL = timedelta(minutes=2)


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


def iso_now(now: datetime | None = None) -> str:
    """Return a stable ISO timestamp."""

    return (now or utc_now()).astimezone(timezone.utc).isoformat()


def decimal_value(value: Any, default: str = "0") -> Decimal:
    """Convert UI/storage values to Decimal without binary rounding."""

    if value is None or value == "":
        return Decimal(default)
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as err:
        raise PillPalError(f"Ungültiger Zahlenwert: {value}") from err
    if not parsed.is_finite():
        raise PillPalError(f"Ungültiger Zahlenwert: {value}")
    return parsed


def decimal_json(value: Decimal | Any) -> int | float:
    """Convert Decimal to a JSON-safe number."""

    parsed = decimal_value(value)
    if parsed == parsed.to_integral_value():
        return int(parsed)
    return float(parsed.normalize())


def slugify(value: str) -> str:
    """Create a stable medication id fragment."""

    value = value.strip().lower()
    value = value.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    value = value.replace("ß", "ss")
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    return value or "medikament"


def deep_merge(base: Mapping[str, Any], update: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge JSON-like mappings."""

    result = deepcopy(dict(base))
    for key, value in update.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


_INTEGER_SETTING_RANGES = {
    "early_minutes": (0, 240),
    "morning_delay_minutes": (0, 120),
    "snooze_minutes": (1, 240),
    "repeat_minutes": (1, 1440),
    "order_warning_days": (0, 3650),
    "practice_lead_days": (0, 3650),
    "low_stock_window_days": (0, 3650),
    "expiry_warning_days": (0, 3650),
    "notification_ttl": (0, 86400),
    "notification_timeout": (0, 86400),
    "ios_badge": (0, 9999),
}
_FLOAT_SETTING_RANGES = {
    "bedtime_offset_hours": (0, 24),
    "evening_before_bedtime_hours": (0, 24),
    "ios_volume": (0, 1),
}
_TIME_SETTING_KEYS = {
    "fallback_wake_time",
    "alarm_window_from",
    "alarm_window_to",
    "lunch_window_from",
    "lunch_window_to",
}
_ENTITY_SETTING_DOMAINS = {
    "notify_target": "notify.",
    "confirm_helper": "input_button.",
    "awake_helper": "input_boolean.",
    "next_alarm_entity": "sensor.",
    "holiday_calendar": "calendar.",
    "intake_calendar": "calendar.",
}
_ENUM_SETTINGS = {
    "notification_importance": {"min", "low", "default", "high", "max"},
    "notification_priority": {"normal", "high"},
    "notification_visibility": {"public", "private", "secret"},
    "ios_interruption_level": {"passive", "active", "time-sensitive", "critical"},
}
_R5_NOTIFICATION_DEFAULT_MIGRATION = {
    "notification_critical": (False, True),
    "notification_group": ("pillpal", "Medikation"),
    "notification_tag": ("pillpal", "Medikation"),
    "notification_icon": ("mdi:pill", "mdi:medication-outline"),
    "notification_color": ("#8057ad", ""),
    "notification_sound": ("default", "alarm.caf"),
    "ios_interruption_level": ("time-sensitive", "critical"),
}
_LEGACY_SETTINGS = {"due_helper", "last_intake_helper", "dashboard_path"}


def _validated_time_setting(key: str, value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value):
        raise PillPalError(f"{key} muss als Uhrzeit HH:MM angegeben werden.")
    try:
        time.fromisoformat(value)
    except ValueError as err:
        raise PillPalError(f"{key} enthält keine gültige Uhrzeit.") from err
    return value


def _validated_setting(key: str, value: Any) -> Any:
    default = DEFAULT_SETTINGS[key]
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise PillPalError(f"{key} muss wahr oder falsch sein.")
        return value
    if key in _INTEGER_SETTING_RANGES:
        if isinstance(value, bool):
            raise PillPalError(f"{key} muss eine ganze Zahl sein.")
        number = decimal_value(value)
        if not number.is_finite():
            raise PillPalError(f"{key} muss eine endliche Zahl sein.")
        if number != number.to_integral_value():
            raise PillPalError(f"{key} muss eine ganze Zahl sein.")
        minimum, maximum = _INTEGER_SETTING_RANGES[key]
        parsed = int(number)
        if not minimum <= parsed <= maximum:
            raise PillPalError(f"{key} muss zwischen {minimum} und {maximum} liegen.")
        return parsed
    if key in _FLOAT_SETTING_RANGES:
        if isinstance(value, bool):
            raise PillPalError(f"{key} muss eine Zahl sein.")
        number = decimal_value(value)
        if not number.is_finite():
            raise PillPalError(f"{key} muss eine endliche Zahl sein.")
        parsed = float(number)
        minimum, maximum = _FLOAT_SETTING_RANGES[key]
        if not minimum <= parsed <= maximum:
            raise PillPalError(f"{key} muss zwischen {minimum} und {maximum} liegen.")
        return parsed
    if key in _TIME_SETTING_KEYS:
        return _validated_time_setting(key, value)
    if not isinstance(value, str):
        raise PillPalError(f"{key} muss Text sein.")
    if len(value) > 1000:
        raise PillPalError(f"{key} ist zu lang.")
    if key in _ENTITY_SETTING_DOMAINS and value:
        domain = _ENTITY_SETTING_DOMAINS[key]
        if not re.fullmatch(rf"{re.escape(domain)}[a-z0-9_]+", value):
            raise PillPalError(
                f"{key} muss eine Entität aus {domain}… sein."
            )
    if key in _ENUM_SETTINGS and value not in _ENUM_SETTINGS[key]:
        raise PillPalError(f"{key} enthält einen nicht unterstützten Wert.")
    if key in {
        "notification_icon",
        "order_notification_icon",
        "expiry_notification_icon",
    } and value and not re.fullmatch(
        r"mdi:[a-z0-9-]+", value
    ):
        raise PillPalError(f"{key} muss ein MDI-Symbol sein.")
    if key in {
        "order_notification_title",
        "order_notification_tag",
        "order_notification_icon",
        "expiry_notification_title",
        "expiry_notification_tag",
        "expiry_notification_icon",
    } and not value.strip():
        raise PillPalError(f"{key} darf nicht leer sein.")
    if key == "notification_color" and value and not re.fullmatch(
        r"#[0-9a-fA-F]{6}", value
    ):
        raise PillPalError("notification_color muss als #RRGGBB angegeben werden.")
    if key == "ios_presentation_options":
        values = {item.strip() for item in value.split(",") if item.strip()}
        if not values <= {"alert", "badge", "sound"}:
            raise PillPalError("ios_presentation_options enthält einen unbekannten Wert.")
    return value


def normalize_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    """Strictly validate the complete settings object against known fields."""

    if not isinstance(settings, Mapping):
        raise PillPalError("Einstellungen müssen ein Objekt sein.")
    unknown = set(settings) - set(DEFAULT_SETTINGS)
    if unknown:
        raise PillPalError(
            "Unbekannte Einstellung: " + ", ".join(sorted(str(key) for key in unknown))
        )
    result = deepcopy(DEFAULT_SETTINGS)
    for key, value in settings.items():
        if key == "times":
            if not isinstance(value, Mapping):
                raise PillPalError("times muss ein Objekt sein.")
            unknown_slots = set(value) - set(SLOTS)
            if unknown_slots:
                raise PillPalError(
                    "Unbekannte Einnahmezeit: "
                    + ", ".join(sorted(str(slot) for slot in unknown_slots))
                )
            for slot, slot_value in value.items():
                result["times"][slot] = _validated_time_setting(
                    f"times.{slot}", slot_value
                )
            continue
        result[key] = _validated_setting(key, value)
    return result


def repair_settings(settings: Any) -> tuple[dict[str, Any], list[str]]:
    """Repair persisted settings field-by-field and report every discarded value."""

    if not isinstance(settings, Mapping):
        return deepcopy(DEFAULT_SETTINGS), ["settings ist kein Objekt"]
    candidate: dict[str, Any] = {}
    reasons: list[str] = []
    for key, value in settings.items():
        if key in _LEGACY_SETTINGS:
            continue
        if key not in DEFAULT_SETTINGS:
            reasons.append(f"unbekannte Einstellung {key}")
            continue
        if key == "times":
            if not isinstance(value, Mapping):
                reasons.append("times ist kein Objekt")
                continue
            candidate["times"] = {}
            for slot, slot_value in value.items():
                if slot not in SLOTS:
                    reasons.append(f"unbekannte Einnahmezeit {slot}")
                    continue
                try:
                    candidate["times"][slot] = _validated_time_setting(
                        f"times.{slot}", slot_value
                    )
                except PillPalError as err:
                    reasons.append(str(err))
            continue
        try:
            candidate[key] = _validated_setting(key, value)
        except PillPalError as err:
            reasons.append(str(err))
    return normalize_settings(candidate), reasons


def new_store() -> dict[str, Any]:
    """Return an empty integration store."""

    return {
        "schema": DATA_SCHEMA_VERSION,
        "profiles": {},
        "migration": {
            "r410_imported_at": None,
            "source": None,
            "schema_migrated_at": None,
        },
    }


def _new_runtime() -> dict[str, Any]:
    """Return the lifecycle state for one person profile."""

    return {
        "cycle_id": None,
        "cycle_state": "inactive",
        "cycle_date": None,
        "cycle_started_at": None,
        "cycle_started_by": None,
        "cycle_ended_at": None,
        "cycle_completed": False,
        "cycle_completed_at": None,
        "last_cycle_id": None,
        "last_cycle_date": None,
        "last_cycle_completed": False,
        "fallback_wake_last_started_date": None,
        "schedule_source": None,
        "effective_due_at": {},
        "slots": {},
        "last_activity": "Pill★Pal wurde eingerichtet.",
        "last_action_result": {
            "status": "idle",
            "action": None,
            "message": "Noch keine Aktion ausgeführt.",
            "timestamp": None,
            "actor": None,
            "result": None,
            "error_code": None,
        },
        "intake_calendar_deliveries": {},
        "last_reminder": {},
        "pending_notification_feedback": {},
        "pending_notification_cleanup": {},
        "removed_notification_cleanup_queued_at": None,
        "inventory_notification_fingerprints": {},
        "inventory_notification_deliveries": {},
        "inventory_notification_reservations": {},
        "holiday_calendar_forecast": {
            "entity_id": "",
            "range_start": None,
            "range_end": None,
            "fetched_on": None,
            "closed_dates": [],
            "event_count": 0,
            "last_error": None,
        },
        "pending_max_dose_confirmations": {},
        "diagnostic_errors_acknowledged_at": None,
    }


def cycle_is_active(profile: Mapping[str, Any]) -> bool:
    """Return whether the profile currently owns an active fachlicher cycle."""

    runtime = profile.get("runtime", {})
    return runtime.get("cycle_state") == "active" and bool(runtime.get("cycle_id"))


def _migrate_runtime(profile: dict[str, Any]) -> dict[str, Any]:
    """Normalize beta runtime fields without inventing a new cycle."""

    existing = profile.get("runtime")
    runtime = deep_merge(_new_runtime(), existing if isinstance(existing, Mapping) else {})
    old_slots = runtime.get("slots")
    if not isinstance(old_slots, Mapping):
        old_slots = {}
    if runtime.get("cycle_state") not in {"inactive", "active", "ended"}:
        runtime["cycle_state"] = "inactive"
    if runtime.get("cycle_state") == "inactive" and runtime.get("cycle_started_at") and old_slots:
        # R5.0.0 had no explicit lifecycle state. Preserve its in-flight data once;
        # the manager subsequently reconciles it with the configured awake helper.
        runtime["cycle_state"] = "active"
    if runtime.get("cycle_state") == "active" and not runtime.get("cycle_id"):
        runtime["cycle_id"] = f"migrated_{uuid4().hex}"
        runtime["cycle_started_by"] = runtime.get("cycle_started_by") or "schema_migration"
    cycle_id = runtime.get("cycle_id")
    normalized_slots: dict[str, Any] = {}
    for slot, raw in old_slots.items():
        if slot not in SLOTS or not isinstance(raw, Mapping):
            continue
        item = deepcopy(dict(raw))
        item.setdefault("slot", slot)
        item.setdefault("cycle_id", cycle_id)
        item.setdefault("slot_id", f"{cycle_id}:{slot}" if cycle_id else None)
        item.setdefault("status", "planned")
        item.setdefault("confirmed_at", None)
        item.setdefault("snoozed_until", None)
        item.setdefault("next_reminder_at", None)
        item.setdefault("last_notification_at", None)
        item.setdefault("notification_state", "idle")
        item.setdefault("notification_reservation_id", None)
        item.setdefault("notification_reserved_at", None)
        item.setdefault("notification_sent_at", item.get("last_notification_at"))
        item.setdefault("notification_error", None)
        item.setdefault("notification_target", None)
        item.setdefault("notification_tag", None)
        item.setdefault("action_token", uuid4().hex)
        item.setdefault("notification_action_target", None)
        item.setdefault("action_consumed_at", None)
        if item.get("status") in TERMINAL_SLOT_STATUSES:
            item["snoozed_until"] = None
            item["next_reminder_at"] = None
            item["notification_reservation_id"] = None
        normalized_slots[slot] = item
    runtime["slots"] = normalized_slots
    pending = runtime.get("pending_max_dose_confirmations")
    runtime["pending_max_dose_confirmations"] = (
        deepcopy(dict(pending)) if isinstance(pending, Mapping) else {}
    )
    profile["runtime"] = runtime
    return runtime


def _legacy_as_needed_flag(medication: Mapping[str, Any]) -> tuple[bool, str]:
    """Infer R4's historical PRN behavior only when no explicit R5 flag exists."""

    if "as_needed_allowed" in medication:
        return bool(medication.get("as_needed_allowed")), "explicit"
    legacy = medication.get("as_needed")
    if isinstance(legacy, Mapping):
        enabled = bool(
            legacy.get("manual_enabled")
            or legacy.get("enabled")
            or medication.get("is_as_needed")
        )
        return enabled, "r4_as_needed"
    if "is_as_needed" in medication:
        return bool(medication.get("is_as_needed")), "r4_is_as_needed"
    return False, "missing_flag_defaults_false"


def _migrate_profile_medications(profile: dict[str, Any], now: datetime | None) -> None:
    """Add the explicit PRN flag once without deriving it from four zero doses."""

    for medication in profile.get("medications", {}).values():
        if not isinstance(medication, dict) or "as_needed_allowed" in medication:
            continue
        allowed, source = _legacy_as_needed_flag(medication)
        medication["as_needed_allowed"] = allowed
        medication["pure_as_needed"] = allowed and not medication_is_regular(medication)
        medication["as_needed_migrated_at"] = iso_now(now)
        medication["as_needed_migration_source"] = source
        append_log(
            profile,
            f"{profile.get('name', 'Person')}: Bedarfskennzeichnung für "
            f"{medication.get('name', medication.get('id', 'Medikament'))} einmalig "
            f"migriert ({'freigegeben' if allowed else 'nicht freigegeben'}).",
            source="migration",
            now=now,
        )


def new_profile(
    person_id: str,
    name: str,
    person_entity_id: str,
    user_id: str | None,
    admin_assistance: bool,
    now: datetime | None = None,
    create_example: bool = True,
) -> dict[str, Any]:
    """Create the initial profile for one HA Person."""

    timestamp = iso_now(now)
    profile = {
        "profile_id": uuid4().hex,
        "person_id": person_id,
        "name": name,
        "person_entity_id": person_entity_id,
        "user_id": user_id or "",
        "admin_assistance": bool(admin_assistance or not user_id),
        "archived": False,
        "removed_at": None,
        "created_at": timestamp,
        "updated_at": timestamp,
        "notification_defaults_migrated_at": timestamp,
        "settings": deepcopy(DEFAULT_SETTINGS),
        "practice_closures": [],
        "medications": {},
        "runtime": _new_runtime(),
        "events": [],
        "history": {"daily": {}},
        "log": [],
        "warning_fingerprints": {},
    }
    if create_example:
        profile["medications"]["beispiel_1"] = normalize_medication(
            {
                "id": "beispiel_1",
                "name": "Beispiel 1",
                "description": "Ein Beispielmedikament zum Einrichten",
                "unit_singular": "Tablette",
                "unit_plural": "Tabletten",
                "step": 0.5,
                "pack_size": 20,
                "stock": 20,
                "doses": {slot: 0 for slot in SLOTS},
                "as_needed_allowed": False,
                "single_max": 0,
                "daily_max": 0,
                "expiry_enabled": False,
            }
        )
    return profile


def ensure_profile(
    store: dict[str, Any],
    *,
    person_id: str,
    name: str,
    person_entity_id: str,
    user_id: str | None,
    admin_assistance: bool,
    person_exists: bool = True,
    now: datetime | None = None,
    create_example: bool = True,
) -> dict[str, Any]:
    """Create/update one profile without touching any other profile."""

    profiles = store.setdefault("profiles", {})
    profile = profiles.get(person_id)
    if profile is None:
        profile = new_profile(
            person_id,
            name,
            person_entity_id,
            user_id,
            admin_assistance,
            now,
            create_example,
        )
        profiles[person_id] = profile
        append_log(profile, f"{name}: Personenprofil angelegt.", now=now)
        return profile

    was_archived = bool(profile.get("archived"))
    profile["name"] = name
    profile.setdefault("profile_id", uuid4().hex)
    profile["person_entity_id"] = person_entity_id
    profile["user_id"] = user_id or ""
    profile["admin_assistance"] = bool(admin_assistance or not user_id)
    profile["updated_at"] = iso_now(now)
    if person_exists:
        profile["archived"] = False
        profile["removed_at"] = None
    stored_settings = profile.get("settings", {})
    if not isinstance(stored_settings, Mapping):
        raise PillPalError("Gespeicherte Einstellungen müssen ein Objekt sein.")
    stored_settings = deepcopy(dict(stored_settings))
    # Native panels and person-specific integration entities replace the three
    # legacy dashboard/output settings.  Accept and discard them during an
    # upgrade so an existing installation is migrated without quarantine.
    for legacy_key in _LEGACY_SETTINGS:
        stored_settings.pop(legacy_key, None)
    # Beta 1–4 stored this integration-owned default.  Restore the Android
    # alarm stream used by R4 unless the user selected a different channel.
    if stored_settings.get("notification_channel") in {"", "PillPal"}:
        stored_settings["notification_channel"] = "alarm_stream"
    migrated_defaults: list[str] = []
    if not profile.get("notification_defaults_migrated_at"):
        untouched_r5_defaults = all(
            stored_settings.get(key) == old_default
            for key, (old_default, _) in _R5_NOTIFICATION_DEFAULT_MIGRATION.items()
        )
        if untouched_r5_defaults:
            for key, (_, restored_default) in _R5_NOTIFICATION_DEFAULT_MIGRATION.items():
                stored_settings[key] = restored_default
                migrated_defaults.append(key)
        profile["notification_defaults_migrated_at"] = iso_now(now)
    profile["settings"] = normalize_settings(stored_settings)
    profile.setdefault("practice_closures", [])
    profile.setdefault("medications", {})
    profile.setdefault("events", [])
    profile.setdefault("history", {"daily": {}})
    profile.setdefault("log", [])
    profile.setdefault("warning_fingerprints", {})
    profile.setdefault("runtime", {})
    _migrate_profile_medications(profile, now)
    runtime = _migrate_runtime(profile)
    if person_exists and was_archived:
        runtime["removed_notification_cleanup_queued_at"] = None
    _migrate_terminal_event_history(profile)
    _sync_cycle_history(profile)
    if migrated_defaults:
        append_log(
            profile,
            f"{profile['name']}: Unbeabsichtigt abweichende R5-Benachrichtigungsstandards "
            "wurden einmalig auf die R4.0.17-Alarmwerte zurückgeführt.",
            source="migration",
            now=now,
        )
    return profile


def archive_removed_profile(
    profile: dict[str, Any], now: datetime | None = None
) -> None:
    """Archive a removed HA person while retaining their complete history."""

    newly_archived = not profile.get("archived")
    if newly_archived:
        profile["archived"] = True
        profile["removed_at"] = iso_now(now)
        for medication in profile.get("medications", {}).values():
            medication["archived"] = True
            medication["archived_at"] = iso_now(now)
    runtime = profile.setdefault("runtime", {})
    runtime["slots"] = {}
    runtime["last_reminder"] = {}
    runtime["pending_notification_feedback"] = {}
    runtime["inventory_notification_fingerprints"] = {}
    runtime["inventory_notification_deliveries"] = {}
    runtime["inventory_notification_reservations"] = {}
    if newly_archived:
        append_log(
            profile,
            f"{profile.get('name', 'Person')}: HA-Person entfernt; Profil und Medikamente archiviert.",
            level="warning",
            now=now,
        )


def get_profile(store: Mapping[str, Any], person_id: str) -> dict[str, Any]:
    """Return one profile or raise."""

    profile = store.get("profiles", {}).get(person_id)
    if profile is None:
        raise ProfileNotFoundError(f"Unbekannte Person: {person_id}")
    return profile


def append_log(
    profile: dict[str, Any],
    message: str,
    *,
    level: str = "info",
    source: str = "system",
    actor: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append a person-scoped diagnostic entry."""

    item = {
        "id": uuid4().hex,
        "timestamp": iso_now(now),
        "level": level,
        "message": message,
        "source": source,
        "actor": actor or "",
        "person_id": profile["person_id"],
    }
    log = profile.setdefault("log", [])
    log.append(item)
    cutoff = _local_now(now) - timedelta(hours=DIAGNOSTIC_RETENTION_HOURS)
    retained: list[dict[str, Any]] = []
    for entry in log:
        try:
            timestamp = _local_now(
                datetime.fromisoformat(str(entry.get("timestamp")))
            )
        except (AttributeError, TypeError, ValueError):
            continue
        if timestamp >= cutoff:
            retained.append(entry)
    profile["log"] = retained
    profile.setdefault("runtime", {})["last_activity"] = message
    profile["updated_at"] = item["timestamp"]
    return item


def acknowledge_errors(
    store: dict[str, Any],
    person_id: str,
    *,
    actor: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Acknowledge all current person-scoped diagnostic errors."""

    profile = get_profile(store, person_id)
    timestamp = iso_now(now)
    profile.setdefault("runtime", {})["diagnostic_errors_acknowledged_at"] = timestamp
    append_log(
        profile,
        f"{profile['name']}: Fehlerhinweis als gelesen markiert.",
        source="dashboard",
        actor=actor,
        now=now,
    )
    return {"acknowledged_at": timestamp}


def add_event(
    profile: dict[str, Any],
    event_type: str,
    *,
    now: datetime | None = None,
    **data: Any,
) -> dict[str, Any]:
    """Add a person-scoped statistics event."""

    item = {
        "id": uuid4().hex,
        "timestamp": iso_now(now),
        "date": (now or utc_now()).date().isoformat(),
        "type": event_type,
        "person_id": profile["person_id"],
        **deepcopy(data),
    }
    profile.setdefault("events", []).append(item)
    return item


def normalize_doses(value: Mapping[str, Any] | None) -> dict[str, int | float]:
    """Normalize four regular slot doses."""

    source = value or {}
    return {slot: decimal_json(max(Decimal("0"), decimal_value(source.get(slot, 0)))) for slot in SLOTS}


def _amount_matches_step(
    amount: Decimal, step: Decimal, *, allow_zero: bool = True
) -> bool:
    """Return whether a nonnegative amount is aligned to the medication step."""

    if amount < 0 or step <= 0 or (not allow_zero and amount == 0):
        return False
    return amount == 0 and allow_zero or amount % step == 0


def _validate_expiry_date(
    value: Any, now: datetime | None = None
) -> str:
    """Validate one MHD against the typo-prevention window around this year."""

    try:
        parsed = date.fromisoformat(str(value))
    except (TypeError, ValueError) as err:
        raise PillPalError("Das MHD muss ein gültiges Datum sein.") from err
    current_year = (now or utc_now()).year
    minimum_year = current_year - 1
    maximum_year = current_year + 5
    if not minimum_year <= parsed.year <= maximum_year:
        raise PillPalError(
            "Das MHD muss zwischen dem Vorjahr und fünf Jahren nach dem "
            f"aktuellen Jahr liegen ({minimum_year}–{maximum_year})."
        )
    return parsed.isoformat()


def medication_is_regular(medication: Mapping[str, Any]) -> bool:
    """Return whether any regular slot dose is positive."""

    return any(decimal_value(medication.get("doses", {}).get(slot, 0)) > 0 for slot in SLOTS)


def normalize_medication(
    data: Mapping[str, Any],
    existing: Mapping[str, Any] | None = None,
    *,
    now: datetime | None = None,
    enforce_business_rules: bool = True,
) -> dict[str, Any]:
    """Validate and normalize one medication payload."""

    merged = deep_merge(existing or {}, data)
    name = str(merged.get("name", "")).strip()
    if not name:
        raise PillPalError("Der Medikamentenname darf nicht leer sein.")

    step = decimal_value(merged.get("step", "1"), "1")
    if step <= 0:
        raise PillPalError("Die kleinste Buchungsmenge muss größer als 0 sein.")
    stock = decimal_value(merged.get("stock", 0))
    pack_size = decimal_value(merged.get("pack_size", 0))
    if stock < 0 or pack_size < 0:
        raise PillPalError("Bestand und Packungsgröße dürfen nicht negativ sein.")

    medication_id = str(merged.get("id") or slugify(name))
    doses = normalize_doses(merged.get("doses"))
    as_needed_allowed, migration_source = _legacy_as_needed_flag(merged)
    regular = any(decimal_value(value) > 0 for value in doses.values())
    pure_as_needed = as_needed_allowed and not regular
    single_max = max(Decimal("0"), decimal_value(merged.get("single_max", 0)))
    daily_max = max(Decimal("0"), decimal_value(merged.get("daily_max", 0)))
    button_amount = decimal_value(merged.get("button_amount", step))
    if enforce_business_rules:
        aligned = [
            ("Bestand", stock, True),
            ("Packungsgröße", pack_size, True),
            ("Maximale Einzeldosis", single_max, True),
            ("Maximale Tagesdosis", daily_max, True),
            ("Menge je Tastendruck", button_amount, False),
            *[
                (f"Dosis {_slot_label(slot)}", decimal_value(doses[slot]), True)
                for slot in SLOTS
            ],
        ]
        invalid = [
            label
            for label, value, allow_zero in aligned
            if not _amount_matches_step(value, step, allow_zero=allow_zero)
        ]
        if invalid:
            raise InvalidStepError(
                f"{', '.join(invalid)} muss in Schritten von "
                f"{decimal_json(step)} angegeben werden."
            )

    result = {
        "id": medication_id,
        "name": name,
        "description": str(merged.get("description", "")).strip(),
        "unit_singular": str(merged.get("unit_singular", "Einheit")).strip() or "Einheit",
        "unit_plural": str(merged.get("unit_plural", "Einheiten")).strip() or "Einheiten",
        "step": decimal_json(step),
        "pack_size": decimal_json(pack_size),
        "cost": decimal_json(decimal_value(merged.get("cost", 0))),
        "stock": decimal_json(stock),
        "doses": doses,
        "as_needed_allowed": as_needed_allowed,
        "pure_as_needed": pure_as_needed,
        "single_max": decimal_json(single_max),
        "daily_max": decimal_json(daily_max),
        "button_helper": str(merged.get("button_helper", "")).strip(),
        "button_amount": decimal_json(button_amount),
        "expiry_enabled": bool(merged.get("expiry_enabled", False)),
        "expiry_date": str(merged.get("expiry_date", "")).strip(),
        "archived": bool(merged.get("archived", False)),
        "created_at": str(merged.get("created_at") or iso_now(now)),
        "updated_at": iso_now(now),
        "archived_at": merged.get("archived_at"),
        "as_needed_migrated_at": merged.get("as_needed_migrated_at"),
        "as_needed_migration_source": merged.get("as_needed_migration_source"),
    }
    if migration_source.startswith("r4_") and not result["as_needed_migrated_at"]:
        result["as_needed_migrated_at"] = iso_now(now)
        result["as_needed_migration_source"] = migration_source
    if result["expiry_date"]:
        if enforce_business_rules:
            result["expiry_date"] = _validate_expiry_date(result["expiry_date"], now)
        else:
            try:
                result["expiry_date"] = date.fromisoformat(
                    result["expiry_date"]
                ).isoformat()
            except ValueError as err:
                raise PillPalError("Das MHD muss ein gültiges Datum sein.") from err
    return result


def _stored_datetime(value: Any) -> str | None:
    """Return one timezone-aware ISO timestamp or ``None``."""

    if value is None or value == "":
        return None
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return value


def _stored_date(value: Any) -> str | None:
    """Return one ISO date or ``None``."""

    if value is None or value == "":
        return None
    if not isinstance(value, str):
        return None
    try:
        date.fromisoformat(value)
    except ValueError:
        return None
    return value


def _repair_runtime_payload(
    value: Any,
    medication_ids: set[str],
    *,
    path: str,
) -> tuple[dict[str, Any], list[str]]:
    """Repair runtime data without allowing malformed timestamps into schedulers."""

    reasons: list[str] = []
    if not isinstance(value, Mapping):
        return _new_runtime(), [f"{path} ist kein Objekt"]

    dynamic_keys = {
        "next_alarm_at",
        "holiday_calendar_active",
        "holiday_calendar_message",
        "inventory_notification_fingerprints",
        "inventory_notification_deliveries",
        "inventory_notification_reservations",
        "holiday_calendar_forecast",
    }
    allowed_keys = set(_new_runtime()) | dynamic_keys
    unknown = set(value) - allowed_keys
    for key in sorted(str(item) for item in unknown):
        reasons.append(f"{path}.{key} ist ein unbekanntes Laufzeitfeld")

    runtime = _new_runtime()
    for key in allowed_keys:
        if key in value:
            runtime[key] = deepcopy(value[key])

    state = runtime.get("cycle_state")
    if not isinstance(state, str) or state not in {"inactive", "active", "ended"}:
        reasons.append(f"{path}.cycle_state ist ungültig")
        runtime["cycle_state"] = "inactive"

    timestamp_keys = {
        "cycle_started_at",
        "cycle_ended_at",
        "cycle_completed_at",
        "diagnostic_errors_acknowledged_at",
        "next_alarm_at",
        "removed_notification_cleanup_queued_at",
    }
    for key in timestamp_keys:
        original = runtime.get(key)
        parsed = _stored_datetime(original)
        if original is not None and original != "" and parsed is None:
            reasons.append(f"{path}.{key} enthält keinen gültigen Zeitstempel")
        runtime[key] = parsed

    date_keys = {
        "cycle_date",
        "last_cycle_date",
        "fallback_wake_last_started_date",
    }
    for key in date_keys:
        original = runtime.get(key)
        parsed = _stored_date(original)
        if original is not None and original != "" and parsed is None:
            reasons.append(f"{path}.{key} enthält kein gültiges Datum")
        runtime[key] = parsed

    for key in {"cycle_completed", "last_cycle_completed", "holiday_calendar_active"}:
        if key in runtime and not isinstance(runtime.get(key), bool):
            reasons.append(f"{path}.{key} ist kein Wahrheitswert")
            runtime[key] = False

    for key in {
        "cycle_id",
        "cycle_started_by",
        "last_cycle_id",
        "schedule_source",
        "last_activity",
        "holiday_calendar_message",
    }:
        if runtime.get(key) is not None and not isinstance(runtime.get(key), str):
            reasons.append(f"{path}.{key} ist kein Text")
            runtime[key] = None if key != "last_activity" else _new_runtime()[key]

    effective = runtime.get("effective_due_at")
    if not isinstance(effective, Mapping):
        reasons.append(f"{path}.effective_due_at ist kein Objekt")
        runtime["effective_due_at"] = {}
    elif effective:
        parsed_effective: dict[str, str] = {}
        for slot, due_at in effective.items():
            parsed = _stored_datetime(due_at)
            if slot not in SLOTS or parsed is None:
                reasons.append(f"{path}.effective_due_at.{slot} ist ungültig")
                continue
            parsed_effective[str(slot)] = parsed
        runtime["effective_due_at"] = (
            parsed_effective if set(parsed_effective) == set(SLOTS) else {}
        )

    if not isinstance(runtime.get("last_reminder"), Mapping):
        reasons.append(f"{path}.last_reminder ist kein Objekt")
        runtime["last_reminder"] = {}
    else:
        runtime["last_reminder"] = deepcopy(dict(runtime["last_reminder"]))

    pending_feedback = runtime.get("pending_notification_feedback", {})
    repaired_feedback: dict[str, dict[str, Any]] = {}
    if not isinstance(pending_feedback, Mapping):
        reasons.append(f"{path}.pending_notification_feedback ist kein Objekt")
    else:
        for feedback_id, raw_feedback in pending_feedback.items():
            if not isinstance(feedback_id, str) or not feedback_id or not isinstance(
                raw_feedback, Mapping
            ):
                reasons.append(
                    f"{path}.pending_notification_feedback.{feedback_id} ist ungültig"
                )
                continue
            target = raw_feedback.get("target")
            payload = raw_feedback.get("payload")
            created_at = _stored_datetime(raw_feedback.get("created_at"))
            updated_at = _stored_datetime(raw_feedback.get("updated_at"))
            next_retry_at = _stored_datetime(raw_feedback.get("next_retry_at"))
            attempts = raw_feedback.get("attempts")
            last_error = raw_feedback.get("last_error")
            if (
                not isinstance(target, str)
                or not target.startswith("notify.")
                or not isinstance(payload, Mapping)
                or created_at is None
                or updated_at is None
                or next_retry_at is None
                or not isinstance(attempts, int)
                or isinstance(attempts, bool)
                or attempts < 1
                or not isinstance(last_error, str)
            ):
                reasons.append(
                    f"{path}.pending_notification_feedback.{feedback_id} ist ungültig"
                )
                continue
            repaired_feedback[feedback_id] = {
                "target": target,
                "payload": deepcopy(dict(payload)),
                "created_at": created_at,
                "updated_at": updated_at,
                "next_retry_at": next_retry_at,
                "attempts": attempts,
                "last_error": last_error,
            }
    runtime["pending_notification_feedback"] = repaired_feedback

    pending_cleanup = runtime.get("pending_notification_cleanup", {})
    repaired_cleanup: dict[str, dict[str, Any]] = {}
    if not isinstance(pending_cleanup, Mapping):
        reasons.append(f"{path}.pending_notification_cleanup ist kein Objekt")
    else:
        for cleanup_id, raw_cleanup in pending_cleanup.items():
            if not isinstance(cleanup_id, str) or not cleanup_id or not isinstance(
                raw_cleanup, Mapping
            ):
                reasons.append(
                    f"{path}.pending_notification_cleanup.{cleanup_id} ist ungültig"
                )
                continue
            target = raw_cleanup.get("target")
            tag = raw_cleanup.get("tag")
            created_at = _stored_datetime(raw_cleanup.get("created_at"))
            updated_at = _stored_datetime(raw_cleanup.get("updated_at"))
            next_retry_at = _stored_datetime(raw_cleanup.get("next_retry_at"))
            attempts = raw_cleanup.get("attempts")
            last_error = raw_cleanup.get("last_error")
            if (
                not isinstance(target, str)
                or not target.startswith("notify.")
                or not isinstance(tag, str)
                or not tag
                or created_at is None
                or updated_at is None
                or next_retry_at is None
                or not isinstance(attempts, int)
                or isinstance(attempts, bool)
                or attempts < 0
                or not isinstance(last_error, str)
            ):
                reasons.append(
                    f"{path}.pending_notification_cleanup.{cleanup_id} ist ungültig"
                )
                continue
            repaired_cleanup[cleanup_id] = {
                "target": target,
                "tag": tag,
                "created_at": created_at,
                "updated_at": updated_at,
                "next_retry_at": next_retry_at,
                "attempts": attempts,
                "last_error": last_error,
            }
    runtime["pending_notification_cleanup"] = repaired_cleanup

    fingerprints = runtime.get("inventory_notification_fingerprints", {})
    if not isinstance(fingerprints, Mapping):
        reasons.append(f"{path}.inventory_notification_fingerprints ist kein Objekt")
        fingerprints = {}
    runtime["inventory_notification_fingerprints"] = {
        str(key): str(item) for key, item in fingerprints.items()
    }
    forecast = runtime.get("holiday_calendar_forecast", {})
    forecast_default = _new_runtime()["holiday_calendar_forecast"]
    if not isinstance(forecast, Mapping):
        reasons.append(f"{path}.holiday_calendar_forecast ist kein Objekt")
        forecast = {}
    repaired_forecast = deepcopy(forecast_default)
    entity_id = forecast.get("entity_id", "")
    if isinstance(entity_id, str):
        repaired_forecast["entity_id"] = entity_id
    else:
        reasons.append(f"{path}.holiday_calendar_forecast.entity_id ist kein Text")
    for key in {"range_start", "range_end", "fetched_on"}:
        original = forecast.get(key)
        parsed = _stored_date(original)
        if original not in (None, "") and parsed is None:
            reasons.append(
                f"{path}.holiday_calendar_forecast.{key} enthält kein gültiges Datum"
            )
        repaired_forecast[key] = parsed
    raw_closed_dates = forecast.get("closed_dates", [])
    if not isinstance(raw_closed_dates, list):
        reasons.append(f"{path}.holiday_calendar_forecast.closed_dates ist keine Liste")
        raw_closed_dates = []
    closed_dates: list[str] = []
    for index, raw_date in enumerate(raw_closed_dates):
        parsed = _stored_date(raw_date)
        if parsed is None:
            reasons.append(
                f"{path}.holiday_calendar_forecast.closed_dates.{index} ist ungültig"
            )
        elif parsed not in closed_dates:
            closed_dates.append(parsed)
    repaired_forecast["closed_dates"] = sorted(closed_dates)
    event_count = forecast.get("event_count", 0)
    if not isinstance(event_count, int) or isinstance(event_count, bool) or event_count < 0:
        reasons.append(f"{path}.holiday_calendar_forecast.event_count ist ungültig")
        event_count = 0
    repaired_forecast["event_count"] = event_count
    last_error = forecast.get("last_error")
    if last_error is not None and not isinstance(last_error, str):
        reasons.append(f"{path}.holiday_calendar_forecast.last_error ist kein Text")
        last_error = None
    repaired_forecast["last_error"] = last_error
    runtime["holiday_calendar_forecast"] = repaired_forecast
    action_result = runtime.get("last_action_result", {})
    default_action_result = _new_runtime()["last_action_result"]
    if not isinstance(action_result, Mapping):
        reasons.append(f"{path}.last_action_result ist kein Objekt")
        action_result = {}
    repaired_action_result = deepcopy(default_action_result)
    status = action_result.get("status", "idle")
    if status not in {"idle", "pending", "success", "error"}:
        reasons.append(f"{path}.last_action_result.status ist ungültig")
        status = "idle"
    repaired_action_result["status"] = status
    for key in {"action", "message", "actor", "error_code"}:
        value = action_result.get(key)
        if value is not None and not isinstance(value, str):
            reasons.append(f"{path}.last_action_result.{key} ist kein Text")
            value = None
        repaired_action_result[key] = value
    timestamp = _stored_datetime(action_result.get("timestamp"))
    if action_result.get("timestamp") not in (None, "") and timestamp is None:
        reasons.append(f"{path}.last_action_result.timestamp ist ungültig")
    repaired_action_result["timestamp"] = timestamp
    repaired_action_result["result"] = deepcopy(action_result.get("result"))
    runtime["last_action_result"] = repaired_action_result

    calendar_deliveries = runtime.get("intake_calendar_deliveries", {})
    if not isinstance(calendar_deliveries, Mapping):
        reasons.append(f"{path}.intake_calendar_deliveries ist kein Objekt")
        calendar_deliveries = {}
    repaired_calendar_deliveries: dict[str, dict[str, Any]] = {}
    for delivery_key, raw_delivery in calendar_deliveries.items():
        if not isinstance(delivery_key, str) or not isinstance(raw_delivery, Mapping):
            reasons.append(f"{path}.intake_calendar_deliveries enthält einen ungültigen Eintrag")
            continue
        delivery_status = raw_delivery.get("status")
        updated_at = _stored_datetime(raw_delivery.get("updated_at"))
        event_id = raw_delivery.get("event_id")
        calendar_entity = raw_delivery.get("calendar_entity")
        error = raw_delivery.get("error")
        if (
            delivery_status not in {"pending", "delivered", "error"}
            or updated_at is None
            or not isinstance(event_id, str)
            or not event_id
            or not isinstance(calendar_entity, str)
            or not calendar_entity.startswith("calendar.")
            or (error is not None and not isinstance(error, str))
        ):
            reasons.append(f"{path}.intake_calendar_deliveries.{delivery_key} ist ungültig")
            continue
        repaired_calendar_deliveries[delivery_key] = {
            "status": delivery_status,
            "event_id": event_id,
            "calendar_entity": calendar_entity,
            "updated_at": updated_at,
            "error": error,
        }
    runtime["intake_calendar_deliveries"] = repaired_calendar_deliveries
    for key in {
        "inventory_notification_deliveries",
        "inventory_notification_reservations",
    }:
        collection = runtime.get(key, {})
        if not isinstance(collection, Mapping):
            reasons.append(f"{path}.{key} ist kein Objekt")
            collection = {}
        repaired_collection: dict[str, dict[str, Any]] = {}
        for item_key, raw_item in collection.items():
            if item_key not in {"stock", "expiry"} or not isinstance(raw_item, Mapping):
                reasons.append(f"{path}.{key}.{item_key} ist ungültig")
                continue
            repaired_collection[str(item_key)] = deepcopy(dict(raw_item))
        runtime[key] = repaired_collection

    pending = runtime.get("pending_max_dose_confirmations")
    repaired_pending: dict[str, Any] = {}
    if not isinstance(pending, Mapping):
        reasons.append(f"{path}.pending_max_dose_confirmations ist kein Objekt")
    else:
        for token, request in pending.items():
            if (
                not isinstance(token, str)
                or not token
                or not isinstance(request, Mapping)
                or _stored_datetime(request.get("expires_at")) is None
            ):
                reasons.append(f"{path}.pending_max_dose_confirmations enthält einen ungültigen Eintrag")
                continue
            repaired_pending[token] = deepcopy(dict(request))
    runtime["pending_max_dose_confirmations"] = repaired_pending

    slots = runtime.get("slots")
    repaired_slots: dict[str, Any] = {}
    if not isinstance(slots, Mapping):
        reasons.append(f"{path}.slots ist kein Objekt")
        slots = {}
    valid_statuses = OPEN_SLOT_STATUSES | TERMINAL_SLOT_STATUSES | {"pending", "notified"}
    timestamp_fields = {
        "confirmed_at",
        "skipped_at",
        "missed_at",
        "snoozed_until",
        "next_reminder_at",
        "last_notification_at",
        "notification_reserved_at",
        "notification_sent_at",
        "action_consumed_at",
    }
    for slot, raw_slot in slots.items():
        slot_path = f"{path}.slots.{slot}"
        if slot not in SLOTS or not isinstance(raw_slot, Mapping):
            reasons.append(f"{slot_path} ist kein gültiger Slot")
            continue
        due_at = _stored_datetime(raw_slot.get("due_at"))
        if due_at is None:
            reasons.append(f"{slot_path}.due_at enthält keinen gültigen Zeitstempel")
            continue
        status = raw_slot.get("status", "planned")
        if not isinstance(status, str) or status not in valid_statuses:
            reasons.append(f"{slot_path}.status ist ungültig")
            status = "planned"
        if status in {"pending", "notified"}:
            status = "planned"

        raw_items = raw_slot.get("items")
        repaired_items: list[dict[str, Any]] = []
        if not isinstance(raw_items, list):
            reasons.append(f"{slot_path}.items ist keine Liste")
            raw_items = []
        for index, raw_item in enumerate(raw_items):
            if not isinstance(raw_item, Mapping):
                reasons.append(f"{slot_path}.items.{index} ist kein Objekt")
                continue
            medication_id = str(raw_item.get("medication_id", ""))
            try:
                quantity = decimal_value(raw_item.get("quantity", 0))
            except PillPalError:
                quantity = Decimal("0")
            if medication_id not in medication_ids or quantity <= 0:
                reasons.append(f"{slot_path}.items.{index} verweist auf ungültige Einnahmedaten")
                continue
            repaired_items.append(
                {
                    "medication_id": medication_id,
                    "name": str(raw_item.get("name", medication_id)),
                    "quantity": decimal_json(quantity),
                    "unit_singular": str(raw_item.get("unit_singular", "Einheit")),
                    "unit_plural": str(raw_item.get("unit_plural", "Einheiten")),
                }
            )
        if not repaired_items:
            reasons.append(f"{slot_path} enthält keine gültige Einnahme")
            continue

        item = _base_slot(
            str(runtime.get("cycle_id") or raw_slot.get("cycle_id") or "repaired"),
            str(slot),
            datetime.fromisoformat(due_at),
            repaired_items,
        )
        item["status"] = status
        for key in timestamp_fields:
            original = raw_slot.get(key)
            parsed = _stored_datetime(original)
            if original is not None and original != "" and parsed is None:
                reasons.append(f"{slot_path}.{key} enthält keinen gültigen Zeitstempel")
            item[key] = parsed
        for key in {
            "notification_state",
            "notification_reservation_id",
            "notification_error",
            "notification_target",
            "notification_tag",
            "notification_action_target",
            "source",
        }:
            original = raw_slot.get(key)
            item[key] = str(original) if original is not None and original != "" else None
        if item["notification_state"] not in {
            None,
            "idle",
            "reserved",
            "sent",
            "failed",
            "cleanup_pending",
        }:
            reasons.append(f"{slot_path}.notification_state ist ungültig")
            item["notification_state"] = "idle"
        action_token = raw_slot.get("action_token")
        item["action_token"] = action_token if isinstance(action_token, str) and action_token else uuid4().hex
        expected_slot_id = item["slot_id"]
        if raw_slot.get("slot_id") not in (None, "", expected_slot_id):
            reasons.append(f"{slot_path}.slot_id passt nicht zum Tages-Zyklus")
        item["slot_id"] = expected_slot_id
        repaired_slots[str(slot)] = item
    runtime["slots"] = repaired_slots

    if runtime["cycle_state"] == "active" and (
        not runtime.get("cycle_id") or not runtime.get("cycle_started_at")
    ):
        reasons.append(f"{path} enthält einen unvollständigen aktiven Tages-Zyklus")
        preserved = _new_runtime()
        for key in {
            "last_cycle_id",
            "last_cycle_date",
            "last_cycle_completed",
            "fallback_wake_last_started_date",
            "diagnostic_errors_acknowledged_at",
            "last_activity",
            "next_alarm_at",
            "holiday_calendar_active",
            "holiday_calendar_message",
            "inventory_notification_fingerprints",
            "inventory_notification_deliveries",
            "inventory_notification_reservations",
            "pending_notification_feedback",
            "pending_notification_cleanup",
            "holiday_calendar_forecast",
            "last_action_result",
            "intake_calendar_deliveries",
        }:
            if key in runtime:
                preserved[key] = deepcopy(runtime[key])
        runtime = preserved
        return runtime, reasons

    if runtime["cycle_state"] == "inactive":
        if runtime["slots"]:
            reasons.append(f"{path} enthält Slots ohne aktiven Tages-Zyklus")
        runtime["slots"] = {}
        runtime["cycle_id"] = None
        runtime["cycle_date"] = None
        runtime["cycle_started_at"] = None
        runtime["cycle_started_by"] = None
        runtime["cycle_ended_at"] = None
        runtime["cycle_completed"] = False
        runtime["cycle_completed_at"] = None
        return runtime, reasons

    if not runtime.get("cycle_date") and runtime.get("cycle_started_at"):
        runtime["cycle_date"] = datetime.fromisoformat(
            runtime["cycle_started_at"]
        ).date().isoformat()
        reasons.append(f"{path}.cycle_date wurde aus dem Zyklusstart rekonstruiert")

    if runtime["cycle_state"] == "ended":
        terminal_at = runtime.get("cycle_ended_at") or runtime.get("cycle_started_at")
        for slot, item in runtime["slots"].items():
            if item.get("status") in TERMINAL_SLOT_STATUSES:
                continue
            reasons.append(
                f"{path}.slots.{slot} war nach Zyklusende noch offen und wurde als verpasst repariert"
            )
            item["status"] = "missed"
            item["missed_at"] = terminal_at
            item["snoozed_until"] = None
            item["next_reminder_at"] = None
            item["notification_reservation_id"] = None
            item["notification_state"] = "cleanup_pending"
            item["action_consumed_at"] = terminal_at

    ordered_slots = [runtime["slots"][slot] for slot in SLOTS if slot in runtime["slots"]]
    should_be_complete = bool(ordered_slots) and all(
        item.get("status") in TERMINAL_SLOT_STATUSES for item in ordered_slots
    ) and ordered_slots[-1].get("status") == "taken"
    if bool(runtime.get("cycle_completed")) != should_be_complete:
        reasons.append(f"{path}.cycle_completed widerspricht den Slotzuständen")
    runtime["cycle_completed"] = should_be_complete
    if should_be_complete and not runtime.get("cycle_completed_at"):
        terminal_times = [
            parsed
            for item in ordered_slots
            for parsed in (
                _stored_datetime(item.get("confirmed_at")),
                _stored_datetime(item.get("action_consumed_at")),
            )
            if parsed is not None
        ]
        runtime["cycle_completed_at"] = (
            max(terminal_times, key=datetime.fromisoformat)
            if terminal_times
            else runtime.get("cycle_started_at")
        )
        reasons.append(f"{path}.cycle_completed_at wurde rekonstruiert")
    elif not should_be_complete and runtime.get("cycle_completed_at"):
        reasons.append(f"{path}.cycle_completed_at wurde ohne vollständigen Zyklus entfernt")
        runtime["cycle_completed_at"] = None
    return runtime, reasons


def validate_store_payload(
    raw: Any, now: datetime | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate persisted data and return a safe repair plus quarantine report."""

    current = _local_now(now)
    safe = new_store()
    reasons: list[str] = []
    repaired_profiles: list[str] = []
    dropped_profiles: list[str] = []
    source_schema: int | None = None
    schema_migrated = False
    unsupported_schema = False
    if not isinstance(raw, Mapping):
        reasons.append("Store-Wurzel ist kein Objekt")
        return safe, {
            "quarantine_required": True,
            "reasons": reasons,
            "repaired_profiles": repaired_profiles,
            "dropped_profiles": dropped_profiles,
            "detected_at": current.isoformat(),
            "source_schema": None,
            "target_schema": DATA_SCHEMA_VERSION,
            "schema_migrated": False,
            "unsupported_schema": False,
        }

    schema = raw.get("schema")
    if not isinstance(schema, int) or isinstance(schema, bool) or schema < 1:
        reasons.append("schema ist ungültig")
    elif schema > DATA_SCHEMA_VERSION:
        source_schema = schema
        unsupported_schema = True
        reasons.append(
            f"schema {schema} ist neuer als das unterstützte Schema {DATA_SCHEMA_VERSION}"
        )
    else:
        source_schema = schema
        schema_migrated = schema < DATA_SCHEMA_VERSION

    known_root_fields = {
        "schema",
        "profiles",
        "migration",
        "persistence",
        "storage_health",
        # Schema 1/2 temporarily stored this shared list at the root.  The
        # manager moves it into profiles after validation.
        "practice_closures",
    }
    for key in sorted(str(item) for item in set(raw) - known_root_fields):
        reasons.append(f"{key} ist ein unbekanntes Store-Feld")

    migration = raw.get("migration", {})
    if not isinstance(migration, Mapping):
        reasons.append("migration ist kein Objekt")
    else:
        for key in safe["migration"]:
            value = migration.get(key)
            if key == "source" and (value is None or isinstance(value, str)):
                safe["migration"][key] = value
            elif key != "source" and value in (None, ""):
                safe["migration"][key] = None
            elif key != "source" and _stored_datetime(value) is not None:
                safe["migration"][key] = _stored_datetime(value)
            else:
                reasons.append(f"migration.{key} ist ungültig")
    if schema_migrated:
        safe["migration"]["schema_migrated_at"] = current.isoformat()

    if "practice_closures" in raw:
        legacy_closures = raw.get("practice_closures")
        repaired_legacy_closures: list[dict[str, str]] = []
        if not isinstance(legacy_closures, list):
            reasons.append("practice_closures ist keine Liste")
        else:
            for index, closure in enumerate(legacy_closures):
                start = (
                    _stored_date(closure.get("start"))
                    if isinstance(closure, Mapping)
                    else None
                )
                end = (
                    _stored_date(closure.get("end") or closure.get("start"))
                    if isinstance(closure, Mapping)
                    else None
                )
                if start is None or end is None or end < start:
                    reasons.append(f"practice_closures.{index} ist ungültig")
                    continue
                repaired_legacy_closures.append({"start": start, "end": end})
        safe["practice_closures"] = repaired_legacy_closures

    persistence = raw.get("persistence", {})
    revision = persistence.get("revision", 0) if isinstance(persistence, Mapping) else 0
    if not isinstance(persistence, Mapping):
        reasons.append("persistence ist kein Objekt")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        reasons.append("persistence.revision ist ungültig")
        revision = 0
    safe["persistence"] = {"revision": revision}

    profiles = raw.get("profiles")
    if not isinstance(profiles, Mapping):
        reasons.append("profiles ist kein Objekt")
        profiles = {}

    for raw_key, raw_profile in profiles.items():
        person_id = str(raw_key).strip() if isinstance(raw_key, str) else ""
        if not person_id or len(person_id) > 255 or not isinstance(raw_profile, Mapping):
            label = person_id or "<ungültiger Schlüssel>"
            reasons.append(f"profiles.{label} ist kein gültiges Profil")
            dropped_profiles.append(label)
            continue
        profile_reasons: list[str] = []
        stored_person_id = raw_profile.get("person_id")
        if stored_person_id != person_id:
            profile_reasons.append(f"profiles.{person_id}.person_id stimmt nicht mit dem Schlüssel überein")

        name = str(raw_profile.get("name", "")).strip()
        if not name:
            name = person_id
            profile_reasons.append(f"profiles.{person_id}.name ist leer")
        entity_id = str(raw_profile.get("person_entity_id", "")).strip()
        user_id = str(raw_profile.get("user_id", "")).strip()
        admin_assistance = raw_profile.get("admin_assistance", not bool(user_id))
        if not isinstance(admin_assistance, bool):
            profile_reasons.append(f"profiles.{person_id}.admin_assistance ist kein Wahrheitswert")
            admin_assistance = not bool(user_id)
        profile = new_profile(
            person_id,
            name,
            entity_id,
            user_id,
            admin_assistance,
            current,
            create_example=False,
        )
        known_profile_fields = set(profile)
        for key in sorted(str(item) for item in set(raw_profile) - known_profile_fields):
            profile_reasons.append(
                f"profiles.{person_id}.{key} ist ein unbekanntes Profilfeld"
            )

        profile_id = raw_profile.get("profile_id")
        if isinstance(profile_id, str) and profile_id:
            profile["profile_id"] = profile_id
        else:
            profile_reasons.append(f"profiles.{person_id}.profile_id ist ungültig")
        for key in {"created_at", "updated_at", "removed_at"}:
            original = raw_profile.get(key)
            parsed = _stored_datetime(original)
            if key == "removed_at" and (original is None or original == ""):
                parsed = None
            elif parsed is None:
                profile_reasons.append(f"profiles.{person_id}.{key} enthält keinen gültigen Zeitstempel")
                parsed = profile[key]
            profile[key] = parsed
        defaults_migrated = raw_profile.get("notification_defaults_migrated_at")
        if defaults_migrated is None or defaults_migrated == "":
            profile["notification_defaults_migrated_at"] = None
        else:
            parsed_defaults_migrated = _stored_datetime(defaults_migrated)
            if parsed_defaults_migrated is None:
                profile_reasons.append(
                    f"profiles.{person_id}.notification_defaults_migrated_at "
                    "enthält keinen gültigen Zeitstempel"
                )
            profile["notification_defaults_migrated_at"] = parsed_defaults_migrated
        archived = raw_profile.get("archived", False)
        if not isinstance(archived, bool):
            profile_reasons.append(f"profiles.{person_id}.archived ist kein Wahrheitswert")
            archived = False
        profile["archived"] = archived

        settings, setting_reasons = repair_settings(raw_profile.get("settings", {}))
        profile["settings"] = settings
        profile_reasons.extend(
            f"profiles.{person_id}.settings: {reason}" for reason in setting_reasons
        )

        raw_medications = raw_profile.get("medications", {})
        medication_items: list[tuple[str, Any]] = []
        if isinstance(raw_medications, Mapping):
            medication_items = [(str(key), value) for key, value in raw_medications.items()]
        elif isinstance(raw_medications, list):
            profile_reasons.append(f"profiles.{person_id}.medications wurde aus einer Liste repariert")
            medication_items = [
                (str(item.get("id") or slugify(str(item.get("name", "medikament")))), item)
                for item in raw_medications
                if isinstance(item, Mapping)
            ]
        else:
            profile_reasons.append(f"profiles.{person_id}.medications ist weder Objekt noch Liste")
        profile["medications"] = {}
        for medication_id, raw_medication in medication_items:
            path = f"profiles.{person_id}.medications.{medication_id}"
            if not medication_id or not isinstance(raw_medication, Mapping):
                profile_reasons.append(f"{path} ist kein gültiges Medikament")
                continue
            try:
                medication = normalize_medication(
                    {**dict(raw_medication), "id": medication_id},
                    now=current,
                    enforce_business_rules=False,
                )
            except PillPalError as err:
                profile_reasons.append(f"{path} wurde verworfen: {err}")
                continue
            known_medication_fields = set(medication) | {"as_needed", "is_as_needed"}
            for key in sorted(
                str(item) for item in set(raw_medication) - known_medication_fields
            ):
                profile_reasons.append(f"{path}.{key} ist ein unbekanntes Medikamentenfeld")
            button_helper = medication.get("button_helper", "")
            if button_helper and not str(button_helper).startswith("input_button."):
                profile_reasons.append(f"{path}.button_helper hat die falsche Entitätsdomäne")
                medication["button_helper"] = ""
            for key in {"created_at", "updated_at", "archived_at", "as_needed_migrated_at"}:
                original = raw_medication.get(key)
                parsed = _stored_datetime(original)
                if original is not None and original != "" and parsed is None:
                    profile_reasons.append(f"{path}.{key} enthält keinen gültigen Zeitstempel")
                if parsed is not None or key in {"archived_at", "as_needed_migrated_at"}:
                    medication[key] = parsed
            profile["medications"][medication_id] = medication

        closures = raw_profile.get("practice_closures", [])
        if not isinstance(closures, list):
            profile_reasons.append(f"profiles.{person_id}.practice_closures ist keine Liste")
            closures = []
        valid_closures: list[Mapping[str, Any]] = []
        for index, closure in enumerate(closures):
            if (
                not isinstance(closure, Mapping)
                or _stored_date(closure.get("start")) is None
                or _stored_date(closure.get("end") or closure.get("start")) is None
            ):
                profile_reasons.append(f"profiles.{person_id}.practice_closures.{index} ist ungültig")
                continue
            valid_closures.append(closure)
        profile["practice_closures"] = normalize_practice_closures(
            valid_closures, current.date()
        )

        for key in {"events", "log"}:
            raw_items = raw_profile.get(key, [])
            repaired_items: list[dict[str, Any]] = []
            if not isinstance(raw_items, list):
                profile_reasons.append(f"profiles.{person_id}.{key} ist keine Liste")
                raw_items = []
            for index, raw_item in enumerate(raw_items):
                if not isinstance(raw_item, Mapping):
                    profile_reasons.append(f"profiles.{person_id}.{key}.{index} ist kein Objekt")
                    continue
                item = deepcopy(dict(raw_item))
                if key == "events":
                    event_date = _stored_date(item.get("date"))
                    if event_date is None:
                        profile_reasons.append(f"profiles.{person_id}.events.{index}.date ist ungültig")
                        continue
                    item["date"] = event_date
                    item["person_id"] = person_id
                    if "quantity" in item:
                        try:
                            item["quantity"] = decimal_json(item["quantity"])
                        except PillPalError:
                            profile_reasons.append(f"profiles.{person_id}.events.{index}.quantity ist ungültig")
                            continue
                else:
                    timestamp = _stored_datetime(item.get("timestamp"))
                    if timestamp is None:
                        profile_reasons.append(
                            f"profiles.{person_id}.log.{index}.timestamp ist ungültig"
                        )
                        continue
                    if datetime.fromisoformat(timestamp) < current - timedelta(
                        hours=DIAGNOSTIC_RETENTION_HOURS
                    ):
                        continue
                    item["timestamp"] = timestamp
                    item["person_id"] = person_id
                repaired_items.append(item)
            profile[key] = repaired_items

        raw_history = raw_profile.get("history", {"daily": {}})
        raw_daily = raw_history.get("daily", {}) if isinstance(raw_history, Mapping) else {}
        if not isinstance(raw_history, Mapping) or not isinstance(raw_daily, Mapping):
            profile_reasons.append(f"profiles.{person_id}.history.daily ist kein Objekt")
            raw_daily = {}
        repaired_daily: dict[str, dict[str, Any]] = {}
        for day_key, raw_day in raw_daily.items():
            normalized_day = _stored_date(day_key)
            raw_slots = raw_day.get("slots", {}) if isinstance(raw_day, Mapping) else {}
            if normalized_day is None or not isinstance(raw_slots, Mapping):
                profile_reasons.append(
                    f"profiles.{person_id}.history.daily.{day_key} ist ungültig"
                )
                continue
            repaired_slots: dict[str, dict[str, Any]] = {}
            for slot_id, raw_slot in raw_slots.items():
                if not isinstance(slot_id, str) or not isinstance(raw_slot, Mapping):
                    profile_reasons.append(
                        f"profiles.{person_id}.history.daily.{day_key}.slots enthält einen ungültigen Eintrag"
                    )
                    continue
                slot = str(raw_slot.get("slot", ""))
                cycle_id = str(raw_slot.get("cycle_id", ""))
                status = str(raw_slot.get("status") or "planned")
                due_at = raw_slot.get("due_at")
                completed_at = raw_slot.get("completed_at")
                if (
                    slot not in SLOTS
                    or not cycle_id
                    or status not in {*OPEN_SLOT_STATUSES, *TERMINAL_SLOT_STATUSES}
                    or (due_at not in (None, "") and _stored_datetime(due_at) is None)
                    or (
                        completed_at not in (None, "")
                        and _stored_datetime(completed_at) is None
                    )
                ):
                    profile_reasons.append(
                        f"profiles.{person_id}.history.daily.{day_key}.slots.{slot_id} ist ungültig"
                    )
                    continue
                try:
                    medications = _history_medications(raw_slot.get("medications", []))
                except PillPalError:
                    profile_reasons.append(
                        f"profiles.{person_id}.history.daily.{day_key}.slots.{slot_id}.medications ist ungültig"
                    )
                    continue
                repaired_slots[slot_id] = {
                    "slot_id": slot_id,
                    "cycle_id": cycle_id,
                    "cycle_date": normalized_day,
                    "slot": slot,
                    "due_at": due_at or None,
                    "status": status,
                    "completed_at": completed_at or None,
                    "source": raw_slot.get("source"),
                    "medications": medications,
                    "snapshot_source": str(
                        raw_slot.get("snapshot_source") or "stored_cycle_slot"
                    ),
                }
            repaired_daily[normalized_day] = {"slots": repaired_slots}
        profile["history"] = {"daily": repaired_daily}

        warning_fingerprints = raw_profile.get("warning_fingerprints", {})
        if not isinstance(warning_fingerprints, Mapping):
            profile_reasons.append(f"profiles.{person_id}.warning_fingerprints ist kein Objekt")
            warning_fingerprints = {}
        profile["warning_fingerprints"] = deepcopy(dict(warning_fingerprints))

        runtime, runtime_reasons = _repair_runtime_payload(
            raw_profile.get("runtime", {}),
            set(profile["medications"]),
            path=f"profiles.{person_id}.runtime",
        )
        profile["runtime"] = runtime
        profile_reasons.extend(runtime_reasons)
        _migrate_terminal_event_history(profile)
        _sync_cycle_history(profile)
        safe["profiles"][person_id] = profile
        if profile_reasons:
            repaired_profiles.append(person_id)
            reasons.extend(profile_reasons)

    existing_health = raw.get("storage_health")
    if isinstance(existing_health, Mapping):
        status = existing_health.get("status")
        if status == "repaired_from_quarantine":
            quarantine_id = existing_health.get("quarantine_id")
            detected_at = _stored_datetime(existing_health.get("detected_at"))
            reason_count = existing_health.get("reason_count", 0)
            repaired = existing_health.get("repaired_profiles", [])
            dropped = existing_health.get("dropped_profiles", [])
            if (
                not isinstance(quarantine_id, str)
                or not quarantine_id
                or detected_at is None
                or not isinstance(reason_count, int)
                or isinstance(reason_count, bool)
                or reason_count < 0
                or not isinstance(repaired, list)
                or not all(isinstance(item, str) for item in repaired)
                or not isinstance(dropped, list)
                or not all(isinstance(item, str) for item in dropped)
            ):
                reasons.append("storage_health enthält ungültige Reparaturdaten")
            else:
                safe["storage_health"] = {
                    "status": status,
                    "quarantine_id": quarantine_id,
                    "detected_at": detected_at,
                    "reason_count": reason_count,
                    "repaired_profiles": deepcopy(repaired),
                    "dropped_profiles": deepcopy(dropped),
                }
        elif status is not None and status != "healthy":
            reasons.append("storage_health.status ist ungültig")
        elif status == "healthy":
            safe["storage_health"] = {"status": "healthy"}
    elif existing_health is not None:
        reasons.append("storage_health ist kein Objekt")

    return safe, {
        "quarantine_required": bool(reasons),
        "reasons": reasons,
        "repaired_profiles": repaired_profiles,
        "dropped_profiles": dropped_profiles,
        "detected_at": current.isoformat(),
        "source_schema": source_schema,
        "target_schema": DATA_SCHEMA_VERSION,
        "schema_migrated": schema_migrated,
        "unsupported_schema": unsupported_schema,
    }


_MEDICATION_CHANGE_LABELS = {
    "name": "Name",
    "description": "Beschreibung",
    "unit_singular": "Einheit (Einzahl)",
    "unit_plural": "Einheit (Mehrzahl)",
    "step": "Kleinste Buchungsmenge",
    "pack_size": "Packungsgröße",
    "cost": "Kosten/Zuzahlung",
    "stock": "Bestand",
    "as_needed_allowed": "Bedarfseinnahme",
    "single_max": "Maximale Einzeldosis",
    "daily_max": "Maximale Tagesdosis",
    "button_helper": "Medikamententaster",
    "button_amount": "Menge je Tastendruck",
    "expiry_enabled": "MHD-Prüfung",
    "expiry_date": "Mindesthaltbarkeitsdatum",
    **{f"doses.{slot}": f"Dosis {SLOT_LABELS[slot]}" for slot in SLOTS},
}

_SETTING_CHANGE_LABELS = {
    "notify_target": "Notify-Ziel",
    "confirm_helper": "Taster-Helfer Sammeleinnahme",
    "awake_helper": "Aufgestanden-Helfer",
    "next_alarm_entity": "Wecker-Sensor",
    "holiday_calendar": "Feiertagskalender",
    "intake_calendar": "Einnahmekalender",
    "early_minutes": "Frühbuchungszeit",
    "morning_delay_minutes": "Verzögerung Morgeneinnahme",
    "fallback_wake_time": "Fallback-Aufstehzeit",
    "snooze_minutes": "Snooze-Dauer",
    "repeat_minutes": "Wiederholungsintervall",
    "order_warning_days": "Bestellwarnfrist",
    "practice_lead_days": "Praxisschließungs-Vorlauf",
    "low_stock_window_days": "Niedrigbestandsfenster",
    "expiry_warning_days": "MHD-Warnfrist",
    "alarm_window_from": "Weckerfenster Beginn",
    "alarm_window_to": "Weckerfenster Ende",
    "lunch_window_from": "Mittagsfenster Beginn",
    "lunch_window_to": "Mittagsfenster Ende",
    "bedtime_offset_hours": "Abstand Zur Nacht zum Wecker",
    "evening_before_bedtime_hours": "Abstand Abends",
    "notification_title": "Push-Titel",
    "notification_intro": "Push-Einleitung",
    "action_take": "Aktion Einnahme",
    "action_snooze": "Aktion Snooze",
    "action_skip": "Aktion Überspringen",
    "notification_sticky": "Android-Sticky",
    "notification_persistent": "Android-Persistent",
    "notification_alert_once": "Android-Alert-once",
    "notification_critical": "Kritische Benachrichtigung",
    "notification_channel": "Android-Kanal",
    "notification_group": "Push-Gruppe",
    "notification_tag": "Push-Tag",
    "notification_icon": "Push-Symbol",
    "order_notification_title": "Nachbestellungs-Titel",
    "order_notification_tag": "Nachbestellungs-Tag",
    "order_notification_icon": "Nachbestellungs-Symbol",
    "expiry_notification_title": "MHD-Titel",
    "expiry_notification_tag": "MHD-Tag",
    "expiry_notification_icon": "MHD-Symbol",
    "notification_color": "Android-Farbe",
    "notification_vibration_pattern": "Android-Vibrationsmuster",
    "notification_led_color": "Android-LED-Farbe",
    "notification_sound": "iOS-Ton",
    "notification_importance": "Android-Wichtigkeit",
    "notification_priority": "Android-Priorität",
    "notification_visibility": "Android-Sichtbarkeit",
    "notification_ttl": "Android-TTL",
    "notification_timeout": "Android-Timeout",
    "ios_interruption_level": "iOS-Unterbrechungsstufe",
    "ios_volume": "iOS-Lautstärke",
    "ios_badge": "iOS-Badge",
    "ios_presentation_options": "iOS-Darstellung",
    "currency": "Währung",
    "show_archived": "Archivansicht",
    "statistics_show_archived": "Archivierte Medikamente in Statistik",
    **{f"times.{slot}": f"Fallback-Zeit {SLOT_LABELS[slot]}" for slot in SLOTS},
}


def _flatten_changes(before: Any, after: Any, prefix: str = "") -> list[tuple[str, Any, Any]]:
    if isinstance(before, Mapping) or isinstance(after, Mapping):
        old = before if isinstance(before, Mapping) else {}
        new = after if isinstance(after, Mapping) else {}
        result: list[tuple[str, Any, Any]] = []
        for key in sorted(set(old) | set(new)):
            path = f"{prefix}.{key}" if prefix else str(key)
            result.extend(_flatten_changes(old.get(key), new.get(key), path))
        return result
    return [] if before == after else [(prefix, deepcopy(before), deepcopy(after))]


def _display_change_value(value: Any) -> str:
    if value in (None, ""):
        return "nicht gesetzt"
    if value is True:
        return "aktiv"
    if value is False:
        return "inaktiv"
    if isinstance(value, str):
        return {
            **SLOT_LABELS,
            "dashboard": "Dashboard",
            "helper": "Helfer",
            "notification": "Benachrichtigung",
        }.get(value, value)
    return str(value)


def _visible_source(source: str) -> str:
    return {
        "dashboard": "Dashboard",
        "helper": "Helfer",
        "notification": "Benachrichtigung",
        "schedule": "Zeitplan",
        "cycle_end": "Zyklusende",
        "service": "Home-Assistant-Dienst",
    }.get(str(source).casefold(), str(source))


def _append_change_log(
    profile: dict[str, Any],
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    labels: Mapping[str, str],
    *,
    subject: str,
    source: str,
    actor: str | None,
    now: datetime | None,
    ignored: set[str],
) -> None:
    for path, old_value, new_value in _flatten_changes(before, after):
        if path in ignored:
            continue
        append_log(
            profile,
            f"{profile['name']}: {subject} – {labels.get(path, path)} geändert: "
            f"{_display_change_value(old_value)} → {_display_change_value(new_value)}.",
            source=source,
            actor=actor,
            now=now,
        )


def save_medication(
    store: dict[str, Any],
    person_id: str,
    data: Mapping[str, Any],
    *,
    actor: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create or update one medication in exactly one profile."""

    profile = get_profile(store, person_id)
    requested_id = str(data.get("id", ""))
    existing = profile["medications"].get(requested_id) if requested_id else None
    before = deepcopy(existing) if isinstance(existing, Mapping) else None
    medication = normalize_medication(data, existing, now=now)
    med_id = medication["id"]
    if not existing and med_id in profile["medications"]:
        suffix = 2
        while f"{med_id}_{suffix}" in profile["medications"]:
            suffix += 1
        med_id = medication["id"] = f"{med_id}_{suffix}"
    profile["medications"][med_id] = medication
    verb = "geändert" if existing else "angelegt"
    append_log(
        profile,
        f"{profile['name']}: {medication['name']} wurde {verb}.",
        source="medication",
        actor=actor,
        now=now,
    )
    if before is not None:
        _append_change_log(
            profile,
            before,
            medication,
            _MEDICATION_CHANGE_LABELS,
            subject=medication["name"],
            source="medication",
            actor=actor,
            now=now,
            ignored={
                "id",
                "created_at",
                "updated_at",
                "archived_at",
                "as_needed_migrated_at",
                "as_needed_migration_source",
                "pure_as_needed",
            },
        )
    rebuild_schedule(profile, now=now)
    return deepcopy(medication)


def _get_medication(profile: Mapping[str, Any], medication_id: str) -> dict[str, Any]:
    medication = profile.get("medications", {}).get(medication_id)
    if medication is None:
        raise MedicationNotFoundError(f"Unbekanntes Medikament: {medication_id}")
    return medication


def archive_medication(
    store: dict[str, Any],
    person_id: str,
    medication_id: str,
    *,
    actor: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Archive a medication and immediately remove future planned doses."""

    profile = get_profile(store, person_id)
    medication = _get_medication(profile, medication_id)
    if medication.get("archived"):
        return deepcopy(medication)
    medication["archived"] = True
    medication["archived_at"] = iso_now(now)
    medication["updated_at"] = iso_now(now)
    append_log(
        profile,
        f"{profile['name']}: {medication['name']} wurde archiviert.",
        source="medication",
        actor=actor,
        now=now,
    )
    rebuild_schedule(profile, now=now)
    return deepcopy(medication)


def reactivate_medication(
    store: dict[str, Any],
    person_id: str,
    medication_id: str,
    *,
    actor: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Reactivate a medication without a later stale write undoing it."""

    profile = get_profile(store, person_id)
    medication = _get_medication(profile, medication_id)
    medication["archived"] = False
    medication["archived_at"] = None
    medication["updated_at"] = iso_now(now)
    append_log(
        profile,
        f"{profile['name']}: {medication['name']} wurde reaktiviert.",
        source="medication",
        actor=actor,
        now=now,
    )
    rebuild_schedule(profile, now=now)
    return deepcopy(medication)


def _quantity_matches_step(quantity: Decimal, step: Decimal) -> bool:
    return _amount_matches_step(quantity, step, allow_zero=False)


def book_as_needed(
    store: dict[str, Any],
    person_id: str,
    medication_id: str,
    quantity: Any,
    *,
    actor: str | None = None,
    actor_user_id: str | None = None,
    source: str = "dashboard",
    confirmation_token: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Book PRN, requiring a bound one-use confirmation above configured maxima."""

    current = _local_now(now)
    profile = get_profile(store, person_id)
    medication = _get_medication(profile, medication_id)
    if medication.get("archived") or not medication.get("as_needed_allowed"):
        raise PillPalError("Dieses Medikament ist nicht für Bedarfseinnahmen freigegeben.")
    amount = decimal_value(quantity)
    step = decimal_value(medication.get("step", 1), "1")
    if not _quantity_matches_step(amount, step):
        raise InvalidStepError(
            f"Die Menge muss in Schritten von {decimal_json(step)} angegeben werden."
        )
    stock = decimal_value(medication.get("stock", 0))
    if amount > stock:
        raise PillPalError("Der Bestand reicht für diese Buchung nicht aus.")
    today_total = sum(
        decimal_value(event.get("quantity", 0))
        for event in profile.get("events", [])
        if event.get("type") == "as_needed"
        and event.get("medication_id") == medication_id
        and event.get("date") == current.date().isoformat()
    )
    single_max = decimal_value(medication.get("single_max", 0))
    daily_max = decimal_value(medication.get("daily_max", 0))
    projected_total = today_total + amount
    exceeded: list[dict[str, Any]] = []
    if single_max > 0 and amount > single_max:
        exceeded.append(
            {
                "kind": "single",
                "requested": decimal_json(amount),
                "limit": decimal_json(single_max),
            }
        )
    if daily_max > 0 and projected_total > daily_max:
        exceeded.append(
            {
                "kind": "daily",
                "projected": decimal_json(projected_total),
                "limit": decimal_json(daily_max),
            }
        )

    runtime = _migrate_runtime(profile)
    pending = runtime["pending_max_dose_confirmations"]
    for token, request in list(pending.items()):
        try:
            expired = current >= datetime.fromisoformat(str(request.get("expires_at")))
        except (TypeError, ValueError):
            expired = True
        if expired:
            pending.pop(token, None)

    maximum_override = False
    if exceeded:
        if not actor_user_id:
            raise PillPalError(
                "Die Höchstdosis kann nur von einem angemeldeten, für diese Person "
                "berechtigten Benutzer ausdrücklich bestätigt werden."
            )
        binding = {
            "person_id": person_id,
            "medication_id": medication_id,
            "quantity": decimal_json(amount),
            "today_total": decimal_json(today_total),
            "projected_total": decimal_json(projected_total),
            "actor_user_id": actor_user_id,
            "date": current.date().isoformat(),
            "cycle_id": runtime.get("cycle_id"),
        }
        request = pending.get(str(confirmation_token)) if confirmation_token else None
        if request is None:
            if confirmation_token:
                raise PillPalError(
                    "Die Bestätigung ist ungültig, abgelaufen oder bereits verwendet."
                )
            for token, candidate in list(pending.items()):
                if (
                    candidate.get("person_id") == person_id
                    and candidate.get("medication_id") == medication_id
                    and candidate.get("actor_user_id") == actor_user_id
                ):
                    pending.pop(token, None)
            token = uuid4().hex
            expires_at = current + MAX_DOSE_CONFIRMATION_TTL
            pending[token] = {
                **binding,
                "created_at": current.isoformat(),
                "expires_at": expires_at.isoformat(),
                "exceeded": deepcopy(exceeded),
            }
            return {
                "status": "confirmation_required",
                "confirmation": {
                    "token": token,
                    "expires_at": expires_at.isoformat(),
                    "person_id": person_id,
                    "medication_id": medication_id,
                    "medication_name": medication["name"],
                    "quantity": decimal_json(amount),
                    "today_total": decimal_json(today_total),
                    "projected_total": decimal_json(projected_total),
                    "exceeded": deepcopy(exceeded),
                    "warning": (
                        "Die konfigurierte Bedarfs-Höchstdosis wird überschritten. "
                        "Nur nach ausdrücklicher zweiter Bestätigung buchen."
                    ),
                },
            }
        comparable = {key: request.get(key) for key in binding}
        if comparable != binding:
            raise PillPalError("Die Bestätigung passt nicht mehr zu dieser Buchung.")
        pending.pop(str(confirmation_token), None)
        maximum_override = True
    elif confirmation_token:
        raise PillPalError(
            "Für diese Buchung ist keine Höchstdosis-Bestätigung erforderlich."
        )

    before = stock
    medication["stock"] = decimal_json(stock - amount)
    medication["updated_at"] = iso_now(current)
    event = add_event(
        profile,
        "as_needed",
        now=current,
        cycle_id=runtime.get("cycle_id"),
        cycle_date=runtime.get("cycle_date"),
        medication_id=medication_id,
        medication_name=medication["name"],
        quantity=decimal_json(amount),
        unit_singular=medication["unit_singular"],
        unit_plural=medication["unit_plural"],
        source=source,
        actor=actor or "",
        maximum_override=maximum_override,
    )
    if maximum_override:
        add_event(
            profile,
            "as_needed_max_override",
            now=current,
            cycle_id=runtime.get("cycle_id"),
            cycle_date=runtime.get("cycle_date"),
            medication_id=medication_id,
            medication_name=medication["name"],
            quantity=decimal_json(amount),
            unit_singular=medication["unit_singular"],
            unit_plural=medication["unit_plural"],
            today_total=decimal_json(today_total),
            projected_total=decimal_json(projected_total),
            single_max=decimal_json(single_max),
            daily_max=decimal_json(daily_max),
            source=source,
            actor=actor or "",
            actor_user_id=actor_user_id,
        )
    unit = (
        medication["unit_singular"]
        if amount == 1
        else medication["unit_plural"]
    )
    append_log(
        profile,
        f"{profile['name']}: Bedarfseinnahme {medication['name']}: "
        f"{decimal_json(amount)} {unit}, Bestand "
        f"{decimal_json(before)} → {medication['stock']} ({_visible_source(source)}).",
        source="as_needed",
        level="warning" if maximum_override else "info",
        actor=actor,
        now=current,
    )
    return deepcopy(event)


def refill(
    store: dict[str, Any],
    person_id: str,
    medication_id: str,
    quantity: Any | None = None,
    expiry_date: str | None = None,
    *,
    actor: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Add stock. Keeping the same MHD is explicitly valid."""

    profile = get_profile(store, person_id)
    medication = _get_medication(profile, medication_id)
    amount = decimal_value(
        medication.get("pack_size", 0) if quantity in (None, "") else quantity
    )
    if amount <= 0:
        raise PillPalError("Die Auffüllmenge muss größer als 0 sein.")
    step = decimal_value(medication.get("step", 1), "1")
    if not _quantity_matches_step(amount, step):
        raise InvalidStepError(
            f"Die Auffüllmenge muss in Schritten von {decimal_json(step)} "
            "angegeben werden."
        )
    if expiry_date not in (None, ""):
        medication["expiry_date"] = _validate_expiry_date(expiry_date, now)
    if medication.get("expiry_enabled") and not medication.get("expiry_date"):
        raise PillPalError("Bitte zuerst ein MHD hinterlegen.")
    before = decimal_value(medication.get("stock", 0))
    medication["stock"] = decimal_json(before + amount)
    medication["updated_at"] = iso_now(now)
    append_log(
        profile,
        f"{profile['name']}: {medication['name']} aufgefüllt: Bestand "
        f"{decimal_json(before)} → {medication['stock']}.",
        source="refill",
        actor=actor,
        now=now,
    )
    return deepcopy(medication)


def adjust_stock(
    store: dict[str, Any],
    person_id: str,
    medication_id: str,
    delta: Any,
    *,
    actor: str | None = None,
    source: str = "Service",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Apply a public, relative, step-aligned stock correction for one person."""

    profile = get_profile(store, person_id)
    medication = _get_medication(profile, medication_id)
    correction = decimal_value(delta)
    step = decimal_value(medication.get("step", 1), "1")
    if correction == 0:
        raise PillPalError("Die Bestandsänderung muss ungleich 0 sein.")
    if abs(correction) > Decimal("10000"):
        raise PillPalError(
            "Die Bestandsänderung muss zwischen -10000 und 10000 liegen."
        )
    if not _quantity_matches_step(abs(correction), step):
        raise InvalidStepError(
            f"Die Bestandsänderung muss in Schritten von {decimal_json(step)} "
            "angegeben werden."
        )
    before = decimal_value(medication.get("stock", 0))
    requested = before + correction
    after = max(Decimal("0"), requested)
    medication["stock"] = decimal_json(after)
    medication["updated_at"] = iso_now(now)
    clamped = requested < 0
    message = (
        f"{profile['name']}: Bestand {medication['name']} korrigiert: "
        f"{decimal_json(before)} → {medication['stock']} ({_visible_source(source)})."
    )
    if clamped:
        message += " Die negative Korrektur wurde auf 0 begrenzt."
    event = add_event(
        profile,
        "stock_adjusted",
        now=now,
        medication_id=medication_id,
        medication_name=medication["name"],
        quantity=decimal_json(correction),
        requested_delta=decimal_json(correction),
        applied_delta=decimal_json(after - before),
        stock_before=decimal_json(before),
        stock_after=medication["stock"],
        clamped=clamped,
        source=source,
        actor=actor or "",
    )
    append_log(
        profile,
        message,
        level="warning" if clamped else "info",
        source="stock",
        actor=actor,
        now=now,
    )
    return {
        "status": "adjusted",
        "person_id": person_id,
        "medication_id": medication_id,
        "medication_name": medication["name"],
        "requested_delta": decimal_json(correction),
        "applied_delta": decimal_json(after - before),
        "stock_before": decimal_json(before),
        "stock_after": medication["stock"],
        "clamped": clamped,
        "event_id": event["id"],
        "source": source,
    }


def parse_time(value: str) -> time:
    """Parse HH:MM with a safe fallback."""

    try:
        return time.fromisoformat(value)
    except (TypeError, ValueError):
        return time(8, 0)


def _local_now(now: datetime | None = None) -> datetime:
    value = now or utc_now()
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _slot_label(slot: str) -> str:
    """Return the visible German label for an internal schedule slot."""

    return SLOT_LABELS.get(slot, slot)


def _desired_slot_items(profile: Mapping[str, Any], slot: str) -> list[dict[str, Any]]:
    """Return the immutable medication snapshot desired for a slot."""

    items: list[dict[str, Any]] = []
    for medication in profile.get("medications", {}).values():
        if medication.get("archived"):
            continue
        amount = decimal_value(medication.get("doses", {}).get(slot, 0))
        if amount <= 0:
            continue
        items.append(
            {
                "medication_id": medication["id"],
                "name": medication["name"],
                "quantity": decimal_json(amount),
                "unit_singular": medication["unit_singular"],
                "unit_plural": medication["unit_plural"],
            }
        )
    return sorted(items, key=lambda item: (item["name"].casefold(), item["medication_id"]))


def _history_medications(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Keep an immutable, display-ready medication snapshot for statistics."""

    result: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, Mapping):
            continue
        medication_id = str(raw.get("medication_id") or raw.get("id") or "")
        if not medication_id:
            continue
        result.append(
            {
                "medication_id": medication_id,
                "name": str(raw.get("name") or medication_id),
                "quantity": decimal_json(raw.get("quantity", raw.get("dose", 0))),
                "unit_singular": str(raw.get("unit_singular") or raw.get("unit") or "Einheit"),
                "unit_plural": str(raw.get("unit_plural") or raw.get("unit") or "Einheiten"),
            }
        )
    return result


def _migrate_terminal_event_history(profile: dict[str, Any]) -> None:
    """Build conservative legacy snapshots without consulting today's plan."""

    history = profile.setdefault("history", {"daily": {}})
    daily = history.setdefault("daily", {})
    if not isinstance(daily, dict):
        history["daily"] = daily = {}
    terminal_types = {
        "regular_taken": "taken",
        "regular_skipped": "skipped",
        "regular_missed": "missed",
    }
    for event in profile.get("events", []):
        status = terminal_types.get(str(event.get("type", "")))
        slot = str(event.get("slot", ""))
        day_key = str(event.get("cycle_date") or event.get("date") or "")
        if status is None or slot not in SLOTS or not _stored_date(day_key):
            continue
        cycle_id = str(event.get("cycle_id") or f"legacy_{event.get('id', day_key)}")
        slot_id = f"{cycle_id}:{slot}"
        day = daily.setdefault(day_key, {"slots": {}})
        slots = day.setdefault("slots", {})
        slots.setdefault(
            slot_id,
            {
                "slot_id": slot_id,
                "cycle_id": cycle_id,
                "cycle_date": day_key,
                "slot": slot,
                "due_at": event.get("due_at"),
                "status": status,
                "completed_at": event.get("timestamp"),
                "source": event.get("source"),
                "medications": _history_medications(event.get("medications", [])),
                "snapshot_source": "legacy_terminal_event",
            },
        )


def _sync_cycle_history(profile: dict[str, Any]) -> None:
    """Persist the exact current-cycle slot plan and status by stable slot id."""

    runtime = profile.get("runtime", {})
    cycle_id = str(runtime.get("cycle_id") or "")
    cycle_date = str(runtime.get("cycle_date") or "")
    if not cycle_id or not _stored_date(cycle_date):
        return
    history = profile.setdefault("history", {"daily": {}})
    daily = history.setdefault("daily", {})
    day = daily.setdefault(cycle_date, {"slots": {}})
    slots = day.setdefault("slots", {})
    prefix = f"{cycle_id}:"
    for slot_id in [key for key in slots if str(key).startswith(prefix)]:
        slots.pop(slot_id, None)
    for slot in SLOTS:
        raw = runtime.get("slots", {}).get(slot)
        if not isinstance(raw, Mapping):
            continue
        slot_id = str(raw.get("slot_id") or f"{cycle_id}:{slot}")
        status = str(raw.get("status") or "planned")
        slots[slot_id] = {
            "slot_id": slot_id,
            "cycle_id": cycle_id,
            "cycle_date": cycle_date,
            "slot": slot,
            "due_at": raw.get("due_at"),
            "status": status,
            "completed_at": (
                raw.get("confirmed_at")
                or raw.get("skipped_at")
                or raw.get("missed_at")
                or (raw.get("action_consumed_at") if status in TERMINAL_SLOT_STATUSES else None)
            ),
            "source": raw.get("source"),
            "medications": _history_medications(raw.get("items", [])),
            "snapshot_source": "cycle_slot",
        }


def calculate_cycle_schedule(
    profile: Mapping[str, Any], cycle_started_at: datetime
) -> dict[str, datetime | str]:
    """Calculate all slot datetimes from one stable cycle start."""

    settings = deep_merge(DEFAULT_SETTINGS, profile.get("settings", {}))
    runtime = profile.get("runtime", {})
    explicit = runtime.get("effective_due_at", {})
    if isinstance(explicit, Mapping) and all(explicit.get(slot) for slot in SLOTS):
        try:
            parsed = {slot: datetime.fromisoformat(str(explicit[slot])) for slot in SLOTS}
            return {**parsed, "source": str(runtime.get("schedule_source") or "dynamic")}
        except (TypeError, ValueError):
            pass

    times = settings.get("times", {})
    next_alarm_raw = runtime.get("next_alarm_at")
    next_alarm: datetime | None = None
    if next_alarm_raw:
        try:
            candidate = datetime.fromisoformat(str(next_alarm_raw))
            window_start = parse_time(str(settings.get("alarm_window_from", "04:00")))
            window_end = parse_time(str(settings.get("alarm_window_to", "11:00")))
            if (
                candidate > cycle_started_at
                and candidate.date() == cycle_started_at.date() + timedelta(days=1)
                and window_start <= candidate.time() <= window_end
            ):
                next_alarm = candidate
        except (TypeError, ValueError):
            next_alarm = None

    if next_alarm is not None:
        night = next_alarm - timedelta(hours=float(settings.get("bedtime_offset_hours", 8.25)))
        evening = night - timedelta(
            hours=float(settings.get("evening_before_bedtime_hours", 2))
        )
        source = "Smartphone-Wecker"
    else:
        evening_time = parse_time(str(times.get("evening", "20:00")))
        night_time = parse_time(str(times.get("night", "23:00")))
        evening = datetime.combine(
            cycle_started_at.date(),
            evening_time,
            cycle_started_at.tzinfo,
        )
        # "Zur Nacht" belongs to the cycle-start date when its clock time is
        # later than "Abends". Only after-midnight values (for example 00:00)
        # cross into the following date.
        night_date = cycle_started_at.date()
        if night_time <= evening_time:
            night_date += timedelta(days=1)
        night = datetime.combine(
            night_date,
            night_time,
            cycle_started_at.tzinfo,
        )
        source = "Fallback-Zeiten"

    lunch_candidate = cycle_started_at + (evening - cycle_started_at) / 2
    lunch_start = datetime.combine(
        cycle_started_at.date(),
        parse_time(str(settings.get("lunch_window_from", "12:00"))),
        cycle_started_at.tzinfo,
    )
    lunch_end = datetime.combine(
        cycle_started_at.date(),
        parse_time(str(settings.get("lunch_window_to", "14:00"))),
        cycle_started_at.tzinfo,
    )
    noon = max(lunch_start, min(lunch_candidate, lunch_end))
    morning = cycle_started_at + timedelta(
        minutes=max(0, min(120, int(settings.get("morning_delay_minutes", 15))))
    )
    return {
        "morning": morning,
        "noon": noon,
        "evening": evening,
        "night": night,
        "source": source,
    }


def _base_slot(cycle_id: str, slot: str, due: datetime, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "slot": slot,
        "cycle_id": cycle_id,
        "slot_id": f"{cycle_id}:{slot}",
        "due_at": due.isoformat(),
        "status": "planned",
        "items": deepcopy(items),
        "confirmed_at": None,
        "skipped_at": None,
        "missed_at": None,
        "snoozed_until": None,
        "next_reminder_at": None,
        "last_notification_at": None,
        "notification_state": "idle",
        "notification_reservation_id": None,
        "notification_reserved_at": None,
        "notification_sent_at": None,
        "notification_error": None,
        "notification_target": None,
        "notification_tag": None,
        "action_token": uuid4().hex,
        "notification_action_target": None,
        "action_consumed_at": None,
        "source": None,
    }


def start_cycle(
    profile: dict[str, Any],
    now: datetime | None = None,
    *,
    started_by: str,
) -> dict[str, Any]:
    """Start exactly one fachlicher cycle for a person."""

    current = _local_now(now)
    previous = _migrate_runtime(profile)
    if cycle_is_active(profile):
        return {"status": "already_active", "cycle_id": previous["cycle_id"]}
    runtime = _new_runtime()
    runtime["last_cycle_id"] = previous.get("last_cycle_id") or previous.get("cycle_id")
    runtime["last_cycle_date"] = previous.get("last_cycle_date") or previous.get("cycle_date")
    runtime["last_cycle_completed"] = bool(
        previous.get("last_cycle_completed") or previous.get("cycle_completed")
    )
    runtime["fallback_wake_last_started_date"] = previous.get(
        "fallback_wake_last_started_date"
    )
    runtime["diagnostic_errors_acknowledged_at"] = previous.get(
        "diagnostic_errors_acknowledged_at"
    )
    for key in {
        "inventory_notification_fingerprints",
        "inventory_notification_deliveries",
        "inventory_notification_reservations",
        "pending_notification_feedback",
        "pending_notification_cleanup",
        "intake_calendar_deliveries",
    }:
        runtime[key] = deepcopy(previous.get(key, {}))
    runtime["last_action_result"] = deepcopy(
        previous.get("last_action_result", runtime["last_action_result"])
    )
    runtime["last_activity"] = previous.get("last_activity", runtime["last_activity"])
    runtime["cycle_id"] = f"cycle_{uuid4().hex}"
    runtime["cycle_state"] = "active"
    runtime["cycle_date"] = current.date().isoformat()
    runtime["cycle_started_at"] = current.isoformat()
    runtime["cycle_started_by"] = started_by
    runtime["cycle_completed"] = False
    runtime["cycle_completed_at"] = None
    runtime["last_cycle_completed"] = False
    runtime["next_alarm_at"] = previous.get("next_alarm_at")
    runtime["schedule_source"] = previous.get("schedule_source")
    if started_by == "fallback_time":
        runtime["fallback_wake_last_started_date"] = current.date().isoformat()
    profile["runtime"] = runtime
    rebuild_schedule(profile, current, force=True)
    # Runtime normalization returns a fresh JSON-safe mapping. Continue with
    # that authoritative instance rather than the pre-normalization object.
    runtime = profile["runtime"]
    append_log(
        profile,
        f"{profile['name']}: Neuer Tages-Zyklus durch "
        f"{'Fallback-Aufstehzeit' if started_by == 'fallback_time' else 'Aufgestanden-Helfer'} gestartet.",
        source="cycle",
        now=current,
    )
    add_event(
        profile,
        "cycle_started",
        now=current,
        cycle_id=runtime["cycle_id"],
        started_by=started_by,
    )
    return {"status": "started", "cycle_id": runtime["cycle_id"]}


def end_cycle(
    profile: dict[str, Any], now: datetime | None = None, *, source: str = "system"
) -> dict[str, Any]:
    """End the active cycle without allowing a calendar rollover to replace it."""

    current = _local_now(now)
    runtime = _migrate_runtime(profile)
    if not cycle_is_active(profile):
        return {"status": "already_inactive", "cycle_id": runtime.get("cycle_id")}
    for slot, item in runtime.get("slots", {}).items():
        if item.get("status") in TERMINAL_SLOT_STATUSES:
            continue
        item["status"] = "missed"
        item["missed_at"] = current.isoformat()
        item["snoozed_until"] = None
        item["next_reminder_at"] = None
        item["notification_reservation_id"] = None
        item["notification_state"] = "cleanup_pending"
        item["action_consumed_at"] = current.isoformat()
        add_event(
            profile,
            "regular_missed",
            now=current,
            date=runtime.get("cycle_date") or current.date().isoformat(),
            cycle_id=runtime.get("cycle_id"),
            cycle_date=runtime.get("cycle_date"),
            slot=slot,
            source="cycle_end",
            medications=deepcopy(item.get("items", [])),
        )
    runtime["last_cycle_id"] = runtime.get("cycle_id")
    runtime["last_cycle_date"] = runtime.get("cycle_date")
    runtime["last_cycle_completed"] = bool(runtime.get("cycle_completed"))
    runtime["cycle_state"] = "ended"
    runtime["cycle_ended_at"] = current.isoformat()
    append_log(
        profile,
        f"{profile['name']}: Tages-Zyklus beendet.",
        source="cycle",
        actor=source,
        now=current,
    )
    add_event(
        profile,
        "cycle_ended",
        now=current,
        cycle_id=runtime.get("cycle_id"),
        completed=runtime["last_cycle_completed"],
        source=source,
    )
    _sync_cycle_history(profile)
    return {"status": "ended", "cycle_id": runtime.get("cycle_id")}


def rebuild_schedule(
    profile: dict[str, Any], now: datetime | None = None, *, force: bool = False
) -> dict[str, Any]:
    """Reconcile the active cycle without rewriting terminal or past snapshots."""

    current = _local_now(now)
    runtime = _migrate_runtime(profile)
    if not cycle_is_active(profile):
        return {}
    try:
        cycle_started = datetime.fromisoformat(str(runtime["cycle_started_at"]))
    except (TypeError, ValueError) as err:
        raise InactiveCycleError("Der aktive Tages-Zyklus ist beschädigt.") from err
    cycle_id = str(runtime["cycle_id"])
    schedule = calculate_cycle_schedule(profile, cycle_started)
    runtime["schedule_source"] = str(schedule["source"])
    old_slots = runtime.get("slots", {})
    new_slots: dict[str, Any] = {}
    for slot in SLOTS:
        desired = _desired_slot_items(profile, slot)
        previous = deepcopy(old_slots.get(slot, {}))
        if previous.get("status") in TERMINAL_SLOT_STATUSES:
            previous["snoozed_until"] = None
            previous["next_reminder_at"] = None
            previous["notification_reservation_id"] = None
            new_slots[slot] = previous
            continue
        if not desired:
            continue
        expected_due = schedule[slot]
        assert isinstance(expected_due, datetime)
        if not previous:
            # Changes may not create a new already-past intake in a running cycle.
            if not force and expected_due <= current:
                continue
            new_slots[slot] = _base_slot(cycle_id, slot, expected_due, desired)
            continue
        previous_status = previous.get("status")
        if previous_status in {"pending", "notified"}:
            previous["status"] = "planned"
        previous_due = datetime.fromisoformat(str(previous.get("due_at")))
        if force or expected_due > current:
            semantic_change = (
                previous_due != expected_due or previous.get("items", []) != desired
            )
            if semantic_change:
                replacement = _base_slot(cycle_id, slot, expected_due, desired)
                replacement["slot_id"] = previous.get("slot_id") or replacement["slot_id"]
                new_slots[slot] = replacement
            else:
                new_slots[slot] = previous
            continue
        # Once a slot is due, new medication/dose data is deferred to the next
        # cycle. Removals are still honored, while names may be refreshed.
        desired_by_id = {item["medication_id"]: item for item in desired}
        kept: list[dict[str, Any]] = []
        for item in previous.get("items", []):
            desired_item = desired_by_id.get(item.get("medication_id"))
            if desired_item is None:
                continue
            retained = deepcopy(item)
            retained["name"] = desired_item["name"]
            kept.append(retained)
        if kept:
            previous["items"] = kept
            new_slots[slot] = previous
    runtime["slots"] = new_slots
    _sync_cycle_history(profile)
    return deepcopy(new_slots)


def ensure_today_schedule(profile: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Compatibility wrapper: repair an active cycle, never start one by date."""

    if not cycle_is_active(profile):
        return {}
    return rebuild_schedule(profile, now)


def slot_is_bookable(
    profile: Mapping[str, Any], slot: str, now: datetime | None = None
) -> bool:
    """Return whether a pending slot may already be confirmed."""

    current = _local_now(now)
    if not cycle_is_active(profile):
        return False
    item = profile.get("runtime", {}).get("slots", {}).get(slot)
    if not item or item.get("status") not in OPEN_SLOT_STATUSES:
        return False
    due = datetime.fromisoformat(item["due_at"])
    early = int(profile.get("settings", {}).get("early_minutes", 30))
    cycle_started = datetime.fromisoformat(
        str(profile.get("runtime", {}).get("cycle_started_at"))
    )
    bookable_at = max(due - timedelta(minutes=early), cycle_started)
    # Snooze only postpones reminders. It must never prevent an actual intake.
    return current >= bookable_at


def slot_is_due(profile: Mapping[str, Any], slot: str, now: datetime | None = None) -> bool:
    """Return whether a pending slot reached its actual reminder time."""

    current = _local_now(now)
    if not cycle_is_active(profile):
        return False
    item = profile.get("runtime", {}).get("slots", {}).get(slot)
    if not item or item.get("status") not in OPEN_SLOT_STATUSES:
        return False
    due = datetime.fromisoformat(item["due_at"])
    snoozed = item.get("snoozed_until")
    if snoozed and current < datetime.fromisoformat(snoozed):
        return False
    return current >= due


def _decrement_slot_medications(
    profile: dict[str, Any], slot_data: Mapping[str, Any]
) -> list[dict[str, Any]]:
    changes = []
    shortages: list[str] = []
    for item in slot_data.get("items", []):
        medication = _get_medication(profile, item["medication_id"])
        before = decimal_value(medication.get("stock", 0))
        amount = decimal_value(item.get("quantity", 0))
        if before < amount:
            shortages.append(
                f"{medication['name']}: benötigt {decimal_json(amount)}, "
                f"vorhanden {decimal_json(before)}"
            )
    if shortages:
        raise PillPalError(
            "Der Bestand reicht für die vollständige reguläre Einnahme nicht aus: "
            + "; ".join(shortages)
            + ". Es wurde nichts gebucht."
        )
    for item in slot_data.get("items", []):
        medication = _get_medication(profile, item["medication_id"])
        before = decimal_value(medication.get("stock", 0))
        amount = decimal_value(item.get("quantity", 0))
        medication["stock"] = decimal_json(before - amount)
        changes.append(
            {
                "medication_id": medication["id"],
                "name": medication["name"],
                "quantity": decimal_json(amount),
                "unit_singular": str(
                    item.get("unit_singular")
                    or medication.get("unit_singular")
                    or "Einheit"
                ),
                "unit_plural": str(
                    item.get("unit_plural")
                    or medication.get("unit_plural")
                    or "Einheiten"
                ),
                "stock_before": decimal_json(before),
                "stock_after": medication["stock"],
            }
        )
    return changes


def select_actionable_slot(
    profile: Mapping[str, Any],
    now: datetime | None = None,
    *,
    medication_id: str | None = None,
) -> str | None:
    """Select an actionable slot, optionally containing one exact medication."""

    if not cycle_is_active(profile):
        return None
    current = _local_now(now)
    begun: list[tuple[datetime, int, str]] = []
    early: list[tuple[datetime, int, str]] = []
    slots = profile.get("runtime", {}).get("slots", {})
    for index, slot in enumerate(SLOTS):
        item = slots.get(slot)
        if not item or item.get("status") not in OPEN_SLOT_STATUSES:
            continue
        if medication_id and not any(
            entry.get("medication_id") == medication_id
            for entry in item.get("items", [])
        ):
            continue
        due = datetime.fromisoformat(str(item["due_at"]))
        if due <= current:
            begun.append((due, index, slot))
        elif slot_is_bookable(profile, slot, current):
            early.append((due, index, slot))
    if begun:
        begun.sort(key=lambda item: (item[0], item[1]))
        return begun[-1][2]
    if early:
        early.sort(key=lambda item: (item[0], item[1]))
        return early[0][2]
    return None


def next_regular_slot_for_medication(
    profile: Mapping[str, Any],
    medication_id: str,
    now: datetime | None = None,
) -> dict[str, str] | None:
    """Return the next still-open regular slot for one medication."""

    current = _local_now(now)
    candidates: list[tuple[datetime, int, str]] = []
    for index, slot in enumerate(SLOTS):
        item = profile.get("runtime", {}).get("slots", {}).get(slot)
        if not item or item.get("status") not in OPEN_SLOT_STATUSES:
            continue
        if not any(
            entry.get("medication_id") == medication_id
            for entry in item.get("items", [])
        ):
            continue
        try:
            due = datetime.fromisoformat(str(item.get("due_at")))
        except (TypeError, ValueError):
            continue
        if due > current:
            candidates.append((due, index, slot))
    if not candidates:
        return None
    due, _, slot = min(candidates, key=lambda item: (item[0], item[1]))
    return {"slot": slot, "due_at": due.isoformat()}


def _finish_slot(
    profile: dict[str, Any], slot_data: dict[str, Any], status: str, current: datetime
) -> None:
    """Apply every terminal invariant in one place."""

    slot_data["status"] = status
    slot_data["snoozed_until"] = None
    slot_data["next_reminder_at"] = None
    slot_data["last_notification_at"] = None
    slot_data["notification_reservation_id"] = None
    slot_data["notification_reserved_at"] = None
    slot_data["notification_state"] = "cleanup_pending"
    slot_data["action_consumed_at"] = current.isoformat()
    runtime = profile["runtime"]
    planned = [runtime["slots"][name] for name in SLOTS if name in runtime["slots"]]
    if (
        planned
        and all(item.get("status") in TERMINAL_SLOT_STATUSES for item in planned)
        and planned[-1].get("status") == "taken"
    ):
        runtime["cycle_completed"] = True
        runtime["cycle_completed_at"] = current.isoformat()
    else:
        runtime["cycle_completed"] = False
        runtime["cycle_completed_at"] = None


def _validate_slot_action(
    profile: Mapping[str, Any],
    slot_data: Mapping[str, Any],
    *,
    expected_cycle_id: str | None,
    action_token: str | None,
) -> None:
    """Reject stale or replayed notification actions inside the mutation."""

    if expected_cycle_id is None and action_token is None:
        return
    runtime = profile.get("runtime", {})
    if (
        not expected_cycle_id
        or not action_token
        or runtime.get("cycle_id") != expected_cycle_id
        or slot_data.get("cycle_id") != expected_cycle_id
        or slot_data.get("action_token") != action_token
        or slot_data.get("action_consumed_at")
    ):
        raise NoDueIntakeError(
            "Diese Benachrichtigungsaktion ist veraltet oder wurde bereits verwendet."
        )


def confirm_slot(
    store: dict[str, Any],
    person_id: str,
    slot: str | None = None,
    *,
    actor: str | None = None,
    source: str = "dashboard",
    now: datetime | None = None,
    allow_early: bool = False,
    expected_cycle_id: str | None = None,
    action_token: str | None = None,
) -> dict[str, Any]:
    """Confirm one person's due slot atomically."""

    current = _local_now(now)
    profile = get_profile(store, person_id)
    if not cycle_is_active(profile):
        raise InactiveCycleError("Der Tages-Zyklus ist noch nicht gestartet.")
    slots = profile["runtime"]["slots"]
    if slot is None:
        slot = select_actionable_slot(profile, current)
        if slot is None:
            raise NoDueIntakeError("Keine fällige Einnahme vorhanden.")
    if slot not in SLOTS or slot not in slots:
        raise NoDueIntakeError("Für diesen Zeitpunkt ist keine Einnahme geplant.")
    slot_data = slots[slot]
    if (
        slot_data.get("status") == "taken"
        and expected_cycle_id
        and action_token
        and profile["runtime"].get("cycle_id") == expected_cycle_id
        and slot_data.get("cycle_id") == expected_cycle_id
        and slot_data.get("action_token") == action_token
    ):
        return {"status": "already_taken", "slot": slot, "changes": []}
    _validate_slot_action(
        profile,
        slot_data,
        expected_cycle_id=expected_cycle_id,
        action_token=action_token,
    )
    if slot_data.get("status") == "taken":
        return {"status": "already_taken", "slot": slot, "changes": []}
    if slot_data.get("status") in {"skipped", "missed"}:
        raise NoDueIntakeError("Diese Einnahme ist bereits abgeschlossen.")
    due = datetime.fromisoformat(slot_data["due_at"])
    early = int(profile.get("settings", {}).get("early_minutes", 30))
    if not allow_early and current < due - timedelta(minutes=early):
        raise NoDueIntakeError("Diese Einnahme ist noch nicht fällig.")
    changes = _decrement_slot_medications(profile, slot_data)
    _finish_slot(profile, slot_data, "taken", current)
    slot_data["confirmed_at"] = current.isoformat()
    slot_data["source"] = source
    _sync_cycle_history(profile)
    add_event(
        profile,
        "regular_taken",
        now=current,
        cycle_id=profile["runtime"].get("cycle_id"),
        cycle_date=profile["runtime"].get("cycle_date"),
        slot=slot,
        source=source,
        actor=actor or "",
        medications=changes,
    )
    med_text = ", ".join(
        f"{change['name']} – {change['quantity']} "
        f"{change['unit_singular'] if decimal_value(change['quantity']) == 1 else change['unit_plural']}"
        for change in changes
    )
    append_log(
        profile,
        f"{profile['name']}: Einnahme {_slot_label(slot)} bestätigt: {med_text} "
        f"({_visible_source(source)}).",
        source="intake",
        actor=actor,
        now=current,
    )
    return {
        "status": "taken",
        "slot": slot,
        "source": source,
        "changes": deepcopy(changes),
    }


def snooze_slot(
    store: dict[str, Any],
    person_id: str,
    slot: str | None = None,
    minutes: int | None = None,
    *,
    actor: str | None = None,
    source: str = "dashboard",
    now: datetime | None = None,
    expected_cycle_id: str | None = None,
    action_token: str | None = None,
) -> dict[str, Any]:
    """Snooze one due slot."""

    current = _local_now(now)
    profile = get_profile(store, person_id)
    if not cycle_is_active(profile):
        raise InactiveCycleError("Der Tages-Zyklus ist noch nicht gestartet.")
    if slot is None:
        due = [name for name in SLOTS if slot_is_due(profile, name, current)]
        if not due:
            raise NoDueIntakeError("Keine fällige Einnahme vorhanden.")
        slot = due[0]
    if slot not in profile["runtime"]["slots"]:
        raise NoDueIntakeError("Für diesen Zeitpunkt ist keine Einnahme geplant.")
    slot_data = profile["runtime"]["slots"][slot]
    _validate_slot_action(
        profile,
        slot_data,
        expected_cycle_id=expected_cycle_id,
        action_token=action_token,
    )
    if slot_data.get("status") in TERMINAL_SLOT_STATUSES:
        raise NoDueIntakeError("Diese Einnahme ist bereits abgeschlossen.")
    if slot_data.get("cycle_id") != profile["runtime"].get("cycle_id"):
        raise NoDueIntakeError(
            "Diese Einnahme gehört nicht zum aktuellen Tages-Zyklus."
        )
    existing_until = slot_data.get("snoozed_until")
    if slot_data.get("status") != "snoozed" and not slot_is_due(
        profile, slot, current
    ):
        raise NoDueIntakeError("Diese Einnahme ist noch nicht fällig.")
    duration = max(1, int(minutes or profile["settings"].get("snooze_minutes", 15)))
    base = current
    if existing_until:
        try:
            existing = datetime.fromisoformat(str(existing_until))
            if existing.tzinfo is None and current.tzinfo is not None:
                existing = existing.replace(tzinfo=current.tzinfo)
            elif existing.tzinfo is not None and current.tzinfo is None:
                existing = existing.replace(tzinfo=None)
            if existing > current:
                base = existing
        except (TypeError, ValueError):
            pass
    until = base + timedelta(minutes=duration)
    slot_data["status"] = "snoozed"
    slot_data["snoozed_until"] = until.isoformat()
    repeat = max(1, int(profile.get("settings", {}).get("repeat_minutes", 5)))
    slot_data["next_reminder_at"] = (until + timedelta(minutes=repeat)).isoformat()
    slot_data["last_notification_at"] = current.isoformat()
    slot_data["notification_state"] = "cleanup_pending"
    slot_data["notification_reservation_id"] = None
    slot_data["action_token"] = uuid4().hex
    slot_data["notification_action_target"] = None
    slot_data["source"] = source
    # The action that requested snooze is consumed, while the freshly rotated
    # token represents the next independently usable notification action.
    slot_data["action_consumed_at"] = None
    add_event(
        profile,
        "regular_snoozed",
        now=current,
        cycle_id=profile["runtime"].get("cycle_id"),
        cycle_date=profile["runtime"].get("cycle_date"),
        slot=slot,
        source=source,
        actor=actor or "",
        snoozed_until=until.isoformat(),
    )
    append_log(
        profile,
        f"{profile['name']}: {_slot_label(slot)} bis {until.strftime('%H:%M')} Uhr "
        f"zurückgestellt ({_visible_source(source)}).",
        source="intake",
        actor=actor,
        now=current,
    )
    return {
        "status": "snoozed",
        "slot": slot,
        "source": source,
        "snoozed_until": until.isoformat(),
    }


def skip_slot(
    store: dict[str, Any],
    person_id: str,
    slot: str | None = None,
    *,
    actor: str | None = None,
    source: str = "dashboard",
    now: datetime | None = None,
    expected_cycle_id: str | None = None,
    action_token: str | None = None,
) -> dict[str, Any]:
    """Skip one due slot without changing stock."""

    current = _local_now(now)
    profile = get_profile(store, person_id)
    if not cycle_is_active(profile):
        raise InactiveCycleError("Der Tages-Zyklus ist noch nicht gestartet.")
    if slot is None:
        due = [name for name in SLOTS if slot_is_due(profile, name, current)]
        if not due:
            raise NoDueIntakeError("Keine fällige Einnahme vorhanden.")
        slot = due[0]
    if slot not in profile["runtime"]["slots"]:
        raise NoDueIntakeError("Für diesen Zeitpunkt ist keine Einnahme geplant.")
    slot_data = profile["runtime"]["slots"][slot]
    _validate_slot_action(
        profile,
        slot_data,
        expected_cycle_id=expected_cycle_id,
        action_token=action_token,
    )
    if slot_data.get("status") == "skipped":
        return {"status": "already_skipped", "slot": slot}
    if slot_data.get("status") in {"taken", "missed"}:
        raise NoDueIntakeError("Diese Einnahme ist bereits abgeschlossen.")
    if not slot_is_bookable(profile, slot, current):
        raise NoDueIntakeError("Diese Einnahme ist noch nicht buchbar.")
    _finish_slot(profile, slot_data, "skipped", current)
    slot_data["skipped_at"] = current.isoformat()
    slot_data["source"] = source
    _sync_cycle_history(profile)
    add_event(
        profile,
        "regular_skipped",
        now=current,
        cycle_id=profile["runtime"].get("cycle_id"),
        cycle_date=profile["runtime"].get("cycle_date"),
        slot=slot,
        source=source,
        actor=actor or "",
        medications=deepcopy(slot_data.get("items", [])),
    )
    append_log(
        profile,
        f"{profile['name']}: Einnahme {_slot_label(slot)} übersprungen "
        f"({_visible_source(source)}).",
        source="intake",
        actor=actor,
        now=current,
    )
    return {"status": "skipped", "slot": slot, "source": source}


def mark_slot_missed(
    profile: dict[str, Any],
    slot: str,
    *,
    now: datetime | None = None,
    source: str = "schedule",
) -> dict[str, Any]:
    """Close one open slot as missed using the same terminal invariants."""

    current = _local_now(now)
    if not cycle_is_active(profile):
        raise InactiveCycleError("Der Tages-Zyklus ist noch nicht gestartet.")
    slot_data = profile.get("runtime", {}).get("slots", {}).get(slot)
    if not slot_data:
        raise NoDueIntakeError("Für diesen Zeitpunkt ist keine Einnahme geplant.")
    if slot_data.get("status") in TERMINAL_SLOT_STATUSES:
        return {"status": slot_data.get("status"), "slot": slot}
    _finish_slot(profile, slot_data, "missed", current)
    slot_data["missed_at"] = current.isoformat()
    slot_data["source"] = source
    _sync_cycle_history(profile)
    add_event(
        profile,
        "regular_missed",
        now=current,
        cycle_id=profile["runtime"].get("cycle_id"),
        cycle_date=profile["runtime"].get("cycle_date"),
        slot=slot,
        source=source,
        medications=deepcopy(slot_data.get("items", [])),
    )
    append_log(
        profile,
        f"{profile['name']}: {_slot_label(slot)} automatisch als verpasst abgeschlossen.",
        level="warning",
        source=source,
        now=current,
    )
    return {"status": "missed", "slot": slot}


def update_settings(
    store: dict[str, Any],
    person_id: str,
    updates: Mapping[str, Any],
    *,
    actor: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Update one person's settings."""

    profile = get_profile(store, person_id)
    if not isinstance(updates, Mapping):
        raise PillPalError("Einstellungsänderungen müssen ein Objekt sein.")
    before = deepcopy(profile.get("settings", {}))
    merged = deep_merge(before, updates)
    validated = normalize_settings(merged)
    profile["settings"] = validated
    profile.setdefault("runtime", {})["effective_due_at"] = {}
    rebuild_schedule(profile, now=now)
    append_log(
        profile,
        f"{profile['name']}: Einstellungen gespeichert.",
        source="settings",
        actor=actor,
        now=now,
    )
    _append_change_log(
        profile,
        before,
        validated,
        _SETTING_CHANGE_LABELS,
        subject="Einstellung",
        source="settings",
        actor=actor,
        now=now,
        ignored=set(),
    )
    return deepcopy(profile["settings"])


def helper_owners(store: Mapping[str, Any], key: str) -> dict[str, str]:
    """Return helper entity -> person id for nonempty assignments."""

    owners: dict[str, str] = {}
    for person_id, profile in store.get("profiles", {}).items():
        if profile.get("archived"):
            continue
        entity_id = str(profile.get("settings", {}).get(key, "")).strip()
        if entity_id:
            owners[entity_id] = person_id
    return owners


def medication_button_owners(store: Mapping[str, Any]) -> dict[str, str]:
    """Return input-button helper owners across all medication records."""

    owners: dict[str, str] = {}
    for person_id, profile in store.get("profiles", {}).items():
        if profile.get("archived"):
            continue
        for medication in profile.get("medications", {}).values():
            entity_id = str(medication.get("button_helper", "")).strip()
            if entity_id:
                owners[entity_id] = person_id
    return owners


def validate_button_helper_assignment(
    store: Mapping[str, Any], person_id: str, entity_id: str
) -> None:
    """Reject a physical input button used by another person or function."""

    if not entity_id:
        return
    owner = helper_owners(store, "confirm_helper").get(entity_id)
    owner = owner or medication_button_owners(store).get(entity_id)
    if owner is not None and owner != person_id:
        raise DuplicateHelperError(
            f"{entity_id} ist bereits einem anderen Personenprofil zugeordnet."
        )


def validate_helper_assignment(
    store: Mapping[str, Any], person_id: str, key: str, entity_id: str
) -> None:
    """Reject one helper being used for two persons."""

    if not entity_id:
        return
    owner = helper_owners(store, key).get(entity_id)
    if owner is not None and owner != person_id:
        raise DuplicateHelperError(
            f"{entity_id} ist bereits einem anderen Personenprofil zugeordnet."
        )


def active_medications(profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return non-archived medications sorted by name."""

    return sorted(
        [deepcopy(med) for med in profile.get("medications", {}).values() if not med.get("archived")],
        key=lambda item: item.get("name", "").casefold(),
    )


def regular_medications(profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return active medications with at least one regular dose."""

    return [med for med in active_medications(profile) if medication_is_regular(med)]


def as_needed_medications(profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return every active medication explicitly enabled for PRN use."""

    return [
        med
        for med in active_medications(profile)
        if med.get("as_needed_allowed")
    ]


def daily_regular_amount(medication: Mapping[str, Any]) -> Decimal:
    """Return total regular daily consumption."""

    return sum(
        (decimal_value(medication.get("doses", {}).get(slot, 0)) for slot in SLOTS),
        Decimal("0"),
    )


def medication_summary(medication: Mapping[str, Any]) -> dict[str, Any]:
    """Return presentation/automation values for one medication."""

    daily = daily_regular_amount(medication)
    stock = decimal_value(medication.get("stock", 0))
    days = int(stock // daily) if daily > 0 else None
    return {
        **deepcopy(dict(medication)),
        "regular": medication_is_regular(medication),
        "days_remaining": days,
    }


def _practice_closed_reason(
    profile: Mapping[str, Any],
    day_value: date,
    *,
    today: date | None = None,
) -> str | None:
    """Return the machine-readable reason why the practice is closed."""

    if day_value.weekday() >= 5:
        return "weekend"
    for closure in profile.get("practice_closures", []):
        try:
            start = date.fromisoformat(str(closure.get("start", "")))
            end = date.fromisoformat(str(closure.get("end", "")))
        except (AttributeError, ValueError):
            continue
        if start <= day_value <= end:
            return "practice_closure"
    runtime = profile.get("runtime", {})
    forecast = runtime.get("holiday_calendar_forecast", {})
    closed_dates = forecast.get("closed_dates", []) if isinstance(forecast, Mapping) else []
    if day_value.isoformat() in closed_dates:
        return "holiday"
    if today == day_value and bool(runtime.get("holiday_calendar_active")):
        return "holiday"
    return None


def _contiguous_closed_interval(
    profile: Mapping[str, Any], day_value: date, *, today: date
) -> tuple[date | None, date | None, list[str]]:
    if _practice_closed_reason(profile, day_value, today=today) is None:
        return None, None, []
    interval_start = day_value
    interval_end = day_value
    reasons: list[str] = []
    cursor = day_value
    for _ in range(740):
        reason = _practice_closed_reason(profile, cursor, today=today)
        if reason is None:
            break
        if reason not in reasons:
            reasons.append(reason)
        interval_start = cursor
        cursor -= timedelta(days=1)
    cursor = day_value + timedelta(days=1)
    for _ in range(740):
        reason = _practice_closed_reason(profile, cursor, today=today)
        if reason is None:
            break
        if reason not in reasons:
            reasons.append(reason)
        interval_end = cursor
        cursor += timedelta(days=1)
    return interval_start, interval_end, reasons


def _count_open_practice_days(
    profile: Mapping[str, Any], start: date | None, end: date, *, today: date
) -> int:
    if start is None or start > end:
        return 0
    count = 0
    cursor = start
    while cursor <= end:
        if _practice_closed_reason(profile, cursor, today=today) is None:
            count += 1
        cursor += timedelta(days=1)
    return count


def _next_open_practice_day(
    profile: Mapping[str, Any], start: date, *, today: date
) -> date | None:
    cursor = start
    for _ in range(1460):
        if _practice_closed_reason(profile, cursor, today=today) is None:
            return cursor
        cursor += timedelta(days=1)
    return None


def _subtract_open_practice_days(
    profile: Mapping[str, Any], boundary: date, count: int, *, today: date
) -> date:
    remaining = max(0, count)
    cursor = boundary - timedelta(days=1)
    for _ in range(1460):
        if remaining <= 0:
            break
        if _practice_closed_reason(profile, cursor, today=today) is None:
            remaining -= 1
        if remaining > 0:
            cursor -= timedelta(days=1)
    return cursor


def order_date_with_practice_calendar(
    profile: Mapping[str, Any],
    normal_date: date,
    empty_date: date,
    lead_days: int,
    *,
    today: date,
) -> dict[str, Any]:
    """Adjust an order date across weekends, closures and future holidays."""

    reason = _practice_closed_reason(profile, normal_date, today=today)
    if reason is None:
        return {
            "effective_date": normal_date,
            "reason": "normal_threshold",
            "closed_from": None,
            "closed_to": None,
            "next_open_date": normal_date,
            "open_days_after_closure": None,
            "closed_reasons": [],
        }
    closed_from, closed_to, reasons = _contiguous_closed_interval(
        profile, normal_date, today=today
    )
    assert closed_from is not None and closed_to is not None
    next_open = _next_open_practice_day(
        profile, closed_to + timedelta(days=1), today=today
    )
    open_after = _count_open_practice_days(
        profile, next_open, empty_date - timedelta(days=1), today=today
    )
    if open_after >= max(0, lead_days):
        effective = normal_date
        result_reason = "practice_closure_noted"
    else:
        effective = _subtract_open_practice_days(
            profile, closed_from, lead_days, today=today
        )
        result_reason = "practice_closure_advanced"
    return {
        "effective_date": effective,
        "reason": result_reason,
        "closed_from": closed_from,
        "closed_to": closed_to,
        "next_open_date": next_open,
        "open_days_after_closure": open_after,
        "closed_reasons": reasons,
    }


def order_plan(profile: Mapping[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Return the complete, machine-readable order projection for one person."""

    current = _local_now(now)
    today = current.date()
    settings = deep_merge(DEFAULT_SETTINGS, profile.get("settings", {}))
    warning_days = int(settings.get("order_warning_days", 10))
    lead_days = int(settings.get("practice_lead_days", 5))
    co_order_days = int(settings.get("low_stock_window_days", 7))
    projections: list[dict[str, Any]] = []
    for medication in regular_medications(profile):
        daily = daily_regular_amount(medication)
        if daily <= 0:
            continue
        stock = decimal_value(medication.get("stock", 0))
        days_remaining = int(stock // daily)
        empty_date = today + timedelta(days=days_remaining)
        normal_date = empty_date - timedelta(days=warning_days)
        timing = order_date_with_practice_calendar(
            profile, normal_date, empty_date, lead_days, today=today
        )
        effective_date = timing["effective_date"]
        projections.append(
            {
                "medication_id": medication["id"],
                "name": medication.get("name", medication["id"]),
                "unit_singular": medication.get("unit_singular", "Einheit"),
                "unit_plural": medication.get("unit_plural", "Einheiten"),
                "current_stock": decimal_json(stock),
                "daily_dose": decimal_json(daily),
                "days_remaining": days_remaining,
                "projected_empty_date": empty_date.isoformat(),
                "normal_order_date": normal_date.isoformat(),
                "effective_order_date": effective_date.isoformat(),
                "normal_order_warning_date": normal_date.isoformat(),
                "effective_order_warning_date": effective_date.isoformat(),
                "due": today >= effective_date,
                "reason": timing["reason"],
                "order_warning_reason": timing["reason"],
                "closed_from": (
                    timing["closed_from"].isoformat() if timing["closed_from"] else None
                ),
                "closed_to": (
                    timing["closed_to"].isoformat() if timing["closed_to"] else None
                ),
                "next_open_date": (
                    timing["next_open_date"].isoformat()
                    if timing["next_open_date"]
                    else None
                ),
                "open_days_after_closure": timing["open_days_after_closure"],
                "closed_reasons": deepcopy(timing["closed_reasons"]),
                "pack_size": decimal_json(medication.get("pack_size", 0)),
                "pack_cost": decimal_json(medication.get("cost", 0)),
            }
        )
    projections.sort(key=lambda item: (item["projected_empty_date"], item["name"].casefold()))
    due_anchors = [item for item in projections if item["due"]]
    included_ids: set[str] = set()
    for anchor in due_anchors:
        cutoff = date.fromisoformat(anchor["projected_empty_date"]) + timedelta(
            days=co_order_days
        )
        included_ids.update(
            item["medication_id"]
            for item in projections
            if date.fromisoformat(item["projected_empty_date"]) <= cutoff
        )
    items: list[dict[str, Any]] = []
    for projection in projections:
        if projection["medication_id"] not in included_ids:
            continue
        item = deepcopy(projection)
        item["status"] = "order_now" if item["due"] else "co_order"
        item["status_label"] = (
            "Jetzt bestellen" if item["due"] else "Mitbestellen empfohlen"
        )
        items.append(item)
    items.sort(
        key=lambda item: (
            0 if item["status"] == "order_now" else 1,
            item["projected_empty_date"],
            item["name"].casefold(),
        )
    )
    known_costs = [
        decimal_value(item["pack_cost"])
        for item in items
        if decimal_value(item["pack_cost"]) > 0
    ]
    cost_total = sum(known_costs, Decimal("0"))
    cost_status = (
        "none"
        if not known_costs
        else "complete"
        if len(known_costs) == len(items)
        else "incomplete"
    )
    clipboard_lines = []
    for item in items:
        pack_size = decimal_value(item["pack_size"])
        clipboard_lines.append(
            f"{decimal_json(pack_size)} {item['name']}" if pack_size > 0 else item["name"]
        )
    currency = str(settings.get("currency", "€"))
    cost_text = (
        ""
        if cost_status == "none"
        else f"Kosten bzw. Zuzahlung: ca. {decimal_json(cost_total)} {currency}"
        if cost_status == "complete"
        else "Kosten bzw. Zuzahlung konnten nicht vollständig ermittelt werden."
    )
    return {
        "active": bool(due_anchors),
        "due_count": len(due_anchors),
        "item_count": len(items),
        "items": items,
        "projections": projections,
        "clipboard_text": "\n".join(clipboard_lines),
        "cost_total": decimal_json(cost_total),
        "cost_status": cost_status,
        "cost_text": cost_text,
        "currency": currency,
    }


def expiry_plan(profile: Mapping[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Return MHD warnings with exact dates and relative day information."""

    current = _local_now(now)
    today = current.date()
    warning_days = int(
        deep_merge(DEFAULT_SETTINGS, profile.get("settings", {})).get(
            "expiry_warning_days", 14
        )
    )
    limit = today + timedelta(days=warning_days)
    items: list[dict[str, Any]] = []
    for medication in active_medications(profile):
        raw_date = str(medication.get("expiry_date", ""))
        if not medication.get("expiry_enabled") or not raw_date:
            continue
        try:
            expiry_date = date.fromisoformat(raw_date)
        except ValueError:
            continue
        if expiry_date > limit:
            continue
        items.append(
            {
                "medication_id": medication["id"],
                "name": medication.get("name", medication["id"]),
                "expiry_date": expiry_date.isoformat(),
                "days_until_expiry": (expiry_date - today).days,
            }
        )
    items.sort(key=lambda item: (item["expiry_date"], item["name"].casefold()))
    return {"active": bool(items), "item_count": len(items), "items": items}


def current_and_upcoming(profile: Mapping[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Return compact past/early/due/upcoming schedule groups."""

    current = _local_now(now)
    past: list[dict[str, Any]] = []
    upcoming: list[dict[str, Any]] = []
    due: list[dict[str, Any]] = []
    early: list[dict[str, Any]] = []
    for slot in SLOTS:
        item = deepcopy(profile.get("runtime", {}).get("slots", {}).get(slot))
        if not item:
            continue
        item["slot"] = slot
        detail = slot_detail(profile, slot, current)
        item.update(
            {
                "status_label": detail["status_label"],
                "bookable_at": detail["bookable_at"],
                "is_due": detail["is_due"],
                "is_bookable": detail["is_bookable"],
            }
        )
        status = item.get("status")
        if status in {"taken", "skipped", "missed"}:
            past.append(item)
        elif item.get("snoozed_until") and current < datetime.fromisoformat(
            str(item["snoozed_until"])
        ):
            # Snooze changes the visible reminder grouping, but the backend may
            # still accept an actual intake from the person's physical helper.
            upcoming.append(item)
        elif item["is_due"]:
            due.append(item)
        elif item["is_bookable"]:
            early.append(item)
        else:
            upcoming.append(item)
    return {"past": past, "early": early, "due": due, "upcoming": upcoming}


_SLOT_STATUS_LABELS = {
    "planned": "Geplant",
    "notified": "Benachrichtigt",
    "snoozed": "Zurückgestellt",
    "taken": "Eingenommen",
    "skipped": "Übersprungen",
    "missed": "Verpasst",
}


def slot_detail(
    profile: Mapping[str, Any], slot: str, now: datetime | None = None
) -> dict[str, Any]:
    """Return the complete public state for one person/cycle/slot tuple."""

    current = _local_now(now)
    runtime = profile.get("runtime", {})
    item = deepcopy(runtime.get("slots", {}).get(slot, {}))
    due_at = item.get("due_at")
    due = datetime.fromisoformat(str(due_at)) if due_at else None
    early_minutes = max(0, int(profile.get("settings", {}).get("early_minutes", 30)))
    bookable_at = due - timedelta(minutes=early_minutes) if due else None
    medications: list[dict[str, Any]] = []
    medication_store = profile.get("medications", {})
    for raw in item.get("items", []):
        medication_id = str(raw.get("medication_id", ""))
        medication = medication_store.get(medication_id, {})
        amount = decimal_json(raw.get("quantity", 0))
        unit_singular = str(
            raw.get("unit_singular")
            or medication.get("unit_singular")
            or "Einheit"
        )
        unit_plural = str(
            raw.get("unit_plural")
            or medication.get("unit_plural")
            or "Einheiten"
        )
        unit = unit_singular if decimal_value(amount) == 1 else unit_plural
        medications.append(
            {
                "medication_id": medication_id,
                "name": raw.get("name") or medication.get("name") or medication_id,
                "amount": amount,
                "unit": unit,
                "amount_display": f"{decimal_json(amount)} {unit}",
            }
        )
    status = str(item.get("status") or "not_planned")
    completed_at = (
        item.get("confirmed_at")
        or item.get("skipped_at")
        or item.get("missed_at")
        or (item.get("action_consumed_at") if status in TERMINAL_SLOT_STATUSES else None)
    )
    return {
        "person_id": profile.get("person_id"),
        "person": profile.get("name"),
        "cycle_id": runtime.get("cycle_id"),
        "cycle_date": runtime.get("cycle_date"),
        "cycle_state": runtime.get("cycle_state"),
        "slot": slot,
        "slot_label": SLOT_LABELS.get(slot, slot),
        "planned": bool(item),
        "status": status,
        "status_label": _SLOT_STATUS_LABELS.get(status, "Nicht geplant"),
        "due_at": due_at,
        "bookable_at": bookable_at.isoformat() if bookable_at else None,
        "snoozed_until": item.get("snoozed_until"),
        "completed_at": completed_at,
        "is_due": slot_is_due(profile, slot, current) if item else False,
        "is_bookable": slot_is_bookable(profile, slot, current) if item else False,
        "medications": medications,
        "medication_text": ", ".join(
            f"{medication['name']}: {medication['amount_display']}"
            for medication in medications
        ),
    }


def _statistics_event_matches(
    event: Mapping[str, Any], medication_id: str | None, slot: str | None
) -> bool:
    event_type = str(event.get("type", ""))
    is_prn = event_type == "as_needed"
    if slot == "as_needed" and not is_prn:
        return False
    if slot and slot != "as_needed" and (is_prn or event.get("slot") != slot):
        return False
    if not medication_id:
        return event_type in {
            "regular_taken",
            "regular_skipped",
            "regular_missed",
            "as_needed",
        }
    if is_prn:
        return event.get("medication_id") == medication_id
    return any(
        isinstance(item, Mapping) and item.get("medication_id") == medication_id
        for item in event.get("medications", [])
    )


def _statistics_slot_matches(
    item: Mapping[str, Any], medication_id: str | None, slot: str | None
) -> bool:
    if slot == "as_needed":
        return False
    if slot and item.get("slot") != slot:
        return False
    if not medication_id:
        return True
    return any(
        isinstance(medication, Mapping)
        and medication.get("medication_id") == medication_id
        for medication in item.get("medications", [])
    )


def statistics(
    profile: Mapping[str, Any],
    days: int = 7,
    now: datetime | None = None,
    *,
    medication_id: str | None = None,
    slot: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    selected_day: date | None = None,
) -> dict[str, Any]:
    """Calculate filterable person-scoped totals, heatmap and day details."""

    current = _local_now(now)
    period_end = min(end_date or current.date(), current.date())
    period_start = start_date or period_end - timedelta(days=max(1, days) - 1)
    if period_start > period_end:
        period_start, period_end = period_end, period_start
    selected = selected_day or period_end
    if selected < period_start or selected > period_end:
        selected = period_end
    events = []
    for event in profile.get("events", []):
        try:
            event_date = date.fromisoformat(str(event.get("date")))
        except (TypeError, ValueError):
            continue
        if not period_start <= event_date <= period_end:
            continue
        if _statistics_event_matches(event, medication_id, slot):
            events.append(deepcopy(event))
    slots_by_day: dict[str, dict[str, dict[str, Any]]] = {}
    history = profile.get("history", {})
    daily_history = history.get("daily", {}) if isinstance(history, Mapping) else {}
    if isinstance(daily_history, Mapping):
        for day_key, raw_day in daily_history.items():
            try:
                history_date = date.fromisoformat(str(day_key))
            except ValueError:
                continue
            if not period_start <= history_date <= period_end or not isinstance(raw_day, Mapping):
                continue
            raw_slots = raw_day.get("slots", {})
            if not isinstance(raw_slots, Mapping):
                continue
            slots_by_day[str(day_key)] = {
                str(slot_id): deepcopy(dict(item))
                for slot_id, item in raw_slots.items()
                if isinstance(item, Mapping)
            }
    # Schema 7 and direct-test events have no daily plan snapshot. Preserve only
    # their terminal facts; never reconstruct a past plan from current doses.
    type_status = {
        "regular_taken": "taken",
        "regular_skipped": "skipped",
        "regular_missed": "missed",
    }
    for event in events:
        status = type_status.get(str(event.get("type", "")))
        event_slot = str(event.get("slot", ""))
        day_key = str(event.get("cycle_date") or event.get("date") or "")
        if status is None or event_slot not in SLOTS:
            continue
        slots = slots_by_day.setdefault(day_key, {})
        cycle_id = str(event.get("cycle_id") or f"legacy_{event.get('id', day_key)}")
        slot_id = f"{cycle_id}:{event_slot}"
        slots.setdefault(
            slot_id,
            {
                "slot_id": slot_id,
                "cycle_id": cycle_id,
                "cycle_date": day_key,
                "slot": event_slot,
                "due_at": event.get("due_at"),
                "status": status,
                "completed_at": event.get("timestamp"),
                "source": event.get("source"),
                "medications": _history_medications(event.get("medications", [])),
                "snapshot_source": "legacy_terminal_event",
            },
        )
    heatmap: list[dict[str, Any]] = []
    daily: dict[str, dict[str, Any]] = {}
    cursor = period_start
    while cursor <= period_end:
        key = cursor.isoformat()
        day_slots = [
            deepcopy(item)
            for item in slots_by_day.get(key, {}).values()
            if _statistics_slot_matches(item, medication_id, slot)
        ]
        day_slots.sort(key=lambda item: (str(item.get("due_at") or ""), str(item.get("slot"))))
        taken = sum(item.get("status") == "taken" for item in day_slots)
        skipped = sum(item.get("status") == "skipped" for item in day_slots)
        missed = sum(item.get("status") == "missed" for item in day_slots)
        pending = len(day_slots) - taken - skipped - missed
        prn = [
            event
            for event in events
            if event.get("date") == key and event.get("type") == "as_needed"
        ]
        planned = len(day_slots)
        # The day colour describes the overall result, not the most severe
        # individual slot. A mixed day therefore stays "partial" even when
        # one slot was missed or skipped. "not_occurred" is reserved for a
        # planned day without any completed regular intake.
        heatmap_status = (
            "complete"
            if planned and taken == planned
            else "partial"
            if planned and taken
            else "not_occurred"
            if planned
            else "manual_only"
            if prn
            else "no_data"
        )
        bookings: list[dict[str, Any]] = []
        status_type = {
            "taken": "regular_taken",
            "skipped": "regular_skipped",
            "missed": "regular_missed",
        }
        for item in day_slots:
            status = str(item.get("status") or "planned")
            medications = [
                medication
                for medication in item.get("medications", [])
                if not medication_id
                or medication.get("medication_id") == medication_id
            ]
            bookings.append(
                {
                    **item,
                    "type": status_type.get(status, "regular_planned"),
                    "timestamp": item.get("completed_at") or item.get("due_at"),
                    "medications": medications,
                }
            )
        bookings.extend(deepcopy(prn))
        bookings.sort(key=lambda item: str(item.get("timestamp") or ""))
        additional_as_needed = bool(prn and planned and slot is None)
        values = {
            "date": key,
            "planned": planned,
            "taken": taken,
            "skipped": skipped,
            "missed": missed,
            "pending": pending,
            "as_needed_bookings": len(prn),
            "as_needed_quantity": decimal_json(
                sum(
                    (decimal_value(event.get("quantity", 0)) for event in prn),
                    Decimal("0"),
                )
            ),
            "heatmap_status": (
                "current" if cursor == current.date() and pending else heatmap_status
            ),
            "additional_as_needed": additional_as_needed,
        }
        daily[key] = {**values, "slots": day_slots, "events": bookings}
        heatmap.append(values)
        cursor += timedelta(days=1)
    taken_total = sum(item["taken"] for item in daily.values())
    skipped_total = sum(item["skipped"] for item in daily.values())
    missed_total = sum(item["missed"] for item in daily.values())
    pending_total = sum(item["pending"] for item in daily.values())
    planned_total = sum(item["planned"] for item in daily.values())
    prn_events = [event for event in events if event.get("type") == "as_needed"]
    include_archived = bool(
        profile.get("settings", {}).get("statistics_show_archived", False)
    )
    medication_options = sorted(
        [
            {
                "medication_id": med["id"],
                "name": med.get("name", med["id"]),
                "archived": bool(med.get("archived")),
            }
            for med in profile.get("medications", {}).values()
            if include_archived or not med.get("archived")
        ],
        key=lambda item: str(item["name"]).casefold(),
    )
    return {
        "days": (period_end - period_start).days + 1,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "selected_day": selected.isoformat(),
        "medication_id": medication_id,
        "slot": slot,
        "planned": planned_total,
        "taken": taken_total,
        "skipped": skipped_total,
        "missed": missed_total,
        "pending": pending_total,
        "as_needed_bookings": len(prn_events),
        "as_needed_quantity": decimal_json(
            sum(
                (decimal_value(event.get("quantity", 0)) for event in prn_events),
                Decimal("0"),
            )
        ),
        "adherence": round((taken_total / planned_total * 100), 1)
        if planned_total
        else 0.0,
        "heatmap": heatmap,
        "day_details": daily.get(selected.isoformat(), {"date": selected.isoformat(), "events": []}),
        "available_medications": medication_options,
        "available_slots": [
            {"value": None, "label": "Alle Einnahmezeiten"},
            *[
                {"value": slot_name, "label": SLOT_LABELS[slot_name]}
                for slot_name in SLOTS
            ],
            {"value": "as_needed", "label": "Bedarf"},
        ],
    }


def normalize_practice_closures(
    closures: Iterable[Mapping[str, Any]], today: date | None = None
) -> list[dict[str, str]]:
    """Normalize closures and discard entries that already ended."""

    current_date = today or date.today()
    normalized: list[dict[str, str]] = []
    for item in closures:
        start_raw = str(item.get("start", "")).strip()
        end_raw = str(item.get("end", "")).strip() or start_raw
        try:
            start_date = date.fromisoformat(start_raw)
            end_date = date.fromisoformat(end_raw)
        except ValueError:
            continue
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        if end_date < current_date:
            continue
        normalized.append({"start": start_date.isoformat(), "end": end_date.isoformat()})
    return sorted(normalized, key=lambda item: (item["start"], item["end"]))


def practice_status(profile: Mapping[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Return the current practice state from weekends, calendars and closures."""

    current = _local_now(now)
    today = current.date()
    runtime = profile.get("runtime", {})
    forecast = runtime.get("holiday_calendar_forecast", {})
    reason_code = _practice_closed_reason(profile, today, today=today)
    reason_labels = {
        "weekend": "Wochenende",
        "practice_closure": "hinterlegte Praxisschließung",
        "holiday": str(runtime.get("holiday_calendar_message") or "Feiertag"),
    }
    reason = reason_labels.get(reason_code, "")
    if reason_code:
        _closed_from, closed_to, _reasons = _contiguous_closed_interval(
            profile, today, today=today
        )
        next_open = _next_open_practice_day(
            profile, (closed_to or today) + timedelta(days=1), today=today
        )
    else:
        next_open = _next_open_practice_day(
            profile, today + timedelta(days=1), today=today
        )
    calendar_entity = str(profile.get("settings", {}).get("holiday_calendar", ""))
    return {
        "open": not bool(reason),
        "reason": reason_code,
        "title": "Praxis heute regulär geöffnet" if not reason else "Praxis heute geschlossen",
        "detail": (
            "Kein Wochenende, keine Schließzeit und kein Feiertag."
            if not reason and calendar_entity
            else "Kein Wochenende und keine Schließzeit; kein Feiertagskalender verbunden."
            if not reason
            else f"Grund: {reason}."
        ),
        "next_open_date": next_open.isoformat() if next_open else None,
        "next_open_weekday": (
            (
                "Montag",
                "Dienstag",
                "Mittwoch",
                "Donnerstag",
                "Freitag",
                "Samstag",
                "Sonntag",
            )[next_open.weekday()]
            if next_open
            else None
        ),
        "holiday_calendar_entity": calendar_entity or None,
        "holiday_calendar_fetched_on": (
            forecast.get("fetched_on") if isinstance(forecast, Mapping) else None
        ),
        "holiday_calendar_event_count": (
            forecast.get("event_count", 0) if isinstance(forecast, Mapping) else 0
        ),
        "holiday_calendar_error": (
            forecast.get("last_error") if isinstance(forecast, Mapping) else None
        ),
    }


def reminder_configuration_warning(
    profile: Mapping[str, Any],
    *,
    notify_target_valid: bool | None = None,
    due_output_available: bool = True,
) -> str | None:
    """Describe a missing reminder path while accepting the native due output."""

    if not regular_medications(profile):
        return None
    settings = profile.get("settings", {})
    target = str(settings.get("notify_target", "")).strip()
    if notify_target_valid is None:
        # The pure model cannot inspect Home Assistant's live service registry.
        # Runtime callers pass the authoritative result; standalone snapshots
        # conservatively accept a configured target and the native due entity.
        notify_target_valid = bool(target)
    if notify_target_valid or due_output_available:
        return None
    if target:
        return (
            f"Das konfigurierte Notify-Ziel {target} ist kein aufrufbarer "
            "Notify-Dienst, und die Fälligkeitsentität ist deaktiviert. Bitte "
            "notify.mobile_app_… auswählen oder die Fälligkeitsentität aktivieren."
        )
    return (
        "Für regelmäßige Medikamente ist weder ein gültiges Notify-Ziel noch "
        "eine aktive Fälligkeitsentität eingerichtet."
    )


def snapshot(profile: Mapping[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Return a safe frontend/API snapshot for one profile only."""

    current = _local_now(now)
    result = deepcopy(dict(profile))
    result.pop("history", None)
    cutoff = current - timedelta(hours=DIAGNOSTIC_RETENTION_HOURS)
    result["log"] = [
        item
        for item in result.get("log", [])
        if _stored_datetime(item.get("timestamp"))
        and datetime.fromisoformat(str(item["timestamp"])) >= cutoff
    ]
    result["practice_closures"] = normalize_practice_closures(
        result.get("practice_closures", []), current.date()
    )
    runtime = result.get("runtime", {})
    runtime.pop("pending_max_dose_confirmations", None)
    for slot_data in runtime.get("slots", {}).values():
        slot_data.pop("action_token", None)
        slot_data.pop("notification_action_target", None)
        slot_data.pop("notification_reservation_id", None)
    result["schema"] = DATA_SCHEMA_VERSION
    result["medications"] = [medication_summary(med) for med in active_medications(profile)]
    result["regular_medications"] = [
        medication_summary(med) for med in regular_medications(profile)
    ]
    result["as_needed_medications"] = [
        medication_summary(med) for med in as_needed_medications(profile)
    ]
    result["archived_medications"] = sorted(
        [
            medication_summary(med)
            for med in profile.get("medications", {}).values()
            if med.get("archived")
        ],
        key=lambda item: item.get("name", "").casefold(),
    )
    result["schedule"] = current_and_upcoming(profile, current)
    for group in result["schedule"].values():
        for slot_data in group:
            slot_data.pop("action_token", None)
            slot_data.pop("notification_action_target", None)
            slot_data.pop("notification_reservation_id", None)
    result["statistics"] = statistics(profile, 7, current)
    result["practice_status"] = practice_status(profile, current)
    result["order_plan"] = order_plan(profile, current)
    result["expiry_plan"] = expiry_plan(profile, current)
    result["warning"] = reminder_configuration_warning(profile)
    return result
