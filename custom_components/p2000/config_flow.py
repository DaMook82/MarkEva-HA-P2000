"""GUI configuration for the experimental P2000 queue integration."""
from __future__ import annotations

import voluptuous as vol
import logging
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv

DOMAIN = "p2000"
_LOGGER = logging.getLogger(__name__)


def me_schema(defaults):
    """Build an editable schema; coordinates and filters are user supplied."""
    fields = {
        vol.Required("name", default=defaults.get("name", "P2000")): cv.string,
        vol.Required("latitude", default=defaults.get("latitude", 0.0)): cv.latitude,
        vol.Required("longitude", default=defaults.get("longitude", 0.0)): cv.longitude,
        vol.Required("radius_m", default=defaults.get("radius_m", 0)): vol.All(vol.Coerce(float), vol.Range(min=0, max=1000000)),
        vol.Required("grip_radius_m", default=defaults.get("grip_radius_m", 0)): vol.All(vol.Coerce(float), vol.Range(min=0, max=1000000)),
        vol.Required("places", default=defaults.get("places", "")): cv.string,
        vol.Required("disciplines", default=defaults.get("disciplines", "")): cv.string,
        vol.Required("fire_codes", default=defaults.get("fire_codes", "")): cv.string,
        vol.Required("lifeliner_codes", default=defaults.get("lifeliner_codes", "")): cv.string,
        vol.Optional("icon", default=defaults.get("icon", "mdi:ambulance")): cv.icon,
        vol.Required("grip_place_fallback", default=defaults.get("grip_place_fallback", False)): cv.boolean,
        vol.Required("scan_interval", default=defaults.get("scan_interval", 30)): vol.All(vol.Coerce(float), vol.Range(min=0, min_included=False)),
    }
    if defaults.get("legacy_yaml"):
        for field in ("places", "grip_radius_m", "grip_place_fallback", "fire_codes", "lifeliner_codes"):
            fields.pop(next(k for k in fields if k.schema == field))
        fields.update({
            vol.Optional("regios", default=defaults.get("regios", "")): cv.string,
            vol.Optional("capcodes", default=defaults.get("capcodes", "")): cv.string,
            vol.Optional("contains", default=defaults.get("contains", "")): cv.string,
            vol.Optional("nolocation", default=defaults.get("nolocation", True)): cv.boolean,
            vol.Optional("migrate_legacy_store", default=defaults.get("migrate_legacy_store", False)): cv.boolean,
        })
    return vol.Schema(fields)


class MeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_import(self, user_input):
        """Import the upstream YAML name as its entity identity, once."""
        name = user_input["name"]
        await self.async_set_unique_id(name.casefold())
        entries = self._async_current_entries()
        if entries:
            existing = entries[0]
            if len(entries) == 1 and existing.unique_id == name.casefold() and existing.data.get("legacy_yaml"):
                if dict(existing.data) != {**user_input, "legacy_yaml": True}:
                    _LOGGER.warning("P2000 YAML changes ignored after import; GUI options own settings. No values were overwritten")
                return self.async_abort(reason="yaml_owned_by_options")
            _LOGGER.error("P2000 YAML import blocked: an entry already exists; resolve identity/ownership manually, no conversion was performed")
            return self.async_abort(reason="identity_conflict")
        return self.async_create_entry(title=name, data={**user_input, "legacy_yaml": True})

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            name = user_input["name"].strip()
            if not name:
                return self.async_show_form(step_id="user", data_schema=me_schema(user_input), errors={"base": "invalid_name"})
            await self.async_set_unique_id(name.casefold())
            if self._async_current_entries():
                return self.async_abort(reason="identity_conflict")
            user_input["name"] = name
            return self.async_create_entry(title=name, data=user_input)
        return self.async_show_form(step_id="user", data_schema=me_schema({"latitude": self.hass.config.latitude, "longitude": self.hass.config.longitude}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return MeOptionsFlow()


class MeOptionsFlow(config_entries.OptionsFlowWithReload):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            # Names determine entity identity. Refuse instead of silently ignoring edits.
            if user_input["name"] != self.config_entry.data["name"]:
                return self.async_show_form(step_id="init", data_schema=me_schema({**self.config_entry.data, **self.config_entry.options}), errors={"base": "name_fixed"})
            return self.async_create_entry(data=user_input)
        return self.async_show_form(step_id="init", data_schema=me_schema({**self.config_entry.data, **self.config_entry.options}))
