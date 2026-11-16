import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

const APP = 'org.gezer.MonitorGlobal';
const PATH = '/org/gezer/MonitorGlobal';

export function formatValue(value, unit = '') {
    if (!Number.isFinite(value))
        return '—';
    let magnitude = '';
    if (Math.abs(value) >= 1000000) {
        value /= 1000000;
        magnitude = 'M';
    } else if (Math.abs(value) >= 10000) {
        value /= 1000;
        magnitude = 'k';
    }
    const digits = unit === '°C' ? 1 : (Math.abs(value) > 0 && Math.abs(value) < 1 ? 4 : 2);
    const number = value.toLocaleString('pt-BR', {minimumFractionDigits: digits, maximumFractionDigits: digits});
    const prefix = {BRL: 'R$', USD: '$', EUR: '€', GBP: '£'}[unit] ?? '';
    return `${prefix}${number}${magnitude}${prefix ? '' : unit}`;
}

export default class MonitorGlobal extends Extension {
    enable() {
        this._alive = true;
        this._state = null;
        this._lastStart = 0;
        this._requesting = false;
        this._cancel = new Gio.Cancellable();
        this._button = new PanelMenu.Button(0.5, 'Monitor Global');
        this._box = new St.BoxLayout({style_class: 'global-monitor-panel'});
        this._button.add_child(this._box);
        this._render();
        const menu = this._button.menu;
        const title = new PopupMenu.PopupMenuItem('Monitor Global', {reactive: false});
        title.label.add_style_class_name('global-monitor-menu-title');
        menu.addMenuItem(title);
        menu.addAction('Abrir Monitor Global', () => this._launch('overview'));
        menu.addAction('Agenda e lembretes', () => this._launch('agenda'));
        menu.addAction('Personalizar barra e aparência', () => this._launch('appearance'));
        menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._status = new PopupMenu.PopupMenuItem('Conectando ao serviço local…', {reactive: false});
        menu.addMenuItem(this._status);
        this._summary = new PopupMenu.PopupMenuSection();
        menu.addMenuItem(this._summary);
        menu.addAction('Atualizar dados', () => {
            this._proxy?.call('Refresh', null, Gio.DBusCallFlags.NONE, 3000, this._cancel, null);
            this._pull();
        });
        Main.panel.addToStatusArea(this.uuid, this._button, 1, 'right');
        this._startService();
        Gio.DBusProxy.new_for_bus(Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START,
            null, APP, PATH, APP, this._cancel, (_source, result) => {
                try {
                    const proxy = Gio.DBusProxy.new_for_bus_finish(result);
                    if (!this._alive)
                        return;
                    this._proxy = proxy;
                    this._signal = proxy.connect('g-signal', (_proxy, _sender, signal, params) => {
                        if (signal === 'StateChanged')
                            this._receive(params.deepUnpack()[0]);
                    });
                    this._pull();
                } catch (error) {
                    if (this._alive)
                        this._status.label.text = 'Abra a janela para iniciar o serviço.';
                }
            });
        this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 15, () => {
            this._pull();
            return GLib.SOURCE_CONTINUE;
        });
        this._button.menu.connect('open-state-changed', (_menu, open) => {
            if (open)
                this._pull();
        });
    }

    _startService() {
        const now = GLib.get_monotonic_time() / 1e6;
        if (now - this._lastStart < 30)
            return;
        this._lastStart = now;
        this._launch(null);
    }

    _launch(page) {
        try {
            const args = ['python3', this.dir.get_child('monitor.py').get_path(), page ? `--page=${page}` : '--service'];
            const child = Gio.Subprocess.new(args, Gio.SubprocessFlags.NONE);
            child.wait_check_async(null, (process, result) => {
                try {
                    process.wait_check_finish(result);
                } catch (error) {
                    if (this._alive)
                        Main.notifyError('Monitor Global', 'Não foi possível iniciar a janela. Confira as dependências com o instalador.');
                }
            });
        } catch (error) {
            Main.notifyError('Monitor Global', error.message);
        }
    }

    _pull() {
        if (!this._alive || !this._proxy || this._requesting)
            return;
        this._requesting = true;
        this._proxy.call('GetState', null, Gio.DBusCallFlags.NONE, 3000, this._cancel, (proxy, result) => {
            this._requesting = false;
            try {
                const [payload] = proxy.call_finish(result).deepUnpack();
                if (this._alive)
                    this._receive(payload);
            } catch (error) {
                if (this._alive) {
                    this._status.label.text = 'Serviço local indisponível; tentando reconectar…';
                    this._startService();
                }
            }
        });
    }

    _receive(payload) {
        try {
            this._state = JSON.parse(payload);
            this._render();
        } catch (error) {
            this._status.label.text = 'Não foi possível ler o estado do monitor.';
        }
    }

    _indicator(kind, text, settings, color) {
        const part = new St.BoxLayout({style_class: 'global-monitor-indicator'});
        if (settings.display !== 'text') {
            part.add_child(new St.Icon({
                gicon: new Gio.FileIcon({file: this.dir.get_child('icons').get_child(`${kind}-symbolic.svg`)}),
                icon_size: settings.icon_size ?? 16, y_align: Clutter.ActorAlign.CENTER,
            }));
        }
        part.add_child(new St.Label({text, y_align: Clutter.ActorAlign.CENTER}));
        if (typeof color === 'string' && /^#[0-9a-f]{6}$/i.test(color))
            part.set_style(`color: ${color}; font-size: ${Math.max(9, Math.min(20, settings.font_size ?? 11))}px;`);
        this._box.add_child(part);
    }

    _syncPosition(position) {
        const target = {left: Main.panel._leftBox, center: Main.panel._centerBox,
            right: Main.panel._rightBox}[position] ?? Main.panel._rightBox;
        const actor = this._button.container;
        const parent = actor.get_parent();
        if (!parent || parent === target)
            return;
        this._button.menu.close();
        parent.remove_child(actor);
        // Center index zero puts the monitor immediately before the date/clock.
        target.insert_child_at_index(actor, position === 'left' ? -1 : position === 'center' ? 0 : 1);
    }

    _render() {
        this._box.destroy_all_children();
        if (!this._state) {
            this._indicator('globe', 'Global', {icon_size: 16, display: 'both'}, '#4ade80');
            return;
        }
        const {settings, assets, reminders} = this._state;
        this._syncPosition(settings.panel_position);
        this._box.set_style(`spacing: ${Math.max(2, Math.min(20, settings.spacing))}px;`);
        const selected = assets.filter(row => row.enabled !== false && row.pinned).slice(0, settings.bar_limit);
        for (const item of selected) {
            const quote = item.quote ?? {};
            const prefix = settings.display === 'icons' ? '' : `${item.symbol} `;
            let text = prefix + formatValue(quote.value, quote.unit);
            if (settings.show_change && Number.isFinite(quote.change))
                text += ` ${quote.change >= 0 ? '▲' : '▼'}${Math.abs(quote.change).toFixed(1).replace('.', ',')}%`;
            if (quote.error)
                text += ' ·';
            this._indicator(item.kind, text, settings, settings.colors[item.kind]);
        }
        const pending = reminders.filter(row => row.enabled && row.status !== 'done');
        if (settings.agenda_bar)
            this._indicator('agenda', String(pending.length), settings, settings.colors.agenda);
        if (!selected.length && !settings.agenda_bar)
            this._indicator('globe', 'Global', settings, settings.accent);
        if (this._summary) {
            this._summary.removeAll();
            const errors = assets.filter(item => item.enabled !== false && item.quote?.error).length;
            this._status.label.text = errors ? `${errors} fonte(s) indisponível(is) · veja os detalhes na janela` : 'Fontes conectadas · horários e atrasos na janela';
            for (const item of selected.slice(0, 4)) {
                const quote = item.quote ?? {};
                this._summary.addMenuItem(new PopupMenu.PopupMenuItem(
                    `${item.symbol}: ${formatValue(quote.value, quote.unit)} · ${quote.source ?? 'consultando…'}`, {reactive: false}));
            }
            if (pending.length) {
                const next = pending.sort((a, b) => a.due - b.due)[0];
                this._summary.addMenuItem(new PopupMenu.PopupMenuItem(`Agenda: ${next.title.slice(0, 45)}`, {reactive: false}));
            }
        }
        this._button.accessible_name = 'Monitor Global. ' + selected.map(item =>
            `${item.name}: ${formatValue(item.quote?.value, item.quote?.unit)}`).join('. ');
    }

    disable() {
        this._alive = false;
        this._cancel?.cancel();
        if (this._timer)
            GLib.source_remove(this._timer);
        if (this._proxy && this._signal)
            this._proxy.disconnect(this._signal);
        this._proxy = null;
        this._button?.destroy();
        this._button = null;
        this._box = null;
        this._summary = null;
        this._state = null;
        this._timer = 0;
    }
}
