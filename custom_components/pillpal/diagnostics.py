"""Diagnostics for Pill★Pal."""

from __future__ import annotations

from collections import Counter
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .manager import PillPalManager


def _profile_summary(profile: dict[str, Any]) -> dict[str, Any]:
    """Return useful structure without people, medication or message content."""

    medications = list(profile.get("medications", {}).values())
    runtime = profile.get("runtime", {})
    slots = runtime.get("slots", {})
    settings = profile.get("settings", {})
    history = profile.get("history", {})
    daily = history.get("daily", {}) if isinstance(history, dict) else {}
    return {
        "archived": bool(profile.get("archived")),
        "admin_assistance": bool(profile.get("admin_assistance")),
        "medication_counts": {
            "total": len(medications),
            "active": sum(not item.get("archived") for item in medications),
            "archived": sum(bool(item.get("archived")) for item in medications),
            "regular": sum(
                any(float(amount or 0) > 0 for amount in item.get("doses", {}).values())
                for item in medications
            ),
            "as_needed": sum(bool(item.get("as_needed_allowed")) for item in medications),
        },
        "configured_interfaces": {
            "notify": bool(settings.get("notify_target")),
            "confirm_helper": bool(settings.get("confirm_helper")),
            "awake_helper": bool(settings.get("awake_helper")),
            "next_alarm": bool(settings.get("next_alarm_entity")),
            "holiday_calendar": bool(settings.get("holiday_calendar")),
            "intake_calendar": bool(settings.get("intake_calendar")),
        },
        "runtime": {
            "cycle_state": runtime.get("cycle_state"),
            "cycle_completed": bool(runtime.get("cycle_completed")),
            "schedule_source_configured": bool(runtime.get("schedule_source")),
            "slot_statuses": {
                slot: item.get("status")
                for slot, item in slots.items()
                if isinstance(item, dict)
            },
            "last_action_status": runtime.get("last_action_result", {}).get("status"),
            "last_action": runtime.get("last_action_result", {}).get("action"),
            "last_action_error_code": runtime.get("last_action_result", {}).get(
                "error_code"
            ),
            "calendar_delivery_statuses": dict(
                Counter(
                    item.get("status", "unknown")
                    for item in runtime.get("intake_calendar_deliveries", {}).values()
                    if isinstance(item, dict)
                )
            ),
        },
        "history_days": len(daily) if isinstance(daily, dict) else 0,
        "event_types": dict(
            Counter(
                item.get("type", "unknown")
                for item in profile.get("events", [])
                if isinstance(item, dict)
            )
        ),
        "diagnostic_log": {
            "entries": len(profile.get("log", [])),
            "levels": dict(
                Counter(
                    item.get("level", "unknown")
                    for item in profile.get("log", [])
                    if isinstance(item, dict)
                )
            ),
            "sources": dict(
                Counter(
                    item.get("source", "unknown")
                    for item in profile.get("log", [])
                    if isinstance(item, dict)
                )
            ),
        },
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    manager: PillPalManager = entry.runtime_data
    storage_health = manager.data.get("storage_health", {"status": "healthy"})
    return {
        "entry": {
            "entry_id": entry.entry_id,
            "data_keys": sorted(str(key) for key in entry.data),
            "subentry_count": len(entry.subentries),
        },
        "subentries": [
            {
                "subentry_id": subentry.subentry_id,
                "subentry_type": subentry.subentry_type,
                "data_keys": sorted(str(key) for key in subentry.data),
            }
            for subentry in entry.subentries.values()
        ],
        "store": {
            "schema": manager.data.get("schema"),
            "storage_health": {
                "status": storage_health.get("status", "healthy"),
                "reason_count": storage_health.get("reason_count", 0),
            },
            "profile_count": len(manager.data.get("profiles", {})),
            "profiles": [
                _profile_summary(profile)
                for profile in manager.data.get("profiles", {}).values()
                if isinstance(profile, dict)
            ],
        },
    }
