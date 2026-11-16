"""GTK 4 / libadwaita interface. All network calls stay off the UI thread."""
from datetime import datetime
import json
import math
from pathlib import Path
import time
import uuid
from zoneinfo import ZoneInfo

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Graphene', '1.0')
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk, Pango

from . import VERSION
from .engine import validate_alert, validate_integration
from .city_search import CitySearch
from .charts import ChartPanel
from .agenda import FILTERS as AGENDA_FILTERS, STATUSES, agenda_view, overdue_text, reminder_status
from .store import COLORS, DEFAULTS, finite, system_zone, validate_asset, weather_symbol

ROOT = Path(__file__).resolve().parents[1]
ICON_SCALE = 1.0
KINDS = {'fx': 'Moedas', 'crypto': 'Criptomoedas', 'stock': 'Bolsa', 'weather': 'Clima', 'agenda': 'Agenda'}
PAGES = [('overview', 'Visão geral', 'globe'), ('agenda', 'Agenda', 'agenda'),
         ('fx', 'Moedas', 'fx'), ('crypto', 'Criptomoedas', 'crypto'),
         ('stock', 'Bolsa', 'stock'), ('weather', 'Clima', 'weather'),
         ('alerts', 'Alertas', 'bell'), ('integrations', 'Integrações', 'link'),
         ('appearance', 'Aparência', 'palette')]


def label(text='', css=None, **kwargs):
    widget = Gtk.Label(label=str(text), xalign=0, **kwargs)
    if css:
        for name in css.split():
            widget.add_css_class(name)
    return widget


def icon(name, size=20, scaled=True):
    image = Gtk.Image.new_from_gicon(Gio.FileIcon.new(Gio.File.new_for_path(str(ROOT / 'icons' / f'{name}-symbolic.svg'))))
    image._mg_size = size
    image._mg_scaled = scaled
    image.set_pixel_size(round(size * ICON_SCALE) if scaled else size)
    image.add_css_class('module-' + name)
    return image


def box(vertical=True, spacing=8, **kwargs):
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL,
                   spacing=spacing, **kwargs)


def button(text=None, callback=None, icon_name=None, css=None):
    widget = Gtk.Button(label=text) if text else Gtk.Button(icon_name=icon_name)
    if callback:
        widget.connect('clicked', lambda _button: callback())
    if css:
        widget.add_css_class(css)
    return widget


def money(value, unit='', compact=False):
    if value is None:
        return '—'
    magnitude = ''
    if compact and abs(value) >= 1_000_000:
        value, magnitude = value / 1_000_000, 'M'
    elif compact and abs(value) >= 10_000:
        value, magnitude = value / 1000, 'k'
    decimals = 1 if unit == '°C' else (4 if 0 < abs(value) < 1 else 2)
    number = f'{value:,.{decimals}f}'.replace(',', '_').replace('.', ',').replace('_', '.')
    prefix = {'BRL': 'R$ ', 'USD': 'US$ ', 'EUR': '€ ', 'GBP': '£ '}.get(unit, '')
    return prefix + number + magnitude + (f' {unit}' if unit and not prefix else '')


def stamp(value, timezone=None):
    if not value:
        return 'Aguardando primeira consulta'
    date = datetime.fromtimestamp(value, ZoneInfo(timezone) if timezone else None)
    return date.strftime('%d/%m/%Y %H:%M')


class Sparkline(Gtk.Widget):
    def __init__(self, points, color='#22c55e', height=100):
        super().__init__(hexpand=True, height_request=height)
        self.points = [point for point in points if point[1] is not None]
        self.color = color

    def do_snapshot(self, drawing):
        width, height = self.get_width(), self.get_height()
        grid = Gdk.RGBA(red=0.5, green=0.5, blue=0.5, alpha=0.15)
        for part in (0.25, 0.5, 0.75, 1):
            drawing.append_color(grid, Graphene.Rect().init(0, (height - 4) * part, width, 1))
        if len(self.points) < 2:
            return
        color = Gdk.RGBA()
        color.parse(self.color)
        values = [point[1] for point in self.points]
        low, high = min(values), max(values)
        span = high - low or max(abs(high) * 0.01, 1)
        first, last = self.points[0][0], self.points[-1][0]
        duration = last - first or 1
        previous = None
        for when, value in self.points:
            x = 2 + (width - 4) * (when - first) / duration
            y = height - 6 - (height - 12) * (value - low) / span
            if previous:
                px, py = previous
                drawing.save()
                drawing.translate(Graphene.Point().init(px, py))
                drawing.rotate(math.degrees(math.atan2(y - py, x - px)))
                drawing.append_color(color, Graphene.Rect().init(0, -1, math.hypot(x - px, y - py), 2))
                drawing.restore()
            previous = x, y


class Form(Adw.Window):
    def __init__(self, parent, title, save_label='Salvar'):
        super().__init__(transient_for=parent, application=parent.get_application(), destroy_with_parent=True,
                         modal=True, title=title, default_width=560, default_height=640)
        self.parent_window = parent
        self.add_css_class('global-monitor')
        root = box(spacing=0)
        self.set_content(root)
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title=title))
        header.pack_start(button('Cancelar', self.close))
        self.save_button = button(save_label, self._save, css='suggested-action')
        header.pack_end(self.save_button)
        root.append(header)
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.body = box(spacing=14, margin_top=20, margin_bottom=24, margin_start=20, margin_end=20)
        scroll.set_child(self.body)
        root.append(scroll)
        self.error = label('', 'error', wrap=True)
        self.body.append(self.error)
        self.on_save = lambda: None

    def entry(self, title, value='', password=False):
        row = Adw.PasswordEntryRow(title=title) if password else Adw.EntryRow(title=title)
        row.set_text(str(value))
        self.body.append(row)
        return row

    def choice(self, title, choices, selected=0):
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new(choices))
        row.set_selected(selected)
        self.body.append(row)
        return row

    def spin(self, title, value, low, high, step=1):
        row = Adw.ActionRow(title=title)
        widget = Gtk.SpinButton.new_with_range(low, high, step)
        widget.set_value(value)
        widget.set_valign(Gtk.Align.CENTER)
        row.add_suffix(widget)
        self.body.append(row)
        return widget

    def check(self, title, active=True):
        row = Adw.SwitchRow(title=title, active=active)
        self.body.append(row)
        return row

    def help(self, text):
        self.body.append(label(text, 'dim-label', wrap=True))

    def channels(self, selected=()):
        self.help('A notificação do Ubuntu acompanha o lembrete. Escolha também os destinos externos desejados.')
        rows = []
        for item in self.parent_window.store.records('integration'):
            if item.get('enabled'):
                rows.append((item['id'], self.check(item['name'], item['id'] in selected)))
        return lambda: [identity for identity, widget in rows if widget.get_active()]

    def _save(self):
        try:
            self.on_save()
            self.parent_window.engine.changed()
            self.parent_window.engine.tick()
            self.close()
        except (ValueError, KeyError, TypeError) as error:
            self.error.set_text(str(error))


class MonitorWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title='Monitor Global', default_width=1180, default_height=800)
        self.add_css_class('global-monitor')
        self.app, self.engine, self.store = app, app.engine, app.store
        self.page = 'overview'
        self._css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self._css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._preview = None
        self._nav = {}
        self._agenda_mode, self._agenda_query = 'today', ''
        self._calendar_day = datetime.now().date()
        self.apply_appearance()
        root = box(spacing=0)
        self.set_content(root)
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title='Monitor Global', subtitle='Moedas · mercados · clima · agenda'))
        header.pack_end(button(callback=self.refresh, icon_name='view-refresh-symbolic'))
        root.append(header)
        horizontal = box(False, 0, vexpand=True)
        root.append(horizontal)
        sidebar = box(spacing=6, width_request=182, margin_top=16, margin_bottom=16, margin_start=12, margin_end=12)
        sidebar.add_css_class('sidebar')
        brand = box(False, 10, margin_start=8, margin_bottom=20)
        logo = Gtk.Image.new_from_gicon(Gio.FileIcon.new(Gio.File.new_for_path(str(ROOT / 'icons/monitor-global-logo-v1.png'))))
        logo.set_pixel_size(36)
        brand.append(logo)
        brand.append(label('GLOBAL', 'brand'))
        sidebar.append(brand)
        for key, title, image in PAGES:
            row = box(False, 12, margin_start=4, margin_end=4, margin_top=6, margin_bottom=6)
            row.append(icon(image, 19))
            row.append(label(title))
            control = Gtk.ToggleButton(child=row)
            control.add_css_class('nav-button')
            control.connect('clicked', lambda _widget, page=key: self.navigate(page))
            sidebar.append(control)
            self._nav[key] = control
        sidebar.append(Gtk.Box(vexpand=True))
        sidebar.append(label('● Serviço local ativo', 'positive', margin_start=12))
        sidebar.append(label(f'Monitor Global {VERSION}', 'dim-label', margin_start=12))
        horizontal.append(sidebar)
        horizontal.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        self._toast = Adw.ToastOverlay(hexpand=True, vexpand=True)
        self._scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self._toast.set_child(self._scroll)
        horizontal.append(self._toast)
        self.content = None
        self.navigate('overview')
        self._agenda_timer = GLib.timeout_add_seconds(5, self._agenda_clock_tick)
        self.connect('close-request', self._stop_agenda_clock)

    def _stop_agenda_clock(self, *_args):
        if self._agenda_timer:
            GLib.source_remove(self._agenda_timer)
            self._agenda_timer = 0
        return False

    def _agenda_clock_tick(self):
        if self.page == 'agenda':
            now = time.time()
            rows = self.store.records('reminder')
            key = self._agenda_clock_signature(rows, now)
            if key != getattr(self, '_agenda_clock_key', None):
                adjustment = self._scroll.get_vadjustment()
                offset = adjustment.get_value()
                self.render_agenda_list()
                adjustment.set_value(offset)
        return GLib.SOURCE_CONTINUE

    @staticmethod
    def _agenda_clock_signature(rows, now):
        return (int(now // 60), tuple((row['id'], row['due'], reminder_status(row, now)) for row in rows))

    def toast(self, message):
        self._toast.add_toast(Adw.Toast.new(message))

    def refresh(self):
        self.engine.tick()
        self.toast('Atualizando consultas disponíveis; os intervalos gratuitos são respeitados.')

    def apply_appearance(self):
        global ICON_SCALE
        settings = self.store.settings()
        ICON_SCALE = settings['window_icon_size'] / 20
        manager = Adw.StyleManager.get_default()
        manager.set_color_scheme({'dark': Adw.ColorScheme.FORCE_DARK, 'light': Adw.ColorScheme.FORCE_LIGHT,
                                  'system': Adw.ColorScheme.DEFAULT}[settings['theme']])
        dark = manager.get_dark()
        background, surface, edge, muted = ('#101411', '#171e19', '#2b382f', '#a5b1a8') if dark else ('#f4f8f5', '#ffffff', '#d5dfd8', '#526258')
        accent = settings['accent']
        rgb = [int(accent[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        luminance = sum(weight * (value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)
                        for weight, value in zip((0.2126, 0.7152, 0.0722), rgb))
        accent_text = '#101411' if luminance > 0.179 else '#ffffff'
        colors = ''.join(f'.module-{kind} {{ color: {color}; }}' for kind, color in settings['colors'].items())
        self._css.load_from_string(f'''
            @define-color accent_bg_color {accent}; @define-color accent_color {accent};
            window.global-monitor {{ background: {background}; font-size: {settings['window_font_size']}px;
                --accent-bg-color: {accent}; --accent-color: {accent}; --accent-fg-color: {accent_text}; }}
            .global-monitor button.suggested-action {{ background: {accent}; color: {accent_text}; }}
            .global-monitor .sidebar {{ background: {background}; }}
            .global-monitor .brand {{ font-size: 20px; font-weight: 800; letter-spacing: 2px; color: {accent}; }}
            .module-globe, .module-link, .module-palette {{ color: {accent}; }}
            .global-monitor .nav-button {{ background: transparent; border: none; box-shadow: none; border-radius: 10px; }}
            .global-monitor .nav-button:checked {{ background: alpha({accent}, 0.16); color: {accent}; }}
            .global-monitor .metric-card, .global-monitor .surface {{ background: {surface}; border: 1px solid {edge}; border-radius: 16px; }}
            .global-monitor button.metric-card:hover {{ border-color: {accent}; }}
            .global-monitor .metric-value {{ font-size: 30px; font-weight: 750; }}
            .global-monitor .muted {{ color: {muted}; }}
            .global-monitor .positive {{ color: {'#4ade80' if dark else '#16713a'}; }}
            .global-monitor .negative {{ color: {'#fb7185' if dark else '#b42338'}; }}
            .global-monitor .warning {{ color: {'#fbbf24' if dark else '#956400'}; }}
            .global-monitor .bar-preview {{ background: {'#050b07' if dark else '#e4eee7'}; border-radius: 10px; padding: 12px; }}
            .global-monitor .agenda-tab {{ border-radius: 10px; padding: 9px 12px; }}
            .global-monitor .agenda-tab:checked {{ background: alpha({accent}, 0.18); color: {accent}; }}
            .global-monitor .status-badge {{ border-radius: 12px; padding: 5px 10px; font-weight: 700; }}
            .global-monitor .status-overdue {{ background: alpha(#fb7185, 0.15); color: {'#fb7185' if dark else '#b42338'}; }}
            .global-monitor .status-pending {{ background: alpha(#60a5fa, 0.15); color: {'#60a5fa' if dark else '#1d4ed8'}; }}
            .global-monitor .status-notified {{ background: alpha(#fbbf24, 0.15); color: {'#fbbf24' if dark else '#956400'}; }}
            .global-monitor .status-done {{ background: alpha(#4ade80, 0.15); color: {'#4ade80' if dark else '#16713a'}; }}
            .global-monitor .status-paused {{ background: alpha(#9ca3af, 0.15); color: {muted}; }}
            {colors}
        ''')
        Gtk.Settings.get_default().set_property('gtk-enable-animations', settings['animations'])
        def resize(widget):
            if hasattr(widget, '_mg_size') and widget._mg_scaled:
                widget.set_pixel_size(round(widget._mg_size * ICON_SCALE))
            child = widget.get_first_child()
            while child:
                resize(child)
                child = child.get_next_sibling()
        resize(self)

    def navigate(self, page):
        if page not in dict((key, title) for key, title, _image in PAGES):
            page = 'overview'
        self.page = page
        self._preview = None
        for key, widget in self._nav.items():
            widget.set_active(key == page)
        self.content = box(spacing=18, margin_top=24, margin_bottom=28, margin_start=26, margin_end=26)
        self._scroll.set_child(self.content)
        self._build_page()

    def updated(self):
        self.apply_appearance()
        if self.page in ('appearance', 'integrations'):
            if self._preview:
                self.render_preview()
            if self.page == 'integrations' and hasattr(self, '_deliveries_box'):
                self.render_deliveries()
            return
        adjustment = self._scroll.get_vadjustment()
        offset = adjustment.get_value()
        self.navigate(self.page)
        GLib.idle_add(lambda: adjustment.set_value(min(offset, adjustment.get_upper())) or False)

    def heading(self, title, description, action=None, action_text='Adicionar'):
        row = box(False, 16)
        text = box(spacing=6, hexpand=True)
        text.append(label(title, 'title-1'))
        text.append(label(description, 'muted', wrap=True))
        row.append(text)
        if action:
            control = button(action_text, action, css='suggested-action')
            control.set_valign(Gtk.Align.CENTER)
            row.append(control)
        self.content.append(row)

    def surface(self, parent=None):
        frame = Gtk.Frame()
        frame.add_css_class('surface')
        child = box(spacing=12, margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)
        frame.set_child(child)
        (parent or self.content).append(frame)
        return child

    def empty(self, title, description, parent=None):
        page = Adw.StatusPage(title=title, description=description, icon_name='view-grid-symbolic')
        (parent or self.content).append(page)

    def _build_page(self):
        if self.page == 'overview':
            self.overview()
        elif self.page in ('fx', 'crypto', 'stock', 'weather'):
            self.asset_page(self.page)
        else:
            getattr(self, self.page + '_page')()

    def overview(self):
        self.heading('Seu mundo, em um lugar.', 'Acompanhe seus favoritos e mantenha os compromissos por perto.')
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                           min_children_per_line=1, max_children_per_line=3, column_spacing=12, row_spacing=12)
        self.content.append(flow)
        state = self.engine.state(history=True)
        for item in state['assets']:
            if item.get('enabled', True):
                flow.append(self.asset_card(item))
        self.content.append(label('Na sua barra', 'title-3'))
        self._preview = box(False, 14)
        self._preview.add_css_class('bar-preview')
        self.content.append(self._preview)
        self.render_preview()
        area = self.surface()
        header = box(False, 12)
        header.append(icon('agenda', 24))
        header.append(label('Próximos lembretes', 'title-3', hexpand=True))
        header.append(button('Abrir agenda', lambda: self.navigate('agenda')))
        area.append(header)
        upcoming = [item for item in state['reminders'] if item.get('enabled') and item.get('status') != 'done'][:3]
        if not upcoming:
            area.append(label('Sua agenda está livre. Adicione um lembrete para começar.', 'muted', wrap=True))
        for item in upcoming:
            area.append(label(f"{stamp(item['due'], item['timezone'])}  ·  {item['title']}", wrap=True))
        self.content.append(label('Cotações são referências da fonte, com os respectivos atrasos. '
                                  'Clima: Open-Meteo.com, CC BY 4.0.', 'dim-label', wrap=True))

    def asset_card(self, item):
        quote = item.get('quote', {})
        content = box(spacing=10, margin_top=14, margin_bottom=14, margin_start=14, margin_end=14)
        header = box(False, 10)
        header.append(icon(item['kind'], 22))
        header.append(label(item['name'], 'muted', ellipsize=Pango.EllipsizeMode.END, hexpand=True))
        content.append(header)
        content.append(label(money(quote.get('value'), quote.get('unit', ''), True), 'metric-value'))
        if item['kind'] == 'weather':
            text = quote.get('condition', 'Aguardando previsão')
            if quote.get('rain') is not None:
                text += f" · Chuva {quote['rain']}%"
            content.append(label(text, 'module-weather', wrap=True))
        else:
            change = quote.get('change')
            text = 'Variação indisponível' if change is None else f'{"▲" if change >= 0 else "▼"} {abs(change):.2f}%'.replace('.', ',')
            content.append(label(text, 'muted' if change is None else ('positive' if change >= 0 else 'negative')))
        content.append(Sparkline(item.get('history', []), self.store.settings()['colors'][item['kind']], 42))
        content.append(label(quote.get('error') or f"{quote.get('source', 'Consultando fonte…')} · {stamp(quote.get('stamp'))}",
                             'warning' if quote.get('error') else 'dim-label', wrap=True))
        control = Gtk.Button(child=content, hexpand=True)
        control.add_css_class('metric-card')
        control.connect('clicked', lambda _button: self.asset_details(item['id']))
        return control

    def render_preview(self):
        if not self._preview:
            return
        while self._preview.get_first_child():
            self._preview.remove(self._preview.get_first_child())
        settings = self.store.settings()
        state = self.engine.state()
        selected = [row for row in state['assets'] if row.get('enabled', True) and row.get('pinned')]
        for item in selected[:settings['bar_limit']]:
            part = box(False, 4)
            quote = item['quote']
            if settings['display'] != 'text':
                part.append(icon(item['kind'], settings['icon_size'], scaled=False))
            prefix = (item['symbol'] + ' ') if settings['display'] != 'icons' else ''
            part.append(label(prefix + money(quote.get('value'), quote.get('unit', ''), True)))
            self._preview.append(part)
        if settings['agenda_bar']:
            self._preview.append(icon('agenda', settings['icon_size'], scaled=False))
            self._preview.append(label(str(len([row for row in state['reminders'] if row.get('enabled') and row.get('status') != 'done']))))
        if not selected and not settings['agenda_bar']:
            self._preview.append(icon('globe', settings['icon_size'], scaled=False))

    def asset_page(self, kind):
        self.heading(KINDS[kind], 'Escolha os indicadores, ajuste a ordem e fixe seus favoritos na barra.',
                     lambda: self.asset_form(kind), 'Adicionar cidade' if kind == 'weather' else 'Adicionar ativo')
        if kind == 'stock':
            self.content.append(label('B3: PETR4, VALE3, MGLU3 e ITUB4 sem chave. Outros ativos usam chave gratuita brapi. '
                                      'Alpha Vantage permite acompanhar outros mercados pelo fechamento, com chave gratuita.', 'muted', wrap=True))
        if kind == 'weather':
            self.content.append(label('Cidade inicial: São Paulo. Você pode removê-la e pesquisar qualquer cidade. '
                                      'Dados por Open-Meteo.com · CC BY 4.0 · uso não comercial.', 'muted', wrap=True))
        rows = [item for item in self.engine.state()['assets'] if item['kind'] == kind]
        if not rows:
            self.empty('Escolha o que acompanhar', 'Adicione seu primeiro indicador usando o botão acima.')
        for item in rows:
            area = self.surface()
            head = box(False, 12)
            head.append(icon(kind, 28))
            text = box(spacing=4, hexpand=True)
            text.append(label(f"{item['name']} · {item['symbol']}", 'heading', wrap=True))
            quote = item['quote']
            text.append(label(money(quote.get('value'), quote.get('unit', '')) +
                              f"  ·  {quote.get('source', 'Aguardando fonte')}  ·  {stamp(quote.get('stamp'))}", 'muted', wrap=True))
            head.append(text)
            head.append(button(callback=lambda row=item: self.asset_form(kind, row), icon_name='document-edit-symbolic'))
            head.append(button(callback=lambda row=item: self.remove('asset', row['id'], row['name']), icon_name='user-trash-symbolic'))
            area.append(head)
            controls = box(False, 10)
            controls.append(label('Monitorar'))
            enabled = Gtk.Switch(active=item.get('enabled', True), valign=Gtk.Align.CENTER)
            enabled.connect('notify::active', lambda widget, _param, row=item:
                self._asset_flag(row, 'enabled', widget.get_active()))
            controls.append(enabled)
            controls.append(label('Mostrar na barra', margin_start=12))
            pin = Gtk.Switch(active=item.get('pinned', False), valign=Gtk.Align.CENTER)
            pin.connect('notify::active', lambda widget, _param, row=item:
                self._asset_flag(row, 'pinned', widget.get_active()))
            controls.append(pin)
            controls.append(Gtk.Box(hexpand=True))
            controls.append(button(callback=lambda row=item: self.engine.move_asset(row['id'], -1), icon_name='go-up-symbolic'))
            controls.append(button(callback=lambda row=item: self.engine.move_asset(row['id'], 1), icon_name='go-down-symbolic'))
            controls.append(button('Detalhes', lambda row=item: self.asset_details(row['id'])))
            area.append(controls)
            if quote.get('error'):
                area.append(label(quote['error'], 'warning', wrap=True))

    def _asset_flag(self, item, key, value):
        current = self.store.get('asset', item['id'])
        if current:
            self.store.put('asset', item['id'], {**current, key: value})
            self.engine.changed()
            self.engine.tick()

    def asset_form(self, kind, existing=None):
        item = existing or {}
        form = Form(self, ('Editar ' if existing else 'Adicionar ') + ('cidade' if kind == 'weather' else 'indicador'))
        if kind == 'weather':
            form.help('Escolha a cidade para acompanhar o tempo. Você pode adicionar várias cidades à barra.')
            def selected(city):
                name.set_text(city['name'])
                latitude.set_text(str(city['latitude']))
                longitude.set_text(str(city['longitude']))
                symbol.set_text(weather_symbol(city['name']))
                form.error.set_text('')
            form.city_search = CitySearch(self.background, selected, existing)
            form.body.append(form.city_search)
            form.connect('close-request', lambda _window: form.city_search.close() or False)
            form.connect('unrealize', lambda _window: form.city_search.close())
        name = form.entry('Nome na janela', item.get('name', '')) if kind == 'weather' else form.entry('Nome', item.get('name', ''))
        initial_symbol = item.get('symbol', {'fx': 'USD-BRL', 'crypto': 'BTC-USD', 'stock': 'PETR4', 'weather': 'CLI'}[kind])
        if kind == 'weather' and (len(initial_symbol) > 3 or not initial_symbol.isalpha()):
            initial_symbol = weather_symbol(item.get('name', ''))
        symbol = form.entry('Rótulo na barra' if kind == 'weather' else 'Código / par', initial_symbol)
        if kind == 'weather':
            symbol.get_delegate().set_max_length(3)
        form.help('Sigla de até 3 letras, gerada ao selecionar a cidade (ex.: VDC). Você pode personalizá-la.'
                  if kind == 'weather' else 'O código/par identifica o ativo consultado e aparece na barra. O nome é personalizável.')
        provider = currency = None
        if kind == 'stock':
            provider = form.choice('Fonte', ['brapi · B3', 'Alpha Vantage · outros mercados'], 1 if item.get('provider') == 'alpha' else 0)
            currency = form.entry('Moeda do ativo no Alpha Vantage', item.get('currency', 'USD'))
            form.help('As chaves gratuitas são cadastradas em Integrações. A cobertura e o atraso dependem da fonte.')
        latitude = longitude = None
        if kind == 'weather':
            advanced = Adw.ExpanderRow(title='Coordenadas manuais', subtitle='Opcional · para locais fora da busca')
            latitude = Adw.EntryRow(title='Latitude', text=str(item.get('latitude', '')))
            longitude = Adw.EntryRow(title='Longitude', text=str(item.get('longitude', '')))
            advanced.add_row(latitude)
            advanced.add_row(longitude)
            advanced_group = Adw.PreferencesGroup()
            advanced_group.add(advanced)
            form.body.append(advanced_group)
        pinned = form.check('Mostrar na barra', item.get('pinned', True))

        def save():
            row = {**item, 'kind': kind, 'name': name.get_text(), 'symbol': symbol.get_text(),
                   'pinned': pinned.get_active(), 'position': item.get('position', len(self.store.records('asset')))}
            if provider:
                row.update(provider=['brapi', 'alpha'][provider.get_selected()], currency=currency.get_text().strip().upper())
            if latitude:
                if not latitude.get_text().strip() or not longitude.get_text().strip():
                    raise ValueError('Selecione uma cidade nos resultados ou informe as coordenadas manuais.')
                row.update(latitude=latitude.get_text(), longitude=longitude.get_text())
            self.engine.save_asset(row)
        form.on_save = save
        form.present()
        return form

    def asset_details(self, identity):
        active = getattr(self, '_detail_form', None)
        if active:
            if getattr(active, '_asset_id', None) == identity:
                active.present()
                return active
            active.close()
        item = next((row for row in self.engine.state(history=True)['assets'] if row['id'] == identity), None)
        if not item:
            return
        form = Form(self, item['name'], 'Fechar')
        form._asset_id = identity
        self._detail_form = form
        def closed(_window):
            if self._detail_form is form:
                self._detail_form = None
            return False
        form.connect('close-request', closed)
        form.set_default_size(820, 760)
        quote = item['quote']
        headline = box(False, 14)
        headline.append(icon(item['kind'], 36))
        headline.append(label(money(quote.get('value'), quote.get('unit', '')), 'title-1'))
        form.body.append(headline)
        form.help(f"{quote.get('source', 'Aguardando fonte')} · Dado de {stamp(quote.get('stamp'))}")
        if quote.get('error'):
            form.error.set_text(quote['error'])
        if item['kind'] == 'weather' and quote.get('forecast'):
            form.help(f"{quote.get('condition')} · Sensação {quote['feels']} °C · Umidade {quote['humidity']}% · Vento {quote['wind']} km/h")
            daily = quote['forecast']
            for index, day in enumerate(daily['time']):
                form.body.append(label(f"{day}  ·  {daily['temperature_2m_min'][index]} a {daily['temperature_2m_max'][index]} °C  ·  Chuva {daily['precipitation_probability_max'][index]}%", wrap=True))
        else:
            unit = quote.get('unit') or item.get('currency') or (item['symbol'].split('-')[-1] if item['kind'] in ('fx', 'crypto') else 'BRL')
            form.chart_panel = ChartPanel(self, item, lambda value: money(value, unit))
            form.body.append(form.chart_panel)
            form.connect('close-request', lambda _window: form.chart_panel.close() or False)
            form.connect('unrealize', lambda _window: form.chart_panel.close())
            if quote.get('ask') is not None:
                form.help('Venda de referência: ' + money(quote['ask'], quote.get('unit', '')))
            form.body.append(button('Criar alerta para este indicador', lambda: (form.close(), self.alert_form(asset_id=identity))))
            if item['kind'] == 'fx' and quote.get('value') is not None:
                amount = form.entry('Converter valor da moeda de origem', '1')
                converted = label('', 'title-3')
                form.body.append(converted)
                def convert(*_args):
                    try:
                        converted.set_text(money(finite(amount.get_text().replace(',', '.')) * quote['value'], quote.get('unit', '')))
                    except ValueError:
                        converted.set_text('Informe um valor numérico.')
                amount.connect('changed', convert)
                convert()
        form.help(quote.get('note', ''))
        form.present()
        return form

    def agenda_page(self):
        self.heading('Sua agenda', 'Compromissos e lembretes, com aviso no Ubuntu e destinos opcionais.', self.reminder_form, 'Novo lembrete')
        tabs = box(False, 6)
        self._agenda_buttons = {}
        first = None
        for key, title in AGENDA_FILTERS:
            control = Gtk.ToggleButton(label=title, css_classes=['agenda-tab'])
            if first:
                control.set_group(first)
            else:
                first = control
            control.set_active(self._agenda_mode == key)
            control.connect('toggled', lambda widget, mode=key: self._select_agenda(mode) if widget.get_active() else None)
            tabs.append(control)
            self._agenda_buttons[key] = control
        tab_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                        vscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True)
        tab_scroll.set_child(tabs)
        self.content.append(tab_scroll)
        self._agenda_search = Gtk.SearchEntry(placeholder_text='Pesquisar título, descrição ou categoria…', hexpand=True)
        self._agenda_search.set_text(self._agenda_query)
        self._agenda_search.connect('search-changed', self._filter_agenda)
        self.content.append(self._agenda_search)
        self._agenda_summary = label('', 'muted', wrap=True)
        self.content.append(self._agenda_summary)
        self._calendar = Gtk.Calendar()
        self._calendar.select_day(GLib.DateTime.new_local(self._calendar_day.year, self._calendar_day.month, self._calendar_day.day, 12, 0, 0))
        self._calendar.connect('day-selected', self._calendar_selected)
        self._calendar.set_visible(self._agenda_mode == 'calendar')
        self._calendar.set_halign(Gtk.Align.START)
        self.content.append(self._calendar)
        self._agenda_list = box(spacing=12)
        self.content.append(self._agenda_list)
        self.render_agenda_list()

    def _calendar_selected(self, calendar):
        date = calendar.get_date()
        self._calendar_day = datetime(date.get_year(), date.get_month(), date.get_day_of_month()).date()
        self.render_agenda_list()

    def _select_agenda(self, mode):
        self._agenda_mode = mode
        self._calendar.set_visible(mode == 'calendar')
        self.render_agenda_list()

    def _filter_agenda(self, *_args):
        self._agenda_query = self._agenda_search.get_text()
        self.render_agenda_list()

    def render_agenda_list(self):
        while self._agenda_list.get_first_child():
            self._agenda_list.remove(self._agenda_list.get_first_child())
        now = time.time()
        items = self.store.records('reminder')
        self._agenda_clock_key = self._agenda_clock_signature(items, now)
        rows, counts = agenda_view(items, self._agenda_mode, self._agenda_query, self._calendar_day, now)
        self._agenda_visible_ids = [row['id'] for row in rows]
        for key, title in AGENDA_FILTERS:
            self._agenda_buttons[key].set_label(f'{title} ({counts[key]})')
        description = {'today': 'Compromissos ativos de hoje, no fuso de cada lembrete.',
                       'upcoming': 'Horários futuros de lembretes ativos.',
                       'overdue': 'Horário ultrapassado e ainda não concluído, mesmo que o aviso já tenha sido gerado.',
                       'done': 'Lembretes marcados como concluídos.', 'paused': 'Lembretes com os avisos pausados.',
                       'all': 'Todos os lembretes, incluindo atrasados, pausados e concluídos.',
                       'calendar': 'Data selecionada no fuso de cada lembrete.'}[self._agenda_mode]
        self._agenda_summary.set_text(description)
        if not rows:
            self.empty('Nenhum lembrete neste filtro', 'Escolha outra aba, ajuste a busca ou crie um novo lembrete.', self._agenda_list)
        for item in rows:
            area = self.surface(self._agenda_list)
            head = box(False, 12)
            head.append(icon('agenda', 26))
            text = box(spacing=5, hexpand=True)
            text.append(label(item['title'], 'heading', wrap=True))
            status = reminder_status(item, now)
            text.append(label(f"{stamp(item['due'], item['timezone'])} · {item.get('category', 'Pessoal')}", 'muted', wrap=True))
            if status == 'overdue':
                text.append(label(overdue_text(item, now), 'negative', wrap=True))
            if item.get('description'):
                text.append(label(item['description'], wrap=True))
            head.append(text)
            badge = label(STATUSES[status], 'status-badge status-' + status, valign=Gtk.Align.START)
            if status == 'overdue':
                badge.set_tooltip_text('O horário passou e o lembrete ainda não foi concluído.')
            head.append(badge)
            head.append(button(callback=lambda row=item: self.reminder_form(row), icon_name='document-edit-symbolic'))
            head.append(button(callback=lambda row=item: self.remove('reminder', row['id'], row['title']), icon_name='user-trash-symbolic'))
            area.append(head)
            controls = box(False, 8)
            if status != 'done':
                controls.append(button('Concluir', lambda row=item: self.finish_reminder(row)))
                controls.append(button('Adiar 10 min', lambda row=item: self.engine.snooze(row['id'])))
                controls.append(button('Pausar' if item.get('enabled', True) else 'Retomar', lambda row=item: self.toggle_record('reminder', row)))
            area.append(controls)

    def finish_reminder(self, item):
        self.store.put('reminder', item['id'], {**item, 'status': 'done', 'enabled': False})
        self.engine.changed()

    def toggle_record(self, kind, item):
        self.store.put(kind, item['id'], {**item, 'enabled': not item.get('enabled', True)})
        self.engine.changed()
        self.engine.tick()

    def reminder_form(self, existing=None):
        item = existing or {}
        form = Form(self, 'Editar lembrete' if existing else 'Novo lembrete')
        title = form.entry('Título', item.get('title', ''))
        description = form.entry('Descrição', item.get('description', ''))
        timezone = item.get('timezone') or system_zone()
        due = datetime.fromtimestamp(item.get('due', time.time() + 3600), ZoneInfo(timezone))
        date = form.entry('Data · AAAA-MM-DD', due.strftime('%Y-%m-%d'))
        calendar = Gtk.Calendar()
        calendar.select_day(GLib.DateTime.new_local(due.year, due.month, due.day, 12, 0, 0))
        popover = Gtk.Popover(child=calendar)
        picker = Gtk.MenuButton(label='Escolher data no calendário', popover=popover)
        def choose_date(widget):
            date.set_text(widget.get_date().format('%Y-%m-%d'))
            popover.popdown()
        calendar.connect('day-selected', choose_date)
        form.body.append(picker)
        clock = form.entry('Horário · HH:MM', due.strftime('%H:%M'))
        zone = form.entry('Fuso horário', timezone)
        recurrence_keys = ['none', 'daily', 'weekly', 'weekdays', 'monthly']
        recurrence = form.choice('Repetir', ['Não repetir', 'Diariamente', 'Semanalmente', 'Dias úteis', 'Mensalmente'],
                                 recurrence_keys.index(item.get('repeat', 'none')))
        lead = form.spin('Avisar antes · minutos', item.get('lead', 0), 0, 10080, 5)
        category = form.entry('Categoria', item.get('category', 'Pessoal'))
        channels = form.channels(item.get('channels', []))
        enabled = form.check('Lembrete ativo', item.get('enabled', True))
        form.help('Os avisos funcionam com a janela fechada, enquanto a sessão estiver ativa. '
                  'Ao retornar de uma suspensão, compromissos vencidos geram um aviso pendente.')

        def save():
            try:
                local = datetime.strptime(date.get_text().strip() + ' ' + clock.get_text().strip(), '%Y-%m-%d %H:%M')
                local = local.replace(tzinfo=ZoneInfo(zone.get_text().strip()))
            except (ValueError, KeyError):
                raise ValueError('Confira data, horário e fuso (ex.: America/Sao_Paulo).') from None
            self.store.save_reminder({**item, 'title': title.get_text(), 'description': description.get_text(),
                'due': local.timestamp(), 'timezone': zone.get_text().strip(),
                'repeat': recurrence_keys[recurrence.get_selected()], 'lead': lead.get_value_as_int(),
                'anchor_day': local.day, 'repeat_time': [local.hour, local.minute],
                'category': category.get_text(), 'channels': channels(),
                'enabled': enabled.get_active(), 'status': 'pending'})
        form.on_save = save
        form.present()
        return form

    def alerts_page(self):
        self.heading('Alertas', 'Receba avisos por preço, variação percentual ou chance de chuva.', self.alert_form, 'Criar alerta')
        rows = self.store.records('alert')
        if not rows:
            self.empty('Acompanhe o que importa', 'Crie uma condição para receber um aviso quando um indicador atingir seu limite.')
        assets = {item['id']: item for item in self.store.records('asset')}
        for item in rows:
            area = self.surface()
            asset = assets.get(item['asset_id'], {'name': 'Indicador removido'})
            area.append(label(item['title'], 'heading'))
            metric = {'value': 'Valor', 'change': 'Variação %', 'rain': 'Probabilidade de chuva %'}.get(item['metric'], item['metric'])
            area.append(label(f"{asset['name']} · {metric} {'≥' if item['operator'] == 'above' else '≤'} {item['threshold']} · intervalo mínimo {item['cooldown']} min", 'muted', wrap=True))
            controls = box(False, 8)
            controls.append(button('Pausar' if item.get('enabled') else 'Ativar', lambda row=item: self.toggle_record('alert', row)))
            controls.append(button('Editar', lambda row=item: self.alert_form(row)))
            controls.append(button('Remover', lambda row=item: self.remove('alert', row['id'], row['title'])))
            area.append(controls)

    def alert_form(self, existing=None, asset_id=None):
        assets = self.engine.state()['assets']
        if not assets:
            self.toast('Adicione um indicador antes de criar o alerta.')
            return
        item = existing or {}
        form = Form(self, 'Editar alerta' if existing else 'Criar alerta')
        title = form.entry('Título do alerta', item.get('title', ''))
        selected = next((i for i, row in enumerate(assets) if row['id'] == (item.get('asset_id') or asset_id)), 0)
        asset = form.choice('Indicador', [row['name'] + ' · ' + row['symbol'] for row in assets], selected)
        metrics = ['value', 'change', 'rain']
        metric = form.choice('Métrica', ['Valor / temperatura', 'Variação percentual', 'Probabilidade de chuva (%)'], metrics.index(item.get('metric', 'value')))
        operator = form.choice('Condição', ['Maior ou igual', 'Menor ou igual'], 1 if item.get('operator') == 'below' else 0)
        threshold = form.entry('Limite', item.get('threshold', ''))
        cooldown = form.spin('Intervalo mínimo entre avisos · minutos', item.get('cooldown', 60), 10, 10080, 10)
        channels = form.channels(item.get('channels', []))
        form.help('O alerta acompanha as consultas da fonte, inclusive seus atrasos. Uma métrica indisponível não gera aviso.')

        def save():
            row = {**item, 'title': title.get_text(), 'asset_id': assets[asset.get_selected()]['id'],
                   'metric': metrics[metric.get_selected()], 'operator': ['above', 'below'][operator.get_selected()],
                   'threshold': threshold.get_text().replace(',', '.'), 'cooldown': cooldown.get_value_as_int(),
                   'channels': channels(), 'enabled': True, 'last_sent': 0}
            self.store.put('alert', item.get('id') or str(uuid.uuid4()), validate_alert(row, self.store))
        form.on_save = save
        form.present()
        return form

    def integrations_page(self):
        self.heading('Integrações', 'Fontes gratuitas, destinos opcionais e histórico de tentativas.', self.integration_form, 'Adicionar destino')
        sources = self.surface()
        sources.append(label('Chaves gratuitas de mercado', 'title-3'))
        sources.append(label('Moedas, cripto e clima funcionam sem chave. Cadastre chaves gratuitas para ampliar a bolsa. '
                             'Nenhum plano pago é contratado pelo aplicativo.', 'muted', wrap=True))
        for provider, title in [('brapi', 'brapi · bolsa brasileira'), ('alpha', 'Alpha Vantage · fechamento de outros mercados')]:
            row = box(False, 12)
            saved = bool(self.store.get('secret', provider, {}).get('value'))
            row.append(label(title + (' · chave cadastrada' if saved else ' · sem chave'), hexpand=True, wrap=True))
            row.append(button('Configurar', lambda name=provider: self.key_form(name)))
            sources.append(row)
        sources.append(label('Credenciais ficam no perfil local, em banco privado (permissão 600), e não entram no backup exportado.', 'dim-label', wrap=True))
        for item in self.engine.state()['integrations']:
            area = self.surface()
            line = box(False, 12)
            line.append(icon('link', 24))
            line.append(label(item['name'] + (' · ativo' if item.get('enabled') else ' · desativado'), 'heading', hexpand=True))
            line.append(button('Editar', lambda row=item: self.integration_form(row)))
            line.append(button('Remover', lambda row=item: self.remove('integration', row['id'], row['name'])))
            area.append(line)
            area.append(label('WhatsApp oficial · template aprovado' if item.get('type') == 'whatsapp' else item.get('url', ''), 'muted', wrap=True))
            test = button('Enviar notificação de teste', lambda row=item: self.test_integration(row))
            test.set_sensitive(item.get('enabled', False))
            area.append(test)
        self.content.append(label('Histórico de notificações', 'title-3'))
        self._deliveries_box = box(spacing=8)
        self.content.append(self._deliveries_box)
        self.render_deliveries()

    def key_form(self, provider):
        form = Form(self, 'Chave ' + ('brapi' if provider == 'brapi' else 'Alpha Vantage'))
        value = form.entry('Chave de API', password=True)
        form.help('Informe a chave para substituir a atual. Salvar vazio remove a chave.')
        link = Gtk.LinkButton.new_with_label('https://brapi.dev/dashboard' if provider == 'brapi' else 'https://www.alphavantage.co/support/#api-key', 'Obter uma chave gratuita')
        form.body.append(link)
        def save():
            self.engine.set_key(provider, value.get_text())
            GLib.idle_add(lambda: self.navigate('integrations') or False)
        form.on_save = save
        form.present()

    def integration_form(self, existing=None):
        item = existing or {}
        form = Form(self, 'Editar destino' if existing else 'Adicionar destino')
        name = form.entry('Nome', item.get('name', ''))
        kind = form.choice('Tipo', ['Webhook HTTP · API personalizada', 'WhatsApp Business Platform'], 1 if item.get('type') == 'whatsapp' else 0)
        url = form.entry('URL do webhook', item.get('url', ''))
        recipient = form.entry('Destinatário / telefone com código do país', item.get('recipient', ''))
        secret = form.entry('Token Bearer · vazio mantém o atual', password=True)
        auth_header = form.entry('Cabeçalho de autenticação', item.get('auth_header', 'Authorization'))
        auth_prefix = form.entry('Prefixo da credencial · pode ficar vazio', item.get('auth_prefix', 'Bearer'))
        body_template = form.entry('Corpo JSON personalizado · opcional', item.get('body_template', ''))
        phone_id = form.entry('WhatsApp · ID do número remetente', item.get('phone_id', ''))
        template = form.entry('WhatsApp · nome do template', item.get('template', ''))
        language = form.entry('WhatsApp · idioma do template', item.get('language', 'pt_BR'))
        version = form.entry('WhatsApp · versão Graph API', item.get('version', 'v23.0'))
        enabled = form.check('Ativar envios para este destino', item.get('enabled', False))
        form.help('Webhook: recebe JSON com event_id, destination, title, body e tipo do evento. '
                  'Use HTTPS para proteger mensagens e credenciais. Redirecionamentos não são seguidos.')
        form.help('No corpo personalizado, use {{title}}, {{body}}, {{destination}}, {{event_id}} e {{due}}. '
                  'Exemplo: {"number":"{{destination}}","text":"{{title}} — {{body}}"}. '
                  'Para X-API-Key, mude o cabeçalho e deixe o prefixo vazio.')
        form.help('WhatsApp oficial: exige conta configurada e template aprovado com dois parâmetros de texto no corpo '
                  '(título e descrição). A Meta pode cobrar pelos envios. Nenhum envio ocorre sem ativar o destino e selecioná-lo num lembrete/alerta.')

        def visibility(*_args):
            whatsapp = kind.get_selected() == 1
            url.set_visible(not whatsapp)
            for row in (auth_header, auth_prefix, body_template):
                row.set_visible(not whatsapp)
            for row in (phone_id, template, language, version):
                row.set_visible(whatsapp)
        kind.connect('notify::selected', visibility)
        visibility()

        def save():
            identity = item.get('id') or str(uuid.uuid4())
            old = self.store.get('integration', identity, {})
            row = {**old, 'name': name.get_text(), 'type': ['webhook', 'whatsapp'][kind.get_selected()],
                   'url': url.get_text().strip(), 'recipient': recipient.get_text().strip(),
                   'secret': secret.get_text().strip() or old.get('secret', ''),
                   'auth_header': auth_header.get_text().strip(), 'auth_prefix': auth_prefix.get_text().strip(),
                   'body_template': body_template.get_text().strip(),
                   'phone_id': phone_id.get_text().strip(), 'template': template.get_text().strip(),
                   'language': language.get_text().strip(), 'version': version.get_text().strip(),
                   'enabled': enabled.get_active()}
            self.store.put('integration', identity, validate_integration(row))
            GLib.idle_add(lambda: self.navigate('integrations') or False)
        form.on_save = save
        form.present()
        return form

    def test_integration(self, item):
        self.store.queue('test:' + str(uuid.uuid4()),
            {'type': 'test', 'title': 'Teste do Monitor Global', 'body': 'Seu destino de notificações está sendo testado.', 'created_at': time.time()}, [item['id']])
        self.engine.tick()
        self.toast('Teste colocado na fila. Acompanhe o resultado no histórico.')

    def render_deliveries(self):
        while self._deliveries_box.get_first_child():
            self._deliveries_box.remove(self._deliveries_box.get_first_child())
        events = self.store.deliveries()
        if not events:
            self._deliveries_box.append(label('Os avisos e suas tentativas aparecerão aqui.', 'muted'))
        for event in events[:30]:
            state = {'pending': 'Aguardando tentativa', 'sending': 'Enviando', 'sent': 'Exibido' if event['channel'] == 'desktop' else 'Aceito pela API',
                     'failed': 'Falhou', 'cancelled': 'Cancelado'}[event['state']]
            text = f"{stamp(event['created'])} · {event['payload']['title']} · {state} · {event['attempts']} tentativa(s)"
            self._deliveries_box.append(label(text, 'warning' if event['state'] == 'failed' else 'muted', wrap=True))
            if event['error']:
                self._deliveries_box.append(label(event['error'], 'dim-label', wrap=True))
            if event['state'] == 'failed':
                self._deliveries_box.append(button('Tentar novamente', lambda identity=event['id']: self.retry_delivery(identity)))

    def retry_delivery(self, identity):
        with self.store.db() as db:
            db.execute("UPDATE deliveries SET state='pending',attempts=0,next_try=?,error='' WHERE id=? AND state='failed'",
                       (time.time(), identity))
        self.engine.tick()
        self.render_deliveries()

    def appearance_page(self):
        settings = self.store.settings()
        self.heading('Do seu jeito', 'Ajustes imediatos, com uma prévia da sua barra.')
        self._preview = box(False, 14)
        self._preview.add_css_class('bar-preview')
        self.content.append(self._preview)
        self.render_preview()
        group = Adw.PreferencesGroup(title='Aparência')
        self.content.append(group)

        def choice(title, choices, keys, key):
            row = Adw.ComboRow(title=title, model=Gtk.StringList.new(choices))
            row.set_selected(keys.index(settings[key]))
            row.connect('notify::selected', lambda widget, _param: self.set_settings({key: keys[widget.get_selected()]}))
            group.add(row)
        choice('Tema', ['Escuro', 'Claro', 'Seguir sistema'], ['dark', 'light', 'system'], 'theme')
        choice('Posição na barra do Ubuntu', ['Esquerda', 'Centro · antes do relógio', 'Direita'],
               ['left', 'center', 'right'], 'panel_position')
        choice('Identificação na barra', ['Ícones e valores', 'Texto e valores', 'Ícones, texto e valores'], ['icons', 'text', 'both'], 'display')
        accent = Adw.ActionRow(title='Cor de destaque')
        picker = self.color_picker(settings['accent'], lambda value: self.set_settings({'accent': value}))
        accent.add_suffix(picker)
        group.add(accent)
        for title, key, low, high in [('Tamanho dos ícones', 'icon_size', 10, 32),
                                     ('Ícones da janela', 'window_icon_size', 16, 32),
                                     ('Fonte da janela', 'window_font_size', 11, 18),
                                     ('Fonte da barra', 'font_size', 9, 20), ('Espaçamento', 'spacing', 2, 20),
                                     ('Quantidade máxima de indicadores', 'bar_limit', 1, 12)]:
            row = Adw.ActionRow(title=title)
            spin = Gtk.SpinButton.new_with_range(low, high, 1)
            spin.set_value(settings[key])
            spin.set_valign(Gtk.Align.CENTER)
            spin.connect('value-changed', lambda widget, name=key: self.set_settings({name: widget.get_value_as_int()}))
            row.add_suffix(spin)
            group.add(row)
        for title, key in [('Mostrar variação na barra', 'show_change'), ('Mostrar agenda na barra', 'agenda_bar'),
                           ('Animações', 'animations'), ('Som das notificações', 'sound'),
                           ('Notificações do Ubuntu', 'desktop_notifications')]:
            row = Adw.SwitchRow(title=title, active=settings[key])
            row.connect('notify::active', lambda widget, _param, name=key: self.set_settings({name: widget.get_active()}))
            group.add(row)
        modules = Adw.PreferencesGroup(title='Cores por módulo')
        self.content.append(modules)
        for kind, title in KINDS.items():
            row = Adw.ActionRow(title=title)
            row.add_prefix(icon(kind, 22))
            row.add_suffix(self.color_picker(settings['colors'][kind], lambda value, name=kind:
                self.set_settings({'colors': {**self.store.settings()['colors'], name: value}})))
            modules.add(row)
        profiles = Adw.PreferencesGroup(title='Perfis de exibição')
        self.content.append(profiles)
        row = box(False, 8)
        for profile in ('Trabalho', 'Mercado', 'Viagem'):
            row.append(button(profile, lambda name=profile: self.profile(name)))
        profiles.add(row)
        polling = Adw.ActionRow(title='Intervalo preferido de consulta · segundos',
            subtitle='Mínimos das fontes: clima 15 min, B3 30 min, Alpha Vantage 6 h.')
        spin = Gtk.SpinButton.new_with_range(60, 3600, 60)
        spin.set_value(settings['refresh_seconds'])
        spin.set_valign(Gtk.Align.CENTER)
        spin.connect('value-changed', lambda widget: self.set_settings({'refresh_seconds': widget.get_value_as_int()}))
        polling.add_suffix(spin)
        profiles.add(polling)
        actions = box(False, 10)
        actions.append(button('Restaurar aparência', self.restore_appearance))
        actions.append(button('Exportar backup', self.export_backup))
        actions.append(button('Importar backup', self.import_backup))
        self.content.append(actions)

    def color_picker(self, value, callback):
        rgba = Gdk.RGBA()
        rgba.parse(value)
        dialog = Gtk.ColorDialog(title='Escolher cor', with_alpha=False)
        picker = Gtk.ColorDialogButton.new(dialog)
        picker.set_rgba(rgba)
        picker.set_valign(Gtk.Align.CENTER)
        picker.connect('notify::rgba', lambda widget, _param: callback('#{:02x}{:02x}{:02x}'.format(
            round(widget.get_rgba().red * 255), round(widget.get_rgba().green * 255), round(widget.get_rgba().blue * 255))))
        return picker

    def set_settings(self, changes):
        self.store.save_settings(changes)
        self.apply_appearance()
        self.render_preview()
        self.engine.changed()

    def profile(self, name):
        kinds = {'Trabalho': ('fx',), 'Mercado': ('fx', 'crypto', 'stock'), 'Viagem': ('fx', 'weather')}[name]
        for row in self.store.records('asset'):
            self.store.put('asset', row['id'], {**row, 'pinned': row['kind'] in kinds})
        self.set_settings({'profile': name, 'agenda_bar': name != 'Mercado'})
        self.navigate('appearance')
        self.toast('Perfil ' + name + ' aplicado. Os indicadores continuam disponíveis na janela.')

    def restore_appearance(self):
        keys = ('theme', 'accent', 'colors', 'icon_size', 'font_size', 'window_font_size', 'window_icon_size',
                'spacing', 'bar_limit', 'display', 'panel_position', 'show_change', 'animations')
        self.set_settings({key: DEFAULTS[key] for key in keys})
        self.navigate('appearance')
        self.toast('Aparência restaurada. Seus indicadores e lembretes foram preservados.')

    def remove(self, kind, identity, title):
        dialog = Adw.MessageDialog.new(self, 'Remover este item?', title)
        dialog.add_response('cancel', 'Cancelar')
        dialog.add_response('remove', 'Remover')
        dialog.set_response_appearance('remove', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response('cancel')
        def response(_dialog, result):
            if result == 'remove':
                self.store.delete(kind, identity)
                self.engine.changed()
                if self.page == 'integrations':
                    self.navigate('integrations')
        dialog.connect('response', response)
        dialog.present()

    def background(self, function, success, failure=None):
        def run():
            try:
                result = function()
            except Exception as error:
                GLib.idle_add(lambda message=str(error): (failure or self.toast)(message) or False)
            else:
                GLib.idle_add(lambda: success(result) or False)
        self.engine.pool.submit(run)

    def export_backup(self):
        dialog = Gtk.FileDialog(title='Exportar backup', initial_name='monitor-global-backup.json')
        def chosen(source, result):
            try:
                target = source.save_finish(result)
                import json
                target.replace_contents(json.dumps(self.store.export_data(), ensure_ascii=False, indent=2).encode(),
                    None, False, Gio.FileCreateFlags.PRIVATE, None)
                self.toast('Backup exportado sem chaves ou tokens.')
            except GLib.Error:
                pass
        dialog.save(self, None, chosen)

    def import_backup(self):
        dialog = Gtk.FileDialog(title='Importar backup')
        def chosen(source, result):
            try:
                target = source.open_finish(result)
                _ok, contents, _etag = target.load_contents(None)
                if len(contents) > 2_000_000:
                    raise ValueError('Backup muito grande.')
                import json
                self.store.import_data(json.loads(contents))
                self.engine.changed()
                self.engine.tick()
                self.navigate('appearance')
                self.toast('Backup importado. Destinos externos importados ficam desativados.')
            except GLib.Error:
                pass
            except (ValueError, KeyError, TypeError) as error:
                self.toast(str(error))
        dialog.open(self, None, chosen)
