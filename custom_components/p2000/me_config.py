"""Shared validated configuration, including legacy YAML semantics."""
import datetime
import voluptuous as vol

DEFAULTS = {"name": "P2000", "icon": "mdi:ambulance", "scan_interval": 10,
            "latitude": 0.0, "longitude": 0.0, "radius_m": 0.0,
            "grip_radius_m": 0.0, "grip_place_fallback": False,
            "places": "", "disciplines": "", "regios": "", "capcodes": "",
            "contains": "", "nolocation": True, "fire_codes": "", "lifeliner_codes": ""}


def me_schema(defaults=None):
    values = {**DEFAULTS, **(defaults or {})}
    validators = {"name": vol.All(str, vol.Length(min=1, max=100)), "icon": str,
                  "scan_interval": vol.All(vol.Coerce(float), vol.Range(min=0, min_included=False)),
                  "latitude": vol.All(vol.Coerce(float), vol.Range(min=-90, max=90)),
                  "longitude": vol.All(vol.Coerce(float), vol.Range(min=-180, max=180)),
                  "radius_m": vol.All(vol.Coerce(float), vol.Range(min=0, max=2000000)),
                  "grip_radius_m": vol.All(vol.Coerce(float), vol.Range(min=0, max=2000000)),
                  "grip_place_fallback": bool, "nolocation": bool}
    return vol.Schema({vol.Optional(k, default=values[k]): validators.get(k, str) for k in DEFAULTS})


def me_normalize(data):
    result = dict(data)
    interval = result.get("scan_interval", 10)
    if isinstance(interval, datetime.timedelta):
        result["scan_interval"] = interval.total_seconds()
    if "radius" in result:
        result["radius_m"] = result.pop("radius")
    result.pop("platform", None)
    return me_schema()(result)


def me_list(value):
    return [v.strip() for v in value.split(",") if v.strip()] if isinstance(value, str) else list(value)


def me_selection(config):
    return {**config, **{key: me_list(config.get(key, "")) for key in
            ("places", "disciplines", "regios", "capcodes", "fire_codes", "lifeliner_codes")}}
