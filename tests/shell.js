import Gio from 'gi://Gio';
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';

export const METRICS = {};
let count = 0;
function check(condition, text) {
    count++;
    if (!condition)
        throw new Error(text);
}

export async function run() {
    Main.overview.hide();
    await Scripting.sleep(3500);
    const extension = Main.extensionManager.lookup('utils-global-monitor@gezer.local');
    check(Boolean(extension?.stateObj?._button), `Extension failed: ${extension?.error}`);
    const monitor = extension.stateObj;
    for (let attempt = 0; attempt < 20 && !monitor._state; attempt++) {
        monitor._pull();
        await Scripting.sleep(500);
    }
    check(Boolean(monitor._state), 'Background app exposes state over D-Bus');
    check(monitor._state.assets.length === 5, 'Five default assets');
    check(monitor._box.get_n_children() >= 4, 'Pinned indicators render in panel');
    const original = JSON.stringify(monitor._state);
    const panelButton = monitor._button;
    for (const [position, parent] of [['left', Main.panel._leftBox], ['center', Main.panel._centerBox],
                                      ['right', Main.panel._rightBox]]) {
        monitor._state.settings.panel_position = position;
        monitor._render();
        check(monitor._button.container.get_parent() === parent, `Indicator moves to ${position}`);
        if (position === 'center') {
            const children = parent.get_children();
            check(children.indexOf(monitor._button.container) < children.indexOf(Main.panel.statusArea.dateMenu.container),
                'Center position is before the clock');
        }
    }
    check(monitor._button === panelButton, 'Moving the indicator preserves the same button and menu');
    const codes = {fx: 'CAD-BRL', crypto: 'ETH-EUR', stock: 'VALE3', weather: 'VDC'};
    monitor._state.settings.display = 'both';
    monitor._state.settings.bar_limit = 12;
    for (const row of monitor._state.assets) {
        row.symbol = codes[row.kind];
        row.pinned = true;
    }
    monitor._render();
    const labels = monitor._box.get_children().map(part => part.get_child()?.get_last_child()?.get_text?.() ?? '');
    for (const code of Object.values(codes))
        check(labels.some(text => text.startsWith(code + ' ')), `Panel displays updated code: ${code}`);
    monitor._receive(original);
    monitor._launch('overview');
    const windows = () => global.get_window_actors().map(actor => actor.meta_window)
        .filter(window => window.get_gtk_application_id() === 'org.gezer.MonitorGlobal');
    for (let attempt = 0; attempt < 20 && !windows().length; attempt++)
        await Scripting.sleep(300);
    check(windows().length === 1, 'Native window opens');
    await Scripting.sleep(2500);
    monitor._launch('overview');
    await Scripting.sleep(500);
    check(windows().length === 1, 'One window after repeated launch');
    const firstIndicator = monitor._box.get_first_child();
    check(!firstIndicator.get_child().get_last_child().clutter_text.get_layout().is_ellipsized(), 'Panel values remain fully visible');
    const targetAsset = monitor._state.assets.find(row => row.enabled !== false && row.pinned);
    const pointer = Clutter.get_default_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const [actorX, actorY] = firstIndicator.get_transformed_position();
    pointer.notify_absolute_motion(GLib.get_monotonic_time(), actorX + firstIndicator.width / 2, actorY + firstIndicator.height / 2);
    await Scripting.sleep(900);
    check(Boolean(monitor._tooltip), 'Hovering a value shows asset details');
    pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
    pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED);
    for (let attempt = 0; attempt < 25 && windows().length < 2; attempt++)
        await Scripting.sleep(200);
    check(windows().some(window => window.get_title() === targetAsset.name), 'Clicking a panel value opens its asset modal');
    check(!monitor._button.menu.isOpen && !monitor._tooltip, 'Asset click closes tooltip and keeps menu closed');
    await Scripting.sleep(2000);
    const chartStream = Gio.File.new_for_path('/tmp/monitor-global-chart-preview.png')
        .replace(null, false, Gio.FileCreateFlags.NONE, null);
    await new Promise((resolve, reject) => {
        const shooter = new Shell.Screenshot();
        shooter.screenshot(false, chartStream, (object, result) => {
            try { object.screenshot_finish(result); resolve(); } catch (error) { reject(error); }
        });
    });
    chartStream.close(null);
    monitor._openDetails(targetAsset.id);
    await Scripting.sleep(400);
    check(windows().length === 2, 'Repeated panel clicks reuse one main window and one modal');
    const modal = windows().find(window => window.get_title() === targetAsset.name);
    modal.delete(global.get_current_time());
    await Scripting.sleep(200);
    check(windows().length === 1, 'Closing asset modal preserves the application window');
    const stream = Gio.File.new_for_path('/tmp/monitor-global-preview.png')
        .replace(null, false, Gio.FileCreateFlags.NONE, null);
    await new Promise((resolve, reject) => {
        const shooter = new Shell.Screenshot();
        shooter.screenshot(false, stream, (object, result) => {
            try { object.screenshot_finish(result); resolve(); } catch (error) { reject(error); }
        });
    });
    stream.close(null);
    windows()[0].delete(global.get_current_time());
    await Scripting.sleep(300);
    check(windows().length === 0, 'Window closes');
    monitor._pull();
    await Scripting.sleep(300);
    check(Boolean(monitor._state), 'Service survives closing the window');
    const oldButton = monitor._button;
    Main.extensionManager.disableExtension('utils-global-monitor@gezer.local');
    await Scripting.sleep(200);
    check(!monitor._button, 'Disable releases panel');
    Main.extensionManager.enableExtension('utils-global-monitor@gezer.local');
    await Scripting.sleep(1500);
    check(monitor._button !== oldButton, 'Extension re-enables');
    const testPath = Gio.File.new_for_uri(import.meta.url).get_parent().get_child('ui.py').get_path();
    const test = Gio.Subprocess.new(['python3', testPath], Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE);
    const [output, errors] = await new Promise((resolve, reject) => {
        test.communicate_utf8_async(null, null, (process, result) => {
            try {
                const [, stdout, stderr] = process.communicate_utf8_finish(result);
                resolve([stdout, stderr]);
            } catch (error) { reject(error); }
        });
    });
    check(test.get_successful() && output.includes('MONITOR_GLOBAL_UI_OK'), `UI tests failed: ${output}\n${errors}`);
    print(output.trim());
    const httpPath = Gio.File.new_for_uri(import.meta.url).get_parent().get_child('test_http.py').get_path();
    const http = Gio.Subprocess.new(['python3', httpPath], Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE);
    const httpErrors = await new Promise((resolve, reject) => {
        http.communicate_utf8_async(null, null, (process, result) => {
            try { resolve(process.communicate_utf8_finish(result)[2]); } catch (error) { reject(error); }
        });
    });
    check(http.get_successful(), `Local HTTP tests failed: ${httpErrors}`);
    print('MONITOR_GLOBAL_HTTP_OK: 3 testes de API local.');
    print(`MONITOR_GLOBAL_SHELL_OK: ${count} verificações.`);
}
