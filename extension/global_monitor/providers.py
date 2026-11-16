"""Documented public APIs. Failures retain the last value with an explicit error."""
from datetime import datetime, timezone
import json
import math
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, urlopen

USER_AGENT = 'MonitorGlobal/1.0 (personal desktop monitor)'


class DataError(Exception):
    pass


def number(value):
    result = float(value)
    if not math.isfinite(result):
        raise DataError('A fonte retornou um valor inválido.')
    return result


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def request_json(url, *, payload=None, headers=None, redirects=True):
    if urlsplit(url).scheme not in ('http', 'https'):
        raise DataError('Use um endereço HTTP ou HTTPS.')
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    request = Request(url, data=data, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json',
                      **({'Content-Type': 'application/json'} if data else {}), **(headers or {})})
    try:
        opener = urlopen if redirects else build_opener(NoRedirect).open
        with opener(request, timeout=12) as response:
            body = response.read(2_000_001)
            if len(body) > 2_000_000:
                raise DataError('Resposta excedeu o tamanho permitido.')
            if not body.strip():
                return {}
            if payload is not None:
                # A webhook may return plain text on success.
                return {'accepted': True, 'http_status': response.status}
            return json.loads(body)
    except HTTPError as error:
        messages = {401: 'Chave de API ausente ou inválida.', 403: 'Acesso não permitido pela fonte.',
                    404: 'Ativo ou endereço não encontrado.', 429: 'Limite gratuito atingido; aguardando nova tentativa.'}
        raise DataError(messages.get(error.code, f'A fonte respondeu HTTP {error.code}.')) from None
    except (URLError, TimeoutError, OSError):
        raise DataError('Sem conexão com a fonte. O último dado foi preservado.') from None
    except (json.JSONDecodeError, UnicodeError):
        raise DataError('A fonte retornou uma resposta inválida.') from None


def ttl(asset, settings):
    minimum = {'fx': 60, 'crypto': 120, 'stock': 1800, 'weather': 900}[asset['kind']]
    if asset.get('provider') == 'alpha':
        minimum = 21600
    return max(minimum, settings['refresh_seconds'])


def timestamp(value, fallback):
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError):
        return fallback


def fx(symbol):
    data = request_json('https://economia.awesomeapi.com.br/json/last/' + quote(symbol))
    return parse_fx(data, symbol)


def parse_fx(data, symbol):
    item = data.get(symbol.replace('-', ''))
    if not item:
        raise DataError('Par de moedas indisponível nesta fonte.')
    now = time.time()
    return {'value': number(item['bid']), 'ask': number(item['ask']),
            'change': number(item.get('pctChange', 0)), 'change_label': 'Variação informada pela fonte',
            'stamp': float(item.get('timestamp', now)), 'unit': symbol.split('-')[1],
            'source': 'AwesomeAPI', 'note': 'Referência de compra; não é cotação final de uma operação.'}


def fx_many(assets, store):
    try:
        data = request_json('https://economia.awesomeapi.com.br/json/last/' + ','.join(quote(asset['symbol']) for asset in assets))
    except DataError:
        data = None
    result = {}
    for asset in assets:
        try:
            try:
                row = parse_fx(data, asset['symbol']) if data else fx_fallback(asset['symbol'])
            except (DataError, KeyError, TypeError):
                row = fx_fallback(asset['symbol'])
            row.update(fetched_at=time.time(), checked_at=time.time(), error='')
        except (DataError, ValueError, KeyError, TypeError):
            row = store.quote(asset['id'])
            row.update(checked_at=time.time(), error='Não foi possível atualizar o câmbio; referência anterior preservada.')
        result[asset['id']] = row
    return result


def historical(asset, store):
    """Optional source history. Never invent past BRL conversion rates."""
    if asset['kind'] == 'fx':
        rows = request_json('https://economia.awesomeapi.com.br/json/daily/' + quote(asset['symbol']) + '/30')
        return sorted([[number(row['timestamp']), number(row['bid'])] for row in rows if row.get('timestamp') and row.get('bid')])
    if asset['kind'] == 'crypto':
        if asset['symbol'].split('-')[1] not in ('USD', 'EUR', 'GBP', 'USDC', 'USDT'):
            return []
        rows = request_json('https://api.exchange.coinbase.com/products/' + quote(asset['symbol']) + '/candles?granularity=86400')
        return sorted([[number(row[0]), number(row[4])] for row in rows])[-30:]
    if asset['kind'] == 'stock' and asset.get('provider', 'brapi') == 'brapi':
        token = store.get('secret', 'brapi', {}).get('value')
        rows = request_json('https://brapi.dev/api/quote/' + quote(asset['symbol']) + '?range=1mo&interval=1d',
                            headers={'Authorization': 'Bearer ' + token} if token else {})
        return [[number(row['date']), number(row['close'])] for row in rows['results'][0].get('historicalDataPrice', []) if row.get('close') is not None]
    return []


def fx_fallback(symbol):
    base, target = symbol.split('-')
    data = request_json('https://api.frankfurter.dev/v2/rates?' + urlencode({'base': base, 'quotes': target}))
    row = next((row for row in data if row.get('quote') == target), None)
    if not row:
        raise DataError('Referência diária indisponível para este par.')
    return {'value': number(row['rate']), 'change': None, 'unit': target,
            'stamp': datetime.fromisoformat(row['date']).replace(tzinfo=timezone.utc).timestamp(),
            'source': 'Frankfurter', 'note': 'Referência diária de câmbio; não é uma cotação intradiária.'}


def crypto(asset):
    base, target = asset['symbol'].split('-')
    market = f'{base}-{target if target in ("USD", "EUR", "GBP", "USDC", "USDT") else "USD"}'
    data = request_json(f'https://api.exchange.coinbase.com/products/{quote(market)}/stats')
    last, opening = number(data['last']), number(data['open'])
    result = {'value': last, 'change': (last / opening - 1) * 100 if opening else None,
              'change_label': f'24 h no mercado {market}', 'unit': target, 'stamp': time.time(),
              'source': 'Coinbase Exchange', 'note': f'Preço do mercado {market}; horário da consulta.',
              'high': number(data['high']), 'low': number(data['low']), 'volume': number(data['volume'])}
    if not market.endswith('-' + target):
        conversion = fx('USD-' + target)
        for key in ('value', 'high', 'low'):
            result[key] *= conversion['value']
        result['source'] += ' + AwesomeAPI'
        result['note'] = f'Convertido de {market} pela referência USD/{target}; a variação é do mercado USD.'
    return result


def stock(asset, store):
    symbol = asset['symbol']
    if asset.get('provider', 'brapi') == 'alpha':
        token = store.get('secret', 'alpha', {}).get('value')
        if not token:
            raise DataError('Cadastre uma chave gratuita Alpha Vantage em Integrações.')
        if not store.budget('alpha', 25):
            raise DataError('Cota local de 25 consultas Alpha Vantage por dia atingida.')
        data = request_json('https://www.alphavantage.co/query?' + urlencode(
            {'function': 'GLOBAL_QUOTE', 'symbol': symbol, 'apikey': token}))
        row = data.get('Global Quote')
        if not row or not row.get('05. price'):
            raise DataError('Cotação indisponível: confira código, chave e cota gratuita Alpha Vantage.')
        return {'value': number(row['05. price']),
                'change': number(row.get('10. change percent', '0%').strip('%')),
                'unit': asset.get('currency', 'USD'), 'source': 'Alpha Vantage',
                'stamp': timestamp(row.get('07. latest trading day'), time.time()),
                'note': 'Cotação de fechamento do plano gratuito. Moeda definida no cadastro do ativo.'}
    token = store.get('secret', 'brapi', {}).get('value')
    headers = {'Authorization': f'Bearer {token}'} if token else {}
    if not token and symbol not in ('PETR4', 'VALE3', 'MGLU3', 'ITUB4'):
        raise DataError('Este ativo requer uma chave gratuita brapi em Integrações. Sem chave: PETR4, VALE3, MGLU3 e ITUB4.')
    data = request_json('https://brapi.dev/api/quote/' + quote(symbol, safe=''), headers=headers)
    rows = data.get('results', [])
    if not rows or rows[0].get('regularMarketPrice') is None:
        raise DataError('Ativo indisponível na brapi.')
    row = rows[0]
    return {'value': number(row['regularMarketPrice']), 'change': number(row.get('regularMarketChangePercent', 0)),
            'unit': row.get('currency', 'BRL'), 'source': 'brapi',
            'stamp': timestamp(row.get('regularMarketTime'), time.time()),
            'note': 'Cotação com atraso do plano gratuito (aproximadamente 30 min); pode refletir o último pregão.',
            'high': row.get('regularMarketDayHigh'), 'low': row.get('regularMarketDayLow')}


WEATHER = {0: 'Céu limpo', 1: 'Predominantemente limpo', 2: 'Parcialmente nublado', 3: 'Nublado',
           45: 'Nevoeiro', 48: 'Nevoeiro', 51: 'Garoa leve', 53: 'Garoa', 55: 'Garoa intensa',
           61: 'Chuva leve', 63: 'Chuva', 65: 'Chuva forte', 71: 'Neve leve', 73: 'Neve',
           75: 'Neve intensa', 80: 'Pancadas leves', 81: 'Pancadas de chuva', 82: 'Pancadas fortes',
           95: 'Trovoadas', 96: 'Trovoadas com granizo', 99: 'Trovoadas com granizo'}


def weather(asset):
    data = request_json('https://api.open-meteo.com/v1/forecast?' + urlencode({
        'latitude': asset['latitude'], 'longitude': asset['longitude'],
        'current': 'temperature_2m,apparent_temperature,weather_code,relative_humidity_2m,wind_speed_10m',
        'daily': 'temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code',
        'timezone': 'auto', 'forecast_days': 7}))
    current, daily = data['current'], data['daily']
    offset = data.get('utc_offset_seconds', 0)
    observed = datetime.fromisoformat(current['time']).replace(tzinfo=timezone.utc).timestamp() - offset
    return {'value': number(current['temperature_2m']), 'unit': '°C', 'change': None,
            'stamp': observed, 'source': 'Open-Meteo', 'condition': WEATHER.get(current['weather_code'], 'Condição variável'),
            'code': current['weather_code'], 'feels': current['apparent_temperature'],
            'humidity': current['relative_humidity_2m'], 'wind': current['wind_speed_10m'],
            'rain': daily['precipitation_probability_max'][0], 'forecast': daily,
            'note': 'Dados meteorológicos por modelo. Open-Meteo.com · CC BY 4.0 · API gratuita para uso não comercial.'}


def fetch(asset, store):
    try:
        if asset['kind'] == 'fx':
            try:
                result = fx(asset['symbol'])
            except DataError:
                result = fx_fallback(asset['symbol'])
        elif asset['kind'] == 'crypto':
            result = crypto(asset)
        elif asset['kind'] == 'stock':
            result = stock(asset, store)
        else:
            result = weather(asset)
        result['value'] = number(result['value'])
        result.update(fetched_at=time.time(), error='', checked_at=time.time())
        return result
    except (DataError, ValueError, KeyError, TypeError, IndexError) as error:
        previous = store.quote(asset['id'])
        previous.update(error=str(error) if isinstance(error, DataError) else 'Resposta incompleta da fonte.',
                        checked_at=time.time())
        return previous


def search_cities(query, country_code=''):
    query = ' '.join(query.split())
    if len(query.split(',')[0].strip()) < 2:
        raise DataError('Digite ao menos duas letras da cidade.')
    if len(query) > 150 or (country_code and not re.fullmatch(r'[A-Z]{2}', country_code)):
        raise DataError('Busca de cidade inválida.')
    params = {'name': query, 'count': 12, 'language': 'pt', 'format': 'json'}
    if country_code:
        params['countryCode'] = country_code
    data = request_json('https://geocoding-api.open-meteo.com/v1/search?' + urlencode(params))
    if not isinstance(data, dict) or not isinstance(data.get('results', []), list) or data.get('error'):
        raise DataError('A busca de cidades retornou uma resposta inválida.')
    cities, seen = [], set()
    for row in data.get('results', []):
        try:
            city = str(row['name']).strip()
            lat, lon = number(row['latitude']), number(row['longitude'])
            if not city or not -90 <= lat <= 90 or not -180 <= lon <= 180:
                continue
            parts = list(dict.fromkeys(filter(None, [city, row.get('admin2'), row.get('admin1'), row.get('country')])))
            identity = (city, lat, lon)
            if identity in seen:
                continue
            seen.add(identity)
            cities.append({'name': ', '.join(parts), 'city': city,
                           'region': row.get('admin1', ''), 'district': row.get('admin2', ''),
                           'country': row.get('country', ''),
                           'country_code': row.get('country_code', ''),
                           'latitude': lat, 'longitude': lon, 'timezone': row.get('timezone', 'UTC')})
        except (KeyError, TypeError, ValueError, DataError):
            continue
    return cities


def deliver(integration, payload, identity):
    headers = {'Idempotency-Key': identity}
    secret = integration.get('secret', '')
    if integration.get('type') == 'whatsapp':
        # Scheduled business-initiated messages use an approved template.
        phone = integration['phone_id']
        version = integration.get('version', 'v23.0')
        url = f'https://graph.facebook.com/{quote(version)}/{quote(phone)}/messages'
        headers['Authorization'] = f'Bearer {secret}'
        body = {'messaging_product': 'whatsapp', 'to': integration['recipient'], 'type': 'template',
                'template': {'name': integration['template'], 'language': {'code': integration.get('language', 'pt_BR')},
                             'components': [{'type': 'body', 'parameters': [
                                 {'type': 'text', 'text': payload['title']},
                                 {'type': 'text', 'text': payload.get('body') or 'Lembrete do Monitor Global'}]}]}}
    else:
        url = integration['url']
        if secret:
            headers[integration.get('auth_header', 'Authorization')] = (integration.get('auth_prefix', 'Bearer') + ' ' + secret).strip()
        body = {'event_id': identity, 'destination': integration.get('recipient', ''), **payload}
        if integration.get('body_template'):
            replacements = {**body, 'due': body.get('due', '')}
            def substitute(value):
                if isinstance(value, str):
                    return re.sub(r'\{\{(title|body|destination|event_id|due)\}\}',
                                  lambda match: str(replacements.get(match.group(1), '')), value)
                if isinstance(value, dict):
                    return {key: substitute(child) for key, child in value.items()}
                if isinstance(value, list):
                    return [substitute(child) for child in value]
                return value
            body = substitute(json.loads(integration['body_template']))
    return request_json(url, payload=body, headers=headers, redirects=False)
