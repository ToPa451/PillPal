"""Services and WebSocket API for Pill★Pal."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import date
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    SERVICE_ACKNOWLEDGE_ERRORS,
    SERVICE_ADJUST_STOCK,
    SERVICE_ARCHIVE_MEDICATION,
    SERVICE_BOOK_AS_NEEDED,
    SERVICE_CONFIRM_SLOT,
    SERVICE_IMPORT_R410,
    SERVICE_REACTIVATE_MEDICATION,
    SERVICE_RECALCULATE,
    SERVICE_REFILL,
    SERVICE_SAVE_MEDICATION,
    SERVICE_SKIP_SLOT,
    SERVICE_SNOOZE_SLOT,
    SERVICE_STATISTICS,
    SERVICE_UPDATE_PRACTICE_CLOSURES,
    SERVICE_UPDATE_SETTINGS,
    SLOTS,
)
from .manager import PillPalManager
from .model import PillPalError

_LOGGER = logging.getLogger(__name__)

ActionHandler = Callable[
    [PillPalManager, str, Mapping[str, Any], str, str | None], Awaitable[Any]
]


def _manager(hass: HomeAssistant) -> PillPalManager:
    managers = hass.data.get(DOMAIN, {}).get("entries", {})
    if not managers:
        raise HomeAssistantError("Pill★Pal ist noch nicht geladen.")
    return next(iter(managers.values()))


def _resolve_person_id(
    hass: HomeAssistant, manager: PillPalManager, reference: Any
) -> str:
    """Accept a stable profile id or the person's Pill★Pal device id."""

    value = str(reference or "").strip()
    if value in manager.data.get("profiles", {}):
        return value
    try:
        from homeassistant.helpers import device_registry as dr

        device = dr.async_get(hass).async_get(value)
        if device is not None:
            for domain, identifier in device.identifiers:
                if domain == DOMAIN and identifier in manager.data.get("profiles", {}):
                    return str(identifier)
    except (AttributeError, ImportError, TypeError):
        pass
    raise HomeAssistantError(
        "Bitte ein vorhandenes Pill★Pal-Personenprofil auswählen."
    )


def _resolve_medication_id(
    manager: PillPalManager, person_id: str, reference: Any
) -> str:
    """Accept the exact id or one unambiguous visible medication name."""

    value = str(reference or "").strip()
    medications = manager.profile(person_id).get("medications", {})
    if value in medications:
        return value
    matches = [
        medication_id
        for medication_id, medication in medications.items()
        if str(medication.get("name", "")).casefold() == value.casefold()
    ]
    if len(matches) == 1:
        return matches[0]
    raise HomeAssistantError(
        "Bitte einen eindeutigen Medikamentennamen aus diesem Personenprofil verwenden."
    )


def _slot_value(value: Any) -> str | None:
    raw = str(value or "").strip()
    aliases = {
        "morgens": "morning",
        "mittags": "noon",
        "abends": "evening",
        "zur nacht": "night",
        "bedarf": "as_needed",
    }
    return aliases.get(raw.casefold(), raw or None)


def _date_value(value: Any) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as err:
        raise HomeAssistantError(f"Ungültiges Datum: {value}") from err


async def _actor_name(hass: HomeAssistant, user_id: str | None) -> str:
    if not user_id:
        return "Automation"
    user = await hass.auth.async_get_user(user_id)
    return user.name if user is not None else "Home Assistant"


def _authorize(
    manager: PillPalManager,
    *,
    user_id: str | None,
    is_admin: bool,
    person_id: str,
    admin_mode: bool,
) -> None:
    if user_id is None:
        return
    if not manager.is_authorized(
        user_id=user_id,
        is_admin=is_admin,
        person_id=person_id,
        admin_mode=admin_mode,
    ):
        raise HomeAssistantError("Kein Zugriff auf dieses Pill★Pal-Personenprofil.")


async def _run_action(
    manager: PillPalManager,
    person_id: str,
    action: str,
    data: Mapping[str, Any],
    actor: str,
    actor_user_id: str | None,
) -> Any:
    data = dict(data)
    if "slot" in data:
        data["slot"] = _slot_value(data.get("slot"))
    if "medication_id" in data:
        data["medication_id"] = _resolve_medication_id(
            manager, person_id, data.get("medication_id")
        )
    if action == SERVICE_CONFIRM_SLOT:
        return await manager.async_confirm_slot(
            person_id,
            data.get("slot"),
            actor=actor,
            source=str(data.get("source", "Dashboard")),
            allow_early=bool(data.get("allow_early", False)),
        )
    if action == SERVICE_SNOOZE_SLOT:
        return await manager.async_snooze_slot(
            person_id,
            data.get("slot"),
            data.get("minutes"),
            actor=actor,
            source=str(data.get("source", "Dashboard")),
        )
    if action == SERVICE_SKIP_SLOT:
        return await manager.async_skip_slot(
            person_id, data.get("slot"), actor=actor
        )
    if action == SERVICE_BOOK_AS_NEEDED:
        return await manager.async_book_as_needed(
            person_id,
            str(data.get("medication_id", "")),
            data.get("quantity"),
            actor=actor,
            actor_user_id=actor_user_id,
            source=str(data.get("source", "Dashboard")),
            confirmation_token=(
                str(data["confirmation_token"])
                if data.get("confirmation_token")
                else None
            ),
        )
    if action == SERVICE_SAVE_MEDICATION:
        medication = data.get("medication", data)
        return await manager.async_save_medication(
            person_id, medication, actor=actor
        )
    if action == SERVICE_ARCHIVE_MEDICATION:
        return await manager.async_archive_medication(
            person_id, str(data.get("medication_id", "")), actor=actor
        )
    if action == SERVICE_REACTIVATE_MEDICATION:
        return await manager.async_reactivate_medication(
            person_id, str(data.get("medication_id", "")), actor=actor
        )
    if action == SERVICE_REFILL:
        return await manager.async_refill(
            person_id,
            str(data.get("medication_id", "")),
            data.get("quantity"),
            data.get("expiry_date"),
            actor=actor,
        )
    if action == SERVICE_ADJUST_STOCK:
        return await manager.async_adjust_stock(
            person_id,
            str(data.get("medication_id", "")),
            data.get("delta"),
            actor=actor,
            source=str(data.get("source", "Service")),
        )
    if action == SERVICE_UPDATE_SETTINGS:
        return await manager.async_update_settings(
            person_id, data.get("settings", data), actor=actor
        )
    if action == SERVICE_UPDATE_PRACTICE_CLOSURES:
        return await manager.async_update_practice_closures(
            person_id, list(data.get("closures", [])), actor=actor
        )
    if action == SERVICE_ACKNOWLEDGE_ERRORS:
        return await manager.async_acknowledge_errors(person_id, actor=actor)
    if action == SERVICE_RECALCULATE:
        return await manager.async_recalculate(person_id, actor=actor)
    if action == SERVICE_STATISTICS:
        return await manager.async_statistics(
            person_id,
            days=max(1, min(3660, int(data.get("days", 30)))),
            medication_id=(
                _resolve_medication_id(manager, person_id, data["medication_id"])
                if data.get("medication_id")
                else None
            ),
            slot=_slot_value(data.get("slot")),
            start_date=_date_value(data.get("start_date")),
            end_date=_date_value(data.get("end_date")),
            selected_day=_date_value(data.get("selected_day")),
        )
    raise HomeAssistantError(f"Unbekannte Pill★Pal-Aktion: {action}")


async def _async_service_handler(
    hass: HomeAssistant, call: ServiceCall
) -> dict[str, Any] | None:
    manager = _manager(hass)
    person_reference = call.data.get("person_id", "")
    if not person_reference:
        raise HomeAssistantError("person_id ist erforderlich.")
    person_id = _resolve_person_id(hass, manager, person_reference)
    if call.context.user_id:
        user = await hass.auth.async_get_user(call.context.user_id)
        if user is None or not (
            manager.is_authorized(
                user_id=user.id,
                is_admin=user.is_admin,
                person_id=person_id,
                admin_mode=False,
            )
            or manager.is_authorized(
                user_id=user.id,
                is_admin=user.is_admin,
                person_id=person_id,
                admin_mode=True,
            )
        ):
            raise HomeAssistantError("Kein Zugriff auf dieses Pill★Pal-Personenprofil.")
    actor = await _actor_name(hass, call.context.user_id)
    await manager.async_record_action_result(
        person_id,
        call.service,
        "pending",
        "Aktion wird ausgeführt.",
        actor=actor,
    )
    try:
        result = await _run_action(
            manager,
            person_id,
            call.service,
            call.data,
            actor,
            call.context.user_id,
        )
    except (PillPalError, HomeAssistantError) as err:
        try:
            await manager.async_record_action_result(
                person_id,
                call.service,
                "error",
                str(err),
                actor=actor,
                error_code=getattr(err, "code", "invalid"),
            )
        except Exception:  # The original, user-facing rejection remains decisive.
            _LOGGER.exception("Could not persist a rejected Pill★Pal service action")
            pass
        raise HomeAssistantError(str(err)) from err
    except Exception as err:
        _LOGGER.exception("Unexpected Pill★Pal service error in %s", call.service)
        message = "Technischer Fehler bei der Ausführung der Pill★Pal-Aktion."
        try:
            await manager.async_record_action_result(
                person_id,
                call.service,
                "error",
                message,
                actor=actor,
                error_code="technical_error",
            )
        except Exception:
            _LOGGER.exception("Could not persist a technical Pill★Pal service error")
        raise HomeAssistantError(message) from err
    await manager.async_record_action_result(
        person_id,
        call.service,
        "success",
        "Aktion erfolgreich abgeschlossen.",
        actor=actor,
        result=result,
    )
    response = result if isinstance(result, dict) else {"result": result}
    return response if call.return_response else None


async def _async_import_service(hass: HomeAssistant, call: ServiceCall) -> None:
    manager = _manager(hass)
    if call.context.user_id:
        user = await hass.auth.async_get_user(call.context.user_id)
        if user is None or not user.is_admin:
            raise HomeAssistantError("Der R4.1-Import erfordert Administratorrechte.")
    try:
        result = await manager.async_import_r410(
            str(call.data["source_path"]), dict(call.data.get("mapping", {}))
        )
    except (OSError, ValueError, PillPalError) as err:
        raise HomeAssistantError(str(err)) from err
    hass.bus.async_fire("pillpal_import_result", result)


def async_register_services(hass: HomeAssistant) -> None:
    """Register explicit-person services once."""

    person_schema = vol.Schema({vol.Required("person_id"): cv.string}, extra=vol.ALLOW_EXTRA)

    async def handle_action(call: ServiceCall) -> dict[str, Any] | None:
        return await _async_service_handler(hass, call)

    async def handle_import(call: ServiceCall) -> None:
        await _async_import_service(hass, call)

    for service in (
        SERVICE_CONFIRM_SLOT,
        SERVICE_SNOOZE_SLOT,
        SERVICE_SKIP_SLOT,
        SERVICE_BOOK_AS_NEEDED,
        SERVICE_SAVE_MEDICATION,
        SERVICE_ARCHIVE_MEDICATION,
        SERVICE_REACTIVATE_MEDICATION,
        SERVICE_REFILL,
        SERVICE_ADJUST_STOCK,
        SERVICE_UPDATE_SETTINGS,
        SERVICE_UPDATE_PRACTICE_CLOSURES,
        SERVICE_ACKNOWLEDGE_ERRORS,
        SERVICE_RECALCULATE,
        SERVICE_STATISTICS,
    ):
        hass.services.async_register(
            DOMAIN,
            service,
            handle_action,
            schema=person_schema,
            supports_response=SupportsResponse.OPTIONAL,
        )
    hass.services.async_register(
        DOMAIN,
        SERVICE_IMPORT_R410,
        handle_import,
        schema=vol.Schema(
            {
                vol.Required("source_path"): cv.string,
                vol.Required("mapping"): dict,
            }
        ),
    )


def async_remove_services(hass: HomeAssistant) -> None:
    for service in (
        SERVICE_CONFIRM_SLOT,
        SERVICE_SNOOZE_SLOT,
        SERVICE_SKIP_SLOT,
        SERVICE_BOOK_AS_NEEDED,
        SERVICE_SAVE_MEDICATION,
        SERVICE_ARCHIVE_MEDICATION,
        SERVICE_REACTIVATE_MEDICATION,
        SERVICE_REFILL,
        SERVICE_ADJUST_STOCK,
        SERVICE_UPDATE_SETTINGS,
        SERVICE_UPDATE_PRACTICE_CLOSURES,
        SERVICE_ACKNOWLEDGE_ERRORS,
        SERVICE_RECALCULATE,
        SERVICE_STATISTICS,
        SERVICE_IMPORT_R410,
    ):
        hass.services.async_remove(DOMAIN, service)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "pillpal/bootstrap",
        vol.Optional("admin_mode", default=False): bool,
        vol.Optional("person_id"): str,
    }
)
@websocket_api.async_response
async def websocket_bootstrap(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    manager = _manager(hass)
    result = manager.frontend_bootstrap(
        user_id=connection.user.id,
        is_admin=connection.user.is_admin,
        admin_mode=bool(msg.get("admin_mode")),
        requested_person_id=msg.get("person_id"),
    )
    if result.get("profile"):
        person_id = result["selected_person_id"]
        result["profile"] = await manager.async_snapshot(person_id)
        profile = manager.profile(person_id)
        result["options"] = {
            "confirm_helpers": manager.available_helpers(person_id, "confirm_helper"),
            "medication_button_helpers": manager.available_helpers(person_id, "button_helper"),
            "notify_targets": manager.available_notify_targets(),
            "input_booleans": sorted(
                state.entity_id for state in hass.states.async_all("input_boolean")
            ),
            "sensors": sorted(
                state.entity_id for state in hass.states.async_all("sensor")
            ),
            "calendars": sorted(
                state.entity_id for state in hass.states.async_all("calendar")
            ),
            "selected_settings": profile.get("settings", {}),
        }
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "pillpal/action",
        vol.Required("person_id"): str,
        vol.Required("action"): vol.In(
            [
                SERVICE_CONFIRM_SLOT,
                SERVICE_SNOOZE_SLOT,
                SERVICE_SKIP_SLOT,
                SERVICE_BOOK_AS_NEEDED,
                SERVICE_SAVE_MEDICATION,
                SERVICE_ARCHIVE_MEDICATION,
                SERVICE_REACTIVATE_MEDICATION,
                SERVICE_REFILL,
                SERVICE_UPDATE_SETTINGS,
                SERVICE_UPDATE_PRACTICE_CLOSURES,
                SERVICE_ACKNOWLEDGE_ERRORS,
                SERVICE_RECALCULATE,
                SERVICE_STATISTICS,
            ]
        ),
        vol.Optional("admin_mode", default=False): bool,
        vol.Optional("data", default={}): dict,
    }
)
@websocket_api.async_response
async def websocket_action(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    manager = _manager(hass)
    person_id = msg["person_id"]
    action_started = False
    try:
        _authorize(
            manager,
            user_id=connection.user.id,
            is_admin=connection.user.is_admin,
            person_id=person_id,
            admin_mode=bool(msg.get("admin_mode")),
        )
        await manager.async_record_action_result(
            person_id,
            msg["action"],
            "pending",
            "Aktion wird ausgeführt.",
            actor=connection.user.name,
        )
        action_started = True
        result = await _run_action(
            manager,
            person_id,
            msg["action"],
            msg.get("data", {}),
            connection.user.name,
            connection.user.id,
        )
    except (PillPalError, HomeAssistantError) as err:
        if action_started:
            try:
                await manager.async_record_action_result(
                    person_id,
                    msg["action"],
                    "error",
                    str(err),
                    actor=connection.user.name,
                    error_code=getattr(err, "code", "invalid"),
                )
            except PillPalError:
                pass
        connection.send_error(msg["id"], getattr(err, "code", "invalid"), str(err))
        return
    except Exception:
        _LOGGER.exception("Unexpected Pill★Pal WebSocket error in %s", msg["action"])
        message = "Technischer Fehler bei der Ausführung der Pill★Pal-Aktion."
        if action_started:
            try:
                await manager.async_record_action_result(
                    person_id,
                    msg["action"],
                    "error",
                    message,
                    actor=connection.user.name,
                    error_code="technical_error",
                )
            except Exception:
                _LOGGER.exception("Could not persist a technical WebSocket error")
        connection.send_error(msg["id"], "technical_error", message)
        return
    await manager.async_record_action_result(
        person_id,
        msg["action"],
        "success",
        "Aktion erfolgreich abgeschlossen.",
        actor=connection.user.name,
        result=result,
    )
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "pillpal/statistics",
        vol.Required("person_id"): str,
        vol.Optional("admin_mode", default=False): bool,
        vol.Optional("days", default=7): int,
        vol.Optional("medication_id"): str,
        vol.Optional("slot"): str,
        vol.Optional("start_date"): str,
        vol.Optional("end_date"): str,
        vol.Optional("selected_day"): str,
    }
)
@websocket_api.async_response
async def websocket_statistics(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the model-owned read-only statistics without mutating action state."""

    manager = _manager(hass)
    person_id = str(msg["person_id"])
    try:
        _authorize(
            manager,
            user_id=connection.user.id,
            is_admin=connection.user.is_admin,
            person_id=person_id,
            admin_mode=bool(msg.get("admin_mode")),
        )
        result = await manager.async_statistics(
            person_id,
            days=max(1, min(3660, int(msg.get("days", 7)))),
            medication_id=(
                _resolve_medication_id(manager, person_id, msg["medication_id"])
                if msg.get("medication_id")
                else None
            ),
            slot=_slot_value(msg.get("slot")),
            start_date=_date_value(msg.get("start_date")),
            end_date=_date_value(msg.get("end_date")),
            selected_day=_date_value(msg.get("selected_day")),
        )
    except (PillPalError, HomeAssistantError, ValueError) as err:
        connection.send_error(msg["id"], getattr(err, "code", "invalid"), str(err))
        return
    except Exception:
        _LOGGER.exception("Unexpected Pill★Pal statistics WebSocket error")
        connection.send_error(
            msg["id"],
            "technical_error",
            "Technischer Fehler beim Laden der Pill★Pal-Statistik.",
        )
        return
    connection.send_result(msg["id"], result)


def async_register_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, websocket_bootstrap)
    websocket_api.async_register_command(hass, websocket_action)
    websocket_api.async_register_command(hass, websocket_statistics)
