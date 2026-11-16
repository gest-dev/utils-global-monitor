"""Interactive history chart and asynchronous period picker for asset details."""
from datetime import datetime, timezone
import math

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Graphene', '1.0')
from gi.repository import Gdk, Graphene, Gtk, Pango

from .history import PERIODS, aggregate, load_history


def text(value='', css=None):
    widget = Gtk.Label(label=value, xalign=0, wrap=True)
    if css:
        widget.add_css_class(css)
    return widget


class HistoryChart(Gtk.Widget):
    def __init__(self, color, formatter, on_hover):
        super().__init__(hexpand=True, height_request=250)
        self.color, self.formatter, self.on_hover = color, formatter, on_hover
        self.points, self.selected = [], None
        self.set_cursor_from_name('crosshair')
        motion = Gtk.EventControllerMotion()
        motion.connect('motion', self._motion)
        motion.connect('leave', lambda _controller: self._leave())
        self.add_controller(motion)
        self.set_accessible_role(Gtk.AccessibleRole.IMG)

    def set_points(self, points):
        self.points, self.selected = points, None
        self.queue_draw()

    def _motion(self, _controller, x, _y):
        if not self.points:
            return
        width = max(1, self.get_width() - 106)
        first, last = self.points[0][0], self.points[-1][0]
        target = first + max(0, min(1, (x - 90) / width)) * (last - first)
        self.selected = min(range(len(self.points)), key=lambda index: abs(self.points[index][0] - target))
        stamp, value = self.points[self.selected]
        self.on_hover(datetime.fromtimestamp(stamp, timezone.utc).strftime('%d/%m/%Y') + ' · ' + self.formatter(value))
        self.queue_draw()

    def _leave(self):
        self.selected = None
        self.on_hover('Passe o mouse sobre o gráfico para ver data e valor.')
        self.queue_draw()

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        left, top, right, bottom = 90, 18, max(91, width - 16), max(19, height - 30)
        grid = Gdk.RGBA(red=.5, green=.5, blue=.5, alpha=.2)
        muted = Gdk.RGBA(red=.65, green=.69, blue=.66, alpha=1)
        color = Gdk.RGBA()
        color.parse(self.color)

        def caption(value, x, y):
            layout = self.create_pango_layout(value)
            layout.set_font_description(Pango.FontDescription.from_string('Sans 9'))
            snapshot.save()
            snapshot.translate(Graphene.Point().init(x, y))
            snapshot.append_layout(layout, muted)
            snapshot.restore()

        if not self.points:
            caption('Sem pontos disponíveis neste período.', left, (top + bottom) / 2)
            return
        values = [row[1] for row in self.points]
        low, high = min(values), max(values)
        span = high - low or max(abs(high) * .01, 1)
        first, last = self.points[0][0], self.points[-1][0]
        duration = last - first or 1
        for part in (0, .25, .5, .75, 1):
            y = top + (bottom - top) * part
            snapshot.append_color(grid, Graphene.Rect().init(left, y, right - left, 1))
            caption(self.formatter(high - span * part), 0, y - 7)
        for index, when in enumerate((first, (first + last) / 2, last)):
            caption(datetime.fromtimestamp(when, timezone.utc).strftime('%d/%m/%y'), left + (right - left) * index / 2 - (48 if index == 2 else 0), bottom + 8)
        previous = None
        for index, (when, value) in enumerate(self.points):
            x = left + (right - left) * (when - first) / duration
            y = bottom - (bottom - top) * (value - low) / span
            if previous:
                px, py = previous
                snapshot.save()
                snapshot.translate(Graphene.Point().init(px, py))
                snapshot.rotate(math.degrees(math.atan2(y - py, x - px)))
                snapshot.append_color(color, Graphene.Rect().init(0, -1, math.hypot(x - px, y - py), 2))
                snapshot.restore()
            if index == self.selected or len(self.points) == 1:
                snapshot.append_color(grid, Graphene.Rect().init(x, top, 1, bottom - top))
                snapshot.append_color(color, Graphene.Rect().init(x - 3, y - 3, 6, 6))
            previous = x, y


class ChartPanel(Gtk.Box):
    def __init__(self, parent, asset, formatter):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.parent, self.asset = parent, asset
        self.formatter = formatter
        self.period, self.data, self._generation, self._closed = '1mo', None, 0, False
        controls = Gtk.Box(spacing=6)
        self.period_buttons = {}
        first = None
        for key, title, _days in PERIODS:
            control = Gtk.ToggleButton(label=title)
            if first:
                control.set_group(first)
            else:
                first = control
            control.set_active(key == self.period)
            control.connect('toggled', lambda widget, period=key: self.change_period(period) if widget.get_active() else None)
            controls.append(control)
            self.period_buttons[key] = control
        self.resolution = Gtk.DropDown.new_from_strings(['Diário', 'Semanal', 'Mensal'])
        self.resolution.set_tooltip_text('Exibir o último valor disponível de cada dia, semana ou mês')
        self.resolution.connect('notify::selected', lambda *_args: self.render())
        controls.append(self.resolution)
        self.append(controls)
        feedback = Gtk.Box(spacing=8)
        self.spinner = Gtk.Spinner()
        self.status = text('', 'dim-label')
        self.status.set_wrap(False)
        feedback.append(self.spinner)
        feedback.append(self.status)
        refresh = Gtk.Button(icon_name='view-refresh-symbolic', tooltip_text='Consultar o histórico novamente')
        refresh.connect('clicked', lambda _button: self.reload(True))
        feedback.append(refresh)
        self.append(feedback)
        self.metrics = text('', 'heading')
        self.append(self.metrics)
        self.hover = text('Passe o mouse sobre o gráfico para ver data e valor.', 'dim-label')
        self.chart = HistoryChart(parent.store.settings()['colors'][asset['kind']], formatter, self.hover.set_text)
        self.append(self.chart)
        self.append(self.hover)
        self.coverage = text('', 'dim-label')
        self.append(self.coverage)
        self.note = text('', 'dim-label')
        self.append(self.note)
        self.reload()

    def change_period(self, period):
        self.period = period
        self.reload()

    def reload(self, force=False):
        if self._closed:
            return
        self._generation += 1
        token, period = self._generation, self.period
        self.data = None
        self.chart.set_points([])
        self.metrics.set_text('')
        self.coverage.set_text('')
        self.note.set_text('')
        self.spinner.start()
        self.status.set_text('Consultando histórico…')
        def work():
            if force:
                self.parent.store.delete('chart', self.asset['id'] + ':' + period)
            return load_history(self.asset, self.parent.store, period)
        self.parent.background(work, lambda result: self._loaded(token, result), lambda message: self._failed(token, message))

    def _loaded(self, token, result):
        if self._closed or token != self._generation:
            return
        current = self.parent.store.get('asset', self.asset['id'])
        if not current or any(current.get(key) != self.asset.get(key) for key in ('kind', 'symbol', 'provider', 'currency')):
            self._failed(token, 'O indicador foi alterado. Feche e abra os detalhes novamente.')
            return
        self.data = result
        self.spinner.stop()
        self.render()

    def _failed(self, token, message):
        if not self._closed and token == self._generation:
            self.spinner.stop()
            self.status.set_text(str(message))

    def render(self):
        if not self.data:
            return
        resolution = ['daily', 'weekly', 'monthly'][self.resolution.get_selected()]
        points = aggregate(self.data['points'], resolution)
        self.chart.set_points(points)
        self.status.set_text(self.data['source'] + ' · ' + str(len(points)) + ' ponto(s)')
        self.note.set_text(' '.join(filter(None, [self.data.get('note'), self.data.get('warning')])))
        if points:
            first, last = points[0], points[-1]
            change = (last[1] / first[1] - 1) * 100 if first[1] else None
            values = [row[1] for row in self.data['points']]
            self.metrics.set_text('Mín. ' + self.formatter(min(values)) + ' · Máx. ' + self.formatter(max(values)) +
                                  (' · ' + f'{change:+.2f}'.replace('.', ',') + '% entre os pontos exibidos' if change is not None else ''))
            self.coverage.set_text('Dados disponíveis: ' + datetime.fromtimestamp(first[0], timezone.utc).strftime('%d/%m/%Y') +
                                   ' a ' + datetime.fromtimestamp(last[0], timezone.utc).strftime('%d/%m/%Y') +
                                   (' · Fonte com fechamentos mensais.' if self.data.get('interval') == 'monthly' else ''))
        else:
            self.coverage.set_text('Ainda não há histórico para o período selecionado.')

    def close(self):
        self._closed = True
        self._generation += 1
