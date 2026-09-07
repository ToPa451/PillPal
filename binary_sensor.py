"""Binary sensors for Pill★Pal person profiles."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import PERSON_SUBENTRY_TYPE
from .entity import PillPalEntity
from .manager import PillPalManager
from .model import current_and_upcoming, regular_medications, slot_detail


class PillPalDueSensor(PillPalEntity, BinarySensorEntity):
    _attr_name = "Einnahme fällig"
    _attr_icon = "mdi:alarm-light-outline"

    def __init__(self, manager, subentry) -> None:
        super().__init__(manager, subentry, "intake_due")

    @property
    def is_on(self) -> bool:
        return bool(current_and_upcoming(self.profile)["due"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        profile = self.profile
        runtime = profile.get("runtime", {})
        due = current_and_upcoming(profile, dt_util.now())["due"]
        return {
            "person_id": self.person_id,
            "person": profile.get("name"),
            "cycle_id": runtime.get("cycle_id"),
            "cycle_date": runtime.get("cycle_date"),
            "cycle_state": runtime.get("cycle_state"),
            "due": bool(due),
            "due_slot": due[0]["slot"] if due else None,
            "due_slot_label": (
                slot_detail(profile, due[0]["slot"], dt_util.now())["slot_label"]
                if due
                else None
            ),
            "slots": [
                slot_detail(profile, item["slot"], dt_util.now()) for item in due
            ],
        }


class PillPalIntakePossibleSensor(PillPalEntity, BinarySensorEntity):
    """Report whether a regular intake can be booked now, including early booking."""

    _attr_name = "Einnahme möglich"
    _attr_icon = "mdi:clock-check-outline"

    def __init__(self, manager, subentry) -> None:
        super().__init__(manager, subentry, "intake_possible")

    def _bookable(self) -> list[dict[str, Any]]:
        groups = current_and_upcoming(self.profile, dt_util.now())
        return [*groups["due"], *groups["early"]]

    @property
    def is_on(self) -> bool:
        return bool(self._bookable())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        profile = self.profile
        runtime = profile.get("runtime", {})
        bookable = self._bookable()
        return {
            "person_id": self.person_id,
            "person": profile.get("name"),
            "cycle_id": runtime.get("cycle_id"),
            "cycle_date": runtime.get("cycle_date"),
            "possible": bool(bookable),
            "slots": [
                slot_detail(profile, item["slot"], dt_util.now()) for item in bookable
            ],
        }


class PillPalDailyCompleteSensor(PillPalEntity, BinarySensorEntity):
    _attr_name = "Tages-Zyklus vollständig"
    _attr_icon = "mdi:check-circle-outline"

    def __init__(self, manager, subentry) -> None:
        super().__init__(manager, subentry, "daily_complete")

    @property
    def is_on(self) -> bool:
        return bool(self.profile.get("runtime", {}).get("cycle_completed"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        runtime = self.profile.get("runtime", {})
        return {
            "person_id": self.person_id,
            "cycle_id": runtime.get("cycle_id"),
            "cycle_date": runtime.get("cycle_date"),
            "cycle_state": runtime.get("cycle_state"),
            "completed_at": runtime.get("cycle_completed_at"),
        }


class PillPalReminderConfiguredSensor(PillPalEntity, BinarySensorEntity):
    _attr_name = "Erinnerungsweg vorhanden"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, manager, subentry) -> None:
        super().__init__(manager, subentry, "reminder_configured")

    def _status(self) -> tuple[bool, bool, bool]:
        settings = self.profile.get("settings", {})
        notify = str(settings.get("notify_target", ""))
        notify_available = bool(
            notify and self.manager.valid_notify_target(notify)
        )
        due_output_available = bool(
            self.manager.due_output_available(self.profile)
        )
        return notify_available or due_output_available, notify_available, due_output_available

    @property
    def is_on(self) -> bool:
        """Return true only when a person needs reminders but has no route."""

        if not regular_medications(self.profile):
            return False
        reminder_path_available, _, _ = self._status()
        return not reminder_path_available

    @property
    def icon(self) -> str:
        return "mdi:bell-alert-outline" if self.is_on else "mdi:bell-check-outline"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        reminder_path_available, notify_available, due_output_available = self._status()
        reminder_required = bool(regular_medications(self.profile))
        return {
            "person_id": self.person_id,
            "person": self.profile.get("name"),
            "reminder_required": reminder_required,
            "reminder_path_available": reminder_path_available,
            "notify_target_available": notify_available,
            "due_output_available": due_output_available,
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    manager: PillPalManager = entry.runtime_data
    for subentry in entry.get_subentries_of_type(PERSON_SUBENTRY_TYPE):
        async_add_entities(
            [
                PillPalDueSensor(manager, subentry),
                PillPalIntakePossibleSensor(manager, subentry),
                PillPalDailyCompleteSensor(manager, subentry),
                PillPalReminderConfiguredSensor(manager, subentry),
            ],
            config_subentry_id=subentry.subentry_id,
        )
