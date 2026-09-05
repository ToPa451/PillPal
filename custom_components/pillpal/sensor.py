"""Sensors for Pill★Pal person profiles."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import PERSON_SUBENTRY_TYPE, SLOT_LABELS
from .entity import PillPalEntity
from .manager import PillPalManager
from .model import (
    current_and_upcoming,
    expiry_plan,
    order_plan,
    practice_status,
    slot_detail,
    statistics,
)

_SLOT_ICONS = {
    "morning": "mdi:weather-sunset-up",
    "noon": "mdi:white-balance-sunny",
    "evening": "mdi:weather-sunset-down",
    "night": "mdi:weather-night",
}

_STATISTICS_PERIODS = (
    (7, "7 Tage"),
    (30, "30 Tage"),
    (90, "90 Tage"),
    (365, "1 Jahr"),
)


class PillPalStatusSensor(PillPalEntity, SensorEntity):
    _attr_name = "Status"
    _attr_icon = "mdi:clipboard-pulse-outline"

    def __init__(self, manager, subentry) -> None:
        super().__init__(manager, subentry, "status")

    @property
    def native_value(self) -> str:
        return "Archiviert" if self.profile.get("archived") else "Aktiv"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        runtime = self.profile.get("runtime", {})
        return {
            "person_id": self.person_id,
            "person": self.profile.get("name"),
            "cycle_id": runtime.get("cycle_id"),
            "cycle_state": runtime.get("cycle_state"),
            "cycle_date": runtime.get("cycle_date"),
            "cycle_completed": runtime.get("cycle_completed", False),
            "schedule_source": runtime.get("schedule_source"),
        }


class PillPalNextIntakeSensor(PillPalEntity, SensorEntity):
    _attr_name = "Nächste Einnahme"
    _attr_icon = "mdi:clock-alert-outline"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, manager, subentry) -> None:
        super().__init__(manager, subentry, "next_intake")

    def _next(self) -> dict[str, Any] | None:
        groups = current_and_upcoming(self.profile)
        candidates = groups["due"] + groups["early"] + groups["upcoming"]
        return min(candidates, key=lambda item: item["due_at"]) if candidates else None

    @property
    def native_value(self) -> datetime | None:
        item = self._next()
        return datetime.fromisoformat(item["due_at"]) if item else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        item = self._next()
        if not item:
            runtime = self.profile.get("runtime", {})
            return {
                "person_id": self.person_id,
                "cycle_id": runtime.get("cycle_id"),
                "cycle_date": runtime.get("cycle_date"),
                "cycle_state": runtime.get("cycle_state"),
            }
        return slot_detail(self.profile, item["slot"], dt_util.now())


class PillPalSlotSensor(PillPalEntity, SensorEntity):
    """Publish one stable, fully detailed entity for each regular slot."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [
        "not_planned",
        "planned",
        "pending",
        "notified",
        "snoozed",
        "taken",
        "skipped",
        "missed",
    ]

    def __init__(self, manager, subentry, slot: str) -> None:
        super().__init__(manager, subentry, f"slot_{slot}")
        self.slot = slot
        self._attr_name = f"Einnahme {SLOT_LABELS[slot]}"
        self._attr_icon = _SLOT_ICONS[slot]
        self._attr_translation_key = f"slot_{slot}"

    @property
    def native_value(self) -> str:
        return str(slot_detail(self.profile, self.slot, dt_util.now())["status"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return slot_detail(self.profile, self.slot, dt_util.now())


class PillPalPracticeStatusSensor(PillPalEntity, SensorEntity):
    _attr_name = "Praxisstatus"
    _attr_icon = "mdi:doctor"

    def __init__(self, manager, subentry) -> None:
        super().__init__(manager, subentry, "practice_status")

    @property
    def native_value(self) -> str:
        return "Geöffnet" if practice_status(self.profile, dt_util.now())["open"] else "Geschlossen"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"person_id": self.person_id, **practice_status(self.profile, dt_util.now())}


class PillPalActionResultSensor(PillPalEntity, SensorEntity):
    _attr_name = "Letzte Aktionsrückmeldung"
    _attr_icon = "mdi:gesture-tap-button"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["idle", "pending", "success", "error"]
    _attr_translation_key = "action_result"

    def __init__(self, manager, subentry) -> None:
        super().__init__(manager, subentry, "action_result")

    @property
    def native_value(self) -> str:
        return str(
            self.profile.get("runtime", {})
            .get("last_action_result", {})
            .get("status", "idle")
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        result = self.profile.get("runtime", {}).get("last_action_result", {})
        return {"person_id": self.person_id, **result}


class PillPalStockAlertSensor(PillPalEntity, SensorEntity):
    _attr_name = "Nachbestellungen"
    _attr_icon = "mdi:cart-arrow-down"

    def __init__(self, manager, subentry) -> None:
        super().__init__(manager, subentry, "stock_alerts")

    def _plan(self) -> dict[str, Any]:
        return order_plan(self.profile, dt_util.now())

    @property
    def native_value(self) -> int:
        return int(self._plan()["due_count"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        plan = self._plan()
        return {
            "person_id": self.person_id,
            "active": plan["active"],
            "medications": [item["name"] for item in plan["items"]],
            "items": plan["items"],
            "projections": plan["projections"],
            "clipboard_text": plan["clipboard_text"],
            "cost_total": plan["cost_total"],
            "cost_status": plan["cost_status"],
            "cost_text": plan["cost_text"],
            "currency": plan["currency"],
        }


class PillPalExpiryAlertSensor(PillPalEntity, SensorEntity):
    _attr_name = "MHD-Hinweise"
    _attr_icon = "mdi:calendar-alert"

    def __init__(self, manager, subentry) -> None:
        super().__init__(manager, subentry, "expiry_alerts")

    def _plan(self) -> dict[str, Any]:
        return expiry_plan(self.profile, dt_util.now())

    @property
    def native_value(self) -> int:
        return int(self._plan()["item_count"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        plan = self._plan()
        return {
            "person_id": self.person_id,
            "active": plan["active"],
            "medications": [item["name"] for item in plan["items"]],
            "items": plan["items"],
        }


class PillPalLastActivitySensor(PillPalEntity, SensorEntity):
    _attr_name = "Letzte Aktivität"
    _attr_icon = "mdi:message-text-outline"

    def __init__(self, manager, subentry) -> None:
        super().__init__(manager, subentry, "last_activity")

    @property
    def native_value(self) -> str:
        return str(self.profile.get("runtime", {}).get("last_activity", ""))[:255]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        log = self.profile.get("log", [])
        return {
            "person_id": self.person_id,
            "timestamp": log[-1].get("timestamp") if log else None,
            "actor": log[-1].get("actor") if log else None,
        }


class PillPalAdherenceSensor(PillPalEntity, SensorEntity):
    _attr_icon = "mdi:percent-circle-outline"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_registry_enabled_default = False

    def __init__(self, manager, subentry, days: int, period_label: str) -> None:
        key = "adherence" if days == 30 else f"adherence_{days}d"
        super().__init__(manager, subentry, key)
        self.days = days
        self._attr_name = f"Einnahmetreue ({period_label})"

    @property
    def native_value(self) -> float:
        return statistics(self.profile, self.days, dt_util.now())["adherence"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "person_id": self.person_id,
            **statistics(self.profile, self.days, dt_util.now()),
        }


class PillPalStatisticsCountSensor(PillPalEntity, SensorEntity):
    _attr_icon = "mdi:counter"
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        manager,
        subentry,
        key: str,
        name: str,
        days: int,
        period_label: str,
    ) -> None:
        entity_key = f"statistics_{key}" if days == 30 else f"statistics_{key}_{days}d"
        super().__init__(manager, subentry, entity_key)
        self.statistics_key = key
        self.days = days
        self._attr_name = f"{name} ({period_label})"

    @property
    def native_value(self) -> int | float:
        return statistics(self.profile, self.days, dt_util.now())[self.statistics_key]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        values = statistics(self.profile, self.days, dt_util.now())
        attributes = {
            "person_id": self.person_id,
            "period_start": values["period_start"],
            "period_end": values["period_end"],
            "days": values["days"],
        }
        if self.statistics_key == "as_needed_bookings":
            attributes["quantity"] = values["as_needed_quantity"]
        return attributes


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    manager: PillPalManager = entry.runtime_data
    for subentry in entry.get_subentries_of_type(PERSON_SUBENTRY_TYPE):
        async_add_entities(
            [
                PillPalStatusSensor(manager, subentry),
                PillPalNextIntakeSensor(manager, subentry),
                *[PillPalSlotSensor(manager, subentry, slot) for slot in SLOT_LABELS],
                PillPalPracticeStatusSensor(manager, subentry),
                PillPalActionResultSensor(manager, subentry),
                PillPalStockAlertSensor(manager, subentry),
                PillPalExpiryAlertSensor(manager, subentry),
                PillPalLastActivitySensor(manager, subentry),
                *[
                    entity
                    for days, period_label in _STATISTICS_PERIODS
                    for entity in (
                        PillPalAdherenceSensor(manager, subentry, days, period_label),
                        PillPalStatisticsCountSensor(manager, subentry, "planned", "Geplante Einnahmen", days, period_label),
                        PillPalStatisticsCountSensor(manager, subentry, "taken", "Eingenommene Einnahmen", days, period_label),
                        PillPalStatisticsCountSensor(manager, subentry, "skipped", "Übersprungene Einnahmen", days, period_label),
                        PillPalStatisticsCountSensor(manager, subentry, "missed", "Verpasste Einnahmen", days, period_label),
                        PillPalStatisticsCountSensor(manager, subentry, "pending", "Ausstehende Einnahmen", days, period_label),
                        PillPalStatisticsCountSensor(manager, subentry, "as_needed_bookings", "Bedarfseinnahmen", days, period_label),
                    )
                ],
            ],
            config_subentry_id=subentry.subentry_id,
        )
