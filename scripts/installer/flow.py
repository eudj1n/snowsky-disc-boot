"""The installer's steps (plan, stage 4): check the computer, the firmware and its image, the
packages and apps from the catalogs, the card. The player's USB Boot write and the first boot
follow in the next parts; --dry-run stages into a folder of its own and writes nothing else."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import catalog
from firmware_profile import load_profile
from installer import card as cards
from installer import device as devices
from installer import tui

ROOT = Path(__file__).resolve().parents[2]
STEPS = ['Check this computer', 'Firmware and image', 'Packages', 'The card', 'The player (USB Boot)', 'First boot']


class Stop(Exception):
    pass


class Installer:
    def __init__(self, args, screen=None):
        self.args, self.screen = args, screen or tui.Screen()
        self.interactive = not args.yes and screen is None and sys.stdin.isatty() and self.screen.look != 'plain'
        self.work = Path(args.work or ROOT/'work'/time.strftime('install-%Y%m%d-%H%M%S'))
        self.report = dict(started=time.strftime('%Y-%m-%dT%H:%M:%S'), dryRun=args.dry_run, steps=[])
        self.step = 0

    # The screen

    def frame(self, *body):
        s = self.screen
        s.clear()
        s.blank()
        s.line(('  ● ', tui.ACCENT), ('S N O W S K Y   D I S C', tui.MUTED), ('   install' + ('  ·  dry run' if self.args.dry_run else ''), tui.MUTED))
        s.blank()
        s.steps(STEPS, self.step)
        s.blank()
        for draw in body:
            draw()
        s.blank()
        s.flush()

    def say(self, title, lines=(), fg=tui.INK):
        self.frame(lambda: self.screen.label(title), lambda: [self.screen.text(l, fg) for l in lines])

    def choose(self, title, rows, marks=None, hint='Space to mark  ·  Enter to go on'):
        """A list in the menu's look; with marks, several may be chosen. Returns the marks or the row."""
        cursor = 0
        while True:
            def body():
                self.screen.label(title)
                self.screen.blank()
                for k, (name, detail) in enumerate(rows):
                    self.screen.row(name, detail, on=k == cursor, mark=None if marks is None else marks[k])
                self.screen.blank()
                self.screen.text(hint, tui.MUTED)
            self.frame(body)
            if not self.interactive:
                return marks if marks is not None else cursor
            key = tui.read_key()
            if key == 'up':
                cursor = max(0, cursor - 1)
            elif key == 'down':
                cursor = min(len(rows) - 1, cursor + 1)
            elif key == 'space' and marks is not None:
                marks[cursor] = not marks[cursor]
            elif key == 'enter':
                return marks if marks is not None else cursor
            elif key == 'quit':
                raise Stop('stopped')

    def choose_groups(self, title, groups, hint='Space to mark  ·  Enter to go on'):
        """Lists under their groups' headings (the menu's look). In a group of one at most, a mark
        clears the group's other mark; it may also be left empty. Returns each group's marks."""
        flat = [(g, k) for g, group in enumerate(groups) for k in range(len(group['rows']))]
        cursor = 0
        while True:
            def body():
                self.screen.label(title)
                for g, group in enumerate(groups):
                    self.screen.blank()
                    self.screen.group(group['label'], 'one at most' if group['single'] else 'any number')
                    for k, (name, detail) in enumerate(group['rows']):
                        self.screen.row(name, detail, on=flat[cursor] == (g, k), mark=group['marks'][k])
                self.screen.blank()
                self.screen.text(hint, tui.MUTED)
            self.frame(body)
            if not self.interactive:
                return [group['marks'] for group in groups]
            key = tui.read_key()
            if key == 'up':
                cursor = max(0, cursor - 1)
            elif key == 'down':
                cursor = min(len(flat) - 1, cursor + 1)
            elif key == 'space':
                g, k = flat[cursor]
                marks, on = groups[g]['marks'], not groups[g]['marks'][k]
                if groups[g]['single'] and on:
                    marks[:] = [False] * len(marks)
                marks[k] = on
            elif key == 'enter':
                return [group['marks'] for group in groups]
            elif key == 'quit':
                raise Stop('stopped')

    def ask(self, title, prompt, default=None):
        if not self.interactive:
            if default is None:
                raise Stop(f'{title}: give it on the command line')
            return default
        self.say(title, [prompt + (f' [{default}]' if default else '')])
        answer = input('  > ').strip()
        return answer or default

    def confirm(self, title, lines, word):
        """An irreversible step goes on only when the word is typed."""
        if not self.interactive:
            if not self.args.yes:
                raise Stop(f'{title}: confirm with --yes')
            return
        self.say(title, list(lines) + [f'Type {word} to go on, anything else to stop.'])
        if input('  > ').strip() != word:
            raise Stop('stopped before ' + title.lower())

    def done(self, name, **facts):
        self.report['steps'].append(dict(step=name, **facts))
        self.step += 1

    # The steps

    def check(self):
        facts = dict(python=sys.version.split()[0])
        # Docker and the emulator's checkout build the image; an image built before needs neither.
        facts['docker'] = bool(not self.args.image and shutil.which('docker')
                               and subprocess.run(['docker', 'info'], capture_output=True).returncode == 0)
        facts['emulator'] = str(self.emulator()) if self.emulator() else None
        problems = []
        if sys.version_info < (3, 11):
            problems.append('Python 3.11 or later is needed.')
        if not self.args.image and not facts['docker']:
            problems.append('Docker must run to build the image (or give a built one with --image).')
        if not self.args.image and not facts['emulator']:
            problems.append('The emulator checkout builds the image (--emulator).')
        found = [f'Python {facts["python"]}'] + (['An image built before: no build'] if self.args.image else
                                                 ['Docker ' + ('runs' if facts['docker'] else 'does not run'),
                                                  'Emulator ' + (facts['emulator'] or 'not found')])
        self.say('Check this computer', found + problems)
        if problems:
            raise Stop(' '.join(problems))
        self.done('check', **facts)

    def emulator(self):
        candidate = Path(self.args.emulator or os.environ.get('DISC_EMULATOR', ROOT.parent/'snowsky-disc-server/work/emulator-ref'))
        return candidate if (candidate/'emulator').is_dir() else None

    def firmware(self):
        profile = load_profile()
        if self.args.image:
            image = Path(self.args.image)
            if not image.is_file():
                raise Stop(f'{image} is not a built image')
            self.say('Firmware and image', [f'The image {image.name}, built before.'])
            self.done('firmware', image=str(image), profile=profile['version'])
            return image
        ota = Path(self.ask('Firmware and image', f'The folder of FiiO\'s {profile["version"]} update (main_os/ota_v...)', self.args.ota))
        if not ota.is_dir():
            raise Stop(f'{ota} is not the update\'s folder')
        out = self.work/'image'
        out.parent.mkdir(parents=True, exist_ok=True)
        self.say('Firmware and image', [f'Building the image from {ota.name} with the boot layer. This takes a few minutes.'])
        revision = subprocess.check_output(['git', '-C', str(self.emulator()), 'rev-parse', '--short=7', 'HEAD'], text=True).strip()
        command = ['docker', 'run', '--rm', '--network', 'none', '-e', 'PYTHONPATH=/repo', '-v', f'{self.emulator()}:/repo:ro',
                   '-v', f'{ota}:/ota:ro', '-v', f'{ROOT}:/src:ro', '-v', f'{out.parent}:/out', '--entrypoint', 'python3',
                   f'snowsky-disc-qemu-ci:{revision}', '-B', '/src/scripts/deployment/build_candidate.py', '--ota', '/ota',
                   '--console', '/src/build/mips/disc-usb-console', '--boot', '/src/build/mips/disc-boot', '--output', f'/out/{out.name}']
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise Stop('the image was not built: ' + (result.stderr.strip().splitlines() or ['no output'])[-1])
        image = next(out.glob('disc-boot-v*-review-only.bin'))
        self.done('firmware', image=str(image), ota=str(ota), profile=profile['version'])
        return image

    def packages(self):
        entries = catalog.load(self.args.catalog or catalog.CATALOG, kind='packages')['entries']
        places = [Path(p) for p in self.args.packages_from]
        wanted = set(self.args.package or [])
        unknown = wanted - {e['name'] for e in entries}
        if unknown:
            raise Stop(f'not in the catalog: {", ".join(sorted(unknown))}')
        # By role, as the boot layer takes them: one service and one menu at most, any number of UIs.
        groups = []
        for role, label, single in (('service', 'Service', True), ('menu', 'Boot menu', True), ('ui', 'UIs', False)):
            members = [e for e in entries if e['role'] == role]
            if not members:
                continue
            marks = [e['name'] in wanted if wanted else e['default'] for e in members]
            if single and sum(marks) > 1:
                raise Stop(f'one {label.lower()} at most: {", ".join(e["name"] for e, on in zip(members, marks) if on)}')
            groups.append(dict(label=label, single=single, entries=members, marks=marks,
                               rows=[(e.get('title') or e['name'], e['version']) for e in members]))
        self.choose_groups('Packages', groups)
        chosen = [e for group in groups for e, on in zip(group['entries'], group['marks']) if on]
        folders = {}
        for k, entry in enumerate(chosen):
            self.frame(lambda: self.screen.label('Packages'), lambda: self.screen.progress(f'{entry["name"]} {entry["version"]}', k / len(chosen)))
            try:
                folders[entry['name']] = catalog.fetch(entry, self.work/'packages'/entry['name'], places, self.args.download)
            except catalog.CatalogError as error:
                raise Stop(f'{entry["name"]}: {error}')
        apps, offered = [], cards.app_entries(folders['disc-server']) if 'disc-server' in folders else []
        if 'disc-server' in folders and not offered:
            self.say('Apps of the server', ['This server package offers no apps (it carries no catalog/apps.json).'], tui.MUTED)
        if offered:
            wanted_apps = set(self.args.app or [])
            app_marks = [a['name'] in wanted_apps if wanted_apps else a['default'] for a in offered]
            app_marks = self.choose('Apps of the server', [(a['name'], a['version']) for a in offered], app_marks)
            apps = [a for a, on in zip(offered, app_marks) if on]
        self.done('packages', packages=[dict(name=e['name'], version=e['version']) for e in chosen],
                  apps=[dict(name=a['name'], version=a['version']) for a in apps])
        return list(folders.values()), apps

    def card(self, folders, apps):
        if self.args.dry_run:
            target = self.work/'card'
            target.mkdir(parents=True, exist_ok=True)
        else:
            path = self.ask('The card', 'Where the player\'s card is mounted', self.args.card)
            try:
                target = cards.card_ok(path)
            except cards.CardError as error:
                raise Stop(str(error))
            self.confirm('The card', [f'On {target}: {len(folders)} package(s) for the recovery with Play, {len(apps)} app(s) in Apps/, '
                                      'and the USB console\'s marker.'], 'CARD')
        profile = load_profile()['version']
        staged = cards.stage_packages(folders, target, profile)
        with tempfile.TemporaryDirectory() as temp:
            placed = cards.stage_apps(apps, target, [Path(p) for p in self.args.packages_from], self.args.download, temp)
        marker = cards.write_marker(target)
        self.say('The card', [f'{s["role"]}: {s["name"]} {s["version"]}' for s in staged] +
                 [f'app: {a["name"]} {a["version"]}' for a in placed] + [f'console marker: {marker.relative_to(target)}'])
        self.done('card', card=str(target), packages=staged, apps=placed, marker=str(marker))
        return target

    def faults(self):
        """--fault no-device | bad-blocks=81,90 | write-stops=5 | readback-flip=123 (simulated player)."""
        faults = {}
        for fault in self.args.fault or []:
            name, _, value = fault.partition('=')
            if name == 'no-device':
                faults[name] = True
            elif name == 'bad-blocks':
                faults[name] = [int(v) for v in value.split(',') if v]
            elif name in ('write-stops', 'readback-flip'):
                faults[name] = int(value)
            else:
                raise Stop(f'unknown fault {fault}')
        return faults

    def device(self):
        geometry = devices.Geometry.for_profile(load_profile())
        if self.args.simulate_small:
            geometry = geometry.scaled(blocks=64, start=8, logical=16, reserve=4)
        folder = Path(self.args.simulate) if self.args.simulate != 'run' else self.work/'simulated-player'
        return devices.Simulated(folder, geometry, self.faults())

    def progress(self, title, label):
        last = [-1]

        def show(fraction):
            if int(fraction * 50) != last[0]:
                last[0] = int(fraction * 50)
                self.frame(lambda: self.screen.label(title), lambda: self.screen.progress(label, fraction))
        return show

    def player(self, image, restore=False):
        title = 'The player (USB Boot)'
        if not self.args.simulate:
            self.say(title, [
                f'The image is ready: {Path(image).name}.',
                'Writing it into the player through USB Boot (backup, write, readback, every byte compared) is the '
                'installer\'s next part; until then it is the reviewed operator procedure, docs/build-and-flash.md. '
                '--simulate runs this step against a simulated player.'])
            self.done('player', image=str(image), written=False)
            return
        player = self.device()
        self.say(title, ['Hold Volume Down and connect the cable to this computer (a simulated player here).'])
        try:
            info = player.identify()
            self.say(title, [f'{info["chip"]}, {info["blocks"]} blocks, {len(info["badBlocks"])} bad.'])
            backup = player.backup(self.work/'backup', self.progress(title, 'Backup of the whole NAND'))
            what = 'stock\'s rootfs' if restore else 'the image with the boot layer'
            self.confirm(title, [f'The backup is {backup["data"]} (SHA-256 {backup["sha256"][:12]}…).',
                                 f'Next: write {what} into the primary rootfs, read it back and compare every byte.'],
                         'RESTORE' if restore else 'WRITE')
            player.write(image, self.progress(title, 'Writing ' + Path(image).name))
            readback = player.readback(self.work/'readback.bin', self.progress(title, 'Reading it back'))
        except devices.DeviceError as error:
            raise Stop(f'{error}. The backup of this run is in {self.work/"backup"}; nothing was retried.')
        differs = devices.compare(image, readback)
        if differs is not None:
            raise Stop(f'the readback differs from the image at byte {differs}: the player is not confirmed. '
                       f'The backup is in {self.work/"backup"}; restore stock with install.py --restore.')
        self.say(title, ['Written and read back: every byte matches the image.'])
        self.done('player', image=str(image), written=True, simulated=True, chip=info['chip'], badBlocks=info['badBlocks'],
                  backup=backup, readbackSha256=devices.sha256_file(readback))

    def first_boot(self):
        self.say('First boot', ['Power the player on holding Play: the boot layer installs the packages from the card and '
                                'writes .disc/boot/result.json; the player page then answers on the network.'])
        self.done('first boot')

    def run(self):
        self.work.mkdir(parents=True, exist_ok=True)
        try:
            self.check()
            image = self.firmware()
            if self.args.restore:
                # Back to stock: the restore image built beside the boot layer's, the same path to the player.
                stock = next(Path(image).parent.glob('stock-v*-restore-review-only.bin'), None)
                if stock is None:
                    raise Stop('no stock restore image beside the image')
                self.step = 4
                self.player(stock, restore=True)
                self.report['status'] = 'restored'
                return 0
            folders, apps = self.packages()
            self.card(folders, apps)
            self.player(image)
            self.first_boot()
            self.report['status'] = 'prepared'
        except Stop as stop:
            self.report['status'] = f'stopped: {stop}'
            self.say('Stopped', [str(stop)], tui.ACCENT)
        finally:
            (self.work/'report.json').write_text(json.dumps(self.report, indent=2) + '\n')
        return 0 if self.report['status'] in ('prepared', 'restored') else 1
