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
VOLUME_UP, VOLUME_DOWN, PLAY, POWER_HOLD = 0xfb, 0xfc, 0xfa, 0x108


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
                            f'for fd in 3 4 5 6 7 8 9; do {{ true >&$fd; }} 2>/dev/null && echo $fd; done > "{self.root}/fds"\nexit 0\n')
        launcher.chmod(0o755)
        self.choices(['alpha', 'beta'], 'beta')

    def choices(self, names, default, titles=None, services=(), packages=(), staged=(), path=None):
        entries = [dict(ui=n, title=(titles or {}).get(n, n), version='1', confirmed=True) for n in names]
        entries.append(dict(ui='stock', version='2.57'))
        data = dict(schema=1, default=default, entries=entries, services=list(services), packages=list(packages), staged=list(staged))
        (path or self.root/'status/ui/choices.json').write_text(json.dumps(data))

    def program(self, after_install=None):
        """A stand-in for the boot program's commands: each is logged and answers ok; an installation
        is shown in install.json and leaves what boot then offers (after_install)."""
        script = self.root/'disc-boot'
        status = self.root/'status'
        script.write_text(f'''#!/bin/sh
echo "$*" >> "{self.root}/asked"
if [ "$1" = install ]; then
  printf '{{"schema":1,"state":"installing","done":0,"total":1,"current":"%s"}}' "$2" > "{status}/install.json"
  sleep 0.6
  [ -f "{self.root}/after-install.json" ] && cp "{self.root}/after-install.json" "{status}/ui/choices.json"
  printf '{{"schema":1,"state":"done","done":1,"total":1}}' > "{status}/install.json"
fi
echo '{{"ok":true,"note":"done"}}'
''')
        script.chmod(0o755)
        return dict(DISC_BOOT_PROGRAM=str(script))

    def asked(self, expected=None, timeout=5):
        """The commands the menu asked, once they are the expected ones (within the timeout)."""
        path = self.root/'asked'
        until = time.monotonic() + timeout
        while True:
            asked = path.read_text().splitlines() if path.exists() else []
            if expected is None or asked == expected or time.monotonic() >= until:
                return asked
            time.sleep(0.05)

    def keys(self, *codes, pause=0.08):
        for code in codes:
            self.key(code)
            time.sleep(pause)

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
        # Services and Packages follow the interfaces: the window keeps the selection in its middle row.
        self.assertEqual(self.pixel(70, 180), SELECTED, 'stock, below beta, is selected')
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

    def test_after_a_start_with_play_its_release_is_not_an_answer(self):
        """Stock's key driver reports Play's gestures when the key is let go: after a start with Play
        (boot.json's reason "recovery") they arrive once the menu runs, and answered the default on
        the player (2026-10-07). Play's gestures in the first 2 s the menu can answer are that
        release; Volume still moves the selection, and a later Play answers."""
        (self.root/'status/boot.json').write_text('{"schema":1,"mode":"platform","reason":"recovery"}')
        menu = self.start(countdown=60000)
        time.sleep(0.3)
        self.send('keys', event(1, 0x10d, 1), event(1, 0x10d, 0), event(1, PLAY, 1), event(1, PLAY, 0))
        self.key(VOLUME_UP)
        time.sleep(0.5)
        self.assertIsNone(menu.poll(), 'the release of the recovery\'s Play chooses nothing')
        self.assertEqual(self.pixel(70, 132), SELECTED, 'Volume still moves the selection to alpha')
        time.sleep(1.8)
        self.key(PLAY)
        self.assertEqual(menu.wait(timeout=10), 0)
        self.assertEqual(self.answer(), 'alpha')
        self.assertIn("Play's release after the recovery", menu.stderr.read())

    def install(self, state, done=0, total=0, current=None):
        (self.root/'status/install.json').write_text(json.dumps(dict(schema=1, state=state, done=done, total=total, current=current)))

    def test_an_installation_is_shown_and_then_what_it_installed_is_offered(self):
        """A start with Play (owner, 2026-10-07): boot installs from the card first; the menu shows it,
        asks nothing and counts nothing, then offers what is installed and counts down."""
        self.install('installing', 0, 2, 'disc-server')
        menu = self.start(countdown=300)
        time.sleep(1.0)
        self.key(PLAY)
        self.key(POWER_HOLD)
        time.sleep(0.5)
        self.assertIsNone(menu.poll(), 'no answer while boot installs, keys or not')
        self.assertIsNone(self.answer())
        self.assertEqual(self.pixel(180, 30), GROUND)
        self.choices(['alpha', 'beta', 'gamma'], 'gamma')
        self.install('done', 2, 2)
        self.assertEqual(menu.wait(timeout=10), 0)
        self.assertEqual(self.answer(), 'gamma', 'the list and the default as boot offers them after the installation')

    def test_the_power_key_held_answers_poweroff(self):
        menu = self.start(countdown=60000)
        time.sleep(0.3)
        self.key(POWER_HOLD)
        self.assertEqual(menu.wait(timeout=10), 0)
        self.assertEqual(self.answer(), 'poweroff')
        self.assertIn('disc-menu: key 0x108 1', menu.stderr.read(), 'each key in the output, to confirm the code on a player')

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

    # The menu's screens (owner, 2026-10-09: rows at the end of the list, Back first on each)

    def three_screens(self, **choices):
        self.choices(['diskos'], 'diskos', titles={'diskos': 'diskOS'},
                     services=[dict(name='disc-health', title='disc-health', version='2.57.7', autostart=True, removing=False),
                               dict(name='disc-network', title='disc-network', version='2.57.7', autostart=False, removing=False)],
                     packages=[dict(role='ui', name='diskos', title='diskOS', version='1.2.0', removing=False),
                               dict(role='service', name='disc-health', title='disc-health', version='2.57.7', removing=False)],
                     **choices)

    def test_services_turn_their_autostart_through_boot(self):
        self.three_screens()
        menu = self.start(countdown=60000, **self.program())
        time.sleep(0.2)
        # diskOS, FiiO, Services, Packages: Services is the third row.
        self.keys(VOLUME_DOWN, VOLUME_DOWN, PLAY, VOLUME_DOWN, PLAY, VOLUME_DOWN, PLAY)
        self.assertEqual(self.asked(['autostart disc-health off', 'autostart disc-network on']),
                         ['autostart disc-health off', 'autostart disc-network on'])
        self.assertIsNone(menu.poll(), 'nothing is chosen on the services screen')
        self.keys(VOLUME_UP, VOLUME_UP, PLAY, VOLUME_UP, VOLUME_UP, PLAY)   # Back, then diskOS
        self.assertEqual(menu.wait(timeout=10), 0)
        self.assertEqual(self.answer(), 'diskos')

    def test_a_removal_takes_a_second_play_and_everything_ours_a_third(self):
        self.three_screens()
        menu = self.start(countdown=60000, **self.program())
        time.sleep(0.2)
        # Packages (the fourth row): Back, diskOS, disc-health, Remove everything ours.
        self.keys(VOLUME_DOWN, VOLUME_DOWN, VOLUME_DOWN, PLAY, VOLUME_DOWN, PLAY)
        time.sleep(0.3)
        self.assertEqual(self.asked(), [], 'the first Play asks')
        self.assertEqual(self.pixel(70, 180), (0x4a, 0x2a, 0x22), 'the question in its warmer colour')
        self.keys(PLAY)
        self.assertEqual(self.asked(['remove ui diskos']), ['remove ui diskos'])
        self.keys(VOLUME_DOWN, VOLUME_DOWN, PLAY, PLAY)
        time.sleep(0.3)
        self.assertEqual(self.asked(), ['remove ui diskos'], 'everything ours takes two questions')
        self.keys(PLAY)
        self.assertEqual(self.asked(['remove ui diskos', 'remove-everything']), ['remove ui diskos', 'remove-everything'])
        self.assertEqual(menu.wait(timeout=10), 0)
        self.assertEqual(self.answer(), 'stock', 'then FiiO\'s own interface, with nothing of ours at the next start')

    def test_packages_waiting_on_the_card_hold_the_countdown_and_install_from_the_menu(self):
        waiting = dict(folder='service/disc-network', role='service', name='disc-network', title='disc-network', version='2.57.7', refused=None)
        refused = dict(folder='ui/broken', role='ui', name='broken', title='broken', version='1', refused='refused: bin/mq_ui has 7 bytes')
        self.three_screens(staged=[refused])
        menu = self.start(countdown=300)
        self.assertEqual(menu.wait(timeout=10), 0, 'a refused package holds nothing')
        self.three_screens(staged=[waiting, refused])
        self.three_screens(path=self.root/'after-install.json')
        menu = self.start(countdown=300, **self.program())
        time.sleep(1.0)
        self.assertIsNone(menu.poll(), 'the countdown stands while a package waits')
        # diskOS, FiiO, "1 on the card", Services, Packages: the third row opens Packages.
        self.keys(VOLUME_DOWN, VOLUME_DOWN, PLAY, VOLUME_DOWN, PLAY)
        self.assertEqual(self.asked(['install service/disc-network']), ['install service/disc-network'])
        time.sleep(1.5)
        self.keys(VOLUME_UP, PLAY, PLAY)   # Back, then diskOS
        self.assertEqual(menu.wait(timeout=10), 0)
        self.assertEqual(self.answer(), 'diskos')

    def test_the_production_build_has_no_fixture_switches(self):
        data = PRODUCTION.read_bytes()
        for switch in (b'DISC_MENU_FB', b'DISC_MENU_KEYS', b'DISC_MENU_HELD', b'DISC_MENU_COUNTDOWN_MS'):
            self.assertNotIn(switch, data)
        self.assertIn(b'DISC_MENU_FB', MENU.read_bytes())


if __name__ == '__main__':
    unittest.main()
