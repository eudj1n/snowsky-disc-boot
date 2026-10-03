"""install.py without a player: the catalogs' packages and the server's apps on a card folder,
the dry run, the confirmation, and what the card step refuses."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
import package  # noqa: E402
from firmware_profile import load_profile  # noqa: E402
from installer import card as cards  # noqa: E402
PROFILE = load_profile()['version']


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class InstallerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.local = self.root/'local'
        self.local.mkdir()
        self.app_zip = self.app(self.local/'player.zip', 'Disc Player', 'p1')
        app_entry = dict(name='Disc Player', version='p1', api=1, license='MIT', default=True,
                         source=dict(url=None, sha256=digest(self.app_zip), size=self.app_zip.stat().st_size),
                         verified=dict(date='2026-10-03', acceptance='test'))
        server = self.folder('disc-server', 'service', 'bin/run', catalog=dict(schema=1, kind='apps', entries=[app_entry]))
        menu = self.folder('disc-menu', 'menu', 'bin/mq_ui')
        entries = []
        for name, role, folder in (('disc-server', 'service', server), ('disc-menu', 'menu', menu)):
            archive = self.local/f'{name}.zip'
            package.zip_package(folder, archive)
            entries.append(dict(name=name, role=role, version='9', profiles=[PROFILE], bootApi=1, license='MIT', default=True,
                                source=dict(url=None, sha256=digest(archive), size=archive.stat().st_size),
                                verified=dict(date='2026-10-03', acceptance='test')))
        self.catalog = self.root/'packages.json'
        self.catalog.write_text(json.dumps(dict(schema=1, kind='packages', entries=entries)))
        self.image = self.root/'disc-boot-v257-review-only.bin'
        self.image.write_bytes(b'an image built before')

    def folder(self, name, role, entry, catalog=None):
        folder = self.root/'src'/name
        (folder/entry).parent.mkdir(parents=True)
        (folder/entry).write_text('#!/bin/sh\nexit 0\n')
        (folder/entry).chmod(0o755)
        if catalog:
            (folder/'catalog').mkdir()
            (folder/'catalog/apps.json').write_text(json.dumps(catalog))
        package.describe(folder, name, '9', role, entry, profiles=[PROFILE])
        return folder

    def app(self, path, name, version, extra=None):
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr(f'{name}/app.json', json.dumps(dict(schema=1, name=name, version=version)))
            z.writestr(f'{name}/index.html', '<!doctype html><title>player</title>')
            for entry, data in (extra or {}).items():
                z.writestr(entry, data)
        return path

    def install(self, *args):
        result = subprocess.run([sys.executable, str(ROOT/'install.py'), '--plain', '--image', str(self.image), '--catalog', str(self.catalog),
                                 '--from', str(self.local), '--work', str(self.root/'run'), *args],
                                capture_output=True, text=True, timeout=120, env=dict(os.environ, DISC_INSTALL_ANY_CARD='1'))
        report = json.loads((self.root/'run/report.json').read_text())
        return result, report

    def test_a_dry_run_stages_into_its_own_folder(self):
        result, report = self.install('--dry-run', '--yes')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(report['status'], 'prepared')
        card = self.root/'run/card'
        self.assertTrue((card/'.disc/boot/install/service/package.json').exists())
        self.assertTrue((card/'.disc/boot/install/menu/package.json').exists())
        self.assertEqual(json.loads((card/'Apps/Disc Player/app.json').read_text())['version'], 'p1')
        self.assertEqual((card/'.disc/dev/usb-console').read_text(), 'DISC_WEB_LOCAL_ROOT_CONSOLE\n')
        self.assertEqual([s['step'] for s in report['steps']], ['check', 'firmware', 'packages', 'card', 'player', 'first boot'])
        self.assertFalse(report['steps'][4]['written'], 'the player is not written in this part')

    def test_the_card_is_written_only_when_confirmed(self):
        card = self.root/'card'
        card.mkdir()
        result, report = self.install('--card', str(card))
        self.assertEqual(result.returncode, 1)
        self.assertIn('confirm with --yes', report['status'])
        self.assertEqual(list(card.iterdir()), [], 'nothing written without the confirmation')
        result, report = self.install('--card', str(card), '--yes')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue((card/'.disc/boot/install/service').is_dir() and (card/'Apps/Disc Player').is_dir())

    def test_a_package_without_a_local_file_stops_the_run(self):
        (self.local/'disc-menu.zip').unlink()
        result, report = self.install('--dry-run', '--yes')
        self.assertEqual(result.returncode, 1)
        self.assertIn('disc-menu: no local file with sha256', report['status'])

    def test_packages_are_chosen_by_role(self):
        data = json.loads(self.catalog.read_text())
        other = dict(data['entries'][0], name='other-server', default=False)
        data['entries'].append(other)
        self.catalog.write_text(json.dumps(data))
        result, report = self.install('--dry-run', '--yes')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('Service   one at most', result.stdout)
        self.assertIn('Boot menu   one at most', result.stdout)
        self.assertEqual([p['name'] for p in report['steps'][2]['packages']], ['disc-server', 'disc-menu'])
        result, report = self.install('--dry-run', '--yes', '--package', 'disc-server', '--package', 'other-server')
        self.assertEqual(result.returncode, 1)
        self.assertIn('one service at most: disc-server, other-server', report['status'])
        result, report = self.install('--dry-run', '--yes', '--package', 'nothing-like-it')
        self.assertIn('not in the catalog: nothing-like-it', report['status'])

    def simulate(self, *args):
        """A simulated player on 64 blocks of the reviewed chip: the image fills 16 logical blocks from block 8."""
        images = self.root/'images'
        images.mkdir(exist_ok=True)
        image = images/'disc-boot-v257-review-only.bin'
        image.write_bytes(bytes(range(256)) * (16 * 131072 // 256))
        (images/'stock-v257-restore-review-only.bin').write_bytes(b'\x5a' * (16 * 131072))
        result = subprocess.run([sys.executable, str(ROOT/'install.py'), '--plain', '--yes', '--dry-run', '--image', str(image),
                                 '--catalog', str(self.catalog), '--from', str(self.local), '--work', str(self.root/'run'),
                                 '--simulate', '--simulate-small', *args], capture_output=True, text=True, timeout=120)
        return result, json.loads((self.root/'run/report.json').read_text())

    def test_a_simulated_player_is_backed_up_written_and_read_back(self):
        result, report = self.simulate('--fault', 'bad-blocks=9,12')
        self.assertEqual(result.returncode, 0, result.stdout)
        player = report['steps'][4]
        self.assertEqual((player['written'], player['badBlocks']), (True, [9, 12]))
        self.assertEqual(player['backup']['bytes'], 64 * 131072, 'the whole NAND')
        self.assertEqual(player['readbackSha256'], digest(self.root/'images/disc-boot-v257-review-only.bin'))
        nand = (self.root/'run/simulated-player/nand.bin').read_bytes()
        self.assertEqual(nand[8 * 131072:9 * 131072], bytes(range(256)) * 512, 'logical block 0 in block 8')
        self.assertEqual(nand[9 * 131072:10 * 131072], bytes(131072), 'bad block 9 skipped')

    def test_a_simulated_player_stops_on_each_fault(self):
        cases = {
            ('no-device',): 'no player in USB Boot was found',
            ('write-stops=3',): 'the write stopped at logical block 3: the outcome is uncertain, nothing is retried',
            ('readback-flip=70000',): 'the readback differs from the image at byte 70000',
            ('bad-blocks=8,9,10,11,12',): 'more bad blocks than the reserve',
        }
        for faults, message in cases.items():
            with self.subTest(faults):
                import shutil
                shutil.rmtree(self.root/'run', ignore_errors=True)
                result, report = self.simulate(*[a for f in faults for a in ('--fault', f)])
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, report['status'])

    def test_stock_goes_back_the_same_way(self):
        result, report = self.simulate('--restore')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(report['status'], 'restored')
        self.assertEqual(report['steps'][-1]['readbackSha256'], digest(self.root/'images/stock-v257-restore-review-only.bin'))

    def test_what_the_card_step_refuses(self):
        for path in ('/', str(Path.home())):
            with self.subTest(path), self.assertRaises(cards.CardError):
                cards.card_ok(path)
        entry = dict(name='Disc Player', version='p1')
        apps = self.root/'Apps'
        cases = {
            'not one folder named': self.app(self.root/'two.zip', 'Disc Player', 'p1', {'Other/x.txt': 'x'}),
            'not a safe file': self.app(self.root/'unsafe.zip', 'Disc Player', 'p1', {'Disc Player/../escape': 'x'}),
            'the catalog names': self.app(self.root/'version.zip', 'Disc Player', 'p2'),
        }
        for message, archive in cases.items():
            with self.subTest(message), self.assertRaisesRegex(cards.CardError, message):
                cards.unpack_app(archive, entry, apps)
        self.assertFalse((apps/'Disc Player').exists())
        self.assertEqual(json.loads((cards.unpack_app(self.app_zip, entry, apps)/'app.json').read_text())['version'], 'p1')


if __name__ == '__main__':
    unittest.main()
