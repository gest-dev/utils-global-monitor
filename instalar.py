#!/usr/bin/env python3
"""Install only this extension and its desktop/session launchers; no downloads."""
from datetime import datetime
import os
from pathlib import Path
import shutil
import subprocess
import sys

UUID = 'utils-global-monitor@gezer.local'
APP = 'org.gezer.MonitorGlobal'


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def main():
    project = Path(__file__).resolve().parent
    for command in ('gnome-shell', 'gnome-extensions', 'gjs'):
        if not shutil.which(command):
            raise RuntimeError(f'Comando necessário não encontrado: {command}')
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    gi.require_version('Graphene', '1.0')
    from gi.repository import Adw, Gtk
    if (Gtk.get_major_version(), Gtk.get_minor_version()) < (4, 10) or Adw.get_minor_version() < 4:
        raise RuntimeError('Requer GTK 4.10+ e libadwaita 1.4+.')
    if not subprocess.check_output(['gnome-shell', '--version'], text=True).startswith('GNOME Shell 50.'):
        raise RuntimeError('Esta extensão foi preparada e validada para GNOME 50.')
    archive = project / 'dist' / f'{UUID}.shell-extension.zip'
    if not archive.is_file():
        raise RuntimeError('Pacote ausente. Execute: bash empacotar.sh')
    data_home = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
    config_home = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
    destination = data_home / 'gnome-shell/extensions' / UUID
    update = destination.exists()
    if update:
        backup = project / 'backups' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        shutil.copytree(destination, backup)
        print('Backup da extensão anterior:', backup)
    run('gnome-extensions', 'install', *(['--force'] if update else []), str(archive))
    # Desktop Exec is parsed by GLib, not a shell. Quote its path explicitly.
    script = str(destination / 'monitor.py').replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    executable = str(Path(sys.executable).resolve()).replace('"', '\\"')
    logo = destination / 'icons/monitor-global-logo-v1.png'
    entry = f'''[Desktop Entry]
Type=Application
Name=Monitor Global
Comment=Moedas, mercados, clima, agenda e notificações
Exec="{executable}" "{script}"
Icon={logo}
Terminal=false
Categories=Utility;Finance;Office;
StartupNotify=true
X-Geoclue-Reason=Usa sua localização aproximada para consultar o tempo, somente quando você solicita.
Actions=Agenda;Preferencias;

[Desktop Action Agenda]
Name=Agenda e lembretes
Exec="{executable}" "{script}" --page=agenda

[Desktop Action Preferencias]
Name=Personalizar
Exec="{executable}" "{script}" --page=appearance
'''
    applications = data_home / 'applications'
    applications.mkdir(parents=True, exist_ok=True)
    (applications / f'{APP}.desktop').write_text(entry)
    icons = data_home / 'icons/hicolor/scalable/apps'
    icons.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(destination / 'icons' / f'{APP}.svg', icons / f'{APP}.svg')
    icon_path = data_home / 'icons/hicolor/scalable/apps'
    icon_path.mkdir(parents=True, exist_ok=True)
    shutil.copy2(destination / 'icons/org.gezer.MonitorGlobal.svg', icon_path / f'{APP}.svg')
    autostart = config_home / 'autostart'
    autostart.mkdir(parents=True, exist_ok=True)
    (autostart / f'{APP}.desktop').write_text(f'''[Desktop Entry]
Type=Application
Name=Monitor Global · lembretes
Exec="{executable}" "{script}" --service
Icon={logo}
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
''')
    run('gjs', '-c', '''
const Gio = imports.gi.Gio;
const settings = new Gio.Settings({schema_id: 'org.gnome.shell'});
const uuid = 'utils-global-monitor@gezer.local';
const enabled = settings.get_strv('enabled-extensions');
if (!enabled.includes(uuid)) settings.set_strv('enabled-extensions', [...enabled, uuid]);
settings.set_strv('disabled-extensions', settings.get_strv('disabled-extensions').filter(item => item !== uuid));
Gio.Settings.sync();
''')
    if shutil.which('update-desktop-database'):
        subprocess.run(['update-desktop-database', str(applications)], check=False, capture_output=True)
    if not update:
        subprocess.run(['gnome-extensions', 'enable', UUID], capture_output=True, check=False)
    print('Monitor Global instalado. O serviço de lembretes inicia com sua sessão.')
    print('Abra pelo menu de aplicativos ou execute: python3 abrir.py')
    print('Para carregar a nova extensão na barra, salve o trabalho, saia da sessão e entre novamente.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, ImportError, ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'Instalação interrompida: {error}', file=sys.stderr)
        print('Dependências Ubuntu: python3-gi gir1.2-gtk-4.0 gir1.2-adw-1', file=sys.stderr)
        sys.exit(1)
