import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('me_core', Path(__file__).parents[1] / 'custom_components/p2000/me_core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

CONFIG = {'places': ['Exampletown', 'Example village'], 'latitude': 51.0, 'longitude': 5.0, 'radius_m': 5000, 'grip_radius_m': 15000, 'grip_place_fallback': True, 'disciplines': ['Politiediensten', 'Brandweerdiensten', 'Lifeliner'], 'fire_codes': ['9900001', '9900002'], 'lifeliner_codes': ['9900003']}

def row(code='9900001', message='P 1 BOB-03 BR woning Exampletown', dienst='', published='Sat, 7 Nov 2026 12:08:14 +0100', **kw):
    return dict(code=code, message=message, dienst=dienst, published=published, **kw)

class Store:
    def __init__(self): self.data = None; self.fail = False
    async def async_load(self): return copy.deepcopy(self.data)
    async def async_save(self, value):
        if self.fail: raise OSError('test failure')
        self.data = copy.deepcopy(value)

class Selection(unittest.TestCase):
    def test_inferred_fire_and_ambulance_negative(self):
        self.assertTrue(core.me_select(row(), CONFIG))
        self.assertFalse(core.me_select(row(), {**CONFIG, 'fire_codes': []}))
        self.assertFalse(core.me_select(row(message='P 1 BOB-03 BR woning Exampletown AMBU 1'), CONFIG))
        self.assertFalse(core.me_select(row(code='9999999'), CONFIG))
        self.assertFalse(core.me_select(row(dienst='Ambulancediensten'), CONFIG))
        self.assertTrue(core.me_select(row(code='9900003', dienst='Ambulancediensten'), CONFIG))
    def test_location_and_grip(self):
        self.assertTrue(core.me_select(row(dienst='Politiediensten', lat=51.01, lon=5), CONFIG))
        self.assertFalse(core.me_select(row(dienst='Politiediensten', lat=52, lon=5), CONFIG))
        self.assertTrue(core.me_select(row(message='GRIP 2 incident', dienst='Ambulancediensten', lat=51.1, lon=5), CONFIG))
        self.assertFalse(core.me_select(row(message='GRIP 2 incident', dienst='Ambulancediensten', lat=52, lon=5), CONFIG))
        self.assertFalse(core.me_select(row(lat='bad', lon=''), CONFIG))
    def test_distinct_keys(self):
        self.assertNotEqual(core.me_key(row()), core.me_key(row(code='9900002')))
        self.assertNotEqual(core.me_key(row()), core.me_key(row(published='Sat, 7 Nov 2026 12:09:14 +0100')))
        self.assertEqual(core.me_key(row()), core.me_key(row(dienst='Brandweerdiensten')))

class Queue(unittest.IsolatedAsyncioTestCase):
    async def test_missing_location_is_unknown_not_zero(self):
        store = Store(); events = []; q = core.MeQueue(store, CONFIG, events.append)
        await q.load(); await q.poll([row(code='999', message='baseline')]); await q.poll([row()])
        self.assertEqual(len(events), 1)
        for field in ('latitude', 'longitude', 'distance'):
            self.assertIsNone(events[0][field])

    async def test_dedup_ack_and_restart(self):
        store = Store(); events = []; q = core.MeQueue(store, CONFIG, events.append); await q.load()
        baseline = row(code='999', message='baseline', dienst='Gereserveerd')
        await q.poll([baseline])
        a = row(); b = row(code='9900002'); c = row(published='Sat, 7 Nov 2026 12:09:14 +0100')
        await q.poll([c,b,a,baseline]); await q.poll([c,b,a,baseline])
        self.assertEqual(len(events), 3); self.assertEqual(len(q.unread()), 3)
        await q.ack(events[0]['id'])
        self.assertEqual(len(q.unread()), 2); self.assertEqual(len(q.data['archive']), 3)
        latest = q.data['latest']; q2 = core.MeQueue(store, CONFIG, events.append); await q2.load()
        self.assertEqual(q2.data['latest'], latest); self.assertEqual(len(q2.unread()), 2)
        await q2.poll([c,b,a,baseline]); self.assertEqual(len(events), 3)
    async def test_no_sibling_inheritance(self):
        store = Store(); events = []; q = core.MeQueue(store, CONFIG, events.append); await q.load()
        await q.poll([row(code='999', message='baseline')]); await q.poll([row(),row(code='9999999')])
        self.assertEqual(len(events),1)
    async def test_failed_save_rolls_back(self):
        store = Store(); events = []; q = core.MeQueue(store, CONFIG, events.append); await q.load(); await q.poll([row(code='999',message='baseline')])
        before = copy.deepcopy(q.data); store.fail = True
        with self.assertRaises(OSError): await q.poll([row()])
        self.assertEqual(q.data, before); self.assertEqual(events, [])
    async def test_invalid_store_not_overwritten(self):
        store=Store(); store.data={'initialized': True}; q=core.MeQueue(store,CONFIG,lambda x: None)
        with self.assertRaises(ValueError): await q.load()
        self.assertEqual(store.data, {'initialized': True})
    async def test_queue_full_retries(self):
        store=Store(); events=[]; q=core.MeQueue(store,CONFIG,events.append); q.max_unread=1
        await q.load(); await q.poll([row(code='999',message='baseline')]); await q.poll([row()]); other=row(code='9900002'); await q.poll([other])
        self.assertEqual(len(events),1); await q.ack(events[0]['id']); await q.poll([other]); self.assertEqual(len(events),2)

    async def test_rejected_selection_change_does_not_replay(self):
        store=Store(); events=[]; q=core.MeQueue(store,dict(CONFIG),events.append)
        await q.load(); await q.poll([row(code='999',message='baseline')])
        rejected=row(code='9999999', dienst='Ambulancediensten')
        await q.poll([rejected]); self.assertEqual(len(q.data['rejected']),1)
        q.config['disciplines'] = ['Ambulancediensten']
        await q.poll([rejected]); self.assertEqual(events,[])
        new=row(code='9999999', dienst='Ambulancediensten', published='Sat, 7 Nov 2026 12:10:14 +0100')
        await q.poll([new]); self.assertEqual(len(events),1)

    async def test_discipline_enrichment_not_redelivered(self):
        store=Store(); events=[]; q=core.MeQueue(store,CONFIG,events.append)
        await q.load(); await q.poll([row(code='999',message='baseline')])
        await q.poll([row()]); await q.poll([row(dienst='Brandweerdiensten')])
        self.assertEqual(len(events),1)

    async def test_bounded_archive_does_not_erase_latest(self):
        store=Store(); events=[]; q=core.MeQueue(store,CONFIG,events.append); q.max_archive=2
        await q.load(); await q.poll([row(code='999',message='baseline')])
        for minute in (10,11,12):
            await q.poll([row(published=f'Sat, 7 Nov 2026 12:{minute}:14 +0100')])
        self.assertEqual(len(q.data['archive']),2)
        self.assertEqual(len(q.unread()),3)
        self.assertEqual(q.data['latest']['id'], events[-1]['id'])

if __name__ == '__main__': unittest.main()
