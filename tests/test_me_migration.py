"""Actual HA Store migration and service API with fixture-only feed/forwarding."""
import asyncio
import copy
import tempfile
import unittest
from pathlib import Path
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.storage import Store
from custom_components.p2000 import async_setup, async_setup_entry, MeCoordinator
from custom_components.p2000.me_core import me_legacy_key

ROW = {'published':'Sat, 7 Nov 2026 12:01:00 +0100', 'message':'Example', 'code':'123', 'dienst':'Politiediensten'}
EVENT = {'id':me_legacy_key(ROW), 'msgtext':'Example', 'msgtime':'2026-11-07T12:01:00+01:00', 'capcode':'123', 'discipline':'Politiediensten'}
OLD = {'initialized':True, 'seen':[EVENT['id']], 'items':[{'event':EVENT, 'ack':False, 'delivered':True}], 'archive':[{**EVENT, 'acknowledged':False, 'archived_at':'2026-11-07T12:02:00+01:00'}], 'latest':EVENT}

class MigrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.tmp.name)
        self.entry = SimpleNamespace(entry_id='fixture', title='Fixture', data={'name':'Fixture', 'legacy_yaml':True, 'scan_interval':30}, options={'migrate_legacy_store':True}, async_on_unload=Mock(), add_update_listener=Mock())
        self.hass.config_entries = SimpleNamespace(async_entries=Mock(return_value=[self.entry]), async_forward_entry_setups=AsyncMock())
        await Store(self.hass, 1, 'p2000_call_queue').async_save(copy.deepcopy(OLD))
        await async_setup(self.hass, {})

    async def asyncTearDown(self):
        await self.hass.async_stop()
        self.tmp.cleanup()

    async def test_copy_retain_ack_history_and_no_replay(self):
        with patch.object(MeCoordinator, 'async_config_entry_first_refresh', new_callable=AsyncMock):
            self.assertIsNone(await async_setup_entry(self.hass, self.entry))
        queue = self.hass.data['p2000']['fixture'].queue
        self.assertEqual(queue.unread(), [EVENT])
        self.assertEqual(await Store(self.hass, 1, 'p2000_call_queue').async_load(), OLD)
        events = []
        queue.emit = events.append
        await queue.poll([ROW])
        await queue.poll([{**ROW, 'dienst':'Gereserveerd'}])
        self.assertEqual(events, [])
        history = await self.hass.services.async_call('p2000','get_history', {}, blocking=True, return_response=True)
        self.assertEqual(history['total'],1)
        self.assertEqual(history['retention_days'],90)
        await self.hass.services.async_call('p2000','acknowledge', {'id':EVENT['id']}, blocking=True)
        self.assertEqual(queue.unread(), [])
        self.assertEqual(queue.data['latest'], EVENT)
        self.assertTrue(queue.data['archive'][0]['acknowledged'])
        self.assertEqual(await Store(self.hass, 1, 'p2000_call_queue').async_load(), OLD)

    async def test_ambiguous_migration_refused_without_write(self):
        self.hass.config_entries.async_entries.return_value.append(SimpleNamespace(entry_id='other'))
        with self.assertRaises(ConfigEntryNotReady):
            await async_setup_entry(self.hass, self.entry)
        self.assertIsNone(await Store(self.hass, 1, 'p2000_me_fixture').async_load())
        self.assertEqual(await Store(self.hass, 1, 'p2000_call_queue').async_load(), OLD)

    async def test_migration_requires_explicit_opt_in(self):
        self.entry.options.clear()
        with self.assertRaises(ConfigEntryNotReady):
            await async_setup_entry(self.hass, self.entry)
        self.assertIsNone(await Store(self.hass, 1, "p2000_me_fixture").async_load())
        self.assertEqual(await Store(self.hass, 1, "p2000_call_queue").async_load(), OLD)


    async def test_invalid_old_records_never_copy_or_change_source(self):
        variants = []
        for change in (
            lambda d: d['seen'].append({'bad': 'unhashable'}),
            lambda d: d['items'][0].update(ack='false'),
            lambda d: d['items'][0].update(delivered=1),
            lambda d: d['items'][0]['event'].update(id=None),
            lambda d: d['items'][0]['event'].update(msgtime='bad'),
            lambda d: d['latest'].update(msgtime='2026-01-01T00:00:00'),
            lambda d: d['latest'].pop('msgtext'),
            lambda d: d['archive'][0].update(id=[]),
            lambda d: d.update(rejected=[None]),
            lambda d: d.update(initialized=False),
            lambda d: d['items'].append(copy.deepcopy(d['items'][0])),
            lambda d: d['archive'][0].update(acknowledged='false'),
            lambda d: d['archive'][0].pop('msgtime'),
            lambda d: d['archive'][0].update(msgtext=[]),
            lambda d: d['archive'].append(copy.deepcopy(d['archive'][0])),
        ):
            bad = copy.deepcopy(OLD)
            change(bad)
            variants.append(bad)
        for bad in variants:
            with self.subTest(bad=bad):
                await Store(self.hass, 1, 'p2000_call_queue').async_save(bad)
                with self.assertRaises(ConfigEntryNotReady):
                    await async_setup_entry(self.hass, self.entry)
                self.assertIsNone(await Store(self.hass, 1, 'p2000_me_fixture').async_load())
                self.assertEqual(await Store(self.hass, 1, 'p2000_call_queue').async_load(), bad)


    async def test_corrupt_and_unsupported_envelopes_left_exactly_untouched(self):
        path = Path(self.tmp.name, '.storage', 'p2000_call_queue')
        for payload in (b'{broken', b'{}', b'{"version":1,"version":1,"data":{}}', b'{"version":1,"data":1e999}', json.dumps({'version':1,'minor_version':2,'data':OLD}).encode(),
                        json.dumps({'version':2,'data':OLD}).encode()):
            with self.subTest(payload=payload[:30]):
                path.write_bytes(payload)
                with self.assertRaises(ConfigEntryNotReady):
                    await async_setup_entry(self.hass, self.entry)
                self.assertEqual(path.read_bytes(), payload)
                self.assertFalse(Path(self.tmp.name, '.storage', 'p2000_me_fixture').exists())
                self.assertEqual(list(path.parent.glob('p2000_call_queue.corrupt.*')), [])

    async def test_invalid_target_is_not_overwritten_by_old_store(self):
        target = Path(self.tmp.name, '.storage', 'p2000_me_fixture')
        payload = b'{broken'
        target.write_bytes(payload)
        with self.assertRaises(ConfigEntryNotReady):
            await async_setup_entry(self.hass, self.entry)
        self.assertEqual(target.read_bytes(), payload)
        self.assertEqual(await Store(self.hass, 1, 'p2000_call_queue').async_load(), OLD)


    async def test_existing_target_is_never_replaced_by_shared_history(self):
        newer = copy.deepcopy(OLD)
        newer['seen'].append('newer-key')
        newer['extra_preserved'] = {'unknown': 'retained'}
        await Store(self.hass, 1, 'p2000_me_fixture').async_save(newer)
        with patch.object(MeCoordinator, 'async_config_entry_first_refresh', new_callable=AsyncMock):
            await async_setup_entry(self.hass, self.entry)
        self.assertEqual(await Store(self.hass, 1, 'p2000_me_fixture').async_load(), newer)
        self.assertEqual(await Store(self.hass, 1, 'p2000_call_queue').async_load(), OLD)
        self.assertEqual(self.hass.data['p2000']['fixture'].queue.data['extra_preserved'], {'unknown':'retained'})

    async def test_no_id_rejected_when_multiple_runtimes(self):
        self.hass.data['p2000'].update({'a':object(), 'b':object()})
        with self.assertRaises(HomeAssistantError):
            await self.hass.services.async_call('p2000','clear_unread', {}, blocking=True)
