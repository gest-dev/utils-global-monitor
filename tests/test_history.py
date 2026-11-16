"""History regressions: no external network, credentials or recipients."""
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'extension'))
from global_monitor import history, providers
from global_monitor.store import Store


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = Store(self.folder.name)
        self.asset = next(row for row in self.store.records('asset') if row['kind'] == 'fx')
        self.now = datetime(2026, 11, 16, tzinfo=timezone.utc).timestamp()
        self.points = [[self.now - 86400 * days, 5 + days / 100] for days in (31, 2, 1)]

    def tearDown(self):
        self.folder.cleanup()

    def test_cache_reuses_same_asset_period(self):
        with patch.object(history, 'remote_history', return_value=(self.points, 'TEST', 'nota', 'daily')) as fetch:
            first = history.load_history(self.asset, self.store, '1mo', self.now)
            second = history.load_history(self.asset, self.store, '1mo', self.now + 1)
        self.assertEqual(first, second)
        self.assertEqual(fetch.call_count, 1)

    def test_cache_does_not_cross_asset_code_or_period(self):
        with patch.object(history, 'remote_history', return_value=(self.points, 'TEST', '', 'daily')) as fetch:
            history.load_history(self.asset, self.store, '1mo', self.now)
            history.load_history(self.asset, self.store, '1y', self.now)
            changed = {**self.asset, 'symbol': 'EUR-BRL'}
            self.store.put('asset', changed['id'], changed)
            history.load_history(changed, self.store, '1mo', self.now)
        self.assertEqual(fetch.call_count, 3)

    def test_errors_show_only_real_local_samples_inside_period(self):
        for stamp, value in self.points:
            self.store.save_quote(self.asset['id'], {'value': value, 'fetched_at': stamp})
        with patch.object(history, 'remote_history', side_effect=providers.DataError('Limite da fonte')):
            result = history.load_history(self.asset, self.store, '7d', self.now)
        self.assertEqual(len(result['points']), 2)
        self.assertEqual(result['source'], 'Coletado neste computador')
        self.assertIn('Limite da fonte', result['warning'])

    def test_failed_refresh_preserves_cached_source_data(self):
        with patch.object(history, 'remote_history', return_value=(self.points, 'TEST', '', 'daily')):
            history.load_history(self.asset, self.store, '1mo', self.now)
        with patch.object(history, 'remote_history', side_effect=providers.DataError('offline')):
            result = history.load_history(self.asset, self.store, '1mo', self.now + 1000)
        self.assertEqual(result['source'], 'TEST')
        self.assertIn('cache', result['warning'])

    def test_changed_or_deleted_asset_does_not_cache_pending_result(self):
        def replaced(*_args):
            self.store.delete('asset', self.asset['id'])
            return self.points, 'TEST', '', 'daily'
        with patch.object(history, 'remote_history', side_effect=replaced):
            history.load_history(self.asset, self.store, '1mo', self.now)
        self.assertEqual(self.store.records('chart'), [])

    def test_delete_asset_cleans_history_cache(self):
        with patch.object(history, 'remote_history', return_value=(self.points, 'TEST', '', 'daily')):
            history.load_history(self.asset, self.store, '1mo', self.now)
        self.store.delete('asset', self.asset['id'])
        self.assertEqual(self.store.records('chart'), [])

    def test_clean_points_rejects_nonfinite_duplicates_and_outside_dates(self):
        self.assertEqual(history.clean_points([[2, 3], [1, 4], [2, 5], [3, float('nan')], {}, None], 2, 5), [[2, 5]])

    def test_monthly_and_weekly_use_last_available_close(self):
        stamps = [datetime(2026, month, day, tzinfo=timezone.utc).timestamp() for month, day in [(1, 2), (1, 3), (2, 2)]]
        points = [[stamp, index] for index, stamp in enumerate(stamps)]
        self.assertEqual(history.aggregate(points, 'monthly'), [points[1], points[2]])
        self.assertEqual(history.aggregate(points, 'weekly'), [points[1], points[2]])
        with self.assertRaises(ValueError):
            history.aggregate(points, 'invented')

    def test_crypto_annual_paginates_below_300_and_filters_overlap(self):
        asset = {'kind': 'crypto', 'symbol': 'BTC-USD'}
        def response(url):
            params = parse_qs(urlsplit(url).query)
            start = datetime.fromisoformat(params['start'][0]).timestamp()
            end = datetime.fromisoformat(params['end'][0]).timestamp()
            self.assertLessEqual((end - start) / 86400, 299)
            return [[start, 1, 2, 1, 1.5, 100], [end, 1, 2, 1, 1.6, 100]]
        with patch.object(providers, 'request_json', side_effect=response) as request:
            points, source, _, _ = history.remote_history(asset, self.store, '1y', self.now - 366 * 86400, self.now)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(source, 'Coinbase Exchange')
        self.assertEqual(len(history.clean_points(points)), 3)

    def test_crypto_does_not_convert_past_prices_with_current_fx(self):
        with self.assertRaises(providers.DataError), patch.object(providers, 'request_json') as request:
            history.remote_history({'kind': 'crypto', 'symbol': 'BTC-BRL'}, self.store, '1y', 1, 2)
        request.assert_not_called()

    def test_annual_fx_uses_full_year_reference_series(self):
        with patch.object(providers, 'request_json', return_value=[{'date': '2026-11-15', 'rate': 5.7}]) as request:
            result = history.remote_history(self.asset, self.store, '1y', self.now - 366 * 86400, self.now)
        params = parse_qs(urlsplit(request.call_args.args[0]).query)
        self.assertIn('frankfurter', request.call_args.args[0])
        self.assertEqual(params['base'], ['USD'])
        self.assertEqual(params['quotes'], ['BRL'])
        self.assertEqual(result[1], 'Frankfurter')

    def test_alpha_annual_uses_monthly_and_respects_shared_budget(self):
        self.store.put('secret', 'alpha', {'value': 'TEST-ONLY'})
        asset = {'kind': 'stock', 'symbol': 'IBM', 'provider': 'alpha'}
        data = {'Monthly Time Series': {'2026-10-30': {'4. close': '300'}}}
        with patch.object(providers, 'request_json', return_value=data) as request:
            result = history.remote_history(asset, self.store, '1y', self.now - 366 * 86400, self.now)
        self.assertEqual(parse_qs(urlsplit(request.call_args.args[0]).query)['function'], ['TIME_SERIES_MONTHLY'])
        self.assertEqual(result[3], 'monthly')
        with patch.object(self.store, 'budget', return_value=False), self.assertRaises(providers.DataError):
            history.remote_history(asset, self.store, '1y', 1, 2)

    def test_invalid_period_never_calls_network(self):
        with patch.object(history, 'remote_history') as request, self.assertRaises(ValueError):
            history.load_history(self.asset, self.store, 'nonsense', self.now)
        request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
