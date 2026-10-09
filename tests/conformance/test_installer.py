"""install.py without a player: the catalogs' packages and the server's apps on a card folder,
the dry run, the confirmation, and what the card step refuses."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
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
        self.assertIn('disc-menu 9: no local file with sha256', report['status'])

    def test_an_archive_not_found_is_asked_for(self):
        """Asked where a missing archive is: a wrong answer asks again, a dropped folder (escaped
        spaces) is looked in, and the run goes on; without questions it stops as before."""
        import argparse
        import builtins
        import io
        from unittest import mock
        from installer import flow, tui
        elsewhere = self.root/'my archives'
        elsewhere.mkdir()
        (self.local/'disc-menu.zip').rename(elsewhere/'disc-menu.zip')
        args = argparse.Namespace(dry_run=True, yes=False, plain=True, ota=None, image=str(self.image), emulator=None, card=None,
                                  package=None, app=None, packages_from=[str(self.local)], download=False, work=str(self.root/'run'),
                                  catalog=str(self.catalog), simulate=None, simulate_small=False, fault=None, restore=False, guest=False,
                                  history=None, diskos=None, libusb=None)
        screen = io.StringIO()
        installer = flow.Installer(args, tui.Screen(look='plain', stream=screen))
        installer.interactive = True
        answers = [str(self.root/'nowhere'), str(elsewhere).replace(' ', '\\ ') + ' ']
        with mock.patch.object(tui, 'read_key', return_value='enter'), mock.patch.object(builtins, 'input', side_effect=answers):
            self.assertEqual(installer.run(), 0, installer.report['status'])
        self.assertIn('nowhere does not exist.', ' '.join(screen.getvalue().split()))
        self.assertIn(elsewhere, installer.places, 'the next archives are looked for there too')
        self.assertTrue((self.root/'run/card/.disc/boot/install/menu/package.json').is_file())
        installer = flow.Installer(args, tui.Screen(look='plain', stream=io.StringIO()))
        installer.interactive = True
        with mock.patch.object(tui, 'read_key', return_value='enter'), mock.patch.object(builtins, 'input', side_effect=['']):
            installer.places = [self.local]
            self.assertEqual(installer.run(), 1)
        self.assertIn('disc-menu 9: no local file with sha256', installer.report['status'])

    def test_the_installers_archive_runs_without_the_repository(self):
        """Plan, stage 6: the installer for users is one archive (release.py installer): install.py with what
        it reads, this release's files and the default server and apps; run from where it was unpacked, its
        own packages come first (offline here) and the run is kept in the user's folder, not in the archive."""
        import release
        mips = self.root/'mips'
        mips.mkdir()
        for name in ('disc-boot', 'disc-usb-console', 'disc-menu'):
            (mips/name).write_bytes(b'\x7fELF' + name.encode() * 100)
        (mips/'build-id').write_text('b43034ba1e27\n')

        def payloads(firmware, diskos, output):
            for name, source in release.payload_members(output):
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(f'{firmware} {name}'.encode())
        dist = self.root/'dist'
        release.build('2.57.9', dist, mips, diskos=self.root/'diskos', payloads=payloads)
        menu = dist/'disc-menu-2.57.9.zip'
        catalog = json.loads(self.catalog.read_text())
        for entry in catalog['entries']:
            if entry['name'] == 'disc-menu':
                entry.update(version='2.57.9', source=dict(url=release.url('2.57.9', menu.name), sha256=digest(menu),
                                                           size=menu.stat().st_size))
        offered = self.root/'release-catalog.json'
        offered.write_text(json.dumps(catalog))
        built = release.installer('2.57.9', dist, [self.local], False, catalog_path=offered, committed=False)
        self.assertEqual(sorted(built['packages']), ['Disc Player-p1.zip', 'disc-boot-2.57.9-mips.tar.gz', 'disc-menu-2.57.9.zip',
                                                     'disc-server-9.zip', 'disc-usb-payloads-2.57.9.tar.gz'])
        unpacked = self.root/'unpacked'
        with tarfile.open(dist/'disc-installer-2.57.9.tar.gz') as tar:
            names = tar.getnames()
            tar.extractall(unpacked, filter='data')
        top = unpacked/'disc-installer-2.57.9'
        self.assertEqual(json.loads((top/'catalog/packages.json').read_text()), catalog)
        self.assertFalse([n for n in names if '/tests/' in n or '/docs/dev/' in n or n.endswith(('/AGENTS.md', '/scripts/test.sh'))])
        self.assertIn('disc-installer-2.57.9/docs/install.md', names, 'the users\' guide, as the README links it')
        self.assertIn('disc-installer-2.57.9/device/usbboot/staging.c', names, 'the payloads are checked by their sources')
        # Its own release is installer.json: the boot programs the image takes are this release's.
        found = subprocess.run([sys.executable, '-c', 'import sys; sys.path.insert(0, "scripts"); import release; '
                                'print(release.boot_release("2.57")["version"])'], cwd=top, capture_output=True, text=True)
        self.assertEqual(found.stdout.strip(), '2.57.9', found.stderr)
        home = self.root/'home'
        env = {k: v for k, v in os.environ.items() if k not in ('XDG_DATA_HOME', 'DISC_EMULATOR')}
        env['HOME'] = str(home)
        result = subprocess.run([sys.executable, 'install.py', '--dry-run', '--yes', '--plain', '--offline', '--image', str(self.image)],
                                cwd=top, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr)
        data = home/'Library/Application Support/SNOWSKY DISC' if sys.platform == 'darwin' else home/'.local/share/snowsky-disc'
        report = next((data/'runs').glob('install-*/report.json'))
        self.assertEqual(json.loads(report.read_text())['status'], 'prepared')
        card = report.parent/'card'
        self.assertEqual(json.loads((card/'.disc/boot/install/menu/package.json').read_text())['version'], '2.57.9')
        self.assertTrue((card/'.disc/boot/install/service/package.json').is_file() and (card/'Apps/Disc Player/app.json').is_file())
        self.assertFalse((top/'work').exists(), 'nothing of the run in the archive')

    def test_the_image_is_built_here_without_docker(self):
        """Plan, stage 6: the guest and the player ran the image built on the computer (2026-10-08), so the
        build in the emulator's Docker image went: without squashfs-tools and openssl the check says what to
        install, and Docker is asked about only for the guest."""
        import argparse
        import io
        from unittest import mock
        from installer import flow, tui
        args = argparse.Namespace(dry_run=True, yes=True, plain=True, ota=None, image=None, emulator=None, card=None,
                                  package=None, app=None, packages_from=[], download=False, work=str(self.root/'run'),
                                  catalog=str(self.catalog), simulate=None, simulate_small=False, fault=None, restore=False,
                                  guest=False, history=None, diskos=None, libusb=None, boot_build=False)
        calls = []
        installer = flow.Installer(args, tui.Screen(look='plain', stream=io.StringIO()),
                                   runner=lambda command, **kw: calls.append(command))
        with mock.patch.object(flow.Installer, 'host_builder', return_value=None):
            with self.assertRaises(flow.Stop) as stopped:
                installer.check()
        self.assertIn('squashfs-tools 4.6 or later and openssl', str(stopped.exception))
        self.assertNotIn('Docker', str(stopped.exception))
        with mock.patch.object(flow.Installer, 'host_builder', return_value='4.7.5'):
            installer.check()
        self.assertEqual(calls, [], 'no Docker asked about without a guest')
        self.assertEqual(installer.report['steps'][-1]['squashfs'], '4.7.5')

    def test_libusb_is_found_where_its_packages_put_it_or_named(self):
        """Plan, stage 6: no --libusb to give; a missing libusb is said with the command that installs it."""
        import argparse
        from unittest import mock
        from installer import flow
        # Homebrew's lib holds a link to the Cellar's file: the file itself is named, the review pins only regular files.
        library = self.root/'Cellar/libusb/1.0.30/lib/libusb-1.0.0.dylib'
        library.parent.mkdir(parents=True)
        library.write_bytes(b'lib')
        (self.root/'lib').mkdir()
        (self.root/'lib/libusb-1.0.0.dylib').symlink_to('../Cellar/libusb/1.0.30/lib/libusb-1.0.0.dylib')
        installer = flow.Installer.__new__(flow.Installer)
        installer.args = argparse.Namespace(libusb=None)
        with mock.patch.object(flow, 'LIBUSB_PLACES', (str(self.root/'nowhere.dylib'), str(self.root/'lib/libusb-*.dylib'))):
            self.assertEqual(installer.libusb(), str(library.resolve()))
            self.assertFalse(Path(installer.libusb()).is_symlink())
        with mock.patch.object(flow, 'LIBUSB_PLACES', (str(self.root/'nowhere.dylib'),)):
            self.assertIsNone(installer.libusb())
        installer.args = argparse.Namespace(libusb='/given/libusb.dylib')
        self.assertEqual(installer.libusb(), '/given/libusb.dylib')
        installer.args = argparse.Namespace(libusb=str(self.root/'lib/libusb-1.0.0.dylib'))
        self.assertEqual(installer.libusb(), str(library.resolve()), 'a link given is followed too')
        self.assertIn('brew install libusb', flow.LIBUSB_HINT)
        self.assertIn('apt install libusb-1.0-0', flow.LIBUSB_HINT)

    def test_the_first_boot_says_how_usb_boot_is_left_and_done_once_the_start_was_checked(self):
        """2026-10-08 (owner): leaving USB Boot restarts the player into the new system by itself, so a
        start with Play comes after it; once the first start's check was fetched, the run says it is done
        rather than repeating the first boot's instructions."""
        import argparse
        import io
        from installer import flow, tui
        args = argparse.Namespace(dry_run=False, yes=True, plain=True, ota=None, image=str(self.image), emulator=None, card=None,
                                  package=None, app=None, packages_from=[str(self.local)], download=False, work=str(self.root/'run'),
                                  catalog=str(self.catalog), simulate=None, simulate_small=False, fault=None, restore=False, guest=False,
                                  history=None, diskos=None, libusb=None)
        for proven, expected, absent in ((False, 'restarts into the new system by itself', 'Done:'),
                                         (True, 'Done: the image is written', 'Disconnect the cable')):
            screen = io.StringIO()
            installer = flow.Installer(args, tui.Screen(look='plain', stream=screen))
            installer.proven_start = proven
            installer.first_boot()
            text = ' '.join(screen.getvalue().split())
            with self.subTest(proven=proven):
                self.assertIn(expected, text)
                self.assertNotIn(absent, text)
                self.assertEqual(installer.report['steps'][-1], dict(step='first boot', **({'proven': True} if proven else {})))
        self.assertIn('switch it off, then switch it on holding Play', flow.Installer.PLAY_AFTER)

    def test_what_an_earlier_run_staged_and_this_one_did_not_choose_leaves_the_card(self):
        """2026-10-07: a server staged by a run that stopped at its review was installed by the next
        run's Play, which had chosen only the menu."""
        card = self.root/'card'
        card.mkdir()
        result, _ = self.install('--card', str(card), '--yes')
        self.assertEqual(result.returncode, 0, result.stdout)
        old_ui = card/'.disc/boot/install/ui/old-ui'
        old_ui.mkdir(parents=True)
        (old_ui/'package.json').write_text('{}')
        result, report = self.install('--card', str(card), '--yes', '--package', 'disc-menu')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue((card/'.disc/boot/install/menu/package.json').exists())
        self.assertFalse((card/'.disc/boot/install/service').exists(), 'the earlier run\'s server is gone')
        self.assertFalse(old_ui.exists())
        step = next(s for s in report['steps'] if s['step'] == 'card')
        self.assertEqual(sorted(step['cleared']), ['.disc/boot/install/service', '.disc/boot/install/ui/old-ui'])
        self.assertIn('no longer staged', result.stdout)

    def test_packages_are_chosen_by_role(self):
        data = json.loads(self.catalog.read_text())
        other = dict(data['entries'][0], name='other-server', default=False)
        data['entries'].append(other)
        self.catalog.write_text(json.dumps(data))
        result, report = self.install('--dry-run', '--yes')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('Service   one at most', result.stdout)
        self.assertIn('Boot menu   one at most', result.stdout)
        self.assertEqual([p['name'] for p in report['steps'][2]['packages']], ['disc-menu', 'disc-server'], 'the menu, the UIs, the service')
        self.assertLess(result.stdout.index('Boot menu'), result.stdout.index('Service'))
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
                shutil.rmtree(self.root/'run', ignore_errors=True)
                result, report = self.simulate(*[a for f in faults for a in ('--fault', f)])
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, report['status'])

    def test_stock_goes_back_the_same_way(self):
        result, report = self.simulate('--restore')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(report['status'], 'restored')
        self.assertEqual(report['steps'][-1]['readbackSha256'], digest(self.root/'images/stock-v257-restore-review-only.bin'))

    def update(self, folder, chunks=None):
        folder.mkdir(parents=True, exist_ok=True)
        for k in range(load_profile()['rootfs_chunks'] if chunks is None else chunks):
            (folder/f'rootfs.squashfs.{k:04d}.{k:064x}.enc').touch()
        return folder

    def test_the_update_is_found_from_any_of_its_folders(self):
        sys.path.insert(0, str(ROOT/'scripts'))
        from installer import flow
        profile = load_profile()
        top = self.root/'SNOWSKY DISC update'
        ota = self.update(top/'main_os'/f'ota_v{profile["main_os_version"]}')
        escaped = str(top).replace(' ', '\\ ')
        for given in (top, top/'main_os', ota, f"'{top}' ", escaped):
            with self.subTest(given):
                self.assertEqual(flow.find_update(given, profile), ota)
        with self.assertRaisesRegex(ValueError, 'is not FiiO'):
            flow.find_update(self.root, profile)
        (ota/f'rootfs.squashfs.0001.{1:064x}.enc').unlink()
        with self.assertRaisesRegex(ValueError, 'numbered from 0000'):
            flow.find_update(top, profile)

    def guest(self, statuses, result=None):
        """install.py --guest with scripts/guest.py replaced by a stand-in: what it was asked, in order."""
        emulator, ota = self.root/'emulator-ref', self.root/'ota'
        (emulator/'emulator').mkdir(parents=True, exist_ok=True)
        self.update(ota)
        shutil.rmtree(self.root/'run', ignore_errors=True)
        calls, card = [], {}
        statuses = list(statuses)

        def runner(command, **kwargs):
            if command[:2] == ['docker', 'info']:
                return subprocess.CompletedProcess(command, 0, '', '')
            args = command[command.index('--state') + 2:]
            state = Path(command[command.index('--state') + 1])
            calls.append(' '.join(args[:3]) if args[0] == 'power' else args[0])
            out = ''
            if args[0] == 'up':
                state.parent.mkdir(parents=True, exist_ok=True)
                state.write_text('{}')
            elif args[0] == 'put':
                tree = Path(args[2])
                card.update({str(f.relative_to(tree)): f.read_bytes() for f in tree.rglob('*') if f.is_file()})
            elif args[0] == 'status':
                out = json.dumps(statuses.pop(0) if len(statuses) > 1 else statuses[0])
            elif args[0] == 'read':
                out = json.dumps(result or dict(schema=1, roles=dict(service=dict(installed=True, note='installed 9'),
                                                                     menu=dict(installed=True, note='installed 9'))))
            elif args[0] == 'down':
                state.unlink()
            return subprocess.CompletedProcess(command, 0, out, '')

        sys.path.insert(0, str(ROOT/'scripts'))
        import argparse
        import io
        from unittest import mock
        from installer import flow, tui
        args = argparse.Namespace(dry_run=False, yes=True, plain=True, ota=str(ota), image=str(self.image), emulator=str(emulator), card=None,
                                  package=None, app=None, packages_from=[str(self.local)], download=False, work=str(self.root/'run'),
                                  catalog=str(self.catalog), simulate=None, simulate_small=False, fault=None, restore=False, guest=True,
                                  history=None, diskos=None, libusb=None)
        installer = flow.Installer(args, tui.Screen(look='plain', stream=io.StringIO()), runner=runner)
        with mock.patch('installer.flow.shutil.which', return_value='/usr/bin/docker'), mock.patch('installer.guest.time.sleep'):
            code = installer.run()
        return code, installer.report, calls, card

    def test_the_guest_takes_the_card_and_is_followed_to_the_end(self):
        code, report, calls, card = self.guest([
            dict(service=dict(state='starting'), menu=dict(state='asking')),
            dict(service=dict(state='ready'), menu=dict(state='answered'), choice=dict(ui='stock', by='menu')),
            dict(service=dict(state='confirmed'), menu=dict(state='answered'), choice=dict(ui='stock', by='menu'))])
        self.assertEqual(code, 0, report['status'])
        self.assertEqual(calls, ['up', 'power off', 'put', 'power on --hold', 'status', 'status', 'status', 'power off', 'read', 'down'])
        self.assertIn('.disc/boot/install/service/package.json', card)
        self.assertIn('.disc/boot/install/menu/package.json', card)
        self.assertEqual(card['.disc/dev/usb-console'], cards.MARKER_TEXT.encode())
        self.assertIn('Apps/Disc Player/index.html', card)
        boot = report['steps'][-1]
        self.assertEqual((boot['step'], boot['status']['service']['state']), ('first boot', 'confirmed'))
        self.assertTrue(boot['result']['roles']['menu']['installed'])
        self.assertEqual(report['steps'][1]['ota'], str(self.root/'ota'), 'the update as given')

    def test_a_guest_that_does_not_get_there_stops_the_run_and_goes(self):
        code, report, calls, card = self.guest([dict(service=dict(state='rolled-back'), menu=dict(state='answered'))])
        self.assertEqual(code, 1)
        self.assertIn('did not finish: service rolled-back', report['status'])
        self.assertEqual(calls[-1], 'down', 'the guest is removed in every case')
        code, report, calls, card = self.guest([dict(service=dict(state='confirmed'), menu=dict(state='answered'))],
                                               result=dict(schema=1, roles=dict(service=dict(installed=False, note='refused: checksum'))))
        self.assertIn('service: refused: checksum', report['status'])

    def test_the_guest_is_not_a_way_back_to_stock(self):
        result = subprocess.run([sys.executable, str(ROOT/'install.py'), '--guest', '--restore'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('a guest starts fresh from each image', result.stderr)
        result = subprocess.run([sys.executable, str(ROOT/'install.py'), '--guest', '--simulate'], capture_output=True, text=True)
        self.assertIn('not allowed with argument', result.stderr)

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
