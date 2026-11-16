import copy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'extension'))
from global_monitor import providers
from global_monitor.agenda import agenda_view, overdue_text, reminder_status
from global_monitor.engine import Engine, validate_alert, validate_integration
from global_monitor.store import Store, next_occurrence, validate_asset, weather_symbol


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = Store(self.folder.name)
        self.engine = Engine(self.store)
        self.asset = self.store.records('asset')[0]

    def tearDown(self):
        self.engine.close()
        self.folder.cleanup()

    def reminder(self, **changes):
        return self.store.save_reminder({'title': 'Reunião', 'description': 'Planejamento', 'due': 1000,
            'repeat': 'none', 'timezone': 'America/Sao_Paulo', **changes})

    def test_defaults_and_private_storage(self):
        self.assertEqual(len(self.store.records('asset')), 5)
        self.assertEqual(self.store.settings()['theme'], 'dark')
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)

    def test_settings_clamp_and_color_validation(self):
        self.store.save_settings({'icon_size': 100, 'bar_limit': 0, 'font_size': 2})
        self.assertEqual(self.store.settings()['icon_size'], 32)
        self.assertEqual(self.store.settings()['bar_limit'], 1)
        self.assertEqual(self.store.settings()['font_size'], 9)
        with self.assertRaises(ValueError):
            self.store.save_settings({'accent': 'red; invalid-css'})

    def test_panel_position_persists_and_rejects_invalid_values(self):
        self.assertEqual(self.store.settings()['panel_position'], 'right')
        for position in ('left', 'center', 'right'):
            self.store.save_settings({'panel_position': position})
            self.assertEqual(Store(self.folder.name).settings()['panel_position'], position)
        with self.assertRaises(ValueError):
            self.store.save_settings({'panel_position': 'bottom'})
        self.assertEqual(self.store.settings()['panel_position'], 'right')

    def test_city_search_preserves_qualifiers_and_country_filter(self):
        row = {'name': 'São José', 'admin1': 'Santa Catarina', 'country': 'Brasil',
               'latitude': -27.6, 'longitude': -48.6, 'country_code': 'BR'}
        with patch.object(providers, 'request_json', return_value={'results': [row, row]}) as request:
            cities = providers.search_cities('  São   José, SC  ', 'BR')
        params = parse_qs(urlsplit(request.call_args.args[0]).query)
        self.assertEqual(params['name'], ['São José, SC'])
        self.assertEqual(params['countryCode'], ['BR'])
        self.assertEqual(params['language'], ['pt'])
        self.assertEqual(len(cities), 1)
        self.assertEqual(cities[0]['name'], 'São José, Santa Catarina, Brasil')
        self.assertEqual(cities[0]['country_code'], 'BR')

    def test_city_search_skips_incomplete_and_invalid_coordinates(self):
        rows = [None, {}, {'name': 'X', 'latitude': 91, 'longitude': 0},
                {'name': 'X', 'latitude': 0, 'longitude': float('nan')},
                {'name': 'Porto', 'latitude': 41.1, 'longitude': -8.6}]
        with patch.object(providers, 'request_json', return_value={'results': rows}):
            self.assertEqual([row['name'] for row in providers.search_cities('Porto')], ['Porto'])

    def test_city_search_distinguishes_homonyms_by_municipality(self):
        rows = [{'name': 'São José', 'admin1': 'Santa Catarina', 'country': 'Brasil',
                 'admin2': 'São José', 'latitude': -27.6, 'longitude': -48.6},
                {'name': 'São José', 'admin1': 'Santa Catarina', 'country': 'Brasil',
                 'admin2': 'Braço do Norte', 'latitude': -28.2, 'longitude': -49.1}]
        with patch.object(providers, 'request_json', return_value={'results': rows}):
            cities = providers.search_cities('São José')
        self.assertNotEqual(cities[0]['name'], cities[1]['name'])
        self.assertEqual(cities[1]['district'], 'Braço do Norte')
        self.assertEqual(cities[0]['name'], 'São José, Santa Catarina, Brasil')

    def test_city_search_empty_and_invalid_responses(self):
        with patch.object(providers, 'request_json', return_value={}):
            self.assertEqual(providers.search_cities('Cidade inexistente'), [])
        for response in ([], {'results': {}}, {'error': True}):
            with patch.object(providers, 'request_json', return_value=response):
                with self.assertRaises(providers.DataError):
                    providers.search_cities('Campinas')

    def test_city_search_validates_before_network_request(self):
        with patch.object(providers, 'request_json') as request:
            for query, country in [('X', ''), (', Brasil', ''), ('x' * 151, ''), ('Paris', 'invalid')]:
                with self.assertRaises(providers.DataError):
                    providers.search_cities(query, country)
            request.assert_not_called()

    def test_asset_validation(self):
        for item in [{'kind': 'fx', 'symbol': 'USD'}, {'kind': 'weather', 'latitude': 100, 'longitude': 0},
                     {'kind': 'crypto', 'symbol': '../../x'}, {'kind': 'stock', 'symbol': 'AAPL', 'provider': 'x'}]:
            with self.assertRaises(ValueError):
                validate_asset(item)

    def test_reminder_delivers_once_after_sleep(self):
        self.reminder()
        self.assertEqual(self.store.due_reminders(2000), 1)
        self.assertEqual(self.store.due_reminders(3000), 0)
        self.assertEqual(len(self.store.pending(3000)), 1)

    def test_lead_time(self):
        self.reminder(due=2000, lead=10)
        self.assertEqual(self.store.due_reminders(1399), 0)
        self.assertEqual(self.store.due_reminders(1400), 1)

    def test_agenda_status_changes_at_due_time(self):
        item = {'due': 1000, 'status': 'pending', 'enabled': True}
        self.assertEqual(reminder_status(item, 999), 'pending')
        self.assertEqual(reminder_status(item, 1000), 'overdue')
        self.assertEqual(reminder_status(item, 1001), 'overdue')
        self.assertEqual(reminder_status({**item, 'enabled': False}, 2000), 'paused')
        self.assertEqual(reminder_status({**item, 'enabled': False, 'status': 'done'}, 2000), 'done')

    def test_agenda_generated_notice_is_not_completion(self):
        identity = self.reminder(due=2000, lead=10)
        self.store.due_reminders(1400)
        item = self.store.get('reminder', identity)
        self.assertEqual(reminder_status(item, 1500), 'notified')
        self.assertEqual(reminder_status(item, 2001), 'overdue')
        self.assertEqual(self.store.get('reminder', identity)['status'], 'notified')

    def test_agenda_filters_exclude_overdue_from_upcoming(self):
        items = [{'id': key, 'due': due, 'status': status, 'enabled': enabled,
                  'title': key, 'timezone': 'UTC'} for key, due, status, enabled in (
                     ('past', 900, 'notified', True), ('future', 1100, 'pending', True),
                     ('done', 800, 'done', False), ('paused', 700, 'pending', False))]
        for mode, expected in [('upcoming', 'future'), ('overdue', 'past'), ('done', 'done'), ('paused', 'paused')]:
            rows, counts = agenda_view(items, mode, now=1000)
            self.assertEqual([row['id'] for row in rows], [expected])
            self.assertEqual(counts['all'], 4)
            self.assertEqual(counts[mode], 1)

    def test_agenda_today_uses_each_reminder_timezone(self):
        now = datetime(2026, 11, 16, 1, tzinfo=ZoneInfo('UTC')).timestamp()
        items = [{'id': 'br', 'due': now - 1800, 'timezone': 'America/Sao_Paulo', 'title': 'Brasil'},
                 {'id': 'jp', 'due': now + 1800, 'timezone': 'Asia/Tokyo', 'title': 'Japão'}]
        rows, _counts = agenda_view(items, 'today', now=now)
        self.assertEqual(len(rows), 2)

    def test_agenda_calendar_includes_done_and_paused_on_selected_date(self):
        day = datetime(2026, 11, 15, 12, tzinfo=ZoneInfo('UTC'))
        items = [{'due': day.timestamp(), 'timezone': 'UTC', 'title': 'A', 'status': 'done'},
                 {'due': day.timestamp() + 100, 'timezone': 'UTC', 'title': 'B', 'enabled': False}]
        rows, counts = agenda_view(items, 'calendar', calendar_day=day.date(), now=day.timestamp() + 86400)
        self.assertEqual(len(rows), 2)
        self.assertEqual(counts['calendar'], 2)

    def test_agenda_search_applies_to_tab_counts_and_category(self):
        items = [{'due': 1, 'timezone': 'UTC', 'title': 'Enviar relatório', 'category': 'Trabalho'},
                 {'due': 2, 'timezone': 'UTC', 'title': 'Pagar conta', 'category': 'Pessoal'}]
        rows, counts = agenda_view(items, 'overdue', query='TRABALHO', now=3)
        self.assertEqual([row['title'] for row in rows], ['Enviar relatório'])
        self.assertEqual(counts['all'], 1)

    def test_agenda_counts_do_not_modify_records_or_queue_notifications(self):
        identity = self.reminder()
        before = self.store.get('reminder', identity)
        agenda_view(self.store.records('reminder'), 'overdue', now=1001)
        self.assertEqual(self.store.get('reminder', identity), before)
        self.assertEqual(self.store.deliveries(), [])

    def test_agenda_elapsed_time(self):
        item = {'due': 1000}
        self.assertIn('menos de 1 minuto', overdue_text(item, 1001))
        self.assertIn('1 min', overdue_text(item, 1061))
        self.assertIn('1 h e 1 min', overdue_text(item, 1000 + 3660))
        self.assertIn('1 dia', overdue_text(item, 1000 + 86400))

    def test_agenda_invalid_filter(self):
        with self.assertRaises(ValueError):
            agenda_view([], 'invalid')

    def test_recurring_schedule_skips_missed_days(self):
        identity = self.reminder(repeat='daily')
        self.store.due_reminders(1_000_000)
        item = self.store.get('reminder', identity)
        self.assertGreater(item['due'], 1_000_000)
        self.assertEqual(len(self.store.deliveries()), 1)

    def test_monthly_anchor_leap_year(self):
        zone = ZoneInfo('America/Sao_Paulo')
        january = datetime(2028, 1, 31, 10, tzinfo=zone).timestamp()
        february = next_occurrence(january, 'monthly', january, zone.key, 31)
        self.assertEqual(datetime.fromtimestamp(february, zone).day, 29)
        march = next_occurrence(february, 'monthly', february, zone.key, 31)
        self.assertEqual(datetime.fromtimestamp(march, zone).day, 31)

    def test_weekdays_skip_weekend(self):
        zone = ZoneInfo('America/Sao_Paulo')
        friday = datetime(2026, 11, 20, 8, tzinfo=zone).timestamp()
        next_due = next_occurrence(friday, 'weekdays', friday, zone.key)
        self.assertEqual(datetime.fromtimestamp(next_due, zone).weekday(), 0)

    def test_weekly_and_dst_preserve_local_hour(self):
        zone = ZoneInfo('America/New_York')
        before = datetime(2026, 3, 7, 9, tzinfo=zone).timestamp()
        after = next_occurrence(before, 'daily', before, zone.key)
        self.assertEqual(datetime.fromtimestamp(after, zone).hour, 9)
        self.assertEqual(after - before, 23 * 3600)
        self.assertEqual(next_occurrence(before, 'weekly', before, zone.key) - after, 6 * 86400)

    def test_snooze_keeps_original_recurrence(self):
        identity = self.reminder(repeat='daily')
        original = self.store.get('reminder', identity)
        self.engine.snooze(identity)
        self.assertEqual(self.store.get('reminder', identity), original)
        child = next(row for row in self.store.records('reminder') if row['id'] != identity)
        self.assertEqual(child['repeat'], 'none')
        self.assertEqual(child['lead'], 0)
        self.assertAlmostEqual(child['due'], time.time() + 600, delta=2)

    def test_nonexistent_dst_time_does_not_shift_the_following_day(self):
        zone = ZoneInfo('America/New_York')
        saturday = datetime(2026, 3, 7, 2, 30, tzinfo=zone).timestamp()
        sunday = next_occurrence(saturday, 'daily', saturday, zone.key, anchor_time=[2, 30])
        self.assertEqual(datetime.fromtimestamp(sunday, zone).hour, 3)
        monday = next_occurrence(sunday, 'daily', sunday, zone.key, anchor_time=[2, 30])
        self.assertEqual(datetime.fromtimestamp(monday, zone).hour, 2)

    def test_transactional_queue_idempotency_and_claim(self):
        payload = {'title': 'Teste'}
        self.store.queue('event', payload, ['dest'])
        self.store.queue('event', payload, ['dest'])
        self.assertEqual(len(self.store.deliveries()), 2)
        self.assertTrue(self.store.claim('event:dest'))
        self.assertFalse(self.store.claim('event:dest'))

    def test_delivery_retry_limit_and_recovery(self):
        self.store.queue('event', {'title': 'Teste'})
        for _ in range(4):
            self.assertTrue(self.store.claim('event:desktop'))
            self.store.delivered('event:desktop', 'falha')
        self.assertEqual(self.store.deliveries()[0]['state'], 'failed')
        self.store.queue('other', {'title': 'Outro'})
        self.store.claim('other:desktop')
        self.store.recover_deliveries()
        self.assertEqual(self.store.deliveries()[0]['state'], 'pending')

    def test_removing_reminder_cancels_pending_delivery(self):
        identity = self.reminder(channels=['dest'])
        self.store.due_reminders(2000)
        self.store.delete('reminder', identity)
        self.assertTrue(all(row['state'] == 'cancelled' for row in self.store.deliveries()))

    def test_integration_validation_and_redacted_state(self):
        item = validate_integration({'name': 'API', 'type': 'webhook', 'url': 'https://example.com/notify', 'secret': 'private'})
        self.store.put('integration', 'dest', item)
        self.store.put('secret', 'brapi', {'value': 'key-private'})
        state = json.dumps(self.engine.state())
        self.assertNotIn('private', state)
        self.assertTrue(self.engine.state()['keys']['brapi'])
        with self.assertRaises(ValueError):
            validate_integration({'name': 'Bad', 'url': 'file:///etc/passwd'})

    def test_backup_excludes_credentials_and_disables_imported_destinations(self):
        self.store.put('integration', 'dest', {'name': 'API', 'url': 'https://example.com', 'secret': 'secret-value', 'enabled': True})
        self.store.put('secret', 'brapi', {'value': 'hidden-key'})
        backup = self.store.export_data()
        self.assertNotIn('secret-value', json.dumps(backup))
        self.assertNotIn('hidden-key', json.dumps(backup))
        self.store.import_data(backup)
        self.assertFalse(self.store.get('integration', 'dest')['enabled'])
        self.assertEqual(self.store.get('integration', 'dest')['secret'], 'secret-value')
        self.assertEqual(len(self.store.records('asset')), 5)

    def test_invalid_backup_does_not_mutate_assets(self):
        backup = self.store.export_data()
        backup['asset'][0]['symbol'] = 'invalid'
        with self.assertRaises(ValueError):
            self.store.import_data(backup)
        self.assertEqual(len(self.store.records('asset')), 5)

    def test_alpha_budget(self):
        self.assertTrue(all(self.store.budget('alpha', 25) for _ in range(25)))
        self.assertFalse(self.store.budget('alpha', 25))

    def test_unchanged_asset_keeps_cache_and_reorder(self):
        self.store.save_quote(self.asset['id'], {'value': 5, 'fetched_at': 1000})
        with patch.object(self.engine, 'tick'):
            self.engine.save_asset({**self.asset, 'name': 'Novo nome'})
        self.assertEqual(self.store.quote(self.asset['id'])['value'], 5)
        self.engine.move_asset(self.asset['id'], 1)
        self.assertEqual(self.engine.state()['assets'][1]['id'], self.asset['id'])

    def test_changed_asset_drops_old_quote(self):
        self.store.save_quote(self.asset['id'], {'value': 5, 'fetched_at': 1000})
        with patch.object(self.engine, 'tick'):
            self.engine.save_asset({**self.asset, 'symbol': 'GBP-BRL'})
        self.assertEqual(self.store.quote(self.asset['id']), {})

    def test_financial_code_edits_drop_previous_asset_data(self):
        for kind, old, new, unit in [('fx', 'USD-BRL', 'EUR-BRL', 'BRL'),
                                     ('crypto', 'BTC-USD', 'ETH-EUR', 'EUR'),
                                     ('stock', 'PETR4', 'VALE3', 'BRL')]:
            with self.subTest(kind=kind), patch.object(self.engine, 'tick'):
                identity = self.engine.save_asset({'kind': kind, 'symbol': old, 'name': 'Nome personalizado'})
                previous = {**self.store.get('asset', identity), 'id': identity}
                self.store.save_quote(identity, {'value': 123, 'fetched_at': 1000})
                self.engine.save_asset({**previous, 'symbol': new.lower()})
                current = {**self.store.get('asset', identity), 'id': identity}
                self.assertEqual(current['symbol'], new)
                self.assertEqual(current['name'], 'Nome personalizado')
                self.assertEqual(self.store.quote(identity), {})
                self.assertEqual(self.store.history(identity), [])
                self.engine._accept_quote(previous, {'value': 999, 'fetched_at': 1001})
                self.assertEqual(self.store.quote(identity), {})
                with patch.object(self.engine, '_submit'):
                    self.engine._accept_quote(current, {'value': 42, 'unit': unit, 'fetched_at': 1002})
                self.assertEqual(self.store.quote(identity)['unit'], unit)

    def test_weather_label_uses_at_most_three_city_initials(self):
        for city, expected in [('Vitória da Conquista, Bahia, Brasil', 'VDC'), ('São Paulo, Brasil', 'SP'),
                               ('Belo Horizonte', 'BH'), ('Salvador', 'SAL'), ('Brasília', 'BRA'),
                               ('Águas de São Pedro', 'ADS'), ('', 'CLI')]:
            self.assertEqual(weather_symbol(city), expected)
        self.assertLessEqual(len(weather_symbol('x' * 80)), 3)

    def test_weather_label_limit_is_enforced_in_model(self):
        asset = {'kind': 'weather', 'name': 'Vitória da Conquista', 'latitude': -14.86, 'longitude': -40.84}
        self.assertEqual(validate_asset(asset)['symbol'], 'VDC')
        self.assertEqual(validate_asset({**asset, 'symbol': ' vc '})['symbol'], 'VC')
        for symbol in ('ABCD', 'VITÓRIA DA CONQUISTA', 'V1C', 'V-C'):
            with self.assertRaises(ValueError):
                validate_asset({**asset, 'symbol': symbol})
        self.assertEqual(validate_asset({'kind': 'fx', 'symbol': 'USD-BRL'})['symbol'], 'USD-BRL')
        self.assertEqual(validate_asset({'kind': 'stock', 'symbol': 'PETR4'})['symbol'], 'PETR4')

    def test_legacy_long_weather_label_is_shortened_without_losing_data(self):
        asset = next(row for row in self.store.records('asset') if row['kind'] == 'weather')
        old = {**asset, 'name': 'Vitória da Conquista, Bahia, Brasil', 'symbol': 'VITÓRIA DA CONQUISTA'}
        self.store.put('asset', asset['id'], old)
        self.store.save_quote(asset['id'], {'value': 22, 'unit': '°C', 'fetched_at': 1000})
        settings = self.store.settings()
        migrated = Store(self.folder.name)
        self.assertEqual(migrated.get('asset', asset['id']), {key: ('VDC' if key == 'symbol' else value) for key, value in old.items() if key != 'id'})
        self.assertEqual(migrated.quote(asset['id'])['value'], 22)
        self.assertEqual(migrated.settings(), settings)
        self.assertEqual(migrated.get('meta', 'weather_labels_before_shortening')[asset['id']], old['symbol'])
        self.store.put('asset', asset['id'], {**old, 'symbol': 'VC'})
        self.assertEqual(Store(self.folder.name).get('asset', asset['id'])['symbol'], 'VC')

    def test_weather_label_edit_preserves_weather_and_accepts_inflight_quote(self):
        asset = next(row for row in self.store.records('asset') if row['kind'] == 'weather')
        self.store.save_quote(asset['id'], {'value': 22, 'unit': '°C', 'fetched_at': 1000})
        with patch.object(self.engine, 'tick'):
            self.engine.save_asset({**asset, 'symbol': 'VDC'})
        self.assertEqual(self.store.quote(asset['id'])['value'], 22)
        self.assertTrue(self.store.history(asset['id']))
        self.engine._accept_quote(asset, {'value': 23, 'unit': '°C', 'fetched_at': 1001})
        self.assertEqual(self.store.quote(asset['id'])['value'], 23)

    def test_weather_city_edit_discards_previous_city_weather(self):
        asset = next(row for row in self.store.records('asset') if row['kind'] == 'weather')
        self.store.save_quote(asset['id'], {'value': 22, 'fetched_at': 1000})
        with patch.object(self.engine, 'tick'):
            self.engine.save_asset({**asset, 'latitude': -14.86, 'longitude': -40.84,
                                    'name': 'Vitória da Conquista', 'symbol': 'VDC'})
        self.engine._accept_quote(asset, {'value': 99, 'fetched_at': 1001})
        self.assertEqual(self.store.quote(asset['id']), {})
        self.assertEqual(self.store.history(asset['id']), [])

    def test_closed_or_changed_asset_ignores_inflight_result(self):
        self.store.delete('asset', self.asset['id'])
        with patch('global_monitor.providers.fetch', return_value={'value': 5, 'fetched_at': 1000}):
            self.engine._refresh(self.asset)
        self.assertEqual(self.store.quote(self.asset['id']), {})

    def test_history_tracks_success_only(self):
        self.store.save_quote(self.asset['id'], {'value': 5, 'fetched_at': 1000})
        self.store.save_quote(self.asset['id'], {'value': 5, 'fetched_at': 1001, 'error': 'offline'})
        self.assertEqual(self.store.history(self.asset['id']), [[1000, 5]])

    def test_error_preserves_last_real_value(self):
        self.store.save_quote(self.asset['id'], {'value': 5.2, 'fetched_at': 1000, 'source': 'Original'})
        with patch('global_monitor.providers.fx', side_effect=providers.DataError('offline')), \
             patch('global_monitor.providers.fx_fallback', side_effect=providers.DataError('offline')):
            result = providers.fetch(self.asset, self.store)
        self.assertEqual(result['value'], 5.2)
        self.assertEqual(result['fetched_at'], 1000)
        self.assertEqual(result['error'], 'offline')

    def test_fx_parser_and_daily_fallback(self):
        with patch('global_monitor.providers.request_json', return_value={'USDBRL': {
            'bid': '5.1', 'ask': '5.2', 'pctChange': '0.4', 'timestamp': '1700000000'}}):
            row = providers.fx('USD-BRL')
        self.assertEqual((row['value'], row['ask'], row['unit']), (5.1, 5.2, 'BRL'))
        with patch('global_monitor.providers.request_json', return_value=[{'quote': 'BRL', 'rate': 5.2, 'date': '2026-11-16'}]):
            row = providers.fx_fallback('USD-BRL')
        self.assertIn('diária', row['note'])

    def test_crypto_conversion_labels_original_change(self):
        with patch('global_monitor.providers.request_json', return_value={'last': '100', 'open': '80', 'high': '110', 'low': '70', 'volume': '20'}), \
             patch('global_monitor.providers.fx', return_value={'value': 5}):
            row = providers.crypto({'symbol': 'BTC-BRL'})
        self.assertEqual(row['value'], 500)
        self.assertEqual(row['change'], 25)
        self.assertIn('USD', row['change_label'])

    def test_brapi_unknown_asset_requires_free_key(self):
        with self.assertRaises(providers.DataError):
            providers.stock({'symbol': 'WEGE3', 'provider': 'brapi'}, self.store)

    def test_provider_minimum_intervals(self):
        settings = self.store.settings() | {'refresh_seconds': 60}
        self.assertEqual(providers.ttl({'kind': 'weather'}, settings), 900)
        self.assertEqual(providers.ttl({'kind': 'stock'}, settings), 1800)
        self.assertEqual(providers.ttl({'kind': 'stock', 'provider': 'alpha'}, settings), 21600)

    def test_alert_cooldown_and_unavailable_metric(self):
        self.store.put('alert', 'alert', validate_alert({'asset_id': self.asset['id'], 'title': 'Dólar alto',
                       'metric': 'value', 'operator': 'above', 'threshold': 5, 'cooldown': 60}, self.store))
        self.engine._alerts(self.asset, {'value': 6, 'source': 'Fonte'})
        self.engine._alerts(self.asset, {'value': 7, 'source': 'Fonte'})
        self.assertEqual(len(self.store.deliveries()), 1)
        with self.assertRaises(ValueError):
            validate_alert({'asset_id': self.asset['id'], 'operator': 'above', 'threshold': 'nan'}, self.store)

    def test_webhook_json_and_idempotency_header(self):
        integration = {'type': 'webhook', 'url': 'https://example.com', 'secret': 'key', 'recipient': 'me'}
        with patch('global_monitor.providers.request_json', return_value={}) as request:
            providers.deliver(integration, {'title': 'Hi', 'body': 'Text'}, 'event-id')
        self.assertFalse(request.call_args.kwargs['redirects'])
        self.assertEqual(request.call_args.kwargs['headers']['Idempotency-Key'], 'event-id')
        self.assertEqual(request.call_args.kwargs['payload']['destination'], 'me')

    def test_backup_validation_is_atomic(self):
        before = self.store.settings()
        backup = self.store.export_data()
        backup['settings']['theme'] = 'light'
        backup['alert'] = [{'asset_id': self.asset['id'], 'operator': 'above', 'metric': 'value', 'threshold': 'nan'}]
        with self.assertRaises(ValueError):
            self.store.import_data(backup)
        self.assertEqual(self.store.settings(), before)
        self.assertEqual(self.store.records('alert'), [])

    def test_concurrent_scheduler_claims_one_occurrence(self):
        from concurrent.futures import ThreadPoolExecutor
        self.reminder()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(self.store.due_reminders, [2000, 2000]))
        self.assertEqual(sum(results), 1)
        self.assertEqual(len(self.store.deliveries()), 1)

    def test_currency_batch_uses_one_request(self):
        assets = [row for row in self.store.records('asset') if row['kind'] == 'fx']
        response = {'USDBRL': {'bid': '5', 'ask': '5.1'}, 'EURBRL': {'bid': '6', 'ask': '6.1'}}
        with patch('global_monitor.providers.request_json', return_value=response) as request:
            result = providers.fx_many(assets, self.store)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(len(result), 2)
        self.assertTrue(all(not row['error'] for row in result.values()))

    def test_historical_crypto_does_not_fabricate_brl_conversion(self):
        with patch('global_monitor.providers.request_json') as request:
            self.assertEqual(providers.historical({'kind': 'crypto', 'symbol': 'BTC-BRL'}, self.store), [])
        request.assert_not_called()

    def test_restart_preserves_preferences_and_agenda(self):
        self.store.save_settings({'icon_size': 28})
        identity = self.reminder()
        reopened = Store(self.folder.name)
        self.assertEqual(reopened.settings()['icon_size'], 28)
        self.assertEqual(reopened.records('reminder')[0]['id'], identity)
        self.assertEqual(len(reopened.records('asset')), 5)

    def test_whatsapp_template_adapter(self):
        integration = {'type': 'whatsapp', 'phone_id': '123456', 'recipient': '5511999999999',
                       'secret': 'key', 'template': 'lembrete', 'language': 'pt_BR'}
        with patch('global_monitor.providers.request_json', return_value={}) as request:
            providers.deliver(integration, {'title': 'Reunião', 'body': 'Hoje'}, 'event-id')
        body = request.call_args.kwargs['payload']
        self.assertEqual(body['type'], 'template')
        self.assertEqual(len(body['template']['components'][0]['parameters']), 2)


if __name__ == '__main__':
    unittest.main()
