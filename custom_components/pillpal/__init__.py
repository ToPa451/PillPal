"""Pill★Pal Home Assistant integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .api import async_register_services, async_register_websocket
from .const import DOMAIN, PLATFORMS, STORAGE_KEY, STORAGE_VERSION
from .manager import PillPalManager, async_cleanup_stored_notifications
from .panel import async_register_panels, async_remove_panels


_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up global integration APIs."""

    hass.data.setdefault(DOMAIN, {"entries": {}, "static_url_registered": None})
    if not hass.data[DOMAIN].get("apis_registered"):
        async_register_services(hass)
        async_register_websocket(hass)
        hass.data[DOMAIN]["apis_registered"] = True
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one Pill★Pal entry."""

    manager = PillPalManager(hass, entry)
    await manager.async_initialize()
    hass.data[DOMAIN]["entries"][entry.entry_id] = manager
    entry.runtime_data = manager
    await async_register_panels(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the entry cleanly."""

    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    manager: PillPalManager | None = hass.data[DOMAIN]["entries"].pop(
        entry.entry_id, None
    )
    if manager is not None:
        await manager.async_shutdown()
    if not hass.data[DOMAIN]["entries"]:
        async_remove_panels(hass)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Permanently remove live and quarantine data with the config entry."""

    store_key = f"{STORAGE_KEY}.{entry.entry_id}"
    live_store = Store(hass, STORAGE_VERSION, store_key)
    try:
        stored = await live_store.async_load()
        if isinstance(stored, Mapping):
            cleared, failed = await async_cleanup_stored_notifications(hass, stored)
            _LOGGER.info(
                "Cleared %s persisted Pill★Pal notification(s) before removal",
                cleared,
            )
            for target, tag, error in failed:
                _LOGGER.warning(
                    "Pill★Pal notification %s at %s could not be cleared before "
                    "removal: %s",
                    tag,
                    target,
                    error,
                )
    except Exception as err:  # Cleanup diagnostics must never block uninstall.
        _LOGGER.warning(
            "Persisted Pill★Pal notifications could not be inspected before "
            "removal: %s",
            err,
        )
    # Remove the recovery-only quarantine first.  If that fails, the live data
    # remains available and Home Assistant reports the incomplete deletion.
    await Store(
        hass, STORAGE_VERSION, f"{store_key}.quarantine"
    ).async_remove()
    await live_store.async_remove()
