"""Register the two Pill★Pal sidebar panels."""

from __future__ import annotations

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import ADMIN_PANEL_URL, FRONTEND_DIR, PANEL_COMPONENT, PANEL_URL, STATIC_URL


def _assistance_panel_configured(hass: HomeAssistant) -> bool:
    """Return whether any active profile explicitly allows admin assistance."""

    managers = hass.data.get("pillpal", {}).get("entries", {}).values()
    return any(
        bool(profile.get("admin_assistance"))
        for manager in managers
        for profile in manager.active_profiles
    )


async def async_register_panels(hass: HomeAssistant) -> None:
    """Register the cache-isolated frontend bundle and configured panels."""

    if hass.data["pillpal"].get("static_url_registered") != STATIC_URL:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(FRONTEND_DIR), cache_headers=False)]
        )
        hass.data["pillpal"]["static_url_registered"] = STATIC_URL

    if hass.data["pillpal"].get("icon_module_registered") != STATIC_URL:
        # Registers the "pillpal:" custom ha-icon prefix sitewide, so the
        # sidebar can render the real brand mark instead of a generic MDI icon.
        frontend.add_extra_js_url(hass, f"{STATIC_URL}/pillpal-icons.js?v=5100-23")
        hass.data["pillpal"]["icon_module_registered"] = STATIC_URL

    # A config-entry reload must replace panel metadata as well. Otherwise an
    # already registered browser panel can keep importing an obsolete module.
    for url_path in (PANEL_URL, ADMIN_PANEL_URL):
        if frontend.async_panel_exists(hass, url_path):
            frontend.async_remove_panel(hass, url_path)

    await panel_custom.async_register_panel(
        hass=hass,
        frontend_url_path=PANEL_URL,
        webcomponent_name=PANEL_COMPONENT,
        module_url=f"{STATIC_URL}/pillpal-panel.js?v=5100-22",
        sidebar_title="Pill★Pal",
        sidebar_icon="pillpal:logo",
        embed_iframe=False,
        require_admin=False,
    )
    if _assistance_panel_configured(hass):
        await panel_custom.async_register_panel(
            hass=hass,
            frontend_url_path=ADMIN_PANEL_URL,
            webcomponent_name=PANEL_COMPONENT,
            module_url=f"{STATIC_URL}/pillpal-panel.js?v=5100-22",
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
