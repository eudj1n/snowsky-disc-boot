#!/usr/bin/env python3
"""disc-menu on the guest (plan, stage 3b), inside the emulator's container.

    python3 scripts/guest.py run -- python3 -B /boot/tests/integration/menu_guest.py \\
        --menu /boot/work/<run>/disc-menu.zip --output /work/menu-guest.json

The real boot menu with two probe ui packages, driven as a player is: Play installs them
with the menu; the menu's screen is read from the framebuffer (the page it panned to);
the emulator's buttons and touch panel choose; the countdown starts the default. Frames
are kept as raw pages in /work (menu-<name>.raw, the panel's view turned 180 degrees back).
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import boot_guest as bg  # noqa: E402
from emulator.runtime.keys import Buttons, Device  # noqa: E402
from emulator.runtime.touch import Touch  # noqa: E402

ONE, TWO, MENU = 'menu-probe-one', 'menu-probe-two', 'disc-menu'
PANEL = 360


def frame(name):
    """The page the menu shows, as the panel shows it, kept for the record."""
    page = (bg.ROOTFS/'emu/fb-live').read_bytes()[:1]
    index = page[0] if page and page[0] in (0, 1) else 0
    data = (bg.ROOTFS/'dev/fb0').read_bytes()[index * PANEL * PANEL * 4:(index + 1) * PANEL * PANEL * 4]
    Path(f'/work/menu-{name}.raw').write_bytes(data)
    return data


def pixel(data, x, y):
    """The panel's pixel at (x, y): the framebuffer holds the screen turned 180 degrees, BGRX."""
    offset = ((PANEL - 1 - y) * PANEL + (PANEL - 1 - x)) * 4
    b, g, r = data[offset:offset + 3]
    return r, g, b


def menu_status():
    return bg.guest_json('/run/disc-boot/menu.json')


def run_json(name):
    """A status file read from the container (no guest command: the menu's 5 s are short)."""
    try:
        return json.loads((bg.ROOTFS/'run/disc-boot'/name).read_text())
    except (OSError, ValueError):
        return None


def soon(read, predicate, label, timeout):
    until, value = time.monotonic() + timeout, None
    while time.monotonic() < until:
        value = read()
        if value is not None and predicate(value):
            return value
        time.sleep(0.2)
    raise AssertionError(f'{label}: {value}')


def asking():
    """The menu runs and has drawn: its status says so, and its frame is on the screen."""
    status = soon(lambda: run_json('menu.json'), lambda m: m['state'] == 'asking', 'the menu asks', 300)
    soon(lambda: frame('probe'), lambda data: pixel(data, 70, 132) != (0, 0, 0), 'the menu drawn', 30)
    return status


def keys_taken():
    """The keys the menu took are gone for later readers. On the player a program that opens the
    key device sees only the presses after it; the guest's device is a file the emulator's buttons
    append to and a reader reads from its start (emptied at each power-on). Stock's player, which
    now starts after the menu's answer, would take the menu's Volume - and Play again, and on the
    guest it dies of them in its start, at every restart (2026-10-07). The player starts 2 s after
    the answer; the release is written 0.12 s after the press the menu answers."""
    (bg.ROOTFS/'dev/input/event0').write_bytes(b'')


def power_on(hold=''):
    """Power on in the background: the emulator's power-on returns once a UI is ready, which with
    the menu is after its choice, so the test watches the menu while the boot goes on."""
    failure = []

    def boot():
        try:
            bg.power('on', hold=hold)
        except Exception as error:      # noqa: BLE001 (reported by join)
            failure.append(error)
    thread = threading.Thread(target=boot)
    thread.start()

    def join():
        thread.join()
        if failure:
            raise failure[0]
    return join


def probes(work):
    for name, title in ((ONE, 'Probe One'), (TWO, None)):
        folder = bg.probe(work/name, '1', role='ui', name=name)
        if title:
            bg.package.describe(folder, name, '1', 'ui', 'bin/mq_ui', ready=30, profiles=[bg.PROFILE], player='bin/player', title=title)
        yield folder


def run(menu, output):
    bg.evidence.clear()
    bg.evidence.update(profile=bg.PROFILE, steps=[])
    work = Path(tempfile.mkdtemp())
    buttons, touch = Buttons(bg.ROOTFS, Device(bg.ROOTFS)), Touch(bg.ROOTFS)
    bg.power('off')
    with bg.card() as root:
        for folder in probes(work):
            bg.package.stage(folder, root, profile=bg.PROFILE)
        bg.package.stage(menu, root, profile=bg.PROFILE)
    # 1. Play installs both UIs and the menu before anything is offered (plan, stage 4c): the menu
    #    then asks, no player having run, so the player waits for its choice; Volume - and Play
    #    choose the second UI. The menu's first frame comes right after the installation, so the
    #    test watches it while the power-on goes on, within its 5 s countdown.
    booted = power_on('play')
    status = asking()
    first = frame('after-play')
    choices = bg.guest_json('/run/disc-boot/ui/choices.json')
    assert [e['ui'] for e in choices['entries']] == [ONE, TWO, 'stock'] and choices['default'] == ONE, choices
    assert pixel(first, 70, 132) == (0x3a, 0x35, 0x30), 'the default, in the first row, in the pill'
    buttons.pulse(0xfc)
    time.sleep(1.5)
    moved = frame('volume-down')
    assert pixel(moved, 70, 180) == (0x3a, 0x35, 0x30) and pixel(moved, 70, 132) != (0x3a, 0x35, 0x30), 'the pill moved down'
    buttons.pulse(0xfa)
    keys_taken()
    ui = bg.ui_runs(TWO)
    choice = bg.guest_json('/run/disc-boot/ui/choice.json')
    assert (choice['ui'], choice['by']) == (TWO, 'menu'), choice
    assert menu_status()['state'] == 'answered'
    booted()
    bg.step('play installs and the keys choose', menu=status, choices=choices, choice=choice, ui=ui)
    # 2. A plain power-on: the countdown starts the default; no player ran, so the pair is not restarted.
    restarts = bg.pair_restarts()
    bg.power('off')
    booted = power_on()
    asking()
    counting = frame('countdown')
    assert pixel(counting, 352, 180) == (0xff, 0x79, 0x5a), 'the ring runs down from the right'
    # The player starts 2 s after the menu and waits for its choice.
    waited = soon(lambda: run_json('ui/player.json'), lambda p: p['launch'] in ('waiting', 'package'), 'the player starts', 10)
    assert waited['launch'] == 'waiting', waited
    booted()
    ui = bg.ui_runs(ONE)
    choice = bg.guest_json('/run/disc-boot/ui/choice.json')
    assert (choice['ui'], choice['by']) == (ONE, 'menu'), choice
    player = bg.wait(lambda: bg.guest_json('/run/disc-boot/ui/player.json'), lambda p: p['launch'] == 'package', 'the player after the choice', 120)
    assert bg.pair_restarts() == restarts, ('stock restarted the pair', restarts, bg.pair_restarts())
    bg.step('the countdown starts the default', choice=choice, player=player, ui=ui)
    # 3. A touch on the second row picks it.
    bg.power('off')
    booted = power_on()
    asking()
    touch.tap(150, 180)
    booted()
    ui = bg.ui_runs(TWO)
    choice = bg.guest_json('/run/disc-boot/ui/choice.json')
    assert (choice['ui'], choice['by']) == (TWO, 'menu'), choice
    bg.step('a touch chooses', choice=choice, ui=ui)
    bg.power('off')
    bg.evidence['status'] = 'passed'
    Path(output).write_text(json.dumps(bg.evidence, indent=2) + '\n')
    print(json.dumps({'status': 'passed', 'steps': [s['step'] for s in bg.evidence['steps']]}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--menu', type=Path, required=True, help='The disc-menu package (zip or folder)')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        run(args.menu, args.output)
    except BaseException as error:
        bg.evidence['status'] = f'failed: {error}'
        bg.evidence['menu'] = menu_status()
        bg.evidence['choice'] = bg.guest_json('/run/disc-boot/ui/choice.json')
        Path(args.output).write_text(json.dumps(bg.evidence, indent=2, default=str) + '\n')
        raise
