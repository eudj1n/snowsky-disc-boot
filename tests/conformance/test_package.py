"""The package tool (scripts/package.py) against disc-boot's own rules and recovery."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from firmware_profile import load_profile  # noqa: E402
# The active reviewed firmware profile; the tests hold for whichever one is selected.
PROFILE = load_profile()['version']
TOOL = ROOT/'scripts/package.py'
spec = importlib.util.spec_from_file_location('package_tool', TOOL)
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)
FIXTURE = Path(os.environ.get('DISC_BOOT_FIXTURE_BINARY', ROOT/'build/host/disc-boot-fixture'))

RUN = '#!/bin/sh\ntrap "exit 0" TERM\n: > "$DISC_BOOT_RUN/ready"\nwhile :; do sleep 0.1; done\n'


class PackageToolTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def folder(self, name='pkg', entry='bin/run', script=RUN, extra=None):
        folder = self.root/name
        (folder/entry).parent.mkdir(parents=True, exist_ok=True)
        (folder/entry).write_text(script)
        (folder/entry).chmod(0o755)
        for path, text in (extra or {}).items():
            (folder/path).parent.mkdir(parents=True, exist_ok=True)
            (folder/path).write_text(text)
            (folder/path).chmod(0o644)
        return folder

    def describe(self, folder, role='service', entry='bin/run', arch='fixture', **kwargs):
        return package.describe(folder, kwargs.pop('name', 'disc-server'), kwargs.pop('version', '1'), role, entry,
                                arch=arch, profiles=[PROFILE], **kwargs)

    def cli(self, *args):
        return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True, timeout=60)

    def test_describe_lists_every_file_with_its_mode(self):
        folder = self.folder(extra={'lib/libx.so': 'x', 'share/info.txt': 'info'})
        manifest = self.describe(folder, args=['--listen', '0.0.0.0'], ready=20)
        self.assertEqual(sorted(manifest['files']), ['bin/run', 'lib/libx.so', 'share/info.txt'])
        self.assertEqual(manifest['files']['bin/run']['mode'], '0755')
        self.assertEqual(manifest['files']['lib/libx.so'], dict(size=1, sha256=hashlib.sha256(b'x').hexdigest(), mode='0644'))
        self.assertEqual((manifest['args'], manifest['ready'], manifest['profiles']), (['--listen', '0.0.0.0'], 20, [PROFILE]))
        self.assertEqual(json.loads((folder/'package.json').read_text())['name'], 'disc-server')
        self.assertEqual(package.check(folder, arch='fixture')['bytes'], len(RUN) + 1 + 4)

    def test_describe_refuses_what_disc_boot_would(self):
        folder = self.folder()
        (folder/'link').symlink_to(folder/'bin/run')
        with self.assertRaisesRegex(package.PackageError, 'link is not a regular file'):
            self.describe(folder)
        (folder/'link').unlink()
        (folder/'my file').write_text('x')
        with self.assertRaisesRegex(package.PackageError, 'my file is not a safe package path'):
            self.describe(folder)
        (folder/'my file').unlink()
        (folder/'bin/run').chmod(0o644)
        with self.assertRaisesRegex(package.PackageError, 'is not executable'):
            self.describe(folder)
        (folder/'bin/run').chmod(0o755)
        with self.assertRaisesRegex(package.PackageError, "entry must be named mq_ui"):
            self.describe(folder, role='ui')
        with self.assertRaisesRegex(package.PackageError, 'bin/other is not in the folder'):
            self.describe(folder, entry='bin/other')

    def test_describe_names_a_ui_package_player_launcher(self):
        folder = self.folder(entry='bin/mq_ui', extra={'share/info': 'x'})
        manifest = self.describe(folder, role='ui', entry='bin/mq_ui', player='bin/mq_ui')
        self.assertEqual(manifest['player'], 'bin/mq_ui')
        self.assertEqual(package.check(folder, arch='fixture')['player'], 'bin/mq_ui')
        self.assertNotIn('player', self.describe(folder, role='ui', entry='bin/mq_ui'))
        for player in ('share/info', 'bin/other'):
            with self.subTest(player), self.assertRaisesRegex(package.PackageError, 'not an executable file'):
                self.describe(folder, role='ui', entry='bin/mq_ui', player=player)
        with self.assertRaisesRegex(package.PackageError, 'only a ui package'):
            self.describe(self.folder(name='service'), player='bin/run')

    def test_a_zip_is_deterministic_and_checks_like_its_folder(self):
        folder = self.folder(extra={'lib/libx.so': 'x'})
        self.describe(folder)
        first = package.zip_package(folder, self.root/'a.zip')
        second = package.zip_package(folder, self.root/'b.zip')
        self.assertEqual(first['sha256'], second['sha256'])
        with zipfile.ZipFile(self.root/'a.zip') as z:
            self.assertEqual(z.namelist(), ['package.json', 'bin/run', 'lib/libx.so'])
            self.assertEqual(stat.S_IMODE(z.getinfo('bin/run').external_attr >> 16), 0o755)
        result = self.cli('check', '--source', str(self.root/'a.zip'), '--arch', 'fixture')
        self.assertEqual(json.loads(result.stdout)['ok'], True, result.stdout + result.stderr)
        with self.assertRaisesRegex(package.PackageError, 'exists'):
            package.zip_package(folder, self.root/'a.zip')

    def test_a_zip_with_unsafe_entries_is_refused(self):
        def make(name, entries):
            path = self.root/name
            with zipfile.ZipFile(path, 'w') as z:
                for entry, data, mode in entries:
                    info = zipfile.ZipInfo(entry)
                    info.external_attr = mode << 16
                    z.writestr(info, data)
            return path
        cases = {
            'is not a regular file': make('link.zip', [('package.json', '{}', stat.S_IFREG | 0o644), ('bin/run', 'target', stat.S_IFLNK | 0o777)]),
            'has an unsafe path': make('escape.zip', [('../evil', 'x', stat.S_IFREG | 0o644)]),
            'holds more than a package may': make('big.zip', [('big', b'\0' * (33 * 1024 * 1024), stat.S_IFREG | 0o644)]),
        }
        for message, archive in cases.items():
            with self.subTest(message), tempfile.TemporaryDirectory() as temp:
                with self.assertRaisesRegex(package.PackageError, message):
                    package.source_folder(archive, temp)

    def test_stage_needs_the_confirmation_and_replaces_the_role_whole(self):
        card = self.root/'card'
        card.mkdir()
        folder = self.folder()
        self.describe(folder, arch=package.ARCH)
        archive = self.root/'v1.zip'
        package.zip_package(folder, archive)
        refused = self.cli('stage', '--package', str(archive), '--card', str(card))
        self.assertEqual(refused.returncode, 2)
        self.assertFalse((card/'.disc').exists())
        staged = self.cli('stage', '--package', str(archive), '--card', str(card), '--confirm-card-write')
        self.assertEqual(staged.returncode, 0, staged.stdout + staged.stderr)
        target = card/'.disc/boot/install/service'
        self.assertEqual(sorted(p.relative_to(target).as_posix() for p in target.rglob('*') if p.is_file()), ['bin/run', 'package.json'])
        # A broken package stages nothing and leaves what was staged.
        (folder/'bin/run').write_text('changed')
        broken = self.cli('stage', '--package', str(folder), '--card', str(card), '--confirm-card-write')
        self.assertEqual(broken.returncode, 1)
        self.assertIn('bin/run has 7 bytes', json.loads(broken.stdout)['error'])
        self.assertEqual((target/'bin/run').read_text(), RUN)
        self.assertFalse((card/'.disc/boot/install/.service.staging').exists())
        self.assertEqual(json.loads(self.cli('result', '--card', str(card)).stdout)['result'], None)

    def test_ui_packages_are_staged_under_their_names(self):
        card = self.root/'card'
        card.mkdir()
        for name in ('alpha', 'beta'):
            folder = self.folder(name=name, entry='bin/mq_ui')
            self.describe(folder, role='ui', entry='bin/mq_ui', name=name, arch=package.ARCH)
            staged = package.stage(folder, card, profile=PROFILE)
            self.assertEqual(staged['path'], str(card/'.disc/boot/install/ui'/name))
        menu = self.folder(name='menu', entry='bin/mq_ui')
        self.describe(menu, role='menu', entry='bin/mq_ui', name='disc-menu', arch=package.ARCH)
        self.assertEqual(package.stage(menu, card, profile=PROFILE)['path'], str(card/'.disc/boot/install/menu'))
        self.assertEqual(sorted(p.name for p in (card/'.disc/boot/install/ui').iterdir()), ['alpha', 'beta'])

    # The tool and disc-boot agree, decision and message alike.

    def boot_verify(self, folder, role):
        env = dict(os.environ, DISC_BOOT_FIXTURE_ROOT=str(self.root/'fixture-root'))
        (self.root/'fixture-root').mkdir(exist_ok=True)
        result = subprocess.run([str(FIXTURE), 'verify', role, str(folder), '--profile', PROFILE], env=env,
                                capture_output=True, text=True, timeout=30)
        answer = json.loads(result.stdout)
        return answer.get('error') if not answer['ok'] else None

    def tool_verify(self, folder, role):
        try:
            package.check(folder, role, PROFILE, arch='fixture')
            return None
        except package.PackageError as error:
            return str(error)

    def test_the_tool_and_disc_boot_agree(self):
        def edit(**fields):
            return lambda m: m.update(fields)
        manifest_cases = {
            'good': None,
            'schema': edit(schema=2), 'name': edit(name='Disc Server'), 'version': edit(version=''),
            'role': edit(role='daemon'), 'api': edit(bootApi=0), 'api ahead': edit(bootApi=2),
            'arch': edit(arch='mips32el-linux-static'), 'no arch': edit(arch=''), 'entry path': edit(entry='/bin/run'),
            'ready': edit(ready=0), 'profiles': edit(profiles=[]), 'profile kind': edit(profiles=[257]),
            'other profile': edit(profiles=['2.58']), 'args': edit(args='--x'), 'arg kind': edit(args=[1]),
            'no files': edit(files={}),
            'unsafe file': lambda m: m['files'].update({'a b': dict(size=1, sha256='0' * 64, mode='0644')}),
            'file fields': lambda m: m['files']['bin/run'].update(mode='0777'),
            'sha case': lambda m: m['files']['bin/run'].update(sha256='A' * 64),
            'too big': lambda m: m['files'].update({'big': dict(size=33 * 1024 * 1024, sha256='0' * 64, mode='0644')}),
            'entry unlisted': edit(entry='bin/other'),
            'float size': lambda m: m['files']['bin/run'].update(size=1.0),
            'service player': edit(player='bin/run'),
            'title': edit(title='Disc Server'), 'title empty': edit(title=''), 'title long': edit(title='x' * 33),
            'title kind': edit(title=7),
        }
        for label, change in manifest_cases.items():
            with self.subTest(label):
                folder = self.folder(name=f'm-{label.replace(" ", "-")}')
                manifest = self.describe(folder)
                if change:
                    change(manifest)
                    (folder/'package.json').write_text(json.dumps(manifest))
                self.assertEqual(self.tool_verify(folder, 'service'), self.boot_verify(folder, 'service'))
        ui_cases = {
            'ui good': None, 'ui player': edit(player='bin/mq_ui'), 'ui player unlisted': edit(player='bin/other'),
            'ui player path': edit(player='../bin/mq_ui'), 'ui player kind': edit(player=1),
            'ui player empty': edit(player=''), 'ui player mode': edit(player='share/info'),
        }
        for label, change in ui_cases.items():
            with self.subTest(label):
                folder = self.folder(name=f'm-{label.replace(" ", "-")}', entry='bin/mq_ui', extra={'share/info': 'x'})
                manifest = self.describe(folder, role='ui', entry='bin/mq_ui')
                if change:
                    change(manifest)
                    (folder/'package.json').write_text(json.dumps(manifest))
                self.assertEqual(self.tool_verify(folder, 'ui'), self.boot_verify(folder, 'ui'))
        menu_cases = {
            'menu good': None, 'menu player': edit(player='bin/mq_ui'), 'menu entry': edit(entry='share/info'),
        }
        for label, change in menu_cases.items():
            with self.subTest(label):
                folder = self.folder(name=f'm-{label.replace(" ", "-")}', entry='bin/mq_ui', extra={'share/info': 'x'})
                (folder/'share/info').chmod(0o755)
                manifest = self.describe(folder, role='menu', entry='bin/mq_ui', name='disc-menu')
                if change:
                    change(manifest)
                    (folder/'package.json').write_text(json.dumps(manifest))
                self.assertEqual(self.tool_verify(folder, 'menu'), self.boot_verify(folder, 'menu'))
        damage_cases = {
            'hash': lambda d: (d/'bin/run').write_text(RUN.replace('0.1', '0.2')),
            'size': lambda d: (d/'lib/libx.so').write_text('xyz'),
            'mode': lambda d: (d/'lib/libx.so').chmod(0o600),
            'extra': lambda d: (d/'extra').write_text('unlisted'),
            'link': lambda d: (d/'link').symlink_to(d/'bin/run'),
            'missing': lambda d: (d/'lib/libx.so').unlink(),
            'listed link': lambda d: ((d/'lib/libx.so').unlink(), (d/'lib/libx.so').symlink_to(d/'bin/run')),
            'repeated key': lambda d: (d/'package.json').write_text((d/'package.json').read_text().rstrip()[:-1] + ',"name":"x"}'),
            'not json': lambda d: (d/'package.json').write_text('[]'),
            'role mismatch': None,
        }
        for label, damage in damage_cases.items():
            with self.subTest(label):
                folder = self.folder(name=f'd-{label.replace(" ", "-")}', extra={'lib/libx.so': 'x'})
                self.describe(folder)
                if damage:
                    damage(folder)
                role = 'ui' if label == 'role mismatch' else 'service'
                tool, boot = self.tool_verify(folder, role), self.boot_verify(folder, role)
                self.assertIsNotNone(boot, label)
                self.assertEqual(tool, boot)

    def test_a_staged_zip_is_installed_by_disc_boot_on_play(self):
        root = self.root/'player'
        for name in ('run', 'usr/data', 'tmp/sdcard', 'proc', 'fixture'):
            (root/name).mkdir(parents=True, exist_ok=True)
        (root/'proc/mounts').write_text('/dev/mmcblk0p1 /tmp/sdcard exfat rw 0 0\n')
        (root/'fixture/keys').write_text('play')
        folder = self.folder()
        self.describe(folder, version='7')
        package.zip_package(folder, self.root/'server.zip')
        staged = package.stage(self.root/'server.zip', root/'tmp/sdcard', profile=PROFILE, arch='fixture')
        self.assertEqual((staged['role'], staged['version']), ('service', '7'))
        env = dict(os.environ, DISC_BOOT_FIXTURE_ROOT=str(root), DISC_BOOT_FIXTURE_TIMING='confirm=1,grace=1,card=2')
        boot = lambda *a: subprocess.run([str(FIXTURE), *a], env=env, capture_output=True, text=True, timeout=30, check=True)
        try:
            boot('early', '--profile', PROFILE, '--card', '/tmp/sdcard', '--card-source', '/dev/mmcblk0p1')
            boot('start')
            until = time.monotonic() + 15
            status = None
            while time.monotonic() < until:
                path = root/'run/disc-boot/service.json'
                status = json.loads(path.read_text()) if path.exists() else None
                if status and status['state'] == 'confirmed':
                    break
                time.sleep(0.1)
            self.assertEqual((status['state'], status['version']), ('confirmed', '7'), status)
            result = package.result(root/'tmp/sdcard')
            self.assertEqual(result['roles']['service'], dict(installed=True, note='installed disc-server 7'))
        finally:
            subprocess.run([str(FIXTURE), 'stop'], env=env, capture_output=True, timeout=30)
            subprocess.run(['pkill', '-f', str(root)], capture_output=True)


if __name__ == '__main__':
    unittest.main()
