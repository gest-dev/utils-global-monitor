"""Transactional local state, schedules and delivery queue."""
import calendar
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid
import unicodedata
from zoneinfo import ZoneInfo

COLORS = {'fx': '#60a5fa', 'crypto': '#fbbf24', 'stock': '#4ade80',
          'weather': '#38bdf8', 'agenda': '#c084fc'}
DEFAULTS = {'theme': 'dark', 'accent': '#22c55e', 'colors': COLORS,
            'icon_size': 16, 'font_size': 11, 'window_font_size': 13, 'window_icon_size': 20,
            'spacing': 8, 'bar_limit': 5,
            'display': 'both', 'panel_position': 'right', 'show_change': False, 'agenda_bar': True,
            'animations': True, 'sound': True, 'desktop_notifications': True,
            'refresh_seconds': 300, 'profile': 'Personalizado'}


def system_zone():
    try:
        path = str(Path('/etc/localtime').resolve()).split('/zoneinfo/')[-1]
        return ZoneInfo(os.environ.get('TZ') or path).key
    except (ValueError, KeyError):
        return 'UTC'


def finite(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('Informe um número válido.')
    return value


def next_occurrence(due, repeat, now, timezone, anchor_day=None, anchor_time=None):
    """Skip missed periods, preserve local wall-clock time and monthly anchor."""
    if repeat == 'none':
        return None
    zone = ZoneInfo(timezone)
    original = datetime.fromtimestamp(due, zone)
    if anchor_time:
        original = original.replace(hour=anchor_time[0], minute=anchor_time[1])
    current = datetime.fromtimestamp(now, zone)
    anchor = anchor_day or original.day
    date = max(original.date(), current.date())
    if repeat == 'monthly':
        year, month = date.year, date.month
        for _ in range(3):
            candidate = original.replace(year=year, month=month,
                day=min(anchor, calendar.monthrange(year, month)[1]))
            if candidate.timestamp() > max(now, due):
                return candidate.timestamp()
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    else:
        for offset in range(9):
            day = date + timedelta(days=offset)
            if repeat == 'weekly' and day.weekday() != original.weekday():
                continue
            if repeat == 'weekdays' and day.weekday() >= 5:
                continue
            candidate = original.replace(year=day.year, month=day.month, day=day.day)
            if candidate.timestamp() > max(now, due):
                return candidate.timestamp()
    raise ValueError('Não foi possível calcular a próxima ocorrência.')


def weather_symbol(name):
    """At most three letters: VDC for Vitória da Conquista, SP for São Paulo."""
    city = unicodedata.normalize('NFKD', str(name).split(',')[0]).upper()
    words = re.findall(r'[A-Z]+', ''.join(char for char in city if not unicodedata.combining(char)))
    if not words:
        return 'CLI'
    return (words[0] if len(words) == 1 else ''.join(word[0] for word in words))[:3]


def validate_asset(data):
    allowed = ('id', 'kind', 'name', 'symbol', 'pinned', 'enabled', 'position', 'provider', 'currency', 'latitude', 'longitude')
    item = {key: deepcopy(value) for key, value in data.items() if key in allowed}
    kind = item.get('kind')
    if kind not in COLORS or kind == 'agenda':
        raise ValueError('Tipo de indicador inválido.')
    item['name'] = str(item.get('name', '')).strip()[:100]
    item['symbol'] = str(item.get('symbol', '')).strip().upper()[:30]
    if kind == 'fx' and not re.fullmatch(r'[A-Z]{3}-[A-Z]{3}', item['symbol']):
        raise ValueError('Use um par como USD-BRL ou EUR-BRL.')
    if kind == 'crypto' and not re.fullmatch(r'[A-Z0-9]{2,12}-[A-Z]{3,5}', item['symbol']):
        raise ValueError('Use um par como BTC-USD, ETH-USD ou BTC-BRL.')
    if kind == 'stock':
        if item.get('provider', 'brapi') not in ('brapi', 'alpha'):
            raise ValueError('Provedor de bolsa inválido.')
        if not re.fullmatch(r'[A-Z0-9.^:-]{1,25}', item['symbol']):
            raise ValueError('Informe o código do ativo, como PETR4 ou AAPL.')
    if kind == 'weather':
        item['latitude'], item['longitude'] = finite(item['latitude']), finite(item['longitude'])
        if not -90 <= item['latitude'] <= 90 or not -180 <= item['longitude'] <= 180:
            raise ValueError('Coordenadas inválidas.')
        item['symbol'] = item.get('symbol') or weather_symbol(item['name'])
        if len(item['symbol']) > 3 or not item['symbol'].isalpha():
            raise ValueError('O rótulo do clima deve ter de 1 a 3 letras (ex.: VDC).')
    item['name'] = item['name'] or item['symbol']
    item['pinned'] = bool(item.get('pinned', False))
    item['enabled'] = bool(item.get('enabled', True))
    item['position'] = max(0, min(100000, int(item.get('position', 0))))
    if item.get('currency') and not re.fullmatch(r'[A-Z]{3,5}', item['currency']):
        raise ValueError('Informe uma moeda como USD, EUR ou BRL.')
    return item


class Store:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get('MONITOR_GLOBAL_HOME') or
                         Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'utils-global-monitor')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / 'monitor.db'
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS records(kind TEXT, id TEXT, data TEXT NOT NULL,
                    PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS quotes(id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS samples(id TEXT, stamp REAL, value REAL,
                    PRIMARY KEY(id,stamp));
                CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY, channel TEXT,
                    data TEXT, state TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0,
                    next_try REAL, created REAL, error TEXT DEFAULT '');
                CREATE TABLE IF NOT EXISTS budget(provider TEXT, day TEXT, used INTEGER,
                    PRIMARY KEY(provider,day));
            ''')
        os.chmod(self.path, 0o600)
        if not self.get('meta', 'initialized'):
            self.put('settings', 'main', DEFAULTS)
            for index, item in enumerate([
                {'kind': 'fx', 'symbol': 'USD-BRL', 'name': 'Dólar americano', 'pinned': True},
                {'kind': 'fx', 'symbol': 'EUR-BRL', 'name': 'Euro', 'pinned': True},
                {'kind': 'crypto', 'symbol': 'BTC-USD', 'name': 'Bitcoin', 'pinned': True},
                {'kind': 'stock', 'symbol': 'PETR4', 'name': 'Petrobras PN', 'provider': 'brapi'},
                {'kind': 'weather', 'symbol': weather_symbol('São Paulo'), 'name': 'São Paulo, Brasil',
                 'latitude': -23.5505, 'longitude': -46.6333, 'pinned': True},
            ]):
                self.put('asset', str(uuid.uuid4()), {**validate_asset(item), 'position': index})
            self.put('meta', 'initialized', {'version': 1})
        self._shorten_weather_labels()

    def _shorten_weather_labels(self):
        """Upgrade legacy long labels without changing weather, identities or preferences."""
        with self.db() as db:
            previous = {}
            for row in db.execute("SELECT id,data FROM records WHERE kind='asset'").fetchall():
                item = json.loads(row['data'])
                old = str(item.get('symbol') or '')
                if item.get('kind') != 'weather' or (1 <= len(old) <= 3 and old.isalpha()):
                    continue
                previous[row['id']] = old
                item['symbol'] = weather_symbol(item.get('name', ''))
                db.execute("UPDATE records SET data=? WHERE kind='asset' AND id=?",
                           (json.dumps(item, ensure_ascii=False), row['id']))
            if previous:
                db.execute("INSERT OR IGNORE INTO records VALUES ('meta','weather_labels_before_shortening',?)",
                           (json.dumps(previous, ensure_ascii=False),))

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, kind, identity, default=None):
        with self.db() as db:
            row = db.execute('SELECT data FROM records WHERE kind=? AND id=?', (kind, identity)).fetchone()
        return json.loads(row[0]) if row else deepcopy(default)

    def records(self, kind):
        with self.db() as db:
            rows = db.execute('SELECT id,data FROM records WHERE kind=? ORDER BY rowid', (kind,)).fetchall()
        return [dict(json.loads(row['data']), id=row['id']) for row in rows]

    def put(self, kind, identity, data):
        data = {key: value for key, value in data.items() if key != 'id'}
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)',
                       (kind, identity, json.dumps(data, ensure_ascii=False, allow_nan=False)))
        return identity

    def delete(self, kind, identity):
        with self.db() as db:
            db.execute('DELETE FROM records WHERE kind=? AND id=?', (kind, identity))
            if kind == 'asset':
                db.execute('DELETE FROM quotes WHERE id=?', (identity,))
                db.execute('DELETE FROM samples WHERE id=?', (identity,))
                db.execute("DELETE FROM records WHERE kind='chart' AND substr(id,1,?)=?", (len(identity) + 1, identity + ':'))
        if kind == 'reminder':
            with self.db() as db:
                for row in db.execute("SELECT id,data FROM deliveries WHERE state IN ('pending','sending')").fetchall():
                    if json.loads(row['data']).get('reminder_id') == identity:
                        db.execute("UPDATE deliveries SET state='cancelled' WHERE id=?", (row['id'],))

    def settings(self):
        saved = self.get('settings', 'main', {})
        return {**deepcopy(DEFAULTS), **saved, 'colors': {**COLORS, **saved.get('colors', {})}}

    def save_settings(self, changes, dry_run=False):
        settings = self.settings()
        for key, value in changes.items():
            if key not in DEFAULTS:
                continue
            if isinstance(DEFAULTS[key], bool) and not isinstance(value, bool):
                raise ValueError('Preferência booleana inválida: ' + key)
            if key in ('icon_size', 'font_size', 'window_font_size', 'window_icon_size', 'spacing', 'bar_limit', 'refresh_seconds'):
                low, high = {'icon_size': (10, 32), 'font_size': (9, 20), 'spacing': (2, 20),
                             'bar_limit': (1, 12), 'refresh_seconds': (60, 3600),
                             'window_font_size': (11, 18), 'window_icon_size': (16, 32)}[key]
                value = max(low, min(high, int(value)))
            if key == 'theme' and value not in ('dark', 'light', 'system'):
                raise ValueError('Tema inválido.')
            if key == 'display' and value not in ('icons', 'text', 'both'):
                raise ValueError('Formato inválido.')
            if key == 'panel_position' and value not in ('left', 'center', 'right'):
                raise ValueError('Posição da barra inválida.')
            if key == 'accent' and not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
                raise ValueError('Cor inválida.')
            if key == 'colors':
                if not isinstance(value, dict):
                    raise ValueError('Configuração de cores inválida.')
                value = {**COLORS, **{k: v for k, v in value.items() if k in COLORS and
                         isinstance(v, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', v)}}
            settings[key] = value
        if not dry_run:
            self.put('settings', 'main', settings)
        return settings

    def save_reminder(self, data, dry_run=False):
        item = deepcopy(data)
        item['title'] = str(item.get('title', '')).strip()[:200]
        if not item['title']:
            raise ValueError('Informe um título para o lembrete.')
        item['due'] = finite(item['due'])
        item['timezone'] = ZoneInfo(item.get('timezone') or system_zone()).key
        item['repeat'] = item.get('repeat', 'none')
        if item['repeat'] not in ('none', 'daily', 'weekly', 'weekdays', 'monthly'):
            raise ValueError('Repetição inválida.')
        item['lead'] = max(0, min(10080, int(item.get('lead', 0))))
        item['description'] = str(item.get('description', ''))[:4000]
        item['category'] = str(item.get('category', 'Pessoal'))[:60]
        item['enabled'] = bool(item.get('enabled', True))
        item['status'] = item.get('status', 'pending')
        if item['status'] not in ('pending', 'notified', 'done'):
            raise ValueError('Estado de lembrete inválido.')
        item['channels'] = list(dict.fromkeys(str(x) for x in item.get('channels', [])))
        item['anchor_day'] = item.get('anchor_day') or datetime.fromtimestamp(item['due'], ZoneInfo(item['timezone'])).day
        original = datetime.fromtimestamp(item['due'], ZoneInfo(item['timezone']))
        item.setdefault('repeat_time', [original.hour, original.minute])
        hour, minute = item['repeat_time']
        if not isinstance(hour, int) or not isinstance(minute, int) or not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError('Horário de repetição inválido.')
        return item if dry_run else self.put('reminder', item.get('id') or str(uuid.uuid4()), item)

    def queue(self, identity, payload, channels=(), now=None):
        now = time.time() if now is None else now
        with self.db() as db:
            for channel in ['desktop', *channels]:
                db.execute('INSERT OR IGNORE INTO deliveries(id,channel,data,next_try,created) VALUES (?,?,?,?,?)',
                           (f'{identity}:{channel}', channel, json.dumps(payload, ensure_ascii=False), now, now))

    def due_reminders(self, now):
        """Claim and advance the schedule atomically before emitting any event."""
        count = 0
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute("SELECT id,data FROM records WHERE kind='reminder'").fetchall():
                item = json.loads(row['data'])
                if not item.get('enabled', True) or item.get('status') != 'pending':
                    continue
                due = item['due']
                if due - item.get('lead', 0) * 60 > now:
                    continue
                payload = {'type': 'reminder', 'reminder_id': row['id'], 'title': item['title'],
                           'body': item.get('description', ''), 'due': due,
                           'timezone': item['timezone'], 'created_at': now}
                for channel in ['desktop', *item.get('channels', [])]:
                    db.execute('INSERT OR IGNORE INTO deliveries(id,channel,data,next_try,created) VALUES (?,?,?,?,?)',
                        (f"reminder:{row['id']}:{due}:{channel}", channel,
                         json.dumps(payload, ensure_ascii=False), now, now))
                following = next_occurrence(due, item['repeat'], max(now, due), item['timezone'], item['anchor_day'], item.get('repeat_time'))
                if following is None:
                    item['status'] = 'notified'
                else:
                    item['due'] = following
                db.execute("UPDATE records SET data=? WHERE kind='reminder' AND id=?",
                           (json.dumps(item, ensure_ascii=False), row['id']))
                count += 1
        return count

    def pending(self, now, channel=None):
        with self.db() as db:
            sql = "SELECT * FROM deliveries WHERE state='pending' AND next_try<=?"
            args = [now]
            if channel:
                sql += ' AND channel=?'
                args.append(channel)
            return [dict(row) | {'payload': json.loads(row['data'])}
                    for row in db.execute(sql + ' ORDER BY created LIMIT 30', args)]

    def claim(self, identity):
        with self.db() as db:
            result = db.execute("UPDATE deliveries SET state='sending',attempts=attempts+1 WHERE id=? AND state='pending'", (identity,))
        return result.rowcount == 1

    def delivered(self, identity, error=None, retry=True):
        with self.db() as db:
            row = db.execute('SELECT attempts FROM deliveries WHERE id=?', (identity,)).fetchone()
            if not row:
                return
            attempts = row[0]
            state = 'sent' if error is None else ('pending' if retry and attempts < 4 else 'failed')
            db.execute('UPDATE deliveries SET state=?,error=?,next_try=? WHERE id=?',
                       (state, (error or '')[:500], time.time() + min(3600, 30 * 2 ** attempts), identity))
            db.execute("DELETE FROM deliveries WHERE state IN ('sent','failed','cancelled') AND id NOT IN "
                       "(SELECT id FROM deliveries ORDER BY created DESC LIMIT 2000)")

    def recover_deliveries(self):
        with self.db() as db:
            db.execute("UPDATE deliveries SET state='pending' WHERE state='sending'")

    def deliveries(self):
        with self.db() as db:
            return [dict(row) | {'payload': json.loads(row['data'])}
                    for row in db.execute('SELECT * FROM deliveries ORDER BY created DESC LIMIT 100')]

    def quote(self, identity):
        with self.db() as db:
            row = db.execute('SELECT data FROM quotes WHERE id=?', (identity,)).fetchone()
        return json.loads(row[0]) if row else {}

    def save_quote(self, identity, quote):
        with self.db() as db:
            # Deleting an asset while a request is in flight must not resurrect it.
            if not db.execute("SELECT 1 FROM records WHERE kind='asset' AND id=?", (identity,)).fetchone():
                return
            db.execute('INSERT OR REPLACE INTO quotes VALUES (?,?)', (identity, json.dumps(quote, allow_nan=False)))
            if not quote.get('error') and quote.get('value') is not None:
                db.execute('INSERT OR IGNORE INTO samples VALUES (?,?,?)', (identity, quote['fetched_at'], quote['value']))
                db.execute('DELETE FROM samples WHERE id=? AND stamp NOT IN (SELECT stamp FROM samples WHERE id=? ORDER BY stamp DESC LIMIT 500)', (identity, identity))

    def history(self, identity):
        with self.db() as db:
            return [list(row) for row in db.execute('SELECT stamp,value FROM samples WHERE id=? ORDER BY stamp', (identity,))]

    def budget(self, provider, maximum):
        day = datetime.now().strftime('%Y-%m-%d')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT used FROM budget WHERE provider=? AND day=?', (provider, day)).fetchone()
            used = row[0] if row else 0
            if used >= maximum:
                return False
            db.execute('INSERT OR REPLACE INTO budget VALUES (?,?,?)', (provider, day, used + 1))
        return True

    def export_data(self):
        return {'format': 'monitor-global', 'version': 1, 'settings': self.settings(),
                **{kind: self.records(kind) for kind in ('asset', 'reminder', 'alert')},
                'integration': [{k: v for k, v in row.items() if k != 'secret'} for row in self.records('integration')]}

    def import_data(self, data):
        if not isinstance(data, dict) or data.get('format') != 'monitor-global' or data.get('version') != 1:
            raise ValueError('Arquivo de backup incompatível.')
        for kind in ('asset', 'reminder', 'alert', 'integration'):
            if not isinstance(data.get(kind, []), list) or not all(isinstance(row, dict) for row in data.get(kind, [])):
                raise ValueError('Coleção inválida no backup: ' + kind)
        # Normalize the entire backup first, then commit it as one transaction.
        from .engine import validate_alert, validate_integration
        settings = self.save_settings(data.get('settings', {}), dry_run=True)
        assets = [validate_asset(row) for row in data.get('asset', [])]
        for row in assets:
            row.setdefault('id', str(uuid.uuid4()))
        asset_map = {row['id']: row for row in [*self.records('asset'), *assets]}
        if len(asset_map) > 50:
            raise ValueError('O backup ultrapassa o limite de 50 indicadores.')
        class ImportView:
            def get(inner, kind, identity):
                return asset_map.get(identity) if kind == 'asset' else self.get(kind, identity)
        reminders = [self.save_reminder(row, dry_run=True) for row in data.get('reminder', [])]
        alerts = [validate_alert(row, ImportView()) for row in data.get('alert', [])]
        integrations = []
        for row in data.get('integration', []):
            old = self.get('integration', row.get('id'), {})
            integrations.append(validate_integration({**row, 'secret': old.get('secret', ''), 'enabled': False}))
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            for kind, rows in [('settings', [settings | {'id': 'main'}]), ('asset', assets),
                               ('reminder', reminders), ('alert', alerts), ('integration', integrations)]:
                for row in rows:
                    identity = row.get('id') or str(uuid.uuid4())
                    db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)', (kind, identity,
                        json.dumps({key: value for key, value in row.items() if key != 'id'}, ensure_ascii=False, allow_nan=False)))
                    if kind == 'asset':
                        db.execute('DELETE FROM quotes WHERE id=?', (identity,))
                        db.execute('DELETE FROM samples WHERE id=?', (identity,))
