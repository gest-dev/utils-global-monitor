"""Background polling, reminder scheduler and optional delivery adapters."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
import json
import re
import threading
import time
from urllib.parse import urlsplit
import uuid

from . import providers
from .store import DEFAULTS, finite, validate_asset


def validate_integration(data):
    item = deepcopy(data)
    item['name'] = str(item.get('name', '')).strip()[:100]
    if not item['name']:
        raise ValueError('Dê um nome à integração.')
    if item.get('type', 'webhook') not in ('webhook', 'whatsapp'):
        raise ValueError('Tipo de integração inválido.')
    if item.get('type') == 'whatsapp':
        for key in ('phone_id', 'recipient'):
            if not re.fullmatch(r'\d{5,25}', item.get(key, '')):
                raise ValueError('Número/ID WhatsApp inválido; use somente dígitos.')
        if not re.fullmatch(r'[a-z0-9_]+', item.get('template', '')):
            raise ValueError('Informe o nome do template aprovado na Meta.')
        if not re.fullmatch(r'v\d+\.\d+', item.get('version', 'v23.0')):
            raise ValueError('Use uma versão Graph API como v23.0.')
        if item.get('enabled') and not item.get('secret'):
            raise ValueError('Informe o token de acesso WhatsApp.')
    else:
        url = urlsplit(item.get('url', ''))
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
            raise ValueError('Informe uma URL HTTP(S) válida, sem credenciais no endereço.')
        header = item.get('auth_header') or 'Authorization'
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,60}', header):
            raise ValueError('Nome do cabeçalho de autenticação inválido.')
        item['auth_header'] = header
        item['auth_prefix'] = str(item.get('auth_prefix', 'Bearer')).strip()
        if '\n' in item['auth_prefix'] or '\r' in item['auth_prefix']:
            raise ValueError('Prefixo de autenticação inválido.')
        template = item.get('body_template', '')
        if template:
            try:
                value = json.loads(template)
                if not isinstance(value, dict) or len(template) > 16000:
                    raise ValueError()
            except (ValueError, TypeError):
                raise ValueError('O modelo do corpo precisa ser um objeto JSON válido.') from None
    if '\n' in item.get('secret', '') or '\r' in item.get('secret', ''):
        raise ValueError('Credencial inválida.')
    item['enabled'] = bool(item.get('enabled', False))
    return item


def validate_alert(data, store):
    item = deepcopy(data)
    if not store.get('asset', item.get('asset_id', '')):
        raise ValueError('Escolha um indicador existente.')
    if item.get('metric', 'value') not in ('value', 'change', 'rain') or item.get('operator') not in ('above', 'below'):
        raise ValueError('Condição de alerta inválida.')
    item['threshold'] = finite(item['threshold'])
    item['cooldown'] = max(10, min(10080, int(item.get('cooldown', 60))))
    item['title'] = str(item.get('title') or 'Alerta do Monitor Global')[:200]
    item['enabled'] = bool(item.get('enabled', True))
    item['channels'] = list(dict.fromkeys(str(channel) for channel in item.get('channels', [])))
    return item


class Engine:
    def __init__(self, store, on_change=lambda: None, dispatch=lambda function, *args: function(*args)):
        self.store = store
        self.on_change = on_change
        self.dispatch = dispatch
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='monitor-global')
        self.inflight = set()
        self.lock = threading.Lock()
        self.closed = False
        self.notify = None
        self.store.recover_deliveries()

    def changed(self):
        if not self.closed:
            self.dispatch(self.on_change)

    def state(self, history=False):
        assets = sorted(self.store.records('asset'), key=lambda row: row.get('position', 0))
        for item in assets:
            item['quote'] = self.store.quote(item['id'])
            if history:
                item['history'] = item['quote'].get('series') or self.store.history(item['id'])[-100:]
        integrations = [{k: v for k, v in row.items() if k != 'secret'} |
                        {'has_secret': bool(row.get('secret'))} for row in self.store.records('integration')]
        return {'settings': self.store.settings(), 'assets': assets,
                'reminders': sorted(self.store.records('reminder'), key=lambda row: row['due']),
                'alerts': self.store.records('alert'), 'integrations': integrations,
                'keys': {name: bool(self.store.get('secret', name, {}).get('value')) for name in ('brapi', 'alpha')},
                'now': time.time()}

    def tick(self):
        if self.closed:
            return False
        now = time.time()
        if self.store.due_reminders(now):
            self.changed()
        settings = self.store.settings()
        currencies = []
        for item in self.store.records('asset'):
            if not item.get('enabled', True):
                continue
            previous = self.store.quote(item['id'])
            interval = providers.ttl(item, settings)
            if previous.get('error'):
                interval = max(60, min(interval, 300))
            if now - previous.get('checked_at', 0) < interval:
                continue
            if item['kind'] == 'fx':
                currencies.append(item)
            else:
                self._submit('asset:' + item['id'], self._refresh, item)
        if currencies:
            self._submit('fx-group', self._refresh_currencies, currencies)
        for event in self.store.pending(now):
            if event['channel'] == 'desktop':
                if self.notify and self.store.claim(event['id']):
                    if settings['desktop_notifications']:
                        self.dispatch(self.notify, event)
                    else:
                        self.store.delivered(event['id'], 'Notificações locais desativadas.', retry=False)
            else:
                self._submit('delivery:' + event['id'], self._deliver, event)
        return True

    def _submit(self, key, function, *args):
        with self.lock:
            if key in self.inflight or self.closed:
                return
            self.inflight.add(key)

        def work():
            try:
                function(*args)
            finally:
                with self.lock:
                    self.inflight.discard(key)
        self.pool.submit(work)

    def _refresh(self, asset):
        quote = providers.fetch(asset, self.store)
        self._accept_quote(asset, quote)

    def _refresh_currencies(self, assets):
        results = providers.fx_many(assets, self.store)
        for asset in assets:
            self._accept_quote(asset, results[asset['id']])

    def _accept_quote(self, asset, quote):
        current = self.store.get('asset', asset['id'])
        if not current or any(current.get(key) != asset.get(key) for key in ('kind', 'symbol', 'provider', 'currency', 'latitude', 'longitude')):
            return
        previous = self.store.quote(asset['id'])
        for key in ('series', 'history_checked'):
            if key in previous:
                quote.setdefault(key, previous[key])
        self.store.save_quote(asset['id'], quote)
        if not quote.get('error') and quote.get('value') is not None:
            self._alerts(asset, quote)
            if asset['kind'] != 'weather' and asset.get('provider') != 'alpha' and time.time() - quote.get('history_checked', 0) >= 21600:
                self._submit('history:' + asset['id'], self._history, asset)
        self.changed()

    def _history(self, asset):
        try:
            points = providers.historical(asset, self.store)
        except (providers.DataError, ValueError, KeyError, IndexError, TypeError):
            points = []
        current = self.store.get('asset', asset['id'])
        if not current or current['symbol'] != asset['symbol']:
            return
        quote = self.store.quote(asset['id'])
        quote['history_checked'] = time.time()
        if points:
            quote['series'] = points[-100:]
        self.store.save_quote(asset['id'], quote)
        self.changed()

    def _alerts(self, asset, quote):
        now = time.time()
        for item in self.store.records('alert'):
            if not item.get('enabled', True) or item['asset_id'] != asset['id']:
                continue
            value = quote.get(item.get('metric', 'value'))
            if value is None:
                continue
            condition = value >= item['threshold'] if item['operator'] == 'above' else value <= item['threshold']
            if condition and now - item.get('last_sent', 0) >= item.get('cooldown', 60) * 60:
                title = item['title']
                body = f"{asset['name']} ({asset['symbol']}): {value:.2f} · {quote['source']}"
                self.store.queue('alert:' + item['id'] + ':' + str(int(now)),
                    {'type': 'alert', 'title': title, 'body': body, 'asset_id': asset['id'], 'created_at': now},
                    item.get('channels', []), now)
                self.store.put('alert', item['id'], {**item, 'last_sent': now})

    def _deliver(self, event):
        if not self.store.claim(event['id']):
            return
        integration = self.store.get('integration', event['channel'])
        if not integration or not integration.get('enabled'):
            self.store.delivered(event['id'], 'Integração desativada ou removida.', retry=False)
            self.changed()
            return
        try:
            providers.deliver(integration, event['payload'], event['id'])
            self.store.delivered(event['id'])
        except (providers.DataError, ValueError, KeyError):
            # Never log credentials, URL query strings, tokens or response bodies.
            self.store.delivered(event['id'], 'Falha na API externa. Confira endereço, credencial e cota no provedor.')
        self.changed()

    def save_asset(self, item):
        item = validate_asset(item)
        assets = self.store.records('asset')
        if not item.get('id') and len(assets) >= 50:
            raise ValueError('Limite de 50 indicadores atingido.')
        identity = item.get('id') or str(uuid.uuid4())
        previous = self.store.get('asset', identity)
        item.setdefault('position', len(assets))
        self.store.put('asset', identity, item)
        if previous and any(previous.get(key) != item.get(key) for key in ('kind', 'symbol', 'provider', 'currency', 'latitude', 'longitude')):
            with self.store.db() as db:
                db.execute('DELETE FROM quotes WHERE id=?', (identity,))
                db.execute('DELETE FROM samples WHERE id=?', (identity,))
        self.changed()
        self.tick()
        return identity

    def move_asset(self, identity, delta):
        items = sorted(self.store.records('asset'), key=lambda row: row.get('position', 0))
        index = next(index for index, row in enumerate(items) if row['id'] == identity)
        target = max(0, min(len(items) - 1, index + delta))
        items[index], items[target] = items[target], items[index]
        for position, item in enumerate(items):
            self.store.put('asset', item['id'], {**item, 'position': position})
        self.changed()

    def snooze(self, reminder_id):
        item = self.store.get('reminder', reminder_id)
        if item:
            self.store.save_reminder({**item, 'due': time.time() + 600, 'repeat': 'none',
                                     'lead': 0, 'status': 'pending', 'snooze_of': reminder_id})
            self.changed()

    def set_key(self, name, value):
        if name not in ('brapi', 'alpha'):
            raise ValueError('Provedor desconhecido.')
        self.store.put('secret', name, {'value': value.strip()})
        for asset in self.store.records('asset'):
            if asset.get('provider', 'brapi' if asset['kind'] == 'stock' else '') == name:
                quote = self.store.quote(asset['id'])
                if quote.get('error'):
                    quote['checked_at'] = 0
                    self.store.save_quote(asset['id'], quote)
        self.tick()
        self.changed()

    def close(self):
        self.closed = True
        self.pool.shutdown(wait=False, cancel_futures=True)
