"""Shared entity base for Pill★Pal."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import CONF_PERSON_ID, DOMAIN, SIGNAL_PROFILE_UPDATED, VERSION
from .manager import PillPalManager


class PillPalEntity(Entity):
    """Base class bound permanently to one HA Person subentry."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        manager: PillPalManager,
        subentry: ConfigSubentry,
        key: str,
    ) -> None:
        self.manager = manager
        self.subentry = subentry
        self.person_id = str(subentry.data[CONF_PERSON_ID])
        self._attr_unique_id = f"{self.person_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.person_id)},
            name=f"Pill★Pal – {subentry.title}",
            manufacturer="Pill★Pal",
            model="Personenprofil",
            sw_version=VERSION,
        )

    @property
    def profile(self) -> dict[str, Any]:
        return self.manager.profile(self.person_id)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_PROFILE_UPDATED.format(self.person_id),
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
