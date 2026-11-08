"""Isolated tests with actual HA 2026.11 classes, not a full HA runtime."""
import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from custom_components.p2000.me_core import MeQueue
from custom_components.p2000.sensor import MeP2000Sensor
from custom_components.p2000.config_flow import me_schema, MeConfigFlow
from custom_components.p2000 import async_setup, MeCoordinator

CONFIG = {'places':['Testville'], 'latitude':51, 'longitude':5, 'radius_m':1000,
          'grip_radius_m':1000, 'disciplines':['Politiediensten']}
BASE = {'published':'Sat, 7 Nov 2026 12:00:00 +0100', 'message':'baseline', 'code':'1', 'dienst':'Politiediensten'}
CALL = {'published':'Sat, 7 Nov 2026 12:01:00 +0100', 'message':'Testville ' + 'x'*300, 'code':'2', 'dienst':'Politiediensten'}

class RealHATests(unittest.IsolatedAsyncioTestCase):
    async def test_real_store_reload_and_real_sensor(self):
        with tempfile.TemporaryDirectory() as directory:
            hass = HomeAssistant(directory)
            events = []
            queue = MeQueue(Store(hass, 1, 'test_p2000'), CONFIG, events.append)
            await queue.load(); await queue.poll([BASE]); await queue.poll([CALL])
            self.assertEqual(len(events), 1)
            coordinator = SimpleNamespace(queue=queue)
            sensor = MeP2000Sensor(coordinator, SimpleNamespace(entry_id='fixture', title='Fixture', data={}, options={}))
            self.assertEqual(len(sensor.native_value), 255)
            self.assertEqual(sensor.extra_state_attributes['unread_count'],1)
            self.assertIn('unread_calls',sensor._unrecorded_attributes)
            await queue.ack(events[0]['id'])
            restored = MeQueue(Store(hass, 1, 'test_p2000'), CONFIG, events.append)
            await restored.load()
            self.assertEqual(restored.unread(), [])
            self.assertEqual(len(restored.data['archive']), 1)
            self.assertEqual(restored.data['latest']['msgtext'], CALL['message'])
            await hass.async_stop()

    async def test_real_ha_schema_validation(self):
        defaults = {'name':'Fixture','latitude':51.,'longitude':5., 'places':'Testville',
                    'disciplines':['Politiediensten'],'radius_m':1000,'grip_radius_m':1000,
                    'grip_place_fallback':False,'scan_interval':30}
        schema = me_schema(defaults)
        self.assertEqual(schema({**defaults, 'disciplines':['Politiediensten']})['name'],'Fixture')
        self.assertEqual(schema({**defaults,'scan_interval':1})['scan_interval'], 1)
        with self.assertRaises(Exception): schema({**defaults,'scan_interval':0})

    async def test_flow_logic_with_mocked_entry_manager(self):
        with tempfile.TemporaryDirectory() as directory:
            hass = HomeAssistant(directory)
            flow = MeConfigFlow(); flow.hass = hass
            flow.async_set_unique_id = AsyncMock()
            flow._async_current_entries = Mock(return_value=[])
            flow.async_create_entry = Mock(return_value={'type':'create_entry'})
            data = {'name':'  Fixture  ', 'latitude':51., 'longitude':5.,
                    'places':'Testville', 'disciplines':'Politiediensten',
                    'radius_m':1000, 'grip_radius_m':1000,
                    'grip_place_fallback':False, 'scan_interval':30}
            result = await flow.async_step_user(data)
            self.assertEqual(result['type'],'create_entry')
            flow.async_set_unique_id.assert_awaited_once_with('fixture')
            flow._async_current_entries.assert_called_once()
            await hass.async_stop()

    async def test_real_service_registry_and_real_store(self):
        with tempfile.TemporaryDirectory() as directory:
            hass = HomeAssistant(directory)
            queue = MeQueue(Store(hass, 1, 'service_p2000'), CONFIG, lambda event: None)
            await queue.load(); await queue.poll([BASE]); await queue.poll([CALL])
            await async_setup(hass, {})
            runtime = SimpleNamespace(queue=queue, lock=asyncio.Lock(), async_set_updated_data=Mock())
            hass.data['p2000']['fixture'] = runtime
            await hass.services.async_call('p2000','acknowledge', {'config_entry_id':'fixture','id':queue.unread()[0]['id']}, blocking=True)
            self.assertEqual(queue.unread(), [])
            self.assertEqual(len(queue.data['archive']), 1)
            self.assertEqual(queue.data['latest']['msgtext'], CALL['message'])
            runtime.async_set_updated_data.assert_called_once()
            await hass.async_stop()

    async def test_mock_feed_real_coordinator_bus_store_and_services(self):
        with tempfile.TemporaryDirectory() as directory:
            hass = HomeAssistant(directory)
            await async_setup(hass, {})
            events = []
            hass.bus.async_listen('p2000_call', lambda event: events.append(event.data))
            entry = SimpleNamespace(entry_id='fixture', title='Fixture', data={'name':'Fixture', 'latitude':51,
                'longitude':5, 'places':'Testville', 'disciplines':'Politiediensten',
                'radius_m':1000, 'grip_radius_m':1000, 'scan_interval':30}, options={}, async_on_unload=Mock())
            queue = MeQueue(Store(hass, 1, 'coordinator_p2000'), CONFIG,
                lambda event: hass.bus.async_fire('p2000_call', event))
            await queue.load()
            coordinator = MeCoordinator(hass, entry, queue, asyncio.Lock())
            hass.data['p2000']['fixture'] = coordinator
            with patch('custom_components.p2000.feedparser.parse', side_effect=[
                SimpleNamespace(bozo=False, status=200, entries=[BASE]),
                SimpleNamespace(bozo=False, status=200, entries=[CALL, BASE]),
                SimpleNamespace(bozo=False, status=200, entries=[CALL, BASE])]):
                await coordinator._async_update_data()
                await coordinator._async_update_data()
                await coordinator._async_update_data()
            await hass.async_block_till_done()
            self.assertEqual(len(events), 1)
            self.assertEqual(len(queue.unread()), 1)
            await hass.services.async_call('p2000', 'clear_unread', {'config_entry_id':'fixture'}, blocking=True)
            self.assertEqual(queue.unread(), [])
            self.assertEqual(len(queue.data['archive']), 1)
            await hass.async_stop()
