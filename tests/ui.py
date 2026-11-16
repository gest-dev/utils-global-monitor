"""Native GUI tests run only on the disposable GNOME test display."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'extension'))
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib
from global_monitor import providers
from global_monitor import city_search
from global_monitor.ui import PAGES

spec = importlib.util.spec_from_file_location('monitor_app', Path(__file__).resolve().parents[1] / 'extension' / 'monitor.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

count = 0
failed = False
stage = 0
started = time.monotonic()
folder = tempfile.TemporaryDirectory()
os.environ['MONITOR_GLOBAL_HOME'] = folder.name


def fake_quote(asset, _store):
    return {'value': 23 if asset['kind'] == 'weather' else 5.25, 'unit': '°C' if asset['kind'] == 'weather' else 'BRL',
            'change': 0.5, 'stamp': time.time(), 'checked_at': time.time(), 'fetched_at': time.time(),
            'source': 'TEST FIXTURE', 'note': 'Dados exclusivos do teste isolado', 'error': ''}


# Fixtures are confined to this test process; the production service uses live APIs.
providers.fetch = fake_quote
providers.fx_many = lambda assets, store: {asset['id']: fake_quote(asset, store) for asset in assets}
providers.historical = lambda _asset, _store: []


class TestApplication(module.Application):
    def __init__(self):
        super().__init__()
        self.set_application_id('org.gezer.MonitorGlobal.Test')


app = TestApplication()


def check(condition, message):
    global count
    count += 1
    if not condition:
        raise AssertionError(message)


def children(widget):
    child = widget.get_first_child()
    while child:
        yield child
        child = child.get_next_sibling()


def entry(form, title, text):
    row = next(row for row in children(form.body) if isinstance(row, Adw.EntryRow) and row.get_title() == title)
    row.set_text(text)


def exercise():
    global stage, failed
    try:
        if time.monotonic() - started > 35:
            raise AssertionError('GUI test exceeded 35 seconds')
        window = app.window
        if not window:
            return GLib.SOURCE_CONTINUE
        if stage < len(PAGES):
            key = PAGES[stage][0]
            window.navigate(key)
            check(window.page == key, 'Page opens: ' + key)
            stage += 1
            return GLib.SOURCE_CONTINUE
        if stage == len(PAGES):
            window.navigate('fx')
            before = len(app.store.records('asset'))
            form = window.asset_form('fx')
            entry(form, 'Nome', 'Libra teste')
            entry(form, 'Código / par', 'GBP-BRL')
            form._save()
            check(len(app.store.records('asset')) == before + 1, 'Asset form saves an indicator')
            asset = next(row for row in app.store.records('asset') if row['symbol'] == 'GBP-BRL')
            check(asset['pinned'], 'New indicator can be pinned')
            form = window.asset_form('weather')
            form._save()
            check('Selecione uma cidade' in form.error.get_text(), 'Weather requires a city or manual coordinates')
            picker = form.city_search
            requests = []
            picker.background = lambda function, success, failure: requests.append((function, success, failure))
            picker.search_entry.set_text('Campinas, SP')
            picker.search_entry.emit('activate')
            check(len(requests) == 1 and picker.spinner.get_spinning(), 'Enter searches with loading feedback')
            picker.search_entry.set_text('São José')
            picker.country.set_selected(1)
            picker.search_button.emit('clicked')
            with patch.object(city_search, 'search_cities', return_value=[]) as search:
                requests[-1][0]()
                check(search.call_args.args == ('São José', 'BR'), 'Search uses the selected country')
            cities = [{'name': 'São José, Santa Catarina, Brasil', 'city': 'São José', 'region': 'Santa Catarina',
                       'country': 'Brasil', 'latitude': -27.6, 'longitude': -48.6}]
            requests[0][1](cities)
            check(picker.results.get_first_child() is None, 'Stale responses cannot replace current results')
            requests[-1][1](cities)
            check(picker.results.get_first_child() is not None, 'City results are selectable')
            check(not any(row['name'] == cities[0]['name'] for row in app.store.records('asset')),
                  'Search does not automatically save or select an ambiguous city')
            picker.results.get_first_child().emit('activated')
            check('Santa Catarina' in picker.selection.get_subtitle(), 'Selected city shows state and country')
            form._save()
            weather = next(row for row in app.store.records('asset') if row['name'] == cities[0]['name'])
            check(weather['latitude'] == -27.6 and weather['longitude'] == -48.6, 'Chosen city coordinates persist')
            form = window.asset_form('weather', weather)
            picker = form.city_search
            check(picker.search_entry.get_text() == 'São José', 'Editing searches by city instead of the full saved label')
            requests = []
            picker.background = lambda function, success, failure: requests.append((function, success, failure))
            picker.search()
            requests[-1][1]([])
            check('Nenhuma cidade' in picker.status.get_text(), 'Empty city searches explain how to refine')
            picker.search()
            requests[-1][2]('Sem conexão de teste')
            check('Sem conexão' in picker.status.get_text() and not picker.spinner.get_spinning(), 'Search errors stop loading')
            picker.search()
            form.close()
            requests[-1][1](cities)
            check(picker._closed and picker.results.get_first_child() is None, 'Closing ignores pending city requests')
            location_requests = []
            client = SimpleNamespace(call_stop=lambda *_args: location_requests.append('stopped'))
            simple = SimpleNamespace(get_location=lambda: SimpleNamespace(props=SimpleNamespace(latitude=12.3, longitude=45.6)),
                                     get_client=lambda: client)
            mock_simple = SimpleNamespace(new=lambda *args: location_requests.append(args), new_finish=lambda _result: simple)
            with patch.object(city_search, 'Geoclue', SimpleNamespace(Simple=mock_simple,
                              AccuracyLevel=SimpleNamespace(CITY=4))):
                form = window.asset_form('weather')
                picker = form.city_search
                check(not location_requests, 'Opening a form never requests geolocation')
                picker.location_button.emit('clicked')
                check(len(location_requests) == 1 and location_requests[0][1] == 4,
                      'Geolocation is requested only by clicking and at city accuracy')
                request_cancel = location_requests[0][2]
                form.close()
                check(request_cancel.is_cancelled(), 'Closing cancels the geolocation request')
                form = window.asset_form('weather')
                picker = form.city_search
                picker.locate()
                location_requests[-1][3](None, object())
                check(picker.selection.get_subtitle() == 'Localização atual', 'Authorized location selects a snapshot')
                check(location_requests[-1] == 'stopped', 'Location acquisition stops after a single snapshot')
                form._save()
                located_asset = next(row for row in app.store.records('asset') if row['name'] == 'Localização atual')
                check(located_asset['latitude'] == 12.3, 'Optional location can be saved for weather')
                form = window.asset_form('weather', weather)
                picker = form.city_search
                picker.locate()
                callback = location_requests[-1][3]
                with patch.object(mock_simple, 'new_finish', side_effect=GLib.Error('Denied')):
                    callback(None, object())
                check('sem permissão' in picker.status.get_text() and not picker.spinner.get_spinning(),
                      'Denied location explains the city search fallback')
                form._save()
                check(app.store.get('asset', weather['id'])['latitude'] == -27.6,
                      'Denied location preserves the existing selected city')
                location_requests.clear()
                form = window.asset_form('weather')
                picker = form.city_search
                picker.locate()
                location_requests[-1][3](None, object())
                check(location_requests[-1] == 'stopped', 'A successful snapshot stops location tracking')
                form._save()
                check(any(row.get('latitude') == 12.3 for row in app.store.records('asset')),
                      'Authorized location coordinates can be saved')
                form = window.asset_form('weather', weather)
                picker = form.city_search
                picker.locate()
                with patch.object(mock_simple, 'new_finish', side_effect=ValueError('denied')):
                    location_requests[-1][3](None, object())
                check('sem permissão' in picker.status.get_text() and not picker.spinner.get_spinning(),
                      'Denied location access explains city-search fallback')
                form._save()
                check(app.store.get('asset', weather['id'])['latitude'] == -27.6,
                      'Failed geolocation preserves a previously selected city')
            window.navigate('agenda')
            form = window.reminder_form()
            entry(form, 'Título', 'Lembrete criado pelo formulário')
            form._save()
            check(len(app.store.records('reminder')) == 1, 'Reminder form saves')
            reminder = app.store.records('reminder')[0]
            form = window.reminder_form(reminder)
            entry(form, 'Título', 'Lembrete editado')
            form._save()
            check(app.store.get('reminder', reminder['id'])['title'] == 'Lembrete editado', 'Reminder edits preserve identity')
            form = window.alert_form(asset_id=asset['id'])
            entry(form, 'Título do alerta', 'Alerta teste')
            entry(form, 'Limite', '9999')
            form._save()
            check(len(app.store.records('alert')) == 1, 'Alert form saves')
            form = window.integration_form()
            entry(form, 'Nome', 'Destino desativado')
            entry(form, 'URL do webhook', 'https://example.invalid/notify')
            form._save()
            check(not app.store.records('integration')[0]['enabled'], 'Integration starts disabled')
            window.navigate('appearance')
            def descendants(widget):
                for child in children(widget):
                    yield child
                    yield from descendants(child)
            position = next(row for row in descendants(window.content) if isinstance(row, Adw.ComboRow)
                            and row.get_title() == 'Posição na barra do Ubuntu')
            position.set_selected(1)
            check(app.store.settings()['panel_position'] == 'center', 'Appearance control saves position before clock')
            window.set_settings({'theme': 'light', 'icon_size': 32, 'window_icon_size': 26, 'window_font_size': 14})
            check(app.store.settings()['theme'] == 'light', 'Light theme applies')
            window.set_settings({'theme': 'dark', 'accent': '#38bdf8'})
            check(app.store.settings()['accent'] == '#38bdf8', 'Custom accent applies')
            window.profile('Viagem')
            check(all(row['kind'] in ('weather', 'fx') for row in app.store.records('asset') if row['pinned']), 'Profile updates pinned modules')
            before = len(app.store.records('reminder'))
            window.restore_appearance()
            check(len(app.store.records('reminder')) == before, 'Restoring appearance preserves agenda')
            window.navigate('overview')
            identity = app.store.save_reminder({'title': 'TESTE ISOLADO: notificação nativa', 'due': time.time() - 1,
                                               'timezone': 'America/Sao_Paulo', 'repeat': 'none'})
            app.engine.tick()
            stage += 1
            return GLib.SOURCE_CONTINUE
        events = app.store.deliveries()
        if not any(row['state'] == 'sent' and row['channel'] == 'desktop' for row in events):
            return GLib.SOURCE_CONTINUE
        check(bool(app._notifications), 'Native notification accepted by GNOME')
        native_id = next(iter(app._notifications))
        before = len(app.store.records('reminder'))
        app._notification_signal(None, None, 'ActionInvoked', GLib.Variant('(us)', (native_id, 'snooze')))
        check(len(app.store.records('reminder')) == before + 1, 'Native snooze action schedules a reminder')
        window.close()
        check(app.window is None, 'Closing releases the window')
        check(app.engine.state()['assets'], 'Background service remains usable')
        print(f'MONITOR_GLOBAL_UI_OK: {count} verificações.', flush=True)
        app.quit()
        return GLib.SOURCE_REMOVE
    except Exception:
        import traceback
        traceback.print_exc()
        failed = True
        app.quit()
        return GLib.SOURCE_REMOVE


GLib.timeout_add(250, exercise)
try:
    app.run([sys.argv[0]])
finally:
    folder.cleanup()
sys.exit(1 if failed else 0)
