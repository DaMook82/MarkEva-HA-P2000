"""Reusable P2000 selection and durable queue primitives."""
from __future__ import annotations

import copy
import datetime as dt
import email.utils
import hashlib
import json
import math
import re


_FIRE = re.compile(r'\bBOB-\d{2}\s+BR\s+(?:woning|buiten|wegvervoer)\b', re.I)
_AMBULANCE = re.compile(r'\b(?:ambu(?:lance)?|ritnummer|ambulancedienst)\b', re.I)
_GRIP = re.compile(r'\bGRIP\s*[-:]?\s*[1-5]\b', re.I)
_UNSET = object()


def me_validate_store(data):
    """Reject unsafe records without modifying or displaying owner data."""
    def fail(path):
        raise ValueError(path)

    def identifier(value, path):
        if not isinstance(value, str) or not value:
            fail(path)

    def event(value, path, timestamp=False):
        if not isinstance(value, dict):
            fail(path)
        identifier(value.get('id'), path + '.id')
        if timestamp:
            if not isinstance(value.get('msgtext'), str):
                fail(path + '.msgtext')
            try:
                stamp = dt.datetime.fromisoformat(value['msgtime'])
                if stamp.tzinfo is None or stamp.utcoffset() is None:
                    fail(path + '.msgtime')
            except (KeyError, TypeError, ValueError):
                fail(path + '.msgtime')

    if not isinstance(data, dict) or type(data.get('initialized')) is not bool:
        fail('initialized')
    for key in ('seen', 'items', 'archive'):
        if not isinstance(data.get(key), list):
            fail(key)
    if not data['initialized'] and (data['seen'] or data['items'] or data['archive'] or data.get('latest') is not None):
        fail('initialized conflicts with stored records')
    for index, key in enumerate(data['seen']):
        identifier(key, f'seen[{index}]')
    item_ids = set()
    for index, item in enumerate(data['items']):
        path = f'items[{index}]'
        if not isinstance(item, dict):
            fail(path)
        event(item.get('event'), path + '.event', True)
        if item['event']['id'] in item_ids:
            fail(path + '.event.id duplicate')
        item_ids.add(item['event']['id'])
        for key in ('ack', 'delivered'):
            if type(item.get(key)) is not bool:
                fail(path + '.' + key)
    archive_ids = set()
    for index, item in enumerate(data['archive']):
        event(item, f'archive[{index}]', True)
        if item['id'] in archive_ids:
            fail(f'archive[{index}].id duplicate')
        archive_ids.add(item['id'])
        if 'acknowledged' in item and type(item['acknowledged']) is not bool:
            fail(f'archive[{index}].acknowledged')
    if data.get('latest') is not None:
        event(data['latest'], 'latest', True)
    if 'rejected' in data and (not isinstance(data['rejected'], list) or any(not isinstance(x, dict) for x in data['rejected'])):
        fail('rejected')


def me_key(row):
    """Use stable source fields; mutable discipline enrichment is not a new call.

    The feed has no GUID. Identical time, text and capcode from different
    services are indistinguishable and can therefore collide.
    """
    parts = [str(row.get(k) or '') for k in ('published', 'message', 'code')]
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def me_legacy_key(row):
    parts = [str(row.get(k) or '') for k in ('published', 'message', 'dienst', 'code')]
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def me_position(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
        if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180 and (lat, lon) != (0, 0):
            return lat, lon
    except (TypeError, ValueError):
        pass
    return None


def me_distance(a, b):
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 12742000 * math.atan2(math.sqrt(h), math.sqrt(max(0, 1-h)))


def me_classify(row, config=None):
    config = config or {}
    discipline = str(row.get('dienst') or '').strip()
    code, text = str(row.get('code') or '').strip(), str(row.get('message') or '')
    if code in config.get('lifeliner_codes', []):
        return 'Lifeliner', 'configured_capcode'
    if not discipline and code in config.get('fire_codes', []) and _FIRE.search(text) and not _AMBULANCE.search(text):
        return 'Brandweerdiensten', 'me_capcode_and_fire_text'
    if discipline == 'Ambulancediensten' and re.search(r'\blife\s*liner\b', text, re.I):
        return 'Lifeliner', 'message_text'
    return discipline, 'source_discipline'


def me_select(row, config):
    """Use configured place names and/or radius; never infer sibling discipline.

    Text matching is a fallback, not evidence of municipality boundary coverage.
    GRIP independently uses a configured radius and requires coordinates, unless
    a configured place-name fallback is explicitly enabled.
    """
    text = str(row.get('message') or '')
    discipline, _ = me_classify(row, config)
    regios = config.get('regios', [])
    capcodes = config.get('capcodes', [])
    if regios and str(row.get('regcode', '')).lstrip('0') not in regios:
        return False
    if capcodes and str(row.get('code', '')) not in capcodes:
        return False
    if config.get('contains') and config['contains'] not in text:
        return False
    # Imported upstream YAML filters are conjunctive, with an unset filter permissive.
    if config.get('legacy_yaml'):
        disciplines = config.get('disciplines', [])
        if disciplines and discipline not in disciplines:
            return False
        pos = me_position(row.get('lat'), row.get('lon'))
        if not pos:
            return bool(config.get('nolocation', True))
        centre = me_position(config.get('latitude'), config.get('longitude'))
        radius = float(config.get('radius_m', 0))
        return not radius or bool(centre and me_distance(centre, pos) <= radius)
    lat, lon = row.get('lat'), row.get('lon')
    pos = me_position(lat, lon)
    if pos is None and (lat not in (None, '') or lon not in (None, '')):
        return False
    centre = me_position(config.get('latitude'), config.get('longitude'))
    distance = me_distance(centre, pos) if centre and pos else None
    names = config.get('places', [])
    place_match = any(re.search(r'(?<!\w)' + re.escape(name.strip()) + r'(?!\w)', text, re.I) for name in names if name.strip())
    radius = float(config.get('radius_m', 0))
    grip_radius = float(config.get('grip_radius_m', 0))
    within = radius > 0 and distance is not None and distance <= radius
    local = within or (pos is None and place_match)
    discipline, _ = me_classify(row, config)
    ordinary = local and discipline in config.get('disciplines', [])
    grip = bool(_GRIP.search(text)) and ((grip_radius > 0 and distance is not None and distance <= grip_radius) or (pos is None and config.get('grip_place_fallback', False) and place_match))
    return bool(ordinary or grip)


class MeQueue:
    """Persist before emitting; at-least-once delivery if interrupted after emit."""
    max_unread = 50
    max_seen = 4096
    max_archive = 2000

    def __init__(self, store, config, emit):
        self.store, self.config, self.emit = store, config, emit
        self.data = {'initialized': False, 'seen': [], 'items': [], 'archive': [], 'latest': None, 'rejected': []}
        self.committed = copy.deepcopy(self.data)

    async def load(self, saved=_UNSET):
        if saved is _UNSET:
            saved = await self.store.async_load()
        if saved is not None:
            me_validate_store(saved)
            self.data = saved
            self.data.setdefault('rejected', [])
        self.committed = copy.deepcopy(self.data)

    async def save(self):
        try:
            await self.store.async_save(copy.deepcopy(self.data))
        except Exception:
            self.data = copy.deepcopy(self.committed)
            raise
        self.committed = copy.deepcopy(self.data)

    def unread(self):
        return [copy.deepcopy(x['event']) for x in self.data['items'] if not x['ack']]

    async def ack(self, item_id):
        for item in self.data['items']:
            if item['event']['id'] == item_id:
                item['ack'] = True
                for archived in self.data['archive']:
                    if archived['id'] == item_id:
                        archived['acknowledged'] = True
                await self.save()
                return True
        return False

    async def deliver(self):
        for item in self.data['items']:
            if not item['delivered']:
                self.emit(copy.deepcopy(item['event']))
                item['delivered'] = True
                await self.save()

    async def poll(self, rows):
        rows = [row for row in rows if isinstance(row, dict)]
        if not rows:
            return
        if not self.data['initialized']:
            self.data['seen'] = list(dict.fromkeys(me_key(row) for row in rows))[-self.max_seen:]
            self.data['initialized'] = True
            await self.save()
            return
        await self.deliver()
        seen = set(self.data['seen']) | {x['id'] for x in self.data['archive']}
        for row in reversed(rows):
            key = me_key(row)
            legacy_keys = {me_legacy_key({**row, 'dienst': discipline}) for discipline in
                           (row.get('dienst'), '', 'Gereserveerd', 'Politiediensten',
                            'Brandweerdiensten', 'Ambulancediensten', 'Lifeliner')}
            if key in seen or not legacy_keys.isdisjoint(seen):
                continue
            if not all(isinstance(row.get(k), str) and row[k].strip() for k in ('published', 'message', 'code')):
                continue
            try:
                stamp = email.utils.parsedate_to_datetime(row['published'])
                if stamp is None or stamp.tzinfo is None:
                    continue
            except (ValueError, TypeError, OverflowError):
                continue
            accepted = me_select(row, self.config)
            if accepted and len(self.unread()) >= self.max_unread:
                continue
            seen.add(key)
            self.data['seen'] = (self.data['seen'] + [key])[-self.max_seen:]
            if not accepted:
                # Remember rejections to prevent unexpected historical events after
                # selection changes. Bounded diagnostics contain no message text.
                discipline, method = me_classify(row, self.config)
                self.data['rejected'] = (self.data['rejected'] + [{'id': key, 'discipline': discipline, 'method': method}])[-100:]
            if accepted:
                discipline, method = me_classify(row, self.config)
                event = {'id': key, 'msgtext': row['message'], 'msgtime': stamp.isoformat(), 'capcode': row['code'], 'discipline': discipline, 'source_discipline': str(row.get('dienst') or ''), 'classification_method': method, 'received_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'source': 'feed'}
                pos = me_position(row.get('lat'), row.get('lon'))
                centre = me_position(self.config.get('latitude'), self.config.get('longitude'))
                event.update({'latitude': pos[0] if pos else None, 'longitude': pos[1] if pos else None,
                              'distance': round(me_distance(centre, pos)) if centre and pos else None,
                              'regio': str(row.get('regcode') or '').lstrip('0'),
                              'regioname': str(row.get('regname') or '')})
                self.data['items'] = [x for x in self.data['items'] if not x['ack'] or not x['delivered']]
                self.data['items'].append({'event': event, 'ack': False, 'delivered': False})
                archived = {**event, 'archived_at': event['received_at'], 'acknowledged': False}
                self.data['archive'].append(archived)
                if self.config.get('legacy_yaml'):
                    # Preserve the prior 90-day retention; records with unknown
                    # archive dates are never silently discarded.
                    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=90)
                    def keep(item):
                        try:
                            stamp = dt.datetime.fromisoformat(item['archived_at'])
                            return stamp.tzinfo is None or stamp >= cutoff
                        except (KeyError, TypeError, ValueError):
                            return True
                    self.data['archive'] = [item for item in self.data['archive'] if keep(item)]
                else:
                    self.data['archive'] = self.data['archive'][-self.max_archive:]
                old = self.data.get('latest')
                if not old or stamp > dt.datetime.fromisoformat(old['msgtime']):
                    self.data['latest'] = copy.deepcopy(event)
            await self.save()
            if accepted:
                await self.deliver()
