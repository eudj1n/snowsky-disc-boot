"""The boot menu (disc-menu) through its fixture build: a framebuffer and input devices that are files.

Events are written as the player's 32-bit kernel writes them (16 bytes); the frame is read from
the framebuffer file, turned back 180 degrees as the panel shows it.
"""
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
MENU = Path(os.environ.get('DISC_MENU_FIXTURE_BINARY', ROOT/'build/host/disc-menu-fixture'))
PRODUCTION = Path(os.environ.get('DISC_MENU_BINARY', ROOT/'build/host/disc-menu'))
PANEL = 360
GROUND, LINE, SELECTED, ACCENT = (0x18, 0x16, 0x14), (0x33, 0x30, 0x2c), (0x3a, 0x35, 0x30), (0xff, 0x79, 0x5a)
VOLUME_UP, VOLUME_DOWN, PLAY = 0xfb, 0xfc, 0xfa


def event(kind, code, value):
    return struct.pack('<iiHHi', 0, 0, kind, code, value)


class MenuTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root/'status/ui').mkdir(parents=True)
        (self.root/'run').mkdir()
        (self.root/'fb0').write_bytes(bytes(PANEL * PANEL * 4 * 3))
        (self.root/'keys').write_bytes(b'')
        (self.root/'touch').write_bytes(b'')
        launcher = self.root/'launcher'
        # The launcher the menu hands over to: its name, and which descriptors above 2 are still open.
        launcher.write_text(f'#!/bin/sh\necho "$0" > "{self.root}/launched"\n'
                            f'for fd in 3 4 5 6 7 8 9; do {{ : >&$fd; }} 2>/dev/null && echo $fd; done > "{self.root}/fds"\nexit 0\n')
        launcher.chmod(0o755)
        self.choices(['alpha', 'beta'], 'beta')

    def choices(self, names, default, titles=None):
        entries = [dict(ui=n, title=(titles or {}).get(n, n), version='1', confirmed=True) for n in names]
        entries.append(dict(ui='stock', version='2.57'))
        (self.root/'status/ui/choices.json').write_text(json.dumps(dict(schema=1, default=default, entries=entries)))

    def start(self, countdown=5000, launcher=True, **extra):
        env = dict(os.environ, DISC_BOOT_STATUS=str(self.root/'status'), DISC_BOOT_RUN=str(self.root/'run'),
                   DISC_MENU_FB=str(self.root/'fb0'), DISC_MENU_KEYS=str(self.root/'keys'), DISC_MENU_TOUCH=str(self.root/'touch'),
                   DISC_MENU_COUNTDOWN_MS=str(countdown), **extra)
        if launcher:
            env['DISC_BOOT_LAUNCHER'] = str(self.root/'launcher')
        process = subprocess.Popen([str(MENU)], env=env, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        return process

    def send(self, device, *events):
        with open(self.root/device, 'ab') as f:
            f.write(b''.join(events))

    def key(self, code):
        self.send('keys', event(1, code, 1), event(1, code, 0))

    def answer(self):
        path = self.root/'run/choice'
        return json.loads(path.read_text())['ui'] if path.exists() else None

    def pixel(self, x, y):
        """The panel's pixel at (x, y): the framebuffer holds the canvas turned 180 degrees, BGRX."""
        data = (self.root/'fb0').read_bytes()
        offset = ((PANEL - 1 - y) * PANEL + (PANEL - 1 - x)) * 4
        b, g, r = data[offset:offset + 3]
        return r, g, b

    def test_the_countdown_starts_the_default_and_hands_over(self):
        menu = self.start(countdown=600)
        time.sleep(0.3)
        self.assertEqual(self.pixel(180, 30), GROUND)
        self.assertEqual(self.pixel(70, 180), SELECTED, 'the default, beta, in the middle row')
        self.assertEqual(self.pixel(352, 180), ACCENT, 'the ring still holds the right half at mid countdown')
        self.assertEqual(self.pixel(8, 180), LINE, 'the left half has run down')
        self.assertEqual(menu.wait(timeout=10), 0)
        self.assertEqual(self.answer(), 'beta')
        self.assertEqual((self.root/'launched').read_text().strip(), str(self.root/'launcher'))
        self.assertEqual((self.root/'fds').read_text(), '', 'nothing the menu opened survives the hand-over')
        # The last frame: the chosen row in the selection's pill, the ring empty.
        self.assertEqual(self.pixel(70, 180), SELECTED)
        self.assertEqual(self.pixel(352, 180), LINE)

    def test_the_keys_choose_and_stop_the_countdown(self):
        menu = self.start(countdown=1500)
        time.sleep(0.2)
        self.key(VOLUME_DOWN)
        time.sleep(2.0)
        self.assertIsNone(menu.poll(), 'a key stops the countdown')
        self.assertEqual(self.pixel(70, 228), SELECTED, 'stock, below beta, is selected')
        self.key(VOLUME_UP)
        self.key(VOLUME_UP)
        self.key(PLAY)
        self.assertEqual(menu.wait(timeout=10), 0)
        self.assertEqual(self.answer(), 'alpha')

    def test_a_key_down_at_the_start_counts_after_its_release(self):
        menu = self.start(countdown=60000, DISC_MENU_HELD=str(PLAY))
        time.sleep(0.2)
        self.send('keys', event(1, PLAY, 1))
        time.sleep(0.5)
        self.assertIsNone(menu.poll(), 'Play still held from power-on chooses nothing')
        self.send('keys', event(1, PLAY, 0))
        self.key(PLAY)
        self.assertEqual(menu.wait(timeout=10), 0)
        self.assertEqual(self.answer(), 'beta')

    def test_a_touch_chooses_its_row(self):
        menu = self.start(countdown=60000)
        time.sleep(0.2)
        # The rows sit at y 132, 180, 228 on the screen; the panel reports coordinates turned 180 degrees.
        x, y = 150, 228
        self.send('touch', event(3, 0x35, PANEL - 1 - x), event(3, 0x36, PANEL - 1 - y), event(1, 0x14a, 1), event(0, 0, 0),
                  event(1, 0x14a, 0), event(0, 0, 0))
        self.assertEqual(menu.wait(timeout=10), 0)
        self.assertEqual(self.answer(), 'stock')

    def test_more_entries_than_rows_scroll_with_the_selection(self):
        self.choices(['a1', 'a2', 'a3', 'a4', 'a5'], 'a1')
        menu = self.start(countdown=60000)
        time.sleep(0.2)
        for _ in range(4):
            self.key(VOLUME_DOWN)
        time.sleep(0.3)
        self.assertEqual(self.pixel(70, 180), SELECTED, 'the selection stays in view, in the middle row')
        self.key(PLAY)
        self.assertEqual(menu.wait(timeout=10), 0)
        self.assertEqual(self.answer(), 'a5')

    def test_without_a_launcher_the_answer_stands(self):
        menu = self.start(countdown=100, launcher=False)
        self.assertEqual(menu.wait(timeout=10), 0)
        self.assertEqual(self.answer(), 'beta')
        self.assertFalse((self.root/'launched').exists())

    def test_nothing_to_offer_answers_nothing(self):
        (self.root/'status/ui/choices.json').write_text('{"schema":1,"default":"x","entries":[]}')
        menu = self.start(countdown=100)
        self.assertEqual(menu.wait(timeout=10), 1)
        self.assertIsNone(self.answer())

    def test_the_production_build_has_no_fixture_switches(self):
        data = PRODUCTION.read_bytes()
        for switch in (b'DISC_MENU_FB', b'DISC_MENU_KEYS', b'DISC_MENU_HELD', b'DISC_MENU_COUNTDOWN_MS'):
            self.assertNotIn(switch, data)
        self.assertIn(b'DISC_MENU_FB', MENU.read_bytes())


if __name__ == '__main__':
    unittest.main()
