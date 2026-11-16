"""Agenda presentation derived from the clock; notification state stays independent."""
from datetime import datetime
import time
from zoneinfo import ZoneInfo

FILTERS = [('today', 'Hoje'), ('upcoming', 'Próximos'), ('overdue', 'Atrasados'),
           ('done', 'Concluídos'), ('paused', 'Pausados'), ('all', 'Todos'), ('calendar', 'Calendário')]
STATUSES = {'pending': 'Agendado', 'notified': 'Aviso gerado', 'overdue': 'Atrasado',
            'done': 'Concluído', 'paused': 'Pausado'}


def reminder_status(item, now=None):
    now = time.time() if now is None else now
    if item.get('status') == 'done':
        return 'done'
    if not item.get('enabled', True):
        return 'paused'
    if item['due'] <= now:
        return 'overdue'
    return 'notified' if item.get('status') == 'notified' else 'pending'


def overdue_text(item, now):
    minutes = max(0, int((now - item['due']) // 60))
    if minutes < 1:
        return 'Atrasado há menos de 1 minuto'
    if minutes < 60:
        return f'Atrasado há {minutes} min'
    if minutes < 1440:
        return f'Atrasado há {minutes // 60} h e {minutes % 60} min'
    days = minutes // 1440
    return f'Atrasado há {days} ' + ('dia' if days == 1 else 'dias')


def agenda_view(items, mode, query='', calendar_day=None, now=None):
    if mode not in dict(FILTERS):
        raise ValueError('Filtro de agenda inválido.')
    now = time.time() if now is None else now
    query = query.strip().casefold()
    result, counts = [], {key: 0 for key, _title in FILTERS}
    for item in sorted(items, key=lambda row: row['due']):
        if query not in ' '.join(str(item.get(key, '')) for key in ('title', 'description', 'category')).casefold():
            continue
        status = reminder_status(item, now)
        zone = ZoneInfo(item['timezone'])
        day = datetime.fromtimestamp(item['due'], zone).date()
        today = datetime.fromtimestamp(now, zone).date()
        matches = {'today': day == today and status not in ('done', 'paused'),
                   'upcoming': status in ('pending', 'notified'), 'overdue': status == 'overdue',
                   'done': status == 'done', 'paused': status == 'paused', 'all': True,
                   'calendar': day == (calendar_day or today)}
        for key, selected in matches.items():
            counts[key] += int(selected)
        if matches[mode]:
            result.append(item)
    return result, counts
