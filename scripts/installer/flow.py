"""The installer's steps (plan, stage 4): check the computer, the firmware and its image, the
packages and apps from the catalogs, the card, the player through USB Boot and its first boot.
--dry-run stages into a folder of its own and writes nothing else; --simulate writes a
simulated player; --guest runs the image in the emulator's guest instead of a player."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

import catalog
import package
import release
import sources
from firmware_profile import load_profile, load_usb_profile
from installer import card as cards
from installer import device as devices
from installer import guest as guests
from installer import tui
from installer import usbboot

ROOT = Path(__file__).resolve().parents[2]
# Where libusb's packages put it: Homebrew (Apple silicon, Intel), Debian/Ubuntu and Fedora's multiarch folders.
LIBUSB_PLACES = ('/opt/homebrew/lib/libusb-1.0.0.dylib', '/usr/local/lib/libusb-1.0.0.dylib',
                 '/usr/lib/*-linux-gnu/libusb-1.0.so.0', '/lib/*-linux-gnu/libusb-1.0.so.0', '/usr/lib64/libusb-1.0.so.0',
                 '/usr/lib/libusb-1.0.so.0')
LIBUSB_HINT = 'install it (macOS: brew install libusb; Debian or Ubuntu: apt install libusb-1.0-0) or give it with --libusb.'
STEPS = ['Check this computer', 'Firmware and image', 'Packages', 'The card', 'The player (USB Boot)', 'First boot']
# The packages without the image (owner, 2026-10-10): the player is not written, its boot menu installs them.
PACKAGE_STEPS = ['Check this computer', 'Packages', 'The card', 'On the player']


def data_home():
    """Where the installer keeps a user's runs and downloads when it runs from its archive (plan, stage 6), so a
    newer archive replaces the old one without losing them: the platform's folder for an application's data."""
    if sys.platform == 'darwin':
        return Path.home()/'Library/Application Support/SNOWSKY DISC'
    return Path(os.environ.get('XDG_DATA_HOME') or Path.home()/'.local/share')/'snowsky-disc'


def fetch_check_over_console(sha256, card, wait=300):
    """The boot layer's check for this image (.disc/boot/rootfs-check.json on the player's card),
    read over the USB console, waiting for the port and the outcome at most wait seconds: its bytes,
    or None."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('disc_console', ROOT/'console.py')
    console = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(console)
    until = time.monotonic() + wait
    while time.monotonic() < until:
        for port in console.find_ports():
            try:
                link = console.Console(port)
                try:
                    out, status = link.run(f'cat {card}/.disc/boot/rootfs-check.json 2>/dev/null', timeout=20)
                finally:
                    link.close()
            except console.ConsoleError:
                continue
            line = next((l for l in out.splitlines() if l.startswith('{"schema":1,"expected":"')), None)
            if status == 0 and line and json.loads(line).get('expected') == sha256:
                return (line + '\n').encode()
        time.sleep(5)
    return None


class Stop(Exception):
    pass


def load_json_safe(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def typed_path(text):
    """A path as typed or dropped into the terminal: quoted, or with its spaces escaped."""
    return Path(str(text).strip().strip('\'"').replace('\\ ', ' ')).expanduser()


def find_update(given, profile):
    """FiiO's update as the build takes it, main_os/ota_v<version>: given as that folder, as
    main_os or as the update's own folder; its rootfs chunks counted against the profile."""
    given = typed_path(given)
    name = f'ota_v{profile["main_os_version"]}'
    for folder in (given, given/name, given/'main_os'/name):
        chunks = sorted(folder.glob('rootfs.squashfs.[0-9][0-9][0-9][0-9].*.enc')) if folder.is_dir() else []
        if not chunks:
            continue
        indices = [int(c.name.split('.')[2]) for c in chunks]
        if len(chunks) != profile['rootfs_chunks'] or indices != list(range(len(chunks))):
            raise ValueError(f'{folder} holds {len(chunks)} rootfs chunks; FiiO\'s {profile["version"]} update has '
                             f'{profile["rootfs_chunks"]}, numbered from 0000')
        return folder
    raise ValueError(f'{given} is not FiiO\'s {profile["version"]} update: no main_os/{name} with its rootfs chunks in it')


class Installer:
    def __init__(self, args, screen=None, runner=subprocess.run):
        self.args, self.screen, self.runner = args, screen or tui.Screen(), runner
        self.interactive = not args.yes and screen is None and sys.stdin.isatty() and self.screen.look != 'plain'
        # From the installer's archive (installer.json beside install.py) the runs are the user's, outside it;
        # from the repository they stay in work/.
        self.archive = release.INSTALLER_MANIFEST.exists()
        base = data_home() if self.archive else ROOT/'work'
        runs = base/'runs' if self.archive else base
        self.work = Path(args.work or runs/time.strftime('install-%Y%m%d-%H%M%S')).resolve()
        self.report = dict(started=time.strftime('%Y-%m-%dT%H:%M:%S'), dryRun=args.dry_run, guest=bool(args.guest), steps=[])
        self.packages_only = bool(getattr(args, 'packages', False))
        if self.packages_only:
            self.report['packagesOnly'] = True
        self.step, self.guest, self.ota = 0, None, None
        # Where archives are looked for by their digest: --from, the places given on the way, and
        # the downloads of earlier runs (work/downloads, kept: each is checked by its digest again).
        self.downloads = base/'downloads'
        self.places = [Path(p) for p in args.packages_from] + [self.downloads] + ([ROOT/'packages'] if self.archive else [])
        # The first start's check over the USB console (plan, stage 4c); tests replace it.
        self.fetch_check, self.image, self.expected = fetch_check_over_console, None, None

    # The screen

    def frame(self, *body):
        s = self.screen
        s.clear()
        s.blank()
        mode = '  ·  dry run' if self.args.dry_run else '  ·  guest' if self.args.guest else ''
        if self.packages_only:
            mode = '  ·  packages' + mode
        s.line(('  ● ', tui.ACCENT), ('S N O W S K Y   D I S C   B O O T', tui.MUTED), (mode, tui.MUTED))
        s.blank()
        s.steps(PACKAGE_STEPS if self.packages_only else STEPS, self.step)
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
        answer = self.input().strip()
        return answer or default

    def input(self):
        try:
            return input('  > ')
        except EOFError:
            raise Stop('stopped')

    def confirm(self, title, lines, word):
        """An irreversible step goes on only when the word is typed."""
        if not self.interactive:
            if not self.args.yes:
                raise Stop(f'{title}: confirm with --yes')
            return
        self.say(title, list(lines) + [f'Type {word} to go on, anything else to stop.'])
        if self.input().strip() != word:
            raise Stop('stopped before ' + title.lower())

    def done(self, name, **facts):
        # The player's step names the restart's outcome after the write (plan, stage 7), not only its run folder.
        if name == 'player' and getattr(self, 'restart_outcome', None) and 'restart' not in facts:
            facts['restart'] = self.restart_outcome
        self.report['steps'].append(dict(step=name, **facts))
        self.step += 1

    # The steps

    def check(self):
        facts = dict(python=sys.version.split()[0])
        if self.packages_only:
            # Packages only: neither the image nor the player, so Python alone.
            problems = [] if sys.version_info >= (3, 11) else ['Python 3.11 or later is needed.']
            self.say('Check this computer', [f'Python {facts["python"]}', 'Packages only: no image is built and the player is '
                                             'not written.'] + problems)
            if problems:
                raise Stop(' '.join(problems))
            self.done('check', **facts)
            return
        # The image is built on this computer with squashfs-tools and openssl (plan, stage 6); only the guest
        # needs Docker and the emulator's checkout; an image built before needs neither.
        facts['squashfs'] = self.host_builder()
        needed = bool(self.args.guest)
        facts['docker'] = bool(needed and shutil.which('docker') and self.runner(['docker', 'info'], capture_output=True).returncode == 0)
        facts['emulator'] = str(self.emulator()) if self.emulator() else None
        problems = []
        if sys.version_info < (3, 11):
            problems.append('Python 3.11 or later is needed.')
        if not self.args.image and not facts['squashfs']:
            problems.append('The image needs squashfs-tools 4.6 or later and openssl (macOS: brew install squashfs openssl; '
                            'Debian or Ubuntu: apt install squashfs-tools openssl), or give a built image with --image.')
        if needed and not facts['docker']:
            problems.append('Docker must run to run the guest.')
        # The image takes the boot layer's programs from its release file (plan, stage 6); a local build
        # only with --boot-build, for development.
        missing = [n for n in release.BOOT_PROGRAMS if not (ROOT/'build/mips'/n).is_file()]
        if not self.args.image and getattr(self.args, 'boot_build', False) and missing:
            problems.append(f'The boot layer is not built ({", ".join(missing)}): bash scripts/build.sh mips.')
        if needed and not facts['emulator']:
            problems.append('The emulator checkout runs the guest (--emulator).')
        # libusb is needed only when the player itself is written (not with --dry-run, --simulate or --guest).
        player = not (self.args.dry_run or self.args.simulate or self.args.guest)
        facts['libusb'] = self.libusb() if player else None
        if player and not facts['libusb']:
            problems.append('libusb is needed to write the player: ' + LIBUSB_HINT)
        found = [f'Python {facts["python"]}'] + ([f'libusb {facts["libusb"]}'] if facts['libusb'] else []) + (
            ['An image built before: no build'] if self.args.image else (
            [f'squashfs-tools {facts["squashfs"]} and openssl: the image is built here'] if facts['squashfs'] else [])) + (
            ['Docker ' + ('runs' if facts['docker'] else 'does not run'), 'Emulator ' + (facts['emulator'] or 'not found')] if needed else [])
        self.say('Check this computer', found + problems)
        if problems:
            raise Stop(' '.join(problems))
        self.done('check', **facts)

    def host_builder(self):
        """squashfs-tools 4.6 or later and openssl on this computer (the image built here, without root), as
        mksquashfs says its version; None without them."""
        if not all(shutil.which(tool) for tool in ('mksquashfs', 'unsquashfs', 'openssl')):
            return None
        out = subprocess.run(['mksquashfs', '-version'], capture_output=True, text=True).stdout or ''
        found = re.search(r'version (\d+)\.(\d+)(?:\.(\d+))?', out)
        if not found or tuple(int(p or 0) for p in found.groups()) < (4, 6, 0):
            return None
        return '.'.join(p for p in found.groups() if p)

    def emulator(self):
        candidate = Path(self.args.emulator or os.environ.get('DISC_EMULATOR', ROOT.parent/'snowsky-disc-server/work/emulator-ref'))
        return candidate if (candidate/'emulator').is_dir() else None

    def firmware(self):
        profile = load_profile()
        if self.args.image:
            image = Path(self.args.image)
            if not image.is_file():
                raise Stop(f'{image} is not a built image')
            if self.args.guest:
                # The guest starts from stock's update with the image's rootfs over it.
                self.ota = self.update_folder(profile)
            self.say('Firmware and image', [f'The image {image.name}, built before.'])
            self.done('firmware', image=str(image), profile=profile['version'], ota=str(self.ota) if self.ota else None)
            return image
        ota = self.ota = self.update_folder(profile)
        out = self.work/'image'
        out.parent.mkdir(parents=True, exist_ok=True)
        programs, boot = self.boot_programs(profile)
        # On this computer, without root (plan, stage 6): the emulator's reader of FiiO's update fetched at its
        # pinned revision, stock's owners and mode bits packed from stock's own listing. The guest and the player
        # ran this build (2026-10-08), so the build in the emulator's Docker image is gone.
        try:
            reader = sources.fetch(profile['version'], cache=self.downloads, allow_download=self.args.download,
                                   name='emulator')/'firmware/tools/firmware_inventory.py'
        except sources.SourceError as error:
            raise Stop(str(error))
        self.say('Firmware and image', [f'Building the image from {ota.name} with the boot layer {boot}, here. '
                                        'This takes a minute.'])
        command = [sys.executable, '-B', str(ROOT/'scripts/deployment/build_candidate.py'), '--ota', str(ota),
                   '--console', str(programs/'disc-usb-console'), '--boot', str(programs/'disc-boot'),
                   '--output', str(out), '--reader', str(reader)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise Stop('the image was not built: ' + (result.stderr.strip().splitlines() or ['no output'])[-1])
        image = next(out.glob('disc-boot-v*-review-only.bin'))
        self.done('firmware', image=str(image), ota=str(ota), profile=profile['version'], boot=boot)
        return image

    def boot_programs(self, profile):
        """The folder of disc-boot and disc-usb-console for the image, and what they are: the newest boot release
        recorded for the firmware, its archive found by its record's digest (--from, earlier downloads) or
        downloaded and checked (plan, stage 6); a local build of build/mips only with --boot-build."""
        if getattr(self.args, 'boot_build', False):
            return ROOT/'build/mips', 'built here (build/mips)'
        try:
            chosen = release.boot_release(profile['version'])
        except release.ReleaseError as error:
            raise Stop(str(error))
        self.downloads.mkdir(parents=True, exist_ok=True)
        archive = self.obtained(chosen['name'], lambda places: catalog.obtain(
            chosen['archive'], places, self.downloads, self.args.download, self.downloading('Firmware and image')))
        try:
            release.extract_boot(archive, chosen['version'], chosen['buildId'], self.work/'boot')
        except release.ReleaseError as error:
            raise Stop(str(error))
        return self.work/'boot', f'{chosen["version"]} (build {chosen["buildId"]}, its release file)'

    def update_folder(self, profile):
        given = self.ask('Firmware and image', f'The folder of FiiO\'s {profile["version"]} update', self.args.ota)
        try:
            return find_update(given, profile)
        except ValueError as error:
            raise Stop(str(error))

    def obtained(self, what, attempt):
        """attempt(places) until its archive is found; asked, a file or a folder to look in is
        added to the places (the next archives are looked for there too)."""
        note = ''
        while True:
            try:
                return attempt(self.places)
            except catalog.NotLocal as error:
                if not self.interactive:
                    raise Stop(f'{what}: {error}')
                answer = self.ask('Packages', f'{note}{what}: {error}. Where is it? A file, or a folder to look in '
                                  '(drop it here; empty stops).', '')
                if not answer:
                    raise Stop(f'{what}: {error}')
                place = typed_path(answer)
                note = f'Not in {place}. ' if place.exists() else f'{place} does not exist. '
                if place.exists():
                    self.places.append(place)
            except catalog.CatalogError as error:
                raise Stop(f'{what}: {error}')

    def downloading(self, title):
        last = [None]

        def show(name, fraction):
            if (name, int(fraction * 50)) != last[0]:
                last[0] = (name, int(fraction * 50))
                self.frame(lambda: self.screen.label(title), lambda: self.screen.progress(f'Downloading {name}', fraction))
        return show

    def packages(self):
        entries = catalog.load(self.args.catalog or catalog.CATALOG, kind='packages')['entries']
        wanted = set(self.args.package or [])
        unknown = wanted - {e['name'] for e in entries}
        if unknown:
            raise Stop(f'not in the catalog: {", ".join(sorted(unknown))}')
        # By the role the boot layer takes them in (package.taken: the server of boot API 1 is the
        # controller), in the order the player meets them: the menu, the UIs it offers, the server
        # behind them and the services beside it; one menu and one server at most.
        groups = []
        for role, label, single in (('menu', 'Boot menu', True), ('ui', 'UIs', False), ('controller', 'Server', True),
                                    ('service', 'Services', False)):
            members = [e for e in entries if package.taken(e) == role]
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
            folders[entry['name']] = self.obtained(f'{entry["name"]} {entry["version"]}', lambda places: catalog.fetch(
                entry, self.work/'packages'/entry['name'], places, self.args.download, self.downloading('Packages'), self.downloads))
        apps, offered = [], cards.app_entries(folders['disc-server']) if 'disc-server' in folders else []
        if 'disc-server' in folders and not offered:
            self.say('Apps of the server', ['This server package offers no apps (it carries no catalog/apps.json).'], tui.MUTED)
        if offered:
            wanted_apps = set(self.args.app or [])
            app_marks = [a['name'] in wanted_apps if wanted_apps else a['default'] for a in offered]
            app_marks = self.choose('Apps of the server', [(a['name'], a['version']) for a in offered], app_marks)
            apps = [a for a, on in zip(offered, app_marks) if on]
        # Each app's archive found now, before the card: its file joins the places the card step uses.
        self.downloads.mkdir(parents=True, exist_ok=True)
        for app in apps:
            self.places.append(self.obtained(f'{app["name"]} {app["version"]}', lambda places: catalog.obtain(
                app['source'], places, self.downloads, self.args.download, self.downloading('Apps of the server'))))
        self.done('packages', packages=[dict(name=e['name'], version=e['version']) for e in chosen],
                  apps=[dict(name=a['name'], version=a['version']) for a in apps])
        return list(folders.values()), apps

    def card(self, folders, apps):
        if self.args.dry_run or self.args.guest:
            target = self.work/'card'
            target.mkdir(parents=True, exist_ok=True)
        else:
            # In a card reader, or the player itself in stock's USB storage (owner, 2026-10-10): a drive either way.
            path = self.ask('The card', 'Where the player\'s card is mounted (a card reader, or the player in USB storage)',
                            self.args.card)
            try:
                target = cards.card_ok(path)
            except cards.CardError as error:
                raise Stop(str(error))
            self.confirm('The card', [f'On {target}: {len(folders)} package(s) for the player, {len(apps)} app(s) in Apps/, '
                                      'and the USB console\'s marker.'], 'CARD')
        profile = load_profile()['version']
        cleared = cards.clear_unchosen(target, cards.places(folders))
        staged = cards.stage_packages(folders, target, profile)
        self.roles = {s['role'] for s in staged}
        self.services = [s['name'] for s in staged if s['role'] == 'service']
        with tempfile.TemporaryDirectory() as temp:
            placed = cards.stage_apps(apps, target, self.places, self.args.download, temp)
        marker = cards.write_marker(target)
        # The image the player is written with, for its own check at its first start (plan, stage 4c).
        if self.image and not self.args.guest:
            self.expected = cards.expect_image(target, self.image)
        self.say('The card', [f'{s["role"]}: {s["name"]} {s["version"]}' for s in staged] +
                 [f'app: {a["name"]} {a["version"]}' for a in placed] + [f'console marker: {marker.relative_to(target)}'] +
                 [f'no longer staged (an earlier run\'s, not chosen now): {path}' for path in cleared])
        self.done('card', card=str(target), packages=staged, apps=placed, marker=str(marker), cleared=cleared,
                  expected=self.expected)
        return target

    def on_the_player(self):
        """Packages only: the player's boot menu installs what waits on the card (since boot release 2.57.7); a
        boot layer without the menu's screens, or no menu, installs it at a start with Play."""
        lines = ['Eject the card, put it back into the player (or leave USB storage), then switch the player off and on: '
                 'the boot menu offers the packages on the card '
                 '(its row "on the card", then Packages, where Play installs each one). A new version of a service or the '
                 'server starts at once; a new menu at the next start.',
                 'Without the boot menu, or with a boot layer before 2.57.7, switch the player on holding Play instead '
                 '(let Play go once the logo shows): it installs everything on the card.']
        if self.args.dry_run:
            lines = [f'Dry run: staged in {self.work/"card"}; nothing was written to a card.']
        self.say('On the player', lines)
        self.done('on the player')

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

    # The player through the reviewed tools (installer/usbboot.py), the owner's procedure:
    # the backup in one entry into USB Boot, the write in a fresh one (each session loads the SPL,
    # whose DDR training a second time in an entry failed in 4 of 7 recorded runs, the last the
    # fourth write's on 2026-10-06, before the writer), the owner's look at the new system's start,
    # then a fresh entry for the readback. After a "no" the way back is stock, from any state
    # (owner, 2026-10-05).

    ENTER = 'Power the player off, hold Volume Down and connect the cable to this computer (USB Boot).'
    STUCK = ('If the player cannot be powered off (its system restarts without end), unplug it and let the battery '
             'run down; then hold Volume Down and connect the cable.')

    def reviewed_tools(self, image, history, work=None):
        libusb = self.libusb()
        if not libusb:
            raise Stop('the player is written through libusb, which was not found: ' + LIBUSB_HINT)
        return usbboot.Reviewed(load_profile()['version'], work or self.work/'usb', Path(image).parent, self.diskos(),
                                libusb, history, run=self.runner, progress=self.session_progress(),
                                payloads=self.release_payloads())

    def release_payloads(self):
        """The folder of the boot release's prebuilt USB payloads (plan, stage 6), by its record's digest; None when
        they are built here: with --boot-build, or for a release that carries none (Docker, the boot layer's
        toolchain)."""
        if getattr(self.args, 'boot_build', False):
            return None
        folder = self.work/'payloads'
        if folder.is_dir():
            return folder
        version = load_profile()['version']
        try:
            chosen = release.boot_release(version)
        except release.ReleaseError as error:
            raise Stop(str(error))
        if not chosen['payloads']:
            return None
        self.downloads.mkdir(parents=True, exist_ok=True)
        archive = self.obtained(chosen['payloads']['name'], lambda places: catalog.obtain(
            chosen['payloads']['archive'], places, self.downloads, self.args.download, self.downloading('The player (USB Boot)')))
        try:
            return release.extract_payloads(archive, chosen['version'], folder)
        except release.ReleaseError as error:
            raise Stop(str(error))

    def libusb(self):
        """The libusb library the reviewed tools load (plan, stage 6): --libusb, else where its packages put it on
        macOS (Homebrew) and Linux; None when it is not there. A history pins the file's digest, so a developer's
        player keeps the one it was written with."""
        # The file itself: the places are links to it (Homebrew's lib, the .so.0 names), and the reviewed tools pin
        # only regular files (a link is refused by the review, after the player's reads).
        if self.args.libusb:
            return str(Path(self.args.libusb).resolve())
        for candidate in LIBUSB_PLACES:
            for path in sorted(Path('/').glob(candidate.lstrip('/'))):
                if path.is_file():
                    return str(path.resolve())
        return None

    def diskos(self):
        """diskOS's writer, SPL and the sources they are checked by (plan, stage 6): the checkout given with
        --diskos or a copy found before, each file by the profiles' pins, else fetched from the pinned revision."""
        version = load_profile()['version']
        try:
            if self.args.diskos:
                wrong = sources.differs(self.args.diskos, sources.diskos_files(version))
                if wrong:
                    raise Stop(f'{self.args.diskos} is not diskOS at its pinned revision: {", ".join(wrong)} differ')
                return Path(self.args.diskos)
            return sources.fetch(version, cache=self.downloads, allow_download=self.args.download,
                                 progress=lambda path, part: self.frame(
                                     lambda: self.screen.label('The player (USB Boot)'),
                                     lambda: self.screen.progress(f'diskOS\'s writer and SPL: {path}', part)))
        except sources.SourceError as error:
            raise Stop(str(error))

    def session_progress(self):
        """A USB session's bar in the menu's look: the share of its calls done, the time gone and left."""
        last = [None]

        def show(label, fraction, seconds):
            mark = (label, int(fraction * 100), int(seconds) // 5)
            if mark == last[0]:
                return
            last[0] = mark
            gone = f'{int(seconds) // 60}:{int(seconds) % 60:02d}'
            left = f'about {max(1, round(seconds * (1 - fraction) / fraction / 60))} min left' if 0.02 < fraction < 1 else ''
            self.frame(lambda: self.screen.label('The player (USB Boot)'), lambda: self.screen.progress(label, fraction),
                       lambda: self.screen.text(f'{gone} gone' + (f'  ·  {left}' if left else ''), tui.MUTED))
        return show

    # Leaving USB Boot restarts the player by itself into what was written (owner, 2026-10-08): a start
    # with Play is possible only after that start, switched off and on again. Once the boot menu is installed
    # it offers the packages on the card itself (owner, 2026-10-10), so Play is for the first installation.
    PLAY_AFTER = ('To install the packages on the card: once the boot menu is installed, it offers them at a start '
                  '(its row "on the card", then Packages). The first time, once the player has started, switch it off, '
                  'then switch it on holding Play (press the power key briefly and let Play go once the logo shows); '
                  'the menu shows Installing.')

    def restart(self, reviewed, title):
        """The player restarted by the installer after the write (plan, stage 7), so the cable stays connected:
        True when it left the bus. Otherwise (a release without the restart payload, no restart observed, an
        error) the cable is the way, as before; the write is done either way."""
        if not reviewed.can_restart():
            self.restart_outcome = dict(outcome='not available', why='the release carries no restart payload')
            return False
        try:
            status = reviewed.restart()
        except usbboot.ReviewedError as error:
            self.restart_outcome = dict(outcome='failed', why=str(error))
            return False
        self.restart_outcome = dict(outcome=status, run=str(reviewed.work/'restart'))
        return status == 'player-restarted'

    def start_answer(self, title, what, then=None, words=True, restarted=False):
        """The owner's look at a new system's first start: (started normally, words, time UTC), or None. words:
        a few words of what was seen, the developers' evidence for the next review with a history; a user
        without one answers yes or no only (owner, 2026-10-08)."""
        if not self.interactive:
            return None
        leaving = (f'Written, and the player is restarting into {what} by itself: keep the cable connected. ' if restarted else
                   f'Written. Disconnect the cable: leaving USB Boot, the player restarts into {what} by itself. ')
        self.say(title, [leaving + 'Let it start without holding a key: is the interface steady, do the volume and playback work, '
                         'does the power key switch it off?'] + ([then] if then else []) +
                 ['Did it start normally? Type yes or no.'])
        word = self.input().strip().lower()
        while word not in ('yes', 'no'):
            self.say(title, ['Type yes or no.'])
            word = self.input().strip().lower()
        reported = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        if not words:
            return word == 'yes', word, reported
        self.say(title, ['Describe what you saw, in a few words.'])
        return word == 'yes', self.input().strip() or word, reported

    def first_start_check(self, reviewed, title, written, answer, connected=False):
        """The new system's own check of what was written (plan, stage 4c): the outcome its boot layer
        left on the card, read over the USB console while it runs. The folder of that proof, or None
        when it does not come back or differs (the readback through USB Boot follows)."""
        if not self.expected:
            return None
        self.say(title, ['The new system\'s boot layer checks the written image a minute or two after its start, and the installer '
                         'reads that over the USB console' + (', the cable still connected.' if connected else
                         ': connect the cable to this computer again while the new system runs.')])
        raw = self.fetch_check(self.expected['sha256'], load_usb_profile(load_profile())['sd_mount'])
        if raw is None:
            self.say(title, ['The check did not come back over the cable: the readback through USB Boot follows.'])
            return None
        record = json.loads(raw)
        folder = reviewed.work/'first-start'
        folder.mkdir(parents=True, exist_ok=True)
        (folder/'rootfs-check.json').write_bytes(raw)
        fetched = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        (folder/'fetch.json').write_text(json.dumps(dict(via='usb-console', fetched_at=fetched, write_session_id=written['session'],
                                         rootfs_check_sha256=hashlib.sha256(raw).hexdigest()), indent=2) + '\n')
        (folder/'owner-boot-confirmation.json').write_text(json.dumps(dict(
            observation='owner-confirmed-normal-first-boot', reported_at=answer[2], owner_answer=answer[1],
            reported_via='install.py, after the write, with the first start\'s check', write_session_id=written['session'],
            automated_boot_test=False, native_process_verified=False), indent=2, ensure_ascii=False) + '\n')
        if record.get('match') is not True:
            self.say(title, [f'The new system\'s check differs: {record.get("error") or "another image on its root device"}. '
                             'The readback through USB Boot follows.'])
            return None
        return folder

    def confirmation(self, read, written, answer):
        """The owner's word, between the write and the readback, in the readback's capture."""
        if not answer or not answer[0]:
            return
        session = load_json_safe(Path(read['capture'])/'result.json').get('session_id')
        record = dict(observation='owner-confirmed-normal-first-boot', reported_at=answer[2], owner_answer=answer[1],
                      reported_via='install.py, after the write and before the readback', write_session_id=written['session'],
                      readback_session_id=session, automated_boot_test=False, native_process_verified=False)
        (Path(read['capture'])/'owner-boot-confirmation.json').write_text(json.dumps(record, indent=2, ensure_ascii=False) + '\n')

    def stock_back(self, reviewed, title, facts):
        """The way back to stock: the write of stock's rootfs in a fresh entry into USB Boot, the
        owner's look at it, a fresh entry for its readback, the audits."""
        stock = reviewed.artifacts/load_json_safe(reviewed.package/'restore-write-plan.json')['plan']['image_name']
        self.confirm(title, [self.ENTER, self.STUCK, 'Next: stock\'s rootfs, written once.'], 'RESTORE')
        written = reviewed.write('restore')
        answer = self.start_answer(title, 'stock', restarted=self.restart(reviewed, title))
        self.confirm(title, [self.ENTER, 'Next: read stock back in a fresh session and compare every byte.'], 'READ')
        read = reviewed.readback('restore')
        audits = reviewed.audit('restore')
        self.confirmation(read, written, answer)
        history = reviewed.next_history(stock, 'restore')
        self.say(title, ['Stock is back: written, read back and every byte compared, both USB journals audited.',
                         f'The player\'s history: {history}'] + ([] if answer and answer[0] else
                         ['Stock\'s start was not confirmed: tell the developers what you saw.']))
        self.done('player', image=str(stock), written=True, simulated=False, target='restore', restore=dict(write=written, read=read,
                  audits=audits, history=str(history), ownerAnswer=answer[1] if answer else None, startedNormally=bool(answer and answer[0])),
                  **facts)
        self.report['status'] = 'restored'

    def player_reviewed(self, image, restore):
        """The candidate's installation, or (--restore with --history) the way back to stock."""
        title = 'The player (USB Boot)'
        target = 'restore' if restore else 'candidate'
        try:
            reviewed = self.reviewed_tools(image, usbboot.History.load(self.args.history))
            self.say(title, ['Preparing the installation package (offline: payloads, the review, the plans).'])
            prepared = reviewed.prepare()
            self.confirm(title, [f'The package is ready: write plan {prepared["write"][:12]}…, readback plan {prepared["read"][:12]}….',
                                 self.ENTER, 'Next, in this one entry: a check of what the player holds (about a minute), '
                                 'then the write.'], 'CHECK')
            # The first blocks in place of a full backup (owner, 2026-10-07): the way back is only ever
            # stock, so a backup's bytes restore nothing; the write follows in the same entry, whose
            # SPL is not run again (ram_transport.bring_up).
            backup = reviewed.identity(strict=not restore)
            if restore:
                return self.stock_back(reviewed, title, dict(backup=backup))
            self.write_and_read(reviewed, title, image, backup)
        except usbboot.ReviewedError as error:
            raise Stop(f'{error}. The run\'s evidence is in {self.work/"usb"}; nothing was retried. '
                       f'The way back to stock: install.py --restore --run {self.work}')

    def write_and_read(self, reviewed, title, image, backup, holds=None):
        """After the backup: the write in a fresh entry, the owner's look at the new system's start,
        the readback in a fresh entry, the audits and the next history. Without a history (holds: what the
        review knows the player holds, plan, stage 6) the same, and no history is written."""
        known = holds is not None
        self.confirm(title, [f'The player holds {holds}.' if known else
                             f'The player holds {backup["matches"]}, as its history says.' if backup.get('matches') else
                             f'What the player holds is in {backup["capture"]}.',
                             'Next, in the same entry (stay connected): the image with the boot layer, written once. '
                             'Its outcome is never retried.'], 'WRITE')
        written = reviewed.write('candidate')
        restarted = self.restart(reviewed, title)
        answer = self.start_answer(title, 'the new system', self.PLAY_AFTER, words=not known, restarted=restarted)
        if answer is not None and not answer[0]:
            # Straight back to stock (owner, 2026-10-05): the player holds this run's own image,
            # known from the writer's completion; reading it back would only cost time.
            self.say(title, ['The new system did not start normally: the way back is stock.'])
            if known:
                return self.known_back(image, dict(backup=backup, write=written, candidateAnswer=answer[1]))
            return self.stock_back(reviewed, title, dict(backup=backup, write=written, candidateAnswer=answer[1]))
        proof = self.first_start_check(reviewed, title, written, answer, connected=restarted) if answer else None
        if proof:
            audits = reviewed.audit('candidate', read=False)
            history = None if known else reviewed.next_history(image, proof=proof)
            self.proven_start = True
            self.say(title, ['Written; the new system read its root device and found the written image, every byte by its '
                             'SHA-256; the USB journal of the write audited.'] +
                     ([f'This installation\'s history: {history}'] if history else []))
            self.done('player', image=str(image), written=True, simulated=False, target='candidate', backup=backup, write=written,
                      firstStart=str(proof), audits=audits, history=history and str(history), ownerAnswer=answer[1])
            return
        self.confirm(title, [self.ENTER, 'Next: read it back in a fresh session and compare every byte.'], 'READ')
        read = reviewed.readback('candidate')
        audits = reviewed.audit('candidate')
        self.confirmation(read, written, answer)
        history = None if known else reviewed.next_history(image)
        self.say(title, ['Written, read back and every byte compared; both USB journals audited.'] +
                 ([f'This installation\'s history: {history}'] if history else []) +
                 ([] if answer or known else ['No answer about the new system\'s start was recorded: the next installation\'s review '
                                              'needs one (owner-boot-confirmation.json in the readback capture).']))
        self.done('player', image=str(image), written=True, simulated=False, target='candidate', backup=backup, write=written, read=read,
                  audits=audits, history=history and str(history), ownerAnswer=answer[1] if answer else None)

    # A user's installation, without a history (plan, stage 6)

    @staticmethod
    def holds(decision):
        """What the review found on the player, in words."""
        found = decision.get('found', {})
        return {'stock': 'FiiO\'s own system (stock, as FiiO\'s update installs it)',
                'release': f'the boot layer {found.get("release")}', 'candidate': 'this run\'s own image',
                'unknown': 'a system this installer does not know'}.get(found.get('kind'), 'an image')

    def player_known(self, image, restore, work=None):
        """In one entry into USB Boot: the player's partition table, bootloader and first rootfs blocks read and
        audited, the review that knows what it holds (stock or one of ours) or stops before anything is written,
        then the write in that entry, proven by its first start's check; --restore: stock's rootfs the same way.
        The typed words are the only consent, so without a terminal nothing is written."""
        title = 'The player (USB Boot)'
        if not self.interactive:
            if restore:
                raise Stop('the way back to stock is written only with the words typed at each step: run install.py --restore '
                           'in a terminal, without --yes')
            # A run without questions stages the card only (as before).
            self.say(title, [f'The image is ready: {Path(image).name}.', 'The player is written only with the words typed at '
                             'each step: run install.py in a terminal, without --yes.'])
            self.done('player', image=str(image), written=False)
            return
        reviewed = self.reviewed_tools(image, None, work=work)
        try:
            self.say(title, ['Preparing the payloads the player runs from its RAM (offline).'])
            reviewed.builds(evidence=True)
            self.confirm(title, [self.ENTER, self.STUCK, 'Next, in this one entry: the player\'s partition table, bootloader and '
                                 'first rootfs blocks are read (about three minutes), each read checked here; nothing is written '
                                 'yet.'], 'CHECK')
            reviewed.entry()
            # Back to stock from any state (owner, 2026-10-05/09): an image it does not know admits the restore only.
            decision = reviewed.review_known(allow_unknown=restore)
            if restore:
                return self.known_restore(reviewed, title, image, decision)
            self.write_and_read(reviewed, title, image, dict(capture=str(reviewed.work/'identity'), found=decision.get('found')),
                                holds=self.holds(decision))
        except usbboot.ReviewedError as error:
            raise Stop(f'{error}. The run\'s evidence is in {reviewed.work}; nothing was retried. '
                       'FiiO\'s own update (Local upgrade, its zip at the card\'s root) returns the player to stock; '
                       'install.py --restore does it through USB Boot when the player holds an image it knows.')

    def known_restore(self, reviewed, title, image, decision):
        """Stock's rootfs, written in the entry of the evidence that knows what the player holds; the owner's look
        at stock's start (stock has no boot layer to check itself)."""
        stock = reviewed.artifacts/load_json_safe(reviewed.package/'restore-write-plan.json')['plan']['image_name']
        if decision.get('found', {}).get('kind') == 'unknown':
            self.confirm(title, ['The player holds a system this installer does not know: a write cut short, or another '
                                 'FiiO version. The player\'s bootloader was checked; its kernel is not read. FiiO\'s 2.57 '
                                 'system written over a player of another FiiO version would not start with its kernel: '
                                 'on such a player, FiiO\'s own update is the way back.'], 'STOCK')
        self.confirm(title, [f'The player holds {self.holds(decision)}.', 'Next, in the same entry (stay connected): stock\'s '
                             'rootfs, written once. Its outcome is never retried.'], 'RESTORE')
        written = reviewed.write('restore')
        answer = self.start_answer(title, 'stock', words=False, restarted=self.restart(reviewed, title))
        audits = reviewed.audit('restore', read=False)
        self.say(title, ['Stock\'s rootfs is written: the writer finished, after the image was checked in the player\'s RAM; '
                         'the USB journal of the write audited.'] + ([] if answer and answer[0] else
                         ['Stock\'s start was not confirmed: tell the developers what you saw.']))
        self.done('player', image=str(stock), written=True, simulated=False, target='restore', restore=dict(
                  write=written, audits=audits, found=decision.get('found'), ownerAnswer=answer[1] if answer else None,
                  startedNormally=bool(answer and answer[0])))
        self.report['status'] = 'restored'

    def known_back(self, image, facts):
        """The way back after a new system that did not start: its own evidence in a new entry into USB Boot (the
        review knows this run's image), then stock's rootfs."""
        self.report.setdefault('wayBack', facts)
        return self.player_known(image, True, work=self.work/'usb-back')

    def resume_write(self):
        """install.py --resume RUN: a candidate's installation whose write stopped before the writer
        ran (the SPL's DDR check failed in a later session of the backup's entry, 2026-10-06): the
        player still holds what it held; this run prepares the same package and, in a fresh entry, checks
        the player's first blocks again (plan, stage 4c) and goes on with the write."""
        title = 'The player (USB Boot)'
        run = Path(self.args.resume).resolve()
        report, used = load_json_safe(run/'report.json'), load_json_safe(run/'usb/history-used.json')
        image = next((s.get('image') for s in report.get('steps', []) if s.get('step') == 'firmware'), None)
        before = load_json_safe(run/'usb/identity/result.json') or load_json_safe(run/'usb/backup/result.json')
        written = load_json_safe(run/'usb/write/result.json')
        untouched = (written.get('status') == 'failed-before-writer' and written.get('writer_execution_attempted') is False
                     and written.get('page_execution_attempted') is False)
        if not image or before.get('status') not in ('rootfs-probe-collected', 'rootfs-collected') or not untouched:
            raise Stop(f'{run} is not a run whose write stopped before the writer after its backup: start a new installation')
        history = usbboot.History.load(self.args.history)
        if used != json.loads(json.dumps(history.data, default=str)):
            raise Stop(f'--history is not the history {run.name} installed with')
        self.done('firmware', image=str(image), profile=load_profile()['version'], resumed=str(run))
        try:
            reviewed = self.reviewed_tools(image, history)
            self.say(title, [f'Going on with {run.name}: its write stopped before the writer, so the player holds what it held.',
                             'Preparing the installation package again (offline: payloads, the review, the plans).'])
            prepared = reviewed.prepare()
            if prepared['write'] != load_json_safe(run/'usb/package/candidate-write-plan.json').get('plan_sha256'):
                raise usbboot.ReviewedError(f'the write plan differs from the one {run.name} prepared')
            self.confirm(title, [self.ENTER, 'Next, in this one entry: a check of what the player holds (about a minute), '
                                 'then the write.'], 'CHECK')
            self.write_and_read(reviewed, title, image, dict(reviewed.identity(strict=True), resumes=str(run)))
        except usbboot.ReviewedError as error:
            raise Stop(f'{error}. The run\'s evidence is in {self.work/"usb"}; nothing was retried. '
                       f'The way back to stock: install.py --restore --run {self.work}')

    def restore_from_run(self):
        """install.py --restore --run DIR: back to stock with that run's package, from whatever the player
        holds (it may no longer start): backup and write in one entry, the owner's look, the readback."""
        title = 'The player (USB Boot)'
        run = Path(self.args.run).resolve()
        used, report = run/'usb/history-used.json', load_json_safe(run/'report.json')
        image = next((s.get('image') for s in report.get('steps', []) if s.get('step') == 'firmware'), None)
        if not used.is_file() or not image:
            raise Stop(f'{run} is not an installer run with a package (usb/history-used.json and its report)')
        try:
            reviewed = self.reviewed_tools(image, usbboot.History.load(used), work=run/'usb')
            written = load_json_safe(run/'usb/write/result.json')
            if written.get('status') == 'writer-completion-observed' and not (run/'usb/restore-write').exists():
                # What the player holds is this run's own image, its write observed complete: no backup
                # of it is needed on the way back to stock (owner, 2026-10-05).
                self.say(title, [f'The player holds the image {run.name} wrote (write {written.get("session_id", "")[:8]}). '
                                 'The way back to stock writes stock\'s rootfs over it.'])
                return self.stock_back(reviewed, title, dict(run=str(run), holds='this run\'s image'))
            self.confirm(title, [f'The way back to stock with the package of {run.name}.', self.ENTER, self.STUCK,
                                 'Next: a check of what the player holds (its first blocks, for the record); stock\'s rootfs '
                                 'follows in a fresh entry.'], 'CHECK')
            backup = reviewed.identity(strict=False, name='restore-identity')
            self.say(title, [f'The player holds {backup["matches"]}.' if backup['matches'] else
                             'The player holds an image this run does not know.'])
            self.stock_back(reviewed, title, dict(backup=backup, run=str(run)))
        except usbboot.ReviewedError as error:
            raise Stop(f'{error}. The evidence is in {run/"usb"}; nothing was retried.')

    def player_guest(self, image):
        """The emulator's guest of the image in the player's place, with the staged card."""
        title = 'The player (USB Boot)'
        self.guest = guests.Guest(self.work, self.emulator(), image, self.ota, run=self.runner)
        self.say(title, ['A disposable guest of the emulator stands in for the player: it starts from the image, as the player '
                         'would after the write. Setting it up takes a minute or two.'])
        try:
            self.guest.up()
            self.guest.card(self.work/'card')
        except guests.GuestError as error:
            raise Stop(f'{error}. The guest\'s log is {self.guest.log}.')
        self.say(title, ['The guest is ready and has the card.'])
        self.done('player', image=str(image), written=False, guest=str(self.guest.state))

    def player(self, image, restore=False):
        title = 'The player (USB Boot)'
        if self.args.guest:
            return self.player_guest(image)
        if self.args.history and not self.args.simulate:
            return self.player_reviewed(image, restore)
        if not self.args.simulate and not self.args.dry_run:
            return self.player_known(image, restore)
        if not self.args.simulate:
            self.say(title, [
                f'The image is ready: {Path(image).name}.',
                'Without --dry-run the player is written through USB Boot: the reviewed tools read what it holds and write '
                'in the same entry, with libusb (found, or --libusb); diskOS\'s writer and SPL come from their pinned revision '
                '(or --diskos). Without a player: --simulate (a simulated NAND) or --guest (the emulator\'s guest).'])
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

    def first_boot_guest(self):
        title = 'First boot'
        roles = self.roles
        self.say(title, ['The guest starts with Play held: the boot layer installs the packages from the card. '
                         'Then it is followed until the menu answers and the server and the services are confirmed (180 s).'])
        services = getattr(self, 'services', [])

        def show(status):
            lines = [f'{name}: {(status.get(name) or {}).get("state", "-")}' for name in ('controller', 'menu', 'ui')]
            lines += [f'{name}: {((status.get("services") or {}).get(name) or {}).get("state", "-")}' for name in services]
            choice = status.get('choice') or {}
            if choice:
                lines.append(f'chosen UI: {choice.get("ui")} (by {choice.get("by")})')
            self.say(title, lines)
        try:
            self.guest.start()
            status, done = self.guest.follow(roles, services, show=show)
            result = self.guest.result()
        except guests.GuestError as error:
            raise Stop(f'{error}. The guest\'s log is {self.guest.log}.')
        roles_result = result.get('roles', {})
        named = ('ui', 'service')
        installed = [k for k, v in roles_result.items() if k not in named and v.get('installed')] + \
                    [f'{g}/{k}' for g in named for k, v in roles_result.get(g, {}).items() if v.get('installed')]
        refused = [f'{k}: {v.get("note")}' for k, v in roles_result.items() if k not in named and not v.get('installed')] + \
                  [f'{g}/{k}: {v.get("note")}' for g in named for k, v in roles_result.get(g, {}).items() if not v.get('installed')]
        self.done('first boot', guest=True, status=status, result=result)
        if refused or not done:
            raise Stop('the first boot on the guest did not finish: ' + '; '.join(refused or [
                f'{name} {(status.get(name) or {}).get("state")}' for name in ('controller', 'menu') if name in roles] + [
                f'{name} {((status.get("services") or {}).get(name) or {}).get("state")}' for name in services]))
        reached = [what for role, what in (('menu', 'the menu answered'), ('controller', 'the server was confirmed'),
                                           ('service', 'the services were confirmed')) if role in roles]
        self.say(title, [f'Installed by Play: {", ".join(installed) or "nothing"}.'] +
                 ([f'On the guest {" and ".join(reached)}.'] if reached else []))

    def first_boot(self):
        if self.args.guest:
            return self.first_boot_guest()
        if getattr(self, 'proven_start', False):
            # The first start was seen and its check fetched: nothing is left to do but what the card holds.
            self.say('First boot', ['Done: the image is written, and its first start found it on the root device.',
                                    'The packages on the card: once the boot menu is installed, it offers them at a start '
                                    '(Packages); the first time, switch the player off, then on holding Play. '
                                    '.disc/boot/result.json on the card says what was installed.',
                                    f'This run\'s report: {self.work/"report.json"}'])
            self.done('first boot', proven=True)
            return
        self.say('First boot', ['Disconnect the cable and put the card in: leaving USB Boot, the player restarts into the new '
                                'system by itself. The first time, once it has started, switch it off; then hold Play, press '
                                'the power key briefly and let Play go once the logo shows (a power key held about ten seconds '
                                'switches the player off): the boot layer installs the packages from the card and writes '
                                '.disc/boot/result.json; the player page then answers on the network. Once the boot menu is '
                                'installed, it offers the packages on the card at a start itself (Packages).'])
        self.done('first boot')

    def run(self):
        self.work.mkdir(parents=True, exist_ok=True)
        try:
            if getattr(self.args, 'resume', None):
                # The write of a run that stopped before the writer: its card and backup stand.
                self.step = 4
                self.resume_write()
                if self.report.get('status') != 'restored':
                    self.first_boot()
                    self.report['status'] = 'prepared'
                return 0
            if self.args.restore and getattr(self.args, 'run', None):
                # Back to stock with a run's own package: no build, no review, the player in any state.
                self.step = 4
                self.restore_from_run()
                return 0
            self.check()
            if self.packages_only:
                folders, apps = self.packages()
                self.card(folders, apps)
                self.on_the_player()
                self.report['status'] = 'prepared'
                return 0
            image = self.firmware()
            if self.args.restore:
                # Back to stock: the restore image built beside the boot layer's, the same path to the player.
                stock = next(Path(image).parent.glob('stock-v*-restore-review-only.bin'), None)
                if stock is None:
                    raise Stop('no stock restore image beside the image')
                self.step = 4
                self.player(stock, restore=True)
                # A dry run prepares stock's image only; nothing was restored.
                self.report['status'] = 'restored' if self.report['steps'][-1].get('written') else 'prepared'
                return 0
            folders, apps = self.packages()
            self.image = image
            self.card(folders, apps)
            self.player(image)
            if self.report.get('status') == 'restored':
                return 0        # the new system did not start: the player went back to stock
            self.first_boot()
            self.report['status'] = 'prepared'
        except Stop as stop:
            self.report['status'] = f'stopped: {stop}'
            self.say('Stopped', [str(stop)], tui.ACCENT)
        except KeyboardInterrupt:
            # Ctrl-C at any question or wait: a stop like any other, with its report.
            self.report['status'] = 'stopped: interrupted'
            self.say('Stopped', ['Interrupted. Nothing was retried; the run\'s report is ' + str(self.work/'report.json') + '.'], tui.ACCENT)
        finally:
            if self.guest is not None:
                # Disposable: the stack goes, the report keeps what it showed.
                self.guest.down()
            (self.work/'report.json').write_text(json.dumps(self.report, indent=2) + '\n')
            self.screen.finish()
        return 0 if self.report['status'] in ('prepared', 'restored') else 1
