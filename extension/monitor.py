#!/usr/bin/env python3
"""Single-instance native app and session-local reminder service."""
from datetime import datetime
import json
from pathlib import Path
import sys
import time
from zoneinfo import ZoneInfo

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from global_monitor import APP_ID, BUS_PATH
from global_monitor.engine import Engine
from global_monitor.store import Store

INTERFACE = f'''<node><interface name="{APP_ID}">
<method name="GetState"><arg type="s" direction="out" name="json"/></method>
<method name="OpenWindow"><arg type="s" direction="in" name="page"/></method>
<method name="Refresh"/>
<signal name="StateChanged"><arg type="s" name="json"/></signal>
</interface></node>'''


class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None
        self.page = 'overview'
        self._notifications = {}
        self._publishing = False

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.hold()
        self.store = Store()
        self.engine = Engine(self.store, self.changed, GLib.idle_add)
        self.engine.notify = self.notify_event
        interface = Gio.DBusNodeInfo.new_for_xml(INTERFACE).interfaces[0]
        self._registration = self.get_dbus_connection().register_object(BUS_PATH, interface, self._dbus_call, None, None)
        for name, target in [('overview', 'overview'), ('agenda', 'agenda'), ('settings', 'appearance')]:
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', lambda _action, _param, page=target: self.open_page(page))
            self.add_action(action)
        self._notify_proxy = Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE,
            None, 'org.freedesktop.Notifications', '/org/freedesktop/Notifications',
            'org.freedesktop.Notifications', None)
        self._notify_proxy.connect('g-signal', self._notification_signal)
        self._timer = GLib.timeout_add_seconds(5, self.engine.tick)
        GLib.idle_add(self._initial_refresh)

    def _initial_refresh(self):
        self.engine.tick()
        return GLib.SOURCE_REMOVE

    def do_command_line(self, command):
        args = command.get_arguments()[1:]
        if '--quit' in args:
            self.quit()
            return 0
        if '--service' not in args:
            self.page = next((arg.split('=', 1)[1] for arg in args if arg.startswith('--page=')), 'overview')
            self.activate()
        return 0

    def do_activate(self):
        self.open_page(self.page)

    def open_page(self, page='overview'):
        if self.window is None:
            from global_monitor.ui import MonitorWindow
            Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(str(Path(__file__).parent / 'icons'))
            self.window = MonitorWindow(self)
            self.window.connect('close-request', self._window_closed)
        self.window.navigate(page)
        self.window.present()

    def _window_closed(self, *_args):
        self.window = None
        return False

    def _dbus_call(self, _connection, _sender, _path, _interface, method, params, invocation):
        try:
            if method == 'GetState':
                invocation.return_value(GLib.Variant('(s)', (json.dumps(self.engine.state(), ensure_ascii=False),)))
            elif method == 'OpenWindow':
                self.open_page(params.unpack()[0])
                invocation.return_value(None)
            elif method == 'Refresh':
                self.engine.tick()
                invocation.return_value(None)
        except Exception as error:
            invocation.return_dbus_error(APP_ID + '.Error', str(error))

    def changed(self):
        if self._publishing:
            return GLib.SOURCE_REMOVE
        self._publishing = True
        GLib.timeout_add(150, self._publish)
        return GLib.SOURCE_REMOVE

    def _publish(self):
        self._publishing = False
        payload = json.dumps(self.engine.state(), ensure_ascii=False)
        self.get_dbus_connection().emit_signal(None, BUS_PATH, APP_ID, 'StateChanged', GLib.Variant('(s)', (payload,)))
        if self.window:
            self.window.updated()
        return GLib.SOURCE_REMOVE

    def notify_event(self, event):
        payload = event['payload']
        body = payload.get('body', '')
        if payload.get('due'):
            date = datetime.fromtimestamp(payload['due'], ZoneInfo(payload.get('timezone', 'UTC')))
            prefix = 'Pendente de' if payload['due'] < time.time() - 60 else 'Agendado para'
            body = f'{prefix} {date:%d/%m às %H:%M}\n{body}'
        actions = ['open', 'Abrir Monitor Global']
        if payload.get('reminder_id'):
            actions += ['snooze', 'Adiar 10 min', 'done', 'Concluir aviso']
        hints = {'desktop-entry': GLib.Variant('s', APP_ID), 'urgency': GLib.Variant('y', 1),
                 'suppress-sound': GLib.Variant('b', not self.store.settings()['sound'])}
        params = GLib.Variant('(susssasa{sv}i)', ('Monitor Global', 0, 'org.gezer.MonitorGlobal',
            GLib.markup_escape_text(payload['title']), GLib.markup_escape_text(body), actions, hints, 0))

        def sent(proxy, result):
            try:
                notification_id = proxy.call_finish(result).unpack()[0]
                self._notifications[notification_id] = payload
                self.store.delivered(event['id'])
            except GLib.Error:
                self.store.delivered(event['id'], 'Não foi possível exibir a notificação local.')
            self.changed()
        self._notify_proxy.call('Notify', params, Gio.DBusCallFlags.NONE, 5000, None, sent)
        return GLib.SOURCE_REMOVE

    def _notification_signal(self, _proxy, _sender, signal, parameters):
        if signal == 'NotificationClosed':
            self._notifications.pop(parameters.unpack()[0], None)
            return
        if signal != 'ActionInvoked':
            return
        identity, action = parameters.unpack()
        payload = self._notifications.pop(identity, {})
        reminder_id = payload.get('reminder_id')
        if action == 'snooze' and reminder_id:
            self.engine.snooze(reminder_id)
        elif action == 'done' and reminder_id:
            item = self.store.get('reminder', reminder_id)
            if item and item.get('repeat') == 'none':
                self.store.put('reminder', reminder_id, {**item, 'status': 'done', 'enabled': False})
        elif action == 'open':
            self.open_page('agenda' if reminder_id else 'alerts')
        self._notify_proxy.call('CloseNotification', GLib.Variant('(u)', (identity,)),
                                Gio.DBusCallFlags.NONE, 5000, None, None)
        self.changed()

    def do_shutdown(self):
        if hasattr(self, 'engine'):
            self.engine.close()
            GLib.source_remove(self._timer)
            self.get_dbus_connection().unregister_object(self._registration)
        Adw.Application.do_shutdown(self)


if __name__ == '__main__':
    sys.exit(Application().run(sys.argv))
