"""City-first location picker; network work is asynchronous and selection explicit."""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk

from . import APP_ID
from .providers import search_cities

try:
    gi.require_version('Geoclue', '2.0')
    from gi.repository import Geoclue
except (ImportError, ValueError):
    Geoclue = None


class CitySearch(Gtk.Box):
    COUNTRIES = ['', 'BR', 'PT', 'US', 'AR', 'ES']

    def __init__(self, background, on_selected, existing=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.background, self.on_selected = background, on_selected
        self._generation, self._timer, self._location_timer = 0, 0, 0
        self._closed, self._location_cancel = False, None
        self.search_entry = Gtk.SearchEntry(placeholder_text='Nome da cidade, ex.: Campinas, SP', hexpand=True)
        self.search_entry.set_text((existing or {}).get('name', '').split(',')[0])
        self.search_entry.set_tooltip_text('Digite a cidade. Acrescente estado ou país após uma vírgula para refinar.')
        line = Gtk.Box(spacing=8)
        line.append(self.search_entry)
        self.search_button = Gtk.Button(label='Buscar', css_classes=['suggested-action'])
        line.append(self.search_button)
        self.append(line)
        self.country = Adw.ComboRow(title='País da busca', model=Gtk.StringList.new(
            ['Todos os países', 'Brasil', 'Portugal', 'Estados Unidos', 'Argentina', 'Espanha']))
        self.append(self.country)
        self.spinner = Gtk.Spinner()
        self.status = Gtk.Label(label='Digite pelo menos 2 letras e pressione Enter ou Buscar.',
                                xalign=0, wrap=True, css_classes=['dim-label'])
        feedback = Gtk.Box(spacing=8)
        feedback.append(self.spinner)
        feedback.append(self.status)
        self.append(feedback)
        self.results = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=['boxed-list'])
        self.append(self.results)
        self.selection = Adw.ActionRow(title='Cidade selecionada', subtitle=GLib.markup_escape_text(
            (existing or {}).get('name', 'Escolha uma cidade nos resultados abaixo da busca.')))
        self.selection.set_subtitle_lines(3)
        self.append(self.selection)
        if Geoclue is not None:
            self.location_button = Gtk.Button(label='Usar localização do Ubuntu', halign=Gtk.Align.START)
            self.location_button.connect('clicked', lambda _button: self.locate())
            self.append(self.location_button)
            self.append(Gtk.Label(label='Opcional: solicita acesso à localização aproximada pelo Ubuntu. '
                                 'Você também pode pesquisar qualquer cidade.', xalign=0, wrap=True,
                                 css_classes=['dim-label']))
        self.search_entry.connect('changed', self._query_changed)
        self.search_entry.connect('activate', lambda _entry: self.search())
        self.search_button.connect('clicked', lambda _button: self.search())
        self.country.connect('notify::selected', self._query_changed)

    def _clear_results(self):
        while self.results.get_first_child():
            self.results.remove(self.results.get_first_child())

    def _cancel_debounce(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _cancel_location(self):
        if self._location_timer:
            GLib.source_remove(self._location_timer)
            self._location_timer = 0
        if self._location_cancel:
            self._location_cancel.cancel()
            self._location_cancel = None

    def _query_changed(self, *_args):
        self._generation += 1
        self._cancel_debounce()
        self._cancel_location()
        self.spinner.stop()
        self._clear_results()
        self.status.set_text('Pressione Enter ou Buscar; a busca automática começa com 3 letras.')
        if len(self.search_entry.get_text().split(',')[0].strip()) >= 3:
            self._timer = GLib.timeout_add(650, self._debounced)

    def _debounced(self):
        self._timer = 0
        self.search()
        return GLib.SOURCE_REMOVE

    def search(self):
        if self._closed:
            return
        self._cancel_debounce()
        self._cancel_location()
        self._generation += 1
        token = self._generation
        query = self.search_entry.get_text().strip()
        country = self.COUNTRIES[self.country.get_selected()]
        self._clear_results()
        if len(query.split(',')[0].strip()) < 2:
            self.spinner.stop()
            self.status.set_text('Digite ao menos duas letras da cidade.')
            return
        self.spinner.start()
        self.status.set_text('Buscando cidades…')
        self.background(lambda: search_cities(query, country),
                        lambda rows: self._found(token, rows), lambda message: self._failed(token, message))

    def _found(self, token, rows):
        if self._closed or token != self._generation:
            return
        self.spinner.stop()
        self._clear_results()
        self.status.set_text(f'{len(rows)} resultado(s). Clique na cidade desejada.' if rows else
                             'Nenhuma cidade encontrada. Confira o nome, o estado ou o filtro de país.')
        for city in rows:
            district = city.get('district') if city.get('district') != city['city'] else ''
            row = Adw.ActionRow(title=GLib.markup_escape_text(city['city']),
                subtitle=GLib.markup_escape_text(' · '.join(dict.fromkeys(filter(None,
                    [district, city['region'], city['country']])))),
                activatable=True)
            row.set_title_lines(2)
            row.set_subtitle_lines(2)
            row.add_suffix(Gtk.Image(icon_name='go-next-symbolic'))
            row.connect('activated', lambda _row, result=city: self.select(result))
            self.results.append(row)

    def select(self, city):
        self._generation += 1
        self._cancel_debounce()
        self._cancel_location()
        self.spinner.stop()
        self.on_selected(city)
        self.selection.set_subtitle(GLib.markup_escape_text(city['name']))
        self.status.set_text('Cidade selecionada. Confira o nome e clique em Salvar.')
        self._clear_results()

    def _failed(self, token, message):
        if not self._closed and token == self._generation:
            self.spinner.stop()
            self.status.set_text(str(message))

    def locate(self):
        if self._closed or Geoclue is None:
            return
        self._cancel_debounce()
        self._cancel_location()
        self._generation += 1
        token = self._generation
        self.spinner.start()
        self.status.set_text('Aguardando autorização e localização do Ubuntu…')
        self._location_cancel = Gio.Cancellable()

        def timeout():
            self._location_timer = 0
            self._generation += 1
            self._cancel_location()
            self.spinner.stop()
            self.status.set_text('O Ubuntu não forneceu uma localização a tempo. Pesquise sua cidade acima.')
            return GLib.SOURCE_REMOVE

        def located(_source, result):
            try:
                simple = Geoclue.Simple.new_finish(result)
                try:
                    if not self._closed and token == self._generation:
                        location = simple.get_location()
                        if location is None:
                            raise ValueError('Localização indisponível.')
                        self.select({'name': 'Localização atual', 'latitude': location.props.latitude,
                                     'longitude': location.props.longitude})
                finally:
                    # A single snapshot is enough: never track the user in the background.
                    simple.get_client().call_stop(None, self._stopped)
            except (GLib.Error, ValueError):
                if token == self._generation:
                    self._cancel_location()
                    self._failed(token, 'Localização indisponível ou sem permissão. Verifique '
                                 'Configurações → Privacidade e segurança → Localização, ou pesquise a cidade.')

        self._location_timer = GLib.timeout_add_seconds(30, timeout)
        Geoclue.Simple.new(APP_ID, Geoclue.AccuracyLevel.CITY, self._location_cancel, located)

    @staticmethod
    def _stopped(client, result):
        try:
            client.call_stop_finish(result)
        except GLib.Error:
            pass

    def close(self):
        self._closed = True
        self._generation += 1
        self._cancel_debounce()
        self._cancel_location()
