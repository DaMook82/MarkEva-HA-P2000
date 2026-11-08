"""Isolated YAML migration and legacy-filter contract tests; no HA lifecycle."""
import datetime
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from custom_components.p2000.me_config import me_normalize, me_selection
from custom_components.p2000.me_core import me_select
from custom_components.p2000.sensor import async_setup_platform, MeP2000Sensor, PLATFORM_SCHEMA
from custom_components.p2000.config_flow import MeConfigFlow


class YamlTests(unittest.IsolatedAsyncioTestCase):
    async def test_yaml_dispatch_does_not_start_legacy_sensor(self):
        init = AsyncMock()
        hass = SimpleNamespace(config=SimpleNamespace(latitude=51.0, longitude=5.0),
                               config_entries=SimpleNamespace(flow=SimpleNamespace(async_init=init)))
        add = Mock()
        yaml = PLATFORM_SCHEMA({'platform': 'p2000', 'name':'Fixture', 'regios':'1,2',
                                'disciplines':'Politiediensten', 'capcodes':'123',
                                'contains':'fixture', 'radius':1000, 'nolocation':False})
        await async_setup_platform(hass, yaml, add)
        add.assert_not_called()
        data = init.await_args.kwargs['data']
        self.assertEqual(data['radius_m'], 1000)
        self.assertEqual(data['scan_interval'], 10)
        self.assertEqual(data['regios'], '1,2')
        self.assertEqual(data['latitude'], 51.0)

    async def test_import_flow_preserves_legacy_unique_id(self):
        flow = MeConfigFlow()
        flow.async_set_unique_id = AsyncMock()
        flow._async_current_entries = Mock(return_value=[])
        flow.async_create_entry = Mock(return_value={'type':'create_entry'})
        data = me_normalize({'name':'Fixture', 'scan_interval':datetime.timedelta(seconds=10)})
        await flow.async_step_import(data)
        flow.async_set_unique_id.assert_awaited_once_with('fixture')
        flow._async_current_entries.assert_called_once()
        saved = flow.async_create_entry.call_args.kwargs['data']
        self.assertTrue(saved['legacy_yaml'])
        sensor = MeP2000Sensor(SimpleNamespace(), SimpleNamespace(title='Fixture', entry_id='id', data=saved, options={}))
        self.assertEqual(sensor.unique_id, 'Fixture')

    def test_legacy_filters_conjunction_and_no_location(self):
        config = me_selection({**me_normalize({'name':'Fixture', 'latitude':51., 'longitude':5.,
             'radius':1000, 'regios':'1,2', 'disciplines':'Politiediensten', 'capcodes':'123',
             'contains':'fixture', 'nolocation':False}), 'legacy_yaml':True})
        row = {'regcode':'01','dienst':'Politiediensten','code':'123','message':'fixture call', 'lat':51., 'lon':5.}
        self.assertTrue(me_select(row, config))
        for key, val in [('regcode','03'), ('dienst','Ambulancediensten'), ('code','999'),
                         ('message','different'), ('lat',52.), ('lat',None)]:
            self.assertFalse(me_select({**row, key:val}, config), key)
        self.assertTrue(me_select({**row, 'lat':None, 'lon':None}, {**config, 'nolocation':True}))

    def test_preserves_unusual_positive_intervals(self):
        self.assertEqual(me_normalize({"scan_interval":datetime.timedelta(milliseconds=500)})["scan_interval"], 0.5)
        self.assertEqual(me_normalize({"scan_interval":datetime.timedelta(days=2)})["scan_interval"], 172800)
