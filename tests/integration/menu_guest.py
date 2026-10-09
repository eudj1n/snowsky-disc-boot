#!/usr/bin/env python3
"""disc-menu on the guest (plan, stage 3b), inside the emulator's container.

    python3 scripts/guest.py run -- python3 -B /boot/tests/integration/menu_guest.py \\
        --menu /boot/work/<run>/disc-menu.zip --output /work/menu-guest.json

The real boot menu with two probe ui packages and a probe service, driven as a player is: Play
installs them with the menu; the menu's screen is read from the framebuffer (the page it panned
to); the emulator's buttons and touch panel choose; the countdown starts the default. Then its
other screens (plan, stage 7) with the boot program's commands: a service's autostart off, a ui
package removed at the hand-over, a package waiting on the card holding the countdown and
installed from the menu, and everything of ours gone at the next start. Frames are kept as raw
pages in /work (menu-<name>.raw, the panel's view turned 180 degrees back).
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

ONE, TWO, THREE, SERVICE, MENU = 'menu-probe-one', 'menu-probe-two', 'menu-probe-three', 'menu-probe-service', 'disc-menu'
VOLUME_UP, VOLUME_DOWN, PLAY = 0xfb, 0xfc, 0xfa
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
    yield bg.probe(work/SERVICE, '1', role='service', name=SERVICE)


def press(buttons, *codes, pause=0.5):
    """The player's keys, one after another, each given the time the menu (and the boot program it
    may run under qemu-user) takes."""
    for code in codes:
        buttons.pulse(code)
        time.sleep(pause)


def gone(path):
    return not bg.guest(f'ls -d {path} 2>/dev/null').strip()


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
    # FiiO's own first (owner, 2026-10-09), then the UIs; the default is the first UI installed.
    assert [e['ui'] for e in choices['entries']] == ['stock', ONE, TWO] and choices['default'] == ONE, choices
    assert pixel(first, 70, 180) == (0x3a, 0x35, 0x30), 'the default, in the second row, in the pill'
    buttons.pulse(0xfc)
    time.sleep(1.5)
    moved = frame('volume-down')
    # Services and Packages follow the UIs (stage 7): the window moves with the selection, which stays in the middle row.
    assert pixel(moved, 70, 180) == (0x3a, 0x35, 0x30) and moved != first, 'the list moved under the pill'
    # After a start with Play the menu takes no Play gesture in its first 2 s (the recovery's own
    # release, reported when the key is let go): this Play comes after them.
    time.sleep(1.0)
    buttons.pulse(0xfa)
    keys_taken()
    ui = bg.ui_runs(TWO)
    choice = bg.guest_json('/run/disc-boot/ui/choice.json')
    assert (choice['ui'], choice['by']) == (TWO, 'menu'), choice
    assert menu_status()['state'] == 'answered'
    booted()
    bg.step('play installs and the keys choose', menu=status, choices=choices, choice=choice, ui=ui)
    # 2. A plain power-on: the menu starts on its last answer, the second UI, and the countdown
    #    starts it (owner, 2026-10-07); no player ran, so the pair is not restarted.
    restarts = bg.pair_restarts()
    bg.power('off')
    booted = power_on()
    asking()
    choices = bg.guest_json('/run/disc-boot/ui/choices.json')
    assert choices['default'] == TWO, choices
    counting = frame('countdown')
    assert pixel(counting, 352, 180) == (0xff, 0x79, 0x5a), 'the ring runs down from the right'
    assert pixel(counting, 70, 180) == (0x3a, 0x35, 0x30), 'the last answer, the third entry, in the pill (middle row)'
    # The player starts 2 s after the menu and waits for its choice.
    waited = soon(lambda: run_json('ui/player.json'), lambda p: p['launch'] in ('waiting', 'package'), 'the player starts', 10)
    assert waited['launch'] == 'waiting', waited
    booted()
    ui = bg.ui_runs(TWO)
    choice = bg.guest_json('/run/disc-boot/ui/choice.json')
    assert (choice['ui'], choice['by']) == (TWO, 'menu'), choice
    player = bg.wait(lambda: bg.guest_json('/run/disc-boot/ui/player.json'), lambda p: p['launch'] == 'package', 'the player after the choice', 120)
    assert bg.pair_restarts() == restarts, ('stock restarted the pair', restarts, bg.pair_restarts())
    bg.step('the countdown starts the last answer', choices=choices, choice=choice, player=player, ui=ui)
    # 3. A touch on the first UI's row picks it, away from the default: the list starts at it
    #    (the second UI, the default, is in the middle row), so it is the first row.
    bg.power('off')
    booted = power_on()
    asking()
    touch.tap(150, 132)
    booted()
    ui = bg.ui_runs(ONE)
    choice = bg.guest_json('/run/disc-boot/ui/choice.json')
    assert (choice['ui'], choice['by']) == (ONE, 'menu'), choice
    # Confirmed after its 180 s: the starts before were short (each a few seconds), and the boot-loop
    # guard counts the platform starts until the chosen UI is confirmed.
    confirmed = bg.wait(lambda: bg.guest_json('/run/disc-boot/ui.json'), lambda u: u['state'] == 'confirmed', f'{ONE} confirmed', 420)
    assert bg.guest_json('/usr/data/disc-boot/state.json')['unconfirmed'] == 0
    bg.step('a touch chooses', choice=choice, ui=ui, confirmed=confirmed)
    screens(buttons, work)
    bg.evidence['status'] = 'passed'
    Path(output).write_text(json.dumps(bg.evidence, indent=2) + '\n')
    print(json.dumps({'status': 'passed', 'steps': [s['step'] for s in bg.evidence['steps']]}, indent=2))


def screens(buttons, work):
    """The menu's other screens (plan, stage 7; owner's decisions of 2026-10-09)."""
    # 4. The service's autostart off, then the first UI removed (its second Play) and the second chosen:
    #    the removal applies at the hand-over. Rows: FiiO, One (the last answer), Two, Services, Packages.
    service_state = f'/usr/data/disc-boot/service/{SERVICE}/state.json'
    bg.power('off')
    booted = power_on()
    asking()
    press(buttons, VOLUME_DOWN, VOLUME_DOWN, PLAY, VOLUME_DOWN, PLAY)       # Services, then its row: autostart off
    soon(lambda: bg.guest_json(service_state), lambda s: s.get('autostart') is False, 'autostart off', 30)
    press(buttons, VOLUME_UP, PLAY)                                         # Back
    press(buttons, *([VOLUME_DOWN] * 4), PLAY)                              # Packages: Back, One, Two, the service, everything
    press(buttons, VOLUME_DOWN, PLAY, PLAY)                                 # One: asked, then removed
    soon(lambda: bg.guest(f'ls /usr/data/disc-boot/ui/{ONE}/remove 2>/dev/null').strip() or None, bool, 'the removal marked', 30)
    press(buttons, VOLUME_UP, PLAY)                                         # Back: FiiO, Two, Services, Packages
    press(buttons, VOLUME_DOWN, PLAY, pause=0.2)                            # Two
    keys_taken()
    booted()
    ui = bg.ui_runs(TWO)
    assert gone(f'/usr/data/disc-boot/ui/{ONE}') and gone(f'/usr/data/disc-boot/data/{ONE}'), 'removed with its data at the hand-over'
    bg.step('autostart off and a ui package removed', ui=ui, service=bg.guest_json(service_state))
    # 5. A package waiting on the card holds the countdown; installed from the menu, it is offered at once.
    #    The service's autostart is off from this start on.
    bg.power('off')
    with bg.card() as root:
        bg.package.stage(bg.probe(work/THREE, '1', role='ui', name=THREE), root, profile=bg.PROFILE)
    booted = power_on()
    asking()
    time.sleep(7)
    assert menu_status()['state'] == 'asking' and bg.guest_json('/run/disc-boot/ui/choice.json')['by'] != 'menu', 'the countdown stands'
    disabled = soon(lambda: run_json(f'service/{SERVICE}.json'), lambda s: s['state'] == 'disabled', 'the service not started', 120)
    press(buttons, VOLUME_DOWN, PLAY)                                       # "1 on the card": Packages
    press(buttons, VOLUME_DOWN, PLAY, pause=1.0)                            # Three: installed by boot
    installed = soon(lambda: run_json('ui/choices.json'), lambda c: not c['staged'] and THREE in [e['ui'] for e in c['entries']],
                     'installed from the menu', 300)
    # The menu takes keys again once boot's installation has ended (its program exited after "done").
    soon(lambda: run_json('install.json'), lambda i: i['state'] == 'done', 'the installation done', 60)
    time.sleep(3.0)
    press(buttons, VOLUME_UP, PLAY)                                         # Back: FiiO, Three, Two (by name), Services, Packages
    press(buttons, VOLUME_DOWN, PLAY, pause=0.2)                            # Three
    keys_taken()
    booted()
    ui = bg.ui_runs(THREE)
    bg.step('a package on the card holds the countdown and installs from the menu', service=disabled, choices=installed, ui=ui)
    # 6. Everything of ours: the third Play asks boot; FiiO's interface runs; at the next start nothing
    #    of ours is left, the card's .disc folder neither.
    bg.power('off')
    booted = power_on()
    asking()
    press(buttons, VOLUME_DOWN, VOLUME_DOWN, VOLUME_DOWN, PLAY)             # FiiO, Three (the last answer), Two, Services, Packages
    press(buttons, *([VOLUME_DOWN] * 4), PLAY, PLAY)                        # Back, Three, Two, the service, everything: asked twice
    press(buttons, PLAY, pause=0.2)
    keys_taken()
    soon(lambda: bg.guest('ls /usr/data/disc-boot/remove-everything 2>/dev/null').strip() or None, bool, 'everything marked', 30)
    booted()
    assert bg.wait(lambda: bg.stock_ui_runs() or None, bool, 'FiiO\'s interface', 120)
    bg.power('off')
    bg.power('on')
    assert bg.wait(lambda: bg.stock_ui_runs() or None, bool, 'FiiO\'s interface with nothing of ours', 120)
    left = sorted(bg.guest('ls -A /usr/data/disc-boot').split())
    assert set(left) <= {'.lock', 'boot.log'}, left
    log = bg.guest('cat /usr/data/disc-boot/boot.log')
    bg.power('off')
    with bg.card() as root:
        card_left = (root/'.disc').exists()
    assert not card_left and 'removed everything of the boot layer' in log, (card_left, log[-500:])
    bg.step('everything of ours removed at the next start', left=left)


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
