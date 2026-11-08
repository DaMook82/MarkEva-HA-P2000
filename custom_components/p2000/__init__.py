"""P2000 integration with GUI entries and legacy YAML import."""
from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import timedelta
from pathlib import Path

import feedparser
import voluptuous as vol
from homeassistant.const import Platform
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .me_core import MeQueue, me_validate_store
from .me_config import me_selection

DOMAIN = "p2000"
ME_URL = "http://p2000.brandweer-berkel-enschot.nl/homeassistant/rss.asp"
PLATFORMS = [Platform.SENSOR]


async def me_read_store(hass, store):
    """Read without HA Store's corrupt-file rename or automatic version rewrite."""
    def read():
        def pairs(values):
            result = {}
            for key, value in values:
                if key in result:
                    raise ValueError('duplicate JSON key')
                result[key] = value
            return result

        def constant(_value):
            raise ValueError('non-finite JSON number')

        def finite_float(value):
            parsed = float(value)
            if not math.isfinite(parsed):
                raise ValueError('non-finite JSON number')
            return parsed

        try:
            with Path(store.path).open(encoding='utf-8') as handle:
                envelope = json.load(handle, object_pairs_hook=pairs, parse_constant=constant, parse_float=finite_float)
        except FileNotFoundError:
            return None
        if (not isinstance(envelope, dict) or type(envelope.get('version')) is not int
                or envelope['version'] != 1 or type(envelope.get('minor_version', 1)) is not int
                or envelope.get('minor_version', 1) != 1
                or envelope.get('key', store.key) != store.key or 'data' not in envelope
                or envelope['data'] is None):
            raise ValueError('unsupported storage envelope')
        return envelope['data']
    try:
        return await hass.async_add_executor_job(read)
    except (ValueError, OSError) as err:
        raise ConfigEntryNotReady(f'P2000 storage {store.key} unreadable or unsupported; original left untouched') from err


def me_config(entry):
    return me_selection({**entry.data, **entry.options})


class MeCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, queue, lock):
        super().__init__(hass, logger=logging.getLogger(__name__), config_entry=entry, name=f"P2000 {entry.title}", update_interval=timedelta(seconds=me_config(entry)["scan_interval"]))
        self.queue = queue
        self.lock = lock

    async def _async_update_data(self):
        try:
            feed = await self.hass.async_add_executor_job(feedparser.parse, ME_URL)
            if feed.bozo or getattr(feed, "status", 200) >= 400:
                raise UpdateFailed("P2000 feed parsing failed")
            async with self.lock:
                await self.queue.poll([dict(row) for row in feed.entries])
                return self.queue.data
        except UpdateFailed:
            raise
        except Exception as err:
            raise UpdateFailed("P2000 update failed") from err


async def async_setup(hass, config):
    hass.data.setdefault(DOMAIN, {})

    def resolve_runtime(call):
        entry_id = call.data.get("config_entry_id")
        runtimes = hass.data[DOMAIN]
        if entry_id is None:
            if len(runtimes) != 1:
                raise HomeAssistantError("Specify config_entry_id unless exactly one entry is loaded")
            return next(iter(runtimes.values()))
        runtime = runtimes.get(entry_id)
        if runtime is None:
            raise HomeAssistantError("P2000 config entry is not loaded")
        return runtime

    async def me_ack(call):
        runtime = resolve_runtime(call)
        async with runtime.lock:
            if not await runtime.queue.ack(call.data["id"]):
                raise HomeAssistantError("P2000 call not found in queue")
            runtime.async_set_updated_data(runtime.queue.data)

    async def me_clear(call):
        runtime = resolve_runtime(call)
        async with runtime.lock:
            # Each ack is saved: failure does not silently delete history.
            for item in runtime.queue.unread():
                await runtime.queue.ack(item["id"])
            runtime.async_set_updated_data(runtime.queue.data)

    async def me_history(call):
        runtime = resolve_runtime(call)
        async with runtime.lock:
            rows = list(reversed(runtime.queue.data["archive"]))
            offset, limit = call.data["offset"], call.data["limit"]
            return {"calls": rows[offset:offset + limit], "total": len(rows),
                    "offset": offset, "retention_days": 90 if runtime.queue.config.get('legacy_yaml') else None}

    target = {vol.Optional("config_entry_id"): cv.string}
    hass.services.async_register(DOMAIN, "acknowledge", me_ack, schema=vol.Schema({**target, vol.Required("id"): cv.string}))
    hass.services.async_register(DOMAIN, "clear_unread", me_clear, schema=vol.Schema(target))
    hass.services.async_register(DOMAIN, "get_history", me_history,
        schema=vol.Schema({**target, vol.Optional("offset", default=0): vol.All(vol.Coerce(int), vol.Range(min=0)),
                           vol.Optional("limit", default=50): vol.All(vol.Coerce(int), vol.Range(min=1, max=100))}),
        supports_response=SupportsResponse.ONLY)
    return True


async def async_setup_entry(hass, entry):
    lock = asyncio.Lock()
    store = Store(hass, 1, f"p2000_me_{entry.entry_id}")
    # Copy (never move or overwrite) the previous shared store only when its
    # owner is unambiguous. Refuse partial/old schemas rather than lose data.
    saved = await me_read_store(hass, store)
    if saved is None:
        old = await me_read_store(hass, Store(hass, 1, 'p2000_call_queue'))
        if old is not None:
            if (not entry.data.get('legacy_yaml') or not entry.options.get('migrate_legacy_store')
                    or len(hass.config_entries.async_entries(DOMAIN)) != 1):
                raise ConfigEntryNotReady('Existing P2000 data requires an explicitly selected single legacy entry')
            try:
                me_validate_store(old)
            except ValueError as err:
                raise ConfigEntryNotReady(f'Existing P2000 data invalid ({err}); manual migration required') from err
            await store.async_save(old)
            saved = old
    queue = MeQueue(store, me_config(entry), lambda event: hass.bus.async_fire("p2000_call", {**event, "config_entry_id": entry.entry_id}))
    try:
        await queue.load(saved)
    except ValueError as err:
        raise ConfigEntryNotReady(f'P2000 entry storage invalid ({err}); original left untouched') from err
    coordinator = MeCoordinator(hass, entry, queue, lock)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    try:
        await coordinator.async_config_entry_first_refresh()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise


async def async_unload_entry(hass, entry):
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    hass.data[DOMAIN].pop(entry.entry_id, None)
    return True
