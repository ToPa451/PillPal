"""Person-scoped action buttons for Pill★Pal."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import PERSON_SUBENTRY_TYPE
from .entity import PillPalEntity
from .manager import PillPalManager
from .model import PillPalError

_LOGGER = logging.getLogger(__name__)


class PillPalActionButton(PillPalEntity, ButtonEntity):
    def __init__(self, manager, subentry, key: str, name: str, icon: str) -> None:
        super().__init__(manager, subentry, key)
        self._attr_name = name
        self._attr_icon = icon
        self.action = key

    async def async_press(self) -> None:
        await self.manager.async_record_action_result(
            self.person_id,
            self.action,
            "pending",
            "Aktion wird ausgeführt.",
            actor="Entität",
        )
        try:
            if self.action == "confirm":
                result = await self.manager.async_confirm_slot(
                    self.person_id, None, actor="Entität", source="Button-Entität"
                )
            elif self.action == "snooze":
                result = await self.manager.async_snooze_slot(
                    self.person_id,
                    None,
                    None,
                    actor="Entität",
                    source="Button-Entität",
                )
            else:
                result = await self.manager.async_skip_slot(
                    self.person_id,
                    None,
                    actor="Entität",
                    source="Button-Entität",
                )
        except PillPalError as err:
            await self.manager.async_record_action_result(
                self.person_id,
                self.action,
                "error",
                str(err),
                actor="Entität",
                error_code=err.code,
            )
            raise
        except Exception:
            _LOGGER.exception("Unexpected Pill★Pal entity-button error")
            await self.manager.async_record_action_result(
                self.person_id,
                self.action,
                "error",
                "Technischer Fehler bei der Ausführung der Pill★Pal-Aktion.",
                actor="Entität",
                error_code="technical_error",
            )
            raise
        await self.manager.async_record_action_result(
            self.person_id,
            self.action,
            "success",
            "Aktion erfolgreich abgeschlossen.",
            actor="Entität",
            result=result,
        )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    manager: PillPalManager = entry.runtime_data
    for subentry in entry.get_subentries_of_type(PERSON_SUBENTRY_TYPE):
        async_add_entities(
            [
                PillPalActionButton(
                    manager, subentry, "confirm", "Fällige Einnahme bestätigen", "mdi:check"
                ),
                PillPalActionButton(
                    manager, subentry, "snooze", "Fällige Einnahme zurückstellen", "mdi:alarm-snooze"
                ),
                PillPalActionButton(
                    manager, subentry, "skip", "Fällige Einnahme überspringen", "mdi:skip-next"
                ),
            ],
            config_subentry_id=subentry.subentry_id,
        )
