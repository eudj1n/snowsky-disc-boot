"""scripts/release.py on a stand-in MIPS build: the same bytes on every build, the record kept
once, and the release workflow's check refusing a build, a list of files or a catalog entry
that is not the accepted one."""
import hashlib
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
import release  # noqa: E402


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.mips = self.root/'mips'
        self.mips.mkdir()
        for name in ('disc-boot', 'disc-usb-console', 'disc-menu'):
            (self.mips/name).write_bytes(b'\x7fELF' + name.encode() * 100)
        (self.mips/'build-id').write_text('b43034ba1e27\n')
        self.releases = self.root/'releases'

    def payloads(self, firmware, diskos, output):
        """A stand-in for the payloads' builds (Docker): the same bytes on every build."""
        for name, source in release.payload_members(output):
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(f'{firmware} {name}'.encode())

    def build(self, folder='dist', version='2.57.1', **kw):
        kw.setdefault('payloads', self.payloads)
        kw.setdefault('diskos', self.root/'diskos')
        return release.build(version, self.root/folder, self.mips, **kw)

    def catalog(self, sha, size, name='disc-menu-2.57.1.zip'):
        path = self.root/'packages.json'
        path.write_text(json.dumps(dict(schema=1, kind='packages', entries=[dict(
            name='disc-menu', source=dict(url=release.url('2.57.1', name), sha256=sha, size=size))])))
        return path

    def test_the_files_are_the_same_on_every_build(self):
        first, second = self.build('a'), self.build('b')
        self.assertEqual(first, second)
        for name in first['files']:
            self.assertEqual((self.root/'a'/name).read_bytes(), (self.root/'b'/name).read_bytes(), name)
        with zipfile.ZipFile(self.root/'a/disc-menu-2.57.1.zip') as z:
            manifest = json.loads(z.read('package.json'))
            self.assertEqual((manifest['name'], manifest['version'], manifest['role'], manifest['profiles'], manifest['homepage']),
                             ('disc-menu', '2.57.1', 'menu', ['2.57'], release.REPOSITORY))
            self.assertEqual(sorted(z.namelist()), ['LICENSE', 'bin/mq_ui', 'licenses/Inter.OFL', 'package.json'])
        with tarfile.open(self.root/'a/disc-boot-2.57.1-mips.tar.gz') as tar:
            self.assertEqual(sorted(tar.getnames()), [f'disc-boot-2.57.1/{n}' for n in ('LICENSE', 'build-id', 'disc-boot', 'disc-usb-console')])
            self.assertEqual(tar.getmember('disc-boot-2.57.1/disc-boot').mode, 0o755)

    def test_what_is_never_a_release(self):
        cases = {'2.57': 'not a release version', '2.57.1-rc.1': 'not a release version', '3.01.1': 'not a reviewed firmware profile'}
        for version, message in cases.items():
            with self.subTest(version), self.assertRaisesRegex(release.ReleaseError, message):
                self.build(version=version)
        self.assertEqual(self.build('ci', version='2.57.0-ci.abc1234', release=False)['version'], '2.57.0-ci.abc1234')
        for build_id in ('b43034ba1e27+changes', 'unknown'):
            (self.mips/'build-id').write_text(build_id + '\n')
            with self.subTest(build_id), self.assertRaisesRegex(release.ReleaseError, 'build committed sources'):
                self.build('x' + build_id[-3:])

    def test_a_recorded_build_passes_its_check_and_others_do_not(self):
        built = self.build()
        menu = built['files']['disc-menu-2.57.1.zip']
        with self.assertRaisesRegex(release.ReleaseError, 'no record'):
            release.check('2.57.1', self.root/'dist', self.releases, self.catalog(menu['sha256'], menu['bytes']))
        data = release.record('2.57.1', self.root/'dist', 'boot_guest and menu_guest on the guest', self.releases)
        self.assertEqual((data['tag'], data['buildId']), ('v2.57.1', 'b43034ba1e27'))
        with self.assertRaisesRegex(release.ReleaseError, 'recorded once'):
            release.record('2.57.1', self.root/'dist', 'again', self.releases)
        checked = release.check('2.57.1', self.root/'dist', self.releases, self.catalog(menu['sha256'], menu['bytes']))
        self.assertEqual(checked['catalogEntries'], 1)
        self.assertIn('`disc-menu-2.57.1.zip`', release.notes(checked))
        with self.assertRaisesRegex(release.ReleaseError, 'another digest'):
            release.check('2.57.1', self.root/'dist', self.releases, self.catalog('0' * 64, menu['bytes']))
        (self.mips/'disc-boot').write_bytes(b'\x7fELF another build')
        self.build('other')
        with self.assertRaisesRegex(release.ReleaseError, 'disc-boot-2.57.1-mips.tar.gz differ'):
            release.check('2.57.1', self.root/'other', self.releases, self.catalog(menu['sha256'], menu['bytes']))
        (self.root/'dist/extra.bin').write_bytes(b'x')
        with self.assertRaisesRegex(release.ReleaseError, 'a release holds'):
            release.check('2.57.1', self.root/'dist', self.releases, self.catalog(menu['sha256'], menu['bytes']))

    def test_the_installer_takes_the_newest_boot_release_and_only_its_programs(self):
        """Plan, stage 6: the image is built from the release file, by its record's digest."""
        for version in ('2.57.1', '2.57.2'):
            self.build(f'dist-{version}', version)
            release.record(version, self.root/f'dist-{version}', 'the guest', self.releases)
        chosen = release.boot_release('2.57', self.releases)
        self.assertEqual((chosen['version'], chosen['name']), ('2.57.2', 'disc-boot-2.57.2-mips.tar.gz'))
        archive = self.root/'dist-2.57.2'/chosen['name']
        self.assertEqual(chosen['archive'], dict(url=release.url('2.57.2', chosen['name']), size=archive.stat().st_size,
                                                 sha256=hashlib.sha256(archive.read_bytes()).hexdigest()))
        self.assertEqual(chosen['payloads']['name'], 'disc-usb-payloads-2.57.2.tar.gz')
        built = release.extract_payloads(self.root/'dist-2.57.2'/chosen['payloads']['name'], '2.57.2', self.root/'payloads')
        self.assertEqual((built/'rootfs-probe/identity.bin').read_bytes(), b'2.57 rootfs-probe/identity.bin')
        self.assertTrue((built/'boot-evidence/uboot/identity.elf').is_file())
        programs = release.extract_boot(archive, '2.57.2', chosen['buildId'], self.root/'boot')
        self.assertEqual(sorted(programs), ['disc-boot', 'disc-usb-console'])
        self.assertEqual(programs['disc-boot'].read_bytes(), (self.mips/'disc-boot').read_bytes())
        with self.assertRaisesRegex(release.ReleaseError, 'another build'):
            release.extract_boot(archive, '2.57.2', 'ffffffffffff', self.root/'other')
        with self.assertRaisesRegex(release.ReleaseError, 'no release of the boot layer'):
            release.boot_release('2.58', self.releases)

    def test_the_repository_records_are_releases(self):
        for path in sorted((ROOT/'releases').glob('*.json')) if (ROOT/'releases').is_dir() else []:
            with self.subTest(path.name):
                data = json.loads(path.read_text())
                self.assertEqual(release.firmware_of(data['version']), data['firmware'])
                self.assertEqual((path.stem, data['tag']), (data['version'], f'v{data["version"]}'))
                self.assertTrue(data['accepted'].strip())


if __name__ == '__main__':
    unittest.main()
