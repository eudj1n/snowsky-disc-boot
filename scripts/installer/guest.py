"""The emulator's guest as the player (install.py --guest): the whole run without a player.

A disposable guest of the image (scripts/guest.py, with its record in the run folder) stands in
for the player after the write: it takes the card the installer staged, starts with Play held
as the owner would start the player, and is followed until the boot layer has done its part:
the recovery installed the packages, the menu answered, the service was confirmed. The card's
.disc/boot/result.json is then read with the guest off, and the guest is removed (its folder in
the run folder keeps the card's media). Nothing here touches a player or a real card.
"""
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
# States a role does not leave in this boot: waiting longer would not change them.
SERVICE_ENDS = ('rolled-back', 'stock-mode', 'absent')
MENU_ENDS = ('absent',)


class GuestError(Exception):
    pass


class Guest:
    def __init__(self, work, reference, image, ota, run=subprocess.run, clock=time):
        self.work, self.reference, self.image, self.ota = Path(work), reference, image, ota
        self.run, self.clock = run, clock
        self.state = self.work/'guest/guest.json'
        self.log = self.work/'guest.log'

    def tool(self, *args):
        command = [sys.executable, '-B', str(ROOT/'scripts/guest.py'), '--state', str(self.state), *[str(a) for a in args]]
        self.work.mkdir(parents=True, exist_ok=True)
        result = self.run(command, cwd=str(ROOT), capture_output=True, text=True)
        with open(self.log, 'a') as log:
            log.write('$ ' + ' '.join(command[2:]) + '\n' + (result.stdout or '') + (result.stderr or ''))
        return result

    def need(self, result, what):
        if result.returncode:
            raise GuestError(f'{what} failed: ' + ((result.stderr or result.stdout).strip().splitlines() or ['no output'])[-1])
        return result

    def up(self):
        """The guest of the image, off, with the emulator's media on its card."""
        if self.reference is None:
            raise GuestError('the emulator checkout is needed for the guest (--emulator)')
        self.need(self.tool('up', '--reference', self.reference, '--image', self.image, '--ota', self.ota, '--name', 'disc-install'),
                  'the guest')
        self.need(self.tool('power', 'off'), 'the power-off')

    def card(self, tree):
        """The staged card's files over the guest's card."""
        self.need(self.tool('put', '--tree', tree), 'the card')

    def start(self):
        """On with Play held; the power-on returns once a UI draws."""
        self.need(self.tool('power', 'on', '--hold', 'play'), 'the power-on with Play')

    def status(self):
        result = self.need(self.tool('status'), 'the status')
        try:
            return json.loads(result.stdout)
        except ValueError:
            raise GuestError('the status is not JSON')

    def follow(self, roles, timeout=600, pause=5, show=lambda status: None):
        """The status until each installed role is where the boot layer leaves it: the menu
        answered, the service confirmed (its 180 s). Returns the last status and whether it got there."""
        until, status = self.clock.monotonic() + timeout, {}
        while True:
            status = self.status()
            show(status)
            service, menu = status.get('service') or {}, status.get('menu') or {}
            done = (('service' not in roles or service.get('state') == 'confirmed')
                    and ('menu' not in roles or menu.get('state') == 'answered'))
            ended = (('service' in roles and service.get('state') in SERVICE_ENDS)
                     or ('menu' in roles and menu.get('state') in MENU_ENDS))
            if done or ended or self.clock.monotonic() >= until:
                return status, done
            self.clock.sleep(pause)

    def result(self):
        """The recovery's result.json, read from the card with the guest off."""
        self.need(self.tool('power', 'off'), 'the power-off')
        result = self.need(self.tool('read', '.disc/boot/result.json'), 'reading result.json')
        try:
            return json.loads(result.stdout)
        except ValueError:
            raise GuestError('result.json is not JSON')

    def down(self):
        if self.state.exists():
            self.tool('down')
