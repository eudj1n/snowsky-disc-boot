"""The installer's terminal look, in the boot menu's colours (plan, stage 4).

The menu's palette on a dark ground: text, muted text, the selection's pill with an
accent dot, the accent for what moves. Truecolour where the terminal says it has it,
the nearest of 256 colours otherwise, plain text when the output is not a terminal
or NO_COLOR is set. Keys: arrows to move, Space to mark, Enter to go on, q to stop.
"""
import os
import shutil
import sys

GROUND, INK, MUTED, LINE, SELECTED, ACCENT = (0x18, 0x16, 0x14), (0xec, 0xe8, 0xe3), (0xa5, 0x9e, 0x96), (0x33, 0x30, 0x2c), \
    (0x3a, 0x35, 0x30), (0xff, 0x79, 0x5a)
WIDTH = 72


def mode(stream=sys.stdout):
    if os.environ.get('NO_COLOR') or not stream.isatty():
        return 'plain'
    if os.environ.get('COLORTERM', '').lower() in ('truecolor', '24bit'):
        return 'truecolor'
    return '256'


def cube(rgb):
    """The nearest colour of the 6x6x6 cube or the grey ramp of 256-colour terminals."""
    levels = (0, 95, 135, 175, 215, 255)
    idx = [min(range(6), key=lambda k: abs(levels[k] - c)) for c in rgb]
    cube_rgb = [levels[k] for k in idx]
    grey = min(range(24), key=lambda k: abs(8 + 10 * k - sum(rgb) / 3))
    grey_rgb = [8 + 10 * grey] * 3
    dist = lambda a: sum((x - y) ** 2 for x, y in zip(a, rgb))  # noqa: E731
    return 232 + grey if dist(grey_rgb) < dist(cube_rgb) else 16 + 36 * idx[0] + 6 * idx[1] + idx[2]


class Screen:
    """Lines drawn on the menu's ground; each line is a list of (text, colour, background) runs."""

    def __init__(self, stream=sys.stdout, look=None):
        self.out, self.look = stream, look or mode(stream)
        self.width = min(WIDTH, shutil.get_terminal_size((WIDTH, 24)).columns - 2)

    def colour(self, fg, bg=GROUND):
        if self.look == 'plain':
            return ''
        if self.look == 'truecolor':
            return f'\x1b[38;2;{fg[0]};{fg[1]};{fg[2]}m\x1b[48;2;{bg[0]};{bg[1]};{bg[2]}m'
        return f'\x1b[38;5;{cube(fg)}m\x1b[48;5;{cube(bg)}m'

    def line(self, *runs, fill=GROUND):
        """A line of runs (text, fg[, bg]), padded with the ground to the screen's width."""
        if self.look == 'plain':
            text = ''.join(r[0] for r in runs).rstrip()
            self.out.write(text + '\n')
            return
        used, parts = 0, []
        for run in runs:
            text, fg = run[0], run[1]
            bg = run[2] if len(run) > 2 else fill
            parts.append(self.colour(fg, bg) + text)
            used += len(text)
        parts.append(self.colour(INK, fill) + ' ' * max(0, self.width - used) + '\x1b[0m')
        self.out.write(' ' + ''.join(parts) + '\n')

    def blank(self):
        self.line(('', INK))

    def label(self, text):
        self.line(('  ' + ' '.join(text.upper()), MUTED))

    def group(self, title, note=''):
        """A group's heading within a list: its name, and what may be chosen in it."""
        self.line(('  ' + title, INK), (('   ' + note) if note else '', LINE if self.look != 'plain' else INK))

    def text(self, text, fg=INK):
        for chunk in wrap(text, self.width - 4):
            self.line(('  ' + chunk, fg))

    def row(self, title, detail='', on=False, mark=None):
        """A list row: on, it sits in the selection's pill with an accent dot (the menu's look)."""
        dot = '● ' if on else '  '
        box = '' if mark is None else ('◉ ' if mark else '○ ')
        inner = self.width - 6
        left = f'{dot}{box}{title}'
        pad = max(1, inner - len(left) - len(detail))
        if self.look == 'plain':
            left = f'  {">" if on else " "} {"[x] " if mark else "[ ] " if mark is not None else ""}{title}'
            self.line((left + ' ' * max(1, self.width - len(left) - len(detail)) + detail, INK))
            return
        bg = SELECTED if on else GROUND
        self.line(('  ', INK), (' ', INK, bg), (dot, ACCENT, bg), (box, ACCENT if mark else MUTED, bg),
                  (title, INK if on else MUTED, bg), (' ' * pad, INK, bg), (detail, MUTED, bg), (' ', INK, bg))

    def progress(self, label, fraction):
        bar = max(10, self.width - 18)
        done = int(bar * max(0.0, min(1.0, fraction)))
        if self.look == 'plain':
            self.line((f'  {label}: {int(fraction * 100)}%', INK))
            return
        self.line(('  ', INK), ('━' * done, ACCENT), ('━' * (bar - done), LINE), (f'  {int(fraction * 100):3d}%', MUTED))
        self.line(('  ' + label, MUTED))

    def steps(self, names, current):
        """The installation's steps: done, the current one, and those ahead."""
        for k, name in enumerate(names):
            if k < current:
                self.line(('  ✓ ', INK), (name, MUTED))
            elif k == current:
                self.line(('  ● ', ACCENT), (name, INK))
            else:
                self.line(('  · ', LINE), (name, LINE))

    def clear(self):
        if self.look != 'plain':
            self.out.write('\x1b[H\x1b[2J')
        self.out.flush()

    def flush(self):
        self.out.flush()


def wrap(text, width):
    words, lines, current = text.split(), [], ''
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f'{current} {word}' if current else word
    return lines + [current] if current else lines or ['']


def read_key():
    """One key from the terminal: 'up', 'down', 'space', 'enter', 'quit' or the character."""
    import termios
    import tty
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == '\x1b':
            seq = sys.stdin.read(2)
            return {'[A': 'up', '[B': 'down'}.get(seq, 'escape')
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    return {' ': 'space', '\r': 'enter', '\n': 'enter', 'q': 'quit', '\x03': 'quit'}.get(ch, ch)
