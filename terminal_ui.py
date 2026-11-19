"""Small terminal UI with a plain-text fallback and no third-party dependencies."""
import getpass
import os
import sys
import textwrap
from functools import wraps
from pathlib import Path

try:
    import curses
except ImportError:
    curses = None

PLAIN = False
CONTEXT = {}
COLORS = False


class Cancelled(Exception):
    def __init__(self, value=None):
        super().__init__()
        self.value = value


def scoped(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        previous = CONTEXT.copy()
        try:
            return function(*args, **kwargs)
        finally:
            CONTEXT.clear()
            CONTEXT.update(previous)
    return wrapped


def set_context(**values):
    CONTEXT.update(values)


def summary():
    labels = [('profile', 'API'), ('agent', 'Agente'), ('mode', 'Modo'), ('model', 'Modelo')]
    return ' → '.join(f'{label}: {CONTEXT[key]}' for key, label in labels if CONTEXT.get(key))


def tone(text):
    text = text.casefold()
    if any(word in text for word in ('error', 'falló', 'rechazada', 'inválid', 'no se pudo')):
        return 'error'
    if any(word in text for word in ('pendiente', 'sin comprobar', 'no instalado', 'disponible para instalar')):
        return 'pending'
    if ('instalado' in text and 'desinstalado' not in text) or any(
            word in text for word in ('✓', 'comprobado', 'respondió', 'catálogo accesible', 'guardado')):
        return 'success'
    return 'normal'


def color(kind):
    return curses.color_pair({'success': 1, 'pending': 2, 'error': 3, 'accent': 4}.get(kind, 0)) if COLORS else 0


def banner(screen):
    put(screen, 0, '◆ MODELHUB  ·  Tus APIs, tus agentes', curses.A_BOLD | color('accent'))
    height, width = screen.getmaxyx()
    lines = textwrap.wrap(clean(summary()), max(10, width - 3))[:2] if summary() else []
    for row, line in enumerate(lines, 1):
        put(screen, row, line, color('accent'))
    if lines and height > 1 + len(lines) + 1:
        put(screen, 1 + len(lines), '─' * max(1, width - 4), color('accent'))
        return 2 + len(lines)
    return 1 + len(lines)


def interactive():
    return (not PLAIN and curses is not None and sys.stdin.isatty()
            and sys.stdout.isatty() and os.environ.get('TERM', '') not in ('', 'dumb'))


def clean(text):
    return ''.join(c if c.isprintable() else ' ' for c in str(text))


def put(screen, row, text, style=0):
    height, width = screen.getmaxyx()
    if 0 <= row < height and width > 2:
        try:
            screen.addnstr(row, 1, clean(text), width - 2, style)
        except curses.error:
            pass


def setup(screen):
    global COLORS
    COLORS = False
    screen.keypad(True)
    if not os.environ.get('NO_COLOR') and curses.has_colors():
        try:
            curses.start_color()
            curses.use_default_colors()
            for pair, foreground in enumerate((curses.COLOR_GREEN, curses.COLOR_YELLOW, curses.COLOR_RED, curses.COLOR_CYAN), 1):
                curses.init_pair(pair, foreground, -1)
            COLORS = True
        except curses.error:
            pass
    if hasattr(curses, 'set_escdelay'):
        curses.set_escdelay(30)
    try:
        curses.curs_set(0)
    except curses.error:
        pass


def matches(values, query):
    words = query.casefold().split()
    return [value for value in values if all(word in value.casefold() for word in words)]


def screen_call(callback, *args):
    global PLAIN
    try:
        return True, curses.wrapper(callback, *args)
    except curses.error:
        PLAIN = True
        return False, None


def _select(screen, title, values, cards=None):
    setup(screen)
    query, index = '', 0
    while True:
        found = matches(values, query)
        index = min(index, max(0, len(found) - 1))
        height, width = screen.getmaxyx()
        heading = []
        for line in title.splitlines():
            heading.extend(textwrap.wrap(clean(line), max(10, width - 4)))
        heading = heading[:min(4, max(1, height - 8))]
        screen.erase()
        top = banner(screen)
        first = top + len(heading) + 3
        card_height = 7 if cards and height >= first + 11 else 0
        size = max(1, height - first - 3 - card_height)
        start = (index // size) * size
        for row, line in enumerate(heading, top):
            put(screen, row, line, curses.A_BOLD if row == top else 0)
        put(screen, first - 2, '⌕ Filtrar: ' + query + '▏', color('accent'))
        for row, value in enumerate(found[start:start + size], first):
            active = row - first + start == index
            put(screen, row, ('› ' if active else '  ') + value,
                (curses.A_REVERSE if active else 0) | color(tone(value)))
        if not found:
            put(screen, first, 'Sin coincidencias — borra el filtro o pulsa Esc', color('pending'))
        if card_height and found:
            card = (cards or {}).get(found[index])
            if card:
                row = height - card_height - 2
                put(screen, row, '─' * max(1, width - 4), color('accent'))
                put(screen, row + 1, card['title'], curses.A_BOLD | color(tone(card['title'])))
                lines = []
                for line in card['detail'].splitlines():
                    lines.extend(textwrap.wrap(clean(line), max(10, width - 4)))
                for offset, line in enumerate(lines[:4], 2):
                    put(screen, row + offset, line, color(tone(line)))
        footer = f'↑↓ Mover · Enter Elegir · Esc Volver · Escribe para buscar  ({len(found)}/{len(values)})'
        put(screen, height - 2, footer, color('accent'))
        screen.refresh()
        key = screen.get_wch()
        if key == '\x1b':
            raise Cancelled()
        if key == '\x03':
            raise KeyboardInterrupt()
        if key in ('\n', '\r', curses.KEY_ENTER) and found:
            return found[index]
        if key == curses.KEY_DOWN and found:
            index = (index + 1) % len(found)
        elif key == curses.KEY_UP and found:
            index = (index - 1) % len(found)
        elif key == curses.KEY_NPAGE:
            index = min(index + size, max(0, len(found) - 1))
        elif key == curses.KEY_PPAGE:
            index = max(0, index - size)
        elif key in ('\x7f', '\b', curses.KEY_BACKSPACE):
            query, index = query[:-1], 0
        elif key == '\x15':
            query, index = '', 0
        elif isinstance(key, str) and key.isprintable():
            query, index = query + key, 0


def select(title, values, cards=None):
    if not values:
        raise ValueError('No hay opciones disponibles.')
    if interactive():
        ok, result = screen_call(_select, title, list(values), cards)
        if ok:
            return result
    print('\n' + title)
    if summary():
        print(summary())
    for i, value in enumerate(values, 1):
        print(f'  {i}. {clean(value)}')
        if cards and value in cards:
            print('     ' + clean(cards[value]['title']))
            for line in str(cards[value]['detail']).splitlines():
                print('       ' + clean(line))
    while True:
        answer = input('Número [1] · 0 para volver: ').strip() or '1'
        if answer in ('0', '\x1b'):
            raise Cancelled()
        if answer.isdigit() and 1 <= int(answer) <= len(values):
            return values[int(answer) - 1]
        print('Selecciona un número de la lista.')


def _prompt(screen, title, default, secret):
    setup(screen)
    value = ''
    while True:
        screen.erase()
        height, width = screen.getmaxyx()
        top = banner(screen) + 1
        for row, line in enumerate(textwrap.wrap(clean(title), max(10, width - 3))[:3], top):
            put(screen, row, line)
        if default and not secret:
            put(screen, top + 4, 'Enter conserva: ' + default)
        elif default and secret:
            put(screen, top + 4, 'Clave conservada · Enter mantiene la clave oculta')
        visible = '*' * len(value) if secret else value
        put(screen, top + 6, '> ' + visible[-max(1, width - 6):] + '▏')
        put(screen, height - 2, 'Enter Aceptar · Esc Volver · Ctrl+U Borrar')
        screen.refresh()
        key = screen.get_wch()
        if key == '\x1b':
            raise Cancelled(value)
        if key == '\x03':
            raise KeyboardInterrupt()
        if key in ('\n', '\r', curses.KEY_ENTER):
            return value.strip() or default
        if key in ('\x7f', '\b', curses.KEY_BACKSPACE):
            value = value[:-1]
        elif key == '\x15':
            value = ''
        elif isinstance(key, str) and key.isprintable():
            value += key


def prompt(title, default='', secret=False):
    if interactive():
        ok, result = screen_call(_prompt, title, default, secret)
        if ok:
            return result
    label = clean(title) + (f' [{default}]' if default and not secret else '') + ': '
    value = (getpass.getpass(label) if secret else input(label)).strip()
    if value == '\x1b':
        raise Cancelled()
    return value or default


def _browse(screen, title, start):
    setup(screen)
    current = Path(start).expanduser().resolve()
    query, index = '', 0
    while True:
        try:
            directories = sorted((entry for entry in current.iterdir() if entry.is_dir()),
                                 key=lambda entry: entry.name.casefold())
            problem = ''
        except OSError as exc:
            directories, problem = [], str(exc)
        matching = [entry for entry in directories if query.casefold() in entry.name.casefold()]
        options = [('✓ Usar esta carpeta', ('use', current))]
        if current.parent != current:
            options.append(('↑ ..  ' + str(current.parent), ('dir', current.parent)))
        options += [(entry.name + '/', ('dir', entry)) for entry in matching]
        index = min(index, max(0, len(options) - 1))
        height, width = screen.getmaxyx()
        screen.erase()
        heading = []
        for line in (title + '\nCarpeta: ' + str(current)).splitlines():
            heading.extend(textwrap.wrap(clean(line), max(10, width - 4)))
        heading = heading[:min(4, max(1, height - 8))]
        top = banner(screen)
        first = top + len(heading) + 2
        for row, line in enumerate(heading, top):
            put(screen, row, line, curses.A_BOLD if row == top else 0)
        put(screen, first - 1, '⌕ Filtrar: ' + query + '▏', color('accent'))
        size = max(1, height - first - 3)
        start_row = (index // size) * size
        for row, (label, _) in enumerate(options[start_row:start_row + size], first):
            active = row - first + start_row == index
            put(screen, row, ('› ' if active else '  ') + label,
                (curses.A_REVERSE if active else 0) | color('accent' if active else 'normal'))
        if problem:
            put(screen, height - 3, 'No se pudo leer la carpeta: ' + problem, color('error'))
        put(screen, height - 2, '↑↓ Mover · Enter Abrir · ← Subir · Esc Cancelar · Escribe para filtrar',
            color('accent'))
        screen.refresh()
        key = screen.get_wch()
        if key == '\x1b':
            raise Cancelled()
        if key == '\x03':
            raise KeyboardInterrupt()
        if key in ('\n', '\r', curses.KEY_ENTER):
            kind, target = options[index][1]
            if kind == 'use':
                return Path(target)
            current, query, index = Path(target), '', 0
        elif key == curses.KEY_LEFT:
            current, query, index = current.parent, '', 0
        elif key == curses.KEY_RIGHT:
            kind, target = options[index][1]
            if kind == 'dir':
                current, query, index = Path(target), '', 0
        elif key == curses.KEY_DOWN:
            index = (index + 1) % len(options)
        elif key == curses.KEY_UP:
            index = (index - 1) % len(options)
        elif key in ('\x7f', '\b', curses.KEY_BACKSPACE):
            query, index = query[:-1], 0
        elif key == '\x15':
            query, index = '', 0
        elif isinstance(key, str) and key.isprintable():
            query, index = query + key, 0


def _browse_plain(title, start):
    current = Path(start).expanduser().resolve()
    while True:
        print('\n' + title)
        print('Carpeta actual: ' + str(current))
        try:
            directories = sorted((entry for entry in current.iterdir() if entry.is_dir()),
                                 key=lambda entry: entry.name.casefold())
            problem = ''
        except OSError as exc:
            directories, problem = [], str(exc)
        if problem:
            print('No se pudo leer: ' + problem)
        print('  1. Usar esta carpeta')
        offset = 2
        if current.parent != current:
            print(f'  2. .. ({current.parent})')
            offset = 3
        for number, entry in enumerate(directories, offset):
            print(f'  {number}. {entry.name}/')
        answer = input('Número, ruta de carpeta, 0 cancelar: ').strip()
        if answer in ('', '0'):
            raise Cancelled()
        if answer.isdigit():
            number = int(answer)
            if number == 1:
                return current
            if current.parent != current and number == 2:
                current = current.parent
                continue
            position = number - offset
            if 0 <= position < len(directories):
                current = (current / directories[position].name).resolve()
            else:
                print('Opción no válida.')
            continue
        target = Path(answer).expanduser()
        if target.is_dir():
            current = target.resolve()
        else:
            print('Esa ruta no es una carpeta.')


def pick_directory(title='Carpeta de trabajo', start=None):
    path = Path(start or os.getcwd()).expanduser()
    if not path.is_dir():
        path = Path.home()
    if interactive():
        ok, result = screen_call(_browse, title, path)
        if ok:
            return result
    return _browse_plain(title, path)


def _message(screen, title, text):
    setup(screen)
    offset = 0
    icons = {'success': '✓ ', 'error': '✕ ', 'pending': '● '}
    while True:
        height, width = screen.getmaxyx()
        lines = []
        for line in text.splitlines():
            lines.extend(textwrap.wrap(clean(line), max(10, width - 4)) or [''])
        screen.erase()
        top = banner(screen)
        size = max(1, height - top - 4)
        offset = min(offset, max(0, len(lines) - size))
        kind = tone(title)
        decorated = (icons.get(kind, '') + title) if icons.get(kind) else title
        put(screen, top, decorated, curses.A_BOLD | color(kind))
        for row, line in enumerate(lines[offset:offset + size], top + 2):
            put(screen, row, line)
        hint = 'Enter / Esc Volver · ↑↓ Desplazar'
        if len(lines) > size:
            hint += f'  ·  {len(lines)} líneas'
        put(screen, height - 2, hint, color('accent'))
        screen.refresh()
        key = screen.get_wch()
        if key in ('\n', '\r', '\x1b', curses.KEY_ENTER):
            return
        if key == '\x03':
            raise KeyboardInterrupt()
        if key == curses.KEY_DOWN:
            offset += 1
        elif key == curses.KEY_UP:
            offset = max(0, offset - 1)


def message(title, text):
    if interactive():
        ok, _ = screen_call(_message, title, text)
        if ok:
            return
    print(f'\n{title}\n{text}')


def confirm(title, detail=''):
    if detail:
        message(title, detail)
        return select('Confirmar', ['Cancelar', 'Sí, continuar']) == 'Sí, continuar'
    return select(title, ['Cancelar', 'Sí, continuar']) == 'Sí, continuar'
