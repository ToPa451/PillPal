"""Config and person-subentry flows for Pill★Pal."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigSubentryFlow,
    ConfigSubentryData,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ADMIN_ASSISTANCE,
    CONF_ASSISTED_PERSON_IDS,
    CONF_CREATE_EXAMPLE,
    CONF_INCLUDED_PERSON_IDS,
    CONF_PERSON_ENTITY_ID,
    CONF_PERSON_ID,
    CONF_PERSON_NAME,
    CONF_USER_ID,
    DOMAIN,
    NAME,
    PERSON_SUBENTRY_TYPE,
)


def _ha_people(hass) -> list[dict[str, Any]]:
    """Return current HA Person records."""

    result: list[dict[str, Any]] = []
    for state in hass.states.async_all("person"):
        attrs = state.attributes
        person_id = str(attrs.get("id") or state.entity_id.split(".", 1)[1])
        result.append(
            {
                "person_id": person_id,
                "entity_id": state.entity_id,
                "name": str(attrs.get("friendly_name") or state.name or person_id),
                "user_id": attrs.get("user_id") or "",
            }
        )
    return sorted(result, key=lambda item: item["name"].casefold())


def _person_options(people: list[dict[str, Any]]) -> list[selector.SelectOptionDict]:
    return [
        selector.SelectOptionDict(value=item["person_id"], label=item["name"])
        for item in people
    ]


def _subentry_data(person: dict[str, Any], assistance: bool) -> ConfigSubentryData:
    return {
        "subentry_type": PERSON_SUBENTRY_TYPE,
        "title": person["name"],
        "unique_id": person["person_id"],
        "data": {
            CONF_PERSON_ID: person["person_id"],
            CONF_PERSON_ENTITY_ID: person["entity_id"],
            CONF_PERSON_NAME: person["name"],
            CONF_USER_ID: person["user_id"],
            CONF_ADMIN_ASSISTANCE: bool(assistance or not person["user_id"]),
        },
    }


class PillPalConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up one household entry and multiple person subentries."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        self._people: list[dict[str, Any]] = []
        self._selected_ids: list[str] = []
        self._create_example = True
        self._assistance: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Select all people to include."""

        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        self._people = _ha_people(self.hass)
        if not self._people:
            return self.async_abort(reason="no_people")
        if user_input is not None:
            self._selected_ids = list(user_input[CONF_INCLUDED_PERSON_IDS])
            self._create_example = bool(user_input.get(CONF_CREATE_EXAMPLE, True))
            if not self._selected_ids:
                return self.async_show_form(
                    step_id="user",
                    data_schema=self._person_schema(),
                    errors={"base": "select_at_least_one"},
                )
            linked = [
                person
                for person in self._people
                if person["person_id"] in self._selected_ids and person["user_id"]
            ]
            if linked:
                return await self.async_step_assistance()
            return await self.async_step_finish()
        return self.async_show_form(step_id="user", data_schema=self._person_schema())

    def _person_schema(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_INCLUDED_PERSON_IDS): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=_person_options(self._people),
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
                vol.Required(CONF_CREATE_EXAMPLE, default=True): bool,
            }
        )

    async def async_step_assistance(
        self, user_input: dict[str, Any] | None = None
    ):
        """Choose which linked-user profiles admins may assist."""

        linked = [
            person
            for person in self._people
            if person["person_id"] in self._selected_ids and person["user_id"]
        ]
        if user_input is not None:
            self._assistance = dict(user_input)
            return await self.async_step_finish()
        return self.async_show_form(
            step_id="assistance",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ASSISTED_PERSON_IDS, default=[]): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=_person_options(linked),
                            multiple=True,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    async def async_step_finish(self, user_input: dict[str, Any] | None = None):
        """Explain how the registered dashboards become visible."""

        if user_input is not None:
            return self._create_household(self._assistance)
        return self.async_show_form(step_id="finish", data_schema=vol.Schema({}))

    def _create_household(self, assistance: dict[str, Any]):
        assisted_ids = set(assistance.get(CONF_ASSISTED_PERSON_IDS, []))
        subentries = []
        for person in self._people:
            if person["person_id"] not in self._selected_ids:
                continue
            enabled = person["person_id"] in assisted_ids
            subentries.append(_subentry_data(person, enabled))
        return self.async_create_entry(
            title=NAME,
            data={"schema": 2, CONF_CREATE_EXAMPLE: self._create_example},
            subentries=subentries,
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {PERSON_SUBENTRY_TYPE: PersonSubentryFlow}


class PersonSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure one person subentry."""

    def __init__(self) -> None:
        self._person: dict[str, Any] | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        entry = self._get_entry()
        configured = {subentry.unique_id for subentry in entry.subentries.values()}
        people = [
            person for person in _ha_people(self.hass) if person["person_id"] not in configured
        ]
        if not people:
            return self.async_abort(reason="no_new_people")
        if user_input is not None:
            person_id = user_input[CONF_PERSON_ID]
            self._person = next(item for item in people if item["person_id"] == person_id)
            if self._person["user_id"]:
                return await self.async_step_assistance()
            return self._create_person(True)
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PERSON_ID): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=_person_options(people))
                    )
                }
            ),
        )

    async def async_step_assistance(
        self, user_input: dict[str, Any] | None = None
    ):
        if user_input is not None:
            return self._create_person(bool(user_input[CONF_ADMIN_ASSISTANCE]))
        return self.async_show_form(
            step_id="assistance",
            data_schema=vol.Schema(
                {vol.Required(CONF_ADMIN_ASSISTANCE, default=False): bool}
            ),
        )

    def _create_person(self, assistance: bool):
        assert self._person is not None
        data = _subentry_data(self._person, assistance)
        return self.async_create_entry(
            title=data["title"], data=data["data"], unique_id=data["unique_id"]
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ):
        subentry = self._get_reconfigure_subentry()
        person = next(
            (
                item
                for item in _ha_people(self.hass)
                if item["person_id"] == subentry.data[CONF_PERSON_ID]
            ),
            None,
        )
        if user_input is not None:
            updates = dict(subentry.data)
            linked_user_id = (
                person["user_id"]
                if person is not None
                else str(subentry.data.get(CONF_USER_ID, ""))
            )
            updates[CONF_ADMIN_ASSISTANCE] = bool(
                user_input[CONF_ADMIN_ASSISTANCE] or not linked_user_id
            )
            if person:
                updates.update(
                    {
                        CONF_PERSON_ENTITY_ID: person["entity_id"],
                        CONF_PERSON_NAME: person["name"],
                        CONF_USER_ID: person["user_id"],
                    }
                )
            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
                data=updates,
                title=person["name"] if person else subentry.title,
            )
        linked_user_id = (
            person["user_id"]
            if person is not None
            else str(subentry.data.get(CONF_USER_ID, ""))
        )
        forced = not bool(linked_user_id)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ADMIN_ASSISTANCE,
                        default=(forced or subentry.data.get(CONF_ADMIN_ASSISTANCE, False)),
                    ): bool
                }
            ),
            description_placeholders={
                "person": person["name"] if person else subentry.title
            },
        )
