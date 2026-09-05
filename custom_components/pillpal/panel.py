"""Register the two Pill★Pal sidebar panels."""

from __future__ import annotations

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import ADMIN_PANEL_URL, FRONTEND_DIR, PANEL_COMPONENT, PANEL_URL, STATIC_URL


async def async_register_panels(hass: HomeAssistant) -> None:
    """Register the cache-isolated frontend bundle and current panels."""

    if hass.data["pillpal"].get("static_url_registered") != STATIC_URL:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(FRONTEND_DIR), cache_headers=False)]
        )
        hass.data["pillpal"]["static_url_registered"] = STATIC_URL

    # A config-entry reload must replace panel metadata as well. Otherwise an
    # already registered browser panel can keep importing an obsolete module.
    for url_path in (PANEL_URL, ADMIN_PANEL_URL):
        if frontend.async_panel_exists(hass, url_path):
            frontend.async_remove_panel(hass, url_path)

    await panel_custom.async_register_panel(
        hass=hass,
        frontend_url_path=PANEL_URL,
        webcomponent_name=PANEL_COMPONENT,
        module_url=f"{STATIC_URL}/pillpal-panel.js?v=5100-21",
        sidebar_title="Pill★Pal",
        sidebar_icon="mdi:medication-outline",
        embed_iframe=False,
        require_admin=False,
    )
    await panel_custom.async_register_panel(
        hass=hass,
        frontend_url_path=ADMIN_PANEL_URL,
        webcomponent_name=PANEL_COMPONENT,
        module_url=f"{STATIC_URL}/pillpal-panel.js?v=5100-21",
        sidebar_title="Pill★Pal Assistenz",
        sidebar_icon="mdi:account-supervisor",
        config={"admin_mode": True},
        embed_iframe=False,
        require_admin=True,
    )


def async_remove_panels(hass: HomeAssistant) -> None:
    """Remove panels after the final entry has unloaded."""

    for url_path in (PANEL_URL, ADMIN_PANEL_URL):
        if frontend.async_panel_exists(hass, url_path):
            frontend.async_remove_panel(hass, url_path)
