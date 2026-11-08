"""Real HA flow manager/registries with temporary config; entry setup is stubbed."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant import loader
from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryState, SOURCE_IMPORT, SOURCE_USER
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.storage import Store
from homeassistant.helpers import config_validation as cv
import json
import probatio
from custom_components.p2000 import async_setup_entry, MeCoordinator
from test_me_migration import OLD
import copy
from homeassistant.helpers import entity_registry as er, device_registry as dr
from custom_components.p2000.me_config import me_normalize
from custom_components.p2000.sensor import MeP2000Sensor
from types import SimpleNamespace


class ManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        Path(self.tmp.name, 'custom_components').symlink_to(Path(__file__).resolve().parents[1] / 'custom_components', target_is_directory=True)
        self.hass = HomeAssistant(self.tmp.name)
        loader.async_setup(self.hass)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        await self.hass.config_entries.async_initialize()
        self.setup = patch.object(ConfigEntry, 'async_setup', new_callable=AsyncMock, return_value=True)
        self.setup.start()

    async def asyncTearDown(self):
        await self.hass.async_stop()
        self.setup.stop()
        self.tmp.cleanup()

    def assert_frontend_form(self, form, expected_fields):
        self.assertEqual(form['type'], 'form')
        fields = probatio.to_field_list(form['data_schema'], custom_serializer=cv.custom_serializer)
        json.dumps(fields)
        self.assertTrue(expected_fields <= {field['name'] for field in fields})
        icon = next(field for field in fields if field['name'] == 'icon')
        self.assertEqual(icon['selector'], {'icon': {}})

    async def test_gui_forms_serialize_for_frontend(self):
        flow = self.hass.config_entries.flow
        form = await flow.async_init('p2000', context={'source': SOURCE_USER})
        self.assert_frontend_form(form, {'name', 'icon', 'places', 'grip_radius_m'})
        values = form['data_schema']({'name':' ', 'latitude':51, 'longitude':5, 'radius_m':0,
            'grip_radius_m':0, 'places':'', 'disciplines':'', 'fire_codes':'', 'lifeliner_codes':'',
            'grip_place_fallback':False, 'scan_interval':30})
        invalid = await flow.async_configure(form['flow_id'], values)
        self.assertEqual(invalid['errors']['base'], 'invalid_name')
        self.assert_frontend_form(invalid, {'name', 'icon', 'places'})
        values['name'] = 'Fixture'
        result = await flow.async_configure(form['flow_id'], values)
        options = await self.hass.config_entries.options.async_init(result['result'].entry_id)
        self.assert_frontend_form(options, {'name', 'icon', 'places'})
        values['name'] = 'Other'
        invalid_options = await self.hass.config_entries.options.async_configure(options['flow_id'], values)
        self.assertEqual(invalid_options['errors']['base'], 'name_fixed')
        self.assert_frontend_form(invalid_options, {'name', 'icon', 'places'})

    async def test_imported_legacy_options_forms_serialize_for_frontend(self):
        result = await self.hass.config_entries.flow.async_init('p2000', context={'source': SOURCE_IMPORT}, data=me_normalize({'name':'Fixture'}))
        options = await self.hass.config_entries.options.async_init(result['result'].entry_id)
        self.assert_frontend_form(options, {'name', 'icon', 'regios', 'capcodes', 'contains', 'nolocation', 'migrate_legacy_store'})
        values = options['data_schema']({'name':'Other', 'latitude':51, 'longitude':5, 'radius_m':0, 'disciplines':'', 'scan_interval':10})
        invalid = await self.hass.config_entries.options.async_configure(options['flow_id'], values)
        self.assertEqual(invalid['errors']['base'], 'name_fixed')
        self.assert_frontend_form(invalid, {'name', 'icon', 'regios', 'migrate_legacy_store'})

    async def test_import_and_duplicates(self):
        flow = self.hass.config_entries.flow
        data = me_normalize({'name':'Fixture', 'scan_interval':1})
        first = await flow.async_init('p2000', context={'source': SOURCE_IMPORT}, data=data)
        self.assertEqual(first['type'], 'create_entry')
        again = await flow.async_init('p2000', context={'source': SOURCE_IMPORT}, data=data)
        self.assertEqual(again['type'], 'abort')
        self.assertEqual(again['reason'], 'yaml_owned_by_options')
        form = await flow.async_init('p2000', context={'source': SOURCE_USER})
        result = await flow.async_configure(form['flow_id'], {'name':'fixture', 'latitude':51., 'longitude':5., 'radius_m':0, 'grip_radius_m':0, 'places':'', 'disciplines':'', 'fire_codes':'', 'lifeliner_codes':'', 'grip_place_fallback':False, 'scan_interval':30})
        self.assertEqual(result['type'], 'abort')
        self.assertEqual(len(self.hass.config_entries.async_entries('p2000')), 1)

    async def test_options_manager_persists_legacy_settings(self):
        flow = self.hass.config_entries.flow
        result = await flow.async_init('p2000', context={'source': SOURCE_IMPORT}, data=me_normalize({'name':'Fixture','scan_interval':1}))
        entry = result['result']
        form = await self.hass.config_entries.options.async_init(entry.entry_id)
        options = form['data_schema']({'name':'Fixture', 'latitude':51, 'longitude':5, 'radius_m':0,
            'disciplines':'Politiediensten', 'scan_interval':2, 'regios':'1', 'capcodes':'',
            'contains':'', 'nolocation':True})
        with patch.object(self.hass.config_entries, 'async_schedule_reload') as reload:
            done = await self.hass.config_entries.options.async_configure(form['flow_id'], options)
            reload.assert_called_once_with(entry.entry_id)
        self.assertEqual(done['type'], 'create_entry')
        self.assertEqual(entry.options['scan_interval'], 2)
        self.assertEqual(entry.options['regios'], '1')

    async def test_gui_first_does_not_silently_convert_to_yaml(self):
        flow = self.hass.config_entries.flow
        form = await flow.async_init('p2000', context={'source': SOURCE_USER})
        result = await flow.async_configure(form['flow_id'], {'name':'Fixture', 'latitude':51., 'longitude':5., 'radius_m':0, 'grip_radius_m':0, 'places':'', 'disciplines':'', 'fire_codes':'', 'lifeliner_codes':'', 'grip_place_fallback':False, 'scan_interval':30})
        self.assertEqual(result['type'], 'create_entry')
        imported = await flow.async_init('p2000', context={'source': SOURCE_IMPORT}, data=me_normalize({'name':'Fixture'}))
        self.assertEqual(imported['reason'], 'identity_conflict')
        self.assertFalse(result['result'].data.get('legacy_yaml', False))


    async def test_changed_yaml_cannot_overwrite_options_or_add_second_entry(self):
        flow = self.hass.config_entries.flow
        first = await flow.async_init('p2000', context={'source': SOURCE_IMPORT}, data=me_normalize({'name':'Fixture', 'scan_interval':1}))
        entry = first['result']
        self.hass.config_entries.async_update_entry(entry, options={'scan_interval': 7})
        with self.assertLogs('custom_components.p2000.config_flow', level='WARNING') as logs:
            again = await flow.async_init('p2000', context={'source': SOURCE_IMPORT}, data=me_normalize({'name':'Fixture', 'scan_interval':99}))
        self.assertEqual(again['reason'], 'yaml_owned_by_options')
        self.assertIn('ignored', ' '.join(logs.output))
        self.assertEqual(entry.data['scan_interval'], 1)
        self.assertEqual(entry.options['scan_interval'], 7)
        other = await flow.async_init('p2000', context={'source': SOURCE_IMPORT}, data=me_normalize({'name':'Other'}))
        self.assertEqual(other['reason'], 'identity_conflict')
        self.assertEqual(len(self.hass.config_entries.async_entries('p2000')), 1)


    async def test_options_after_retry_schedules_reload_and_migration(self):
        result = await self.hass.config_entries.flow.async_init('p2000', context={'source': SOURCE_IMPORT}, data=me_normalize({'name':'Fixture'}))
        entry = result['result']
        await Store(self.hass, 1, 'p2000_call_queue').async_save(copy.deepcopy(OLD))
        with self.assertRaises(ConfigEntryNotReady):
            await async_setup_entry(self.hass, entry)
        entry._async_set_state(self.hass, ConfigEntryState.SETUP_RETRY, 'migration selection required')
        form = await self.hass.config_entries.options.async_init(entry.entry_id)
        values = form['data_schema']({'name':'Fixture', 'latitude':51, 'longitude':5, 'radius_m':0,
            'disciplines':'', 'scan_interval':10, 'migrate_legacy_store':True})
        async def reload_entry(entry_id):
            self.assertEqual(entry_id, entry.entry_id)
            return await async_setup_entry(self.hass, entry)
        with patch.object(self.hass.config_entries, 'async_reload', side_effect=reload_entry) as reload, \
             patch.object(self.hass.config_entries, 'async_forward_entry_setups', new_callable=AsyncMock), \
             patch.object(MeCoordinator, 'async_config_entry_first_refresh', new_callable=AsyncMock):
            done = await self.hass.config_entries.options.async_configure(form['flow_id'], values)
            self.assertEqual(done['type'], 'create_entry')
            await self.hass.async_block_till_done()
            reload.assert_awaited_once_with(entry.entry_id)
        self.assertTrue(entry.options['migrate_legacy_store'])
        self.assertEqual(await Store(self.hass, 1, 'p2000_me_'+entry.entry_id).async_load(), OLD)
        self.assertEqual(await Store(self.hass, 1, 'p2000_call_queue').async_load(), OLD)

    async def test_options_refuse_identity_rename(self):
        result = await self.hass.config_entries.flow.async_init('p2000', context={'source': SOURCE_IMPORT}, data=me_normalize({'name':'Fixture'}))
        entry = result['result']
        form = await self.hass.config_entries.options.async_init(entry.entry_id)
        values = form['data_schema']({'name':'Other', 'latitude':51, 'longitude':5, 'radius_m':0, 'disciplines':'', 'scan_interval':10})
        result = await self.hass.config_entries.options.async_configure(form['flow_id'], values)
        self.assertEqual(result['errors']['base'], 'name_fixed')
        self.assertEqual(entry.options, {})


    async def test_concurrent_imports_different_names_do_not_make_two_entries(self):
        flow = self.hass.config_entries.flow
        results = await asyncio.gather(*(flow.async_init('p2000', context={'source': SOURCE_IMPORT}, data=me_normalize({'name': name})) for name in ('One', 'Two')))
        self.assertEqual(sum(result['type'] == 'create_entry' for result in results), 1)
        self.assertEqual(len(self.hass.config_entries.async_entries('p2000')), 1)


    async def test_full_entry_retry_options_reload_with_real_manager(self):
        self.setup.stop()
        await Store(self.hass, 1, 'p2000_call_queue').async_save(copy.deepcopy(OLD))
        self.hass.config.components.add('p2000')
        from custom_components.p2000 import async_setup
        await async_setup(self.hass, {})
        with patch.object(self.hass.config_entries, 'async_forward_entry_setups', new_callable=AsyncMock), \
             patch.object(MeCoordinator, 'async_config_entry_first_refresh', new_callable=AsyncMock):
            result = await self.hass.config_entries.flow.async_init('p2000', context={'source': SOURCE_IMPORT}, data=me_normalize({'name':'Fixture'}))
            entry = result['result']
            await self.hass.async_block_till_done()
            self.assertEqual(entry.state, ConfigEntryState.SETUP_RETRY)
            form = await self.hass.config_entries.options.async_init(entry.entry_id)
            values = form['data_schema']({'name':'Fixture','latitude':51,'longitude':5,'radius_m':0,'disciplines':'','scan_interval':10,'migrate_legacy_store':True})
            await self.hass.config_entries.options.async_configure(form['flow_id'], values)
            await self.hass.async_block_till_done()
            self.assertEqual(entry.state, ConfigEntryState.LOADED)
            self.assertEqual(await Store(self.hass, 1, 'p2000_me_'+entry.entry_id).async_load(), OLD)

    async def test_legacy_registry_identity(self):
        dr.async_setup(self.hass)
        await dr.async_load(self.hass)
        await er.async_load(self.hass)
        registry = er.async_get(self.hass)
        old = registry.async_get_or_create('sensor', 'p2000', 'Fixture', suggested_object_id='old_sensor')
        sensor = MeP2000Sensor(SimpleNamespace(), SimpleNamespace(title='Fixture', entry_id='entry', data={'name':'Fixture', 'legacy_yaml':True}, options={}))
        new = registry.async_get_or_create('sensor', 'p2000', sensor.unique_id, suggested_object_id='new_sensor')
        self.assertEqual(old.entity_id, new.entity_id)
