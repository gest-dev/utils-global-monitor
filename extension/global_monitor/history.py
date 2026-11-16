"""On-demand price history with bounded caching and explicit source coverage."""
from datetime import datetime, timezone
import time
from urllib.parse import quote, urlencode

from . import providers

PERIODS = [('7d', '7 dias', 7), ('1mo', '1 mês', 31), ('3mo', '3 meses', 93),
           ('6mo', '6 meses', 186), ('1y', '1 ano', 366)]


def clean_points(points, start=0, end=float('inf')):
    result = {}
    for point in points:
        try:
            stamp, value = providers.number(point[0]), providers.number(point[1])
            if start <= stamp <= end:
                result[stamp] = value
        except (IndexError, KeyError, TypeError, ValueError, providers.DataError):
            continue
    return [[stamp, result[stamp]] for stamp in sorted(result)]


def aggregate(points, resolution):
    if resolution not in ('daily', 'weekly', 'monthly'):
        raise ValueError('Resolução inválida.')
    result = {}
    for stamp, value in clean_points(points):
        date = datetime.fromtimestamp(stamp, timezone.utc)
        key = (date.year, date.month, date.day) if resolution == 'daily' else (
            tuple(date.isocalendar()[:2]) if resolution == 'weekly' else (date.year, date.month))
        result[key] = [stamp, value]
    return sorted(result.values())


def remote_history(asset, store, period, start, end):
    kind, symbol = asset['kind'], asset['symbol']
    if kind == 'fx':
        if period == '1y':
            # The other FX provider caps requests at 360 daily values.
            base, target = symbol.split('-')
            rows = providers.request_json('https://api.frankfurter.dev/v2/rates?' + urlencode({
                'base': base, 'quotes': target, 'from': datetime.fromtimestamp(start, timezone.utc).date().isoformat(),
                'to': datetime.fromtimestamp(end, timezone.utc).date().isoformat()}))
            return [[providers.timestamp(row['date'], 0), row['rate']] for row in rows], 'Frankfurter', 'Referências diárias de câmbio; podem diferir da cotação atual.', 'daily'
        count = next(days for key, _label, days in PERIODS if key == period)
        rows = providers.request_json('https://economia.awesomeapi.com.br/json/daily/' + quote(symbol) + '/' + str(count))
        return [[float(row['timestamp']) / (1000 if float(row['timestamp']) > 1e11 else 1), row['bid']] for row in rows], 'AwesomeAPI', 'Fechamentos diários de referência.', 'daily'
    if kind == 'crypto':
        if symbol.split('-')[1] not in ('USD', 'EUR', 'GBP', 'USDC', 'USDT'):
            raise providers.DataError('Histórico convertido para esta moeda não está disponível; não usamos a taxa atual para converter preços passados.')
        points, cursor = [], start
        while cursor < end:
            finish = min(end, cursor + 299 * 86400)
            params = {'granularity': 86400, 'start': datetime.fromtimestamp(cursor, timezone.utc).isoformat(),
                      'end': datetime.fromtimestamp(finish, timezone.utc).isoformat()}
            rows = providers.request_json('https://api.exchange.coinbase.com/products/' + quote(symbol) + '/candles?' + urlencode(params))
            points.extend([row[0], row[4]] for row in rows)
            cursor = finish
        return points, 'Coinbase Exchange', 'Fechamentos diários do mercado ' + symbol + '.', 'daily'
    if kind == 'stock' and asset.get('provider', 'brapi') == 'brapi':
        token = store.get('secret', 'brapi', {}).get('value')
        data = providers.request_json('https://brapi.dev/api/quote/' + quote(symbol, safe='') + '?' +
            urlencode({'range': period, 'interval': '1d'}), headers={'Authorization': 'Bearer ' + token} if token else {})
        rows = data['results'][0].get('historicalDataPrice', [])
        return [[row['date'], row['close']] for row in rows if row.get('close') is not None], 'brapi', 'Fechamentos informados pela brapi; cobertura e ajustes dependem da fonte e do plano.', 'daily'
    if kind == 'stock' and asset.get('provider') == 'alpha':
        token = store.get('secret', 'alpha', {}).get('value')
        if not token:
            raise providers.DataError('Cadastre sua chave gratuita Alpha Vantage em Integrações para consultar o histórico.')
        if not store.budget('alpha', 25):
            raise providers.DataError('Cota local de 25 consultas Alpha Vantage por dia atingida.')
        monthly = period in ('6mo', '1y')
        data = providers.request_json('https://www.alphavantage.co/query?' + urlencode({
            'function': 'TIME_SERIES_MONTHLY' if monthly else 'TIME_SERIES_DAILY', 'symbol': symbol,
            'outputsize': 'compact', 'apikey': token}))
        rows = data.get('Monthly Time Series' if monthly else 'Time Series (Daily)')
        if not rows:
            raise providers.DataError('Histórico Alpha Vantage indisponível; confira chave, cobertura e cota gratuita.')
        return [[providers.timestamp(date, 0), row['4. close']] for date, row in rows.items()], 'Alpha Vantage', (
            'Fechamentos mensais da fonte.' if monthly else 'Fechamentos diários da fonte; até 100 pregões.'), 'monthly' if monthly else 'daily'
    raise providers.DataError('Histórico de preços indisponível para este indicador.')


def load_history(asset, store, period, now=None):
    days = next((days for key, _label, days in PERIODS if key == period), None)
    if days is None:
        raise ValueError('Período inválido.')
    now = time.time() if now is None else now
    start = now - days * 86400
    signature = [asset.get(key) for key in ('kind', 'symbol', 'provider', 'currency', 'latitude', 'longitude')]
    cache_id = asset['id'] + ':' + period
    cached = store.get('chart', cache_id, {})
    ttl = 21600 if asset.get('provider') == 'alpha' else 900
    if cached.get('signature') == signature and 0 <= now - cached.get('checked_at', 0) < ttl:
        return cached['result']
    try:
        points, source, note, interval = remote_history(asset, store, period, start, now)
        points = clean_points(points, start, now)
        if not points:
            raise providers.DataError('A fonte não forneceu histórico neste período. O plano pode limitar o acesso.')
        result = {'points': points, 'source': source, 'note': note, 'interval': interval, 'warning': '', 'checked_at': now}
    except (providers.DataError, ValueError, TypeError, KeyError, IndexError) as error:
        # Old source history is usable only for the same instrument and period.
        cached_points = clean_points(cached.get('result', {}).get('points', []), start, now)
        if cached.get('signature') == signature and cached_points:
            result = {**cached['result'], 'points': cached_points,
                      'warning': 'Não foi possível atualizar; exibindo o histórico em cache.'}
        else:
            points = clean_points(store.history(asset['id']), start, now)
            reason = str(error) if isinstance(error, providers.DataError) else 'Resposta incompleta da fonte de histórico.'
            result = {'points': points, 'source': 'Coletado neste computador', 'interval': 'daily', 'checked_at': now,
                      'note': 'Amostras locais reais; podem cobrir apenas parte do período selecionado.', 'warning': reason}
    current = store.get('asset', asset['id'])
    if current and signature == [current.get(key) for key in ('kind', 'symbol', 'provider', 'currency', 'latitude', 'longitude')]:
        store.put('chart', cache_id, {'signature': signature, 'checked_at': now, 'result': result})
    return result
