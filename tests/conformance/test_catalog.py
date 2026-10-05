"""The catalogs (scripts/catalog.py): this repository's packages, a server's apps, and how an entry
becomes a checked package folder from a local file or from someone else's release."""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
spec = importlib.util.spec_from_file_location('catalog_tool', ROOT/'scripts/catalog.py')
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)
package = catalog.package
from firmware_profile import load_profile  # noqa: E402
PROFILE = load_profile()['version']


def digest(data):
    return hashlib.sha256(data).hexdigest()


class CatalogTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def write(self, data, name='catalog.json'):
        path = self.root/name
        path.write_text(json.dumps(data))
        return path

    def test_this_repositorys_catalog_is_valid(self):
        data = catalog.load()
        entries = {e['name']: e for e in data['entries']}
        self.assertEqual(sorted(entries), ['disc-menu', 'disc-server', 'diskos'])
        self.assertEqual({n: e['role'] for n, e in entries.items()}, {'disc-menu': 'menu', 'disc-server': 'service', 'diskos': 'ui'})
        self.assertEqual(sorted(n for n, e in entries.items() if e['default']), ['disc-menu', 'disc-server'])
        # diskOS is built from its own published release on the user's computer, never from here.
        self.assertEqual(entries['diskos']['source']['recipe'], 'diskos-release')
        self.assertTrue(all(PROFILE in e['profiles'] for e in entries.values()))

    def test_this_repositorys_packages_are_releases(self):
        """Debug builds stay local; our own packages are release files, named by the release's
        address and the digests its record keeps (scripts/release.py)."""
        import release
        for entry in catalog.load()['entries']:
            with self.subTest(entry['name']):
                self.assertNotIn('debug', entry['version'])
                if 'recipe' in entry['source']:
                    continue
                self.assertEqual(release.firmware_of(entry['version']), PROFILE)
                name = f'{entry["name"]}-{entry["version"]}.zip'
                if entry['name'] == 'disc-server':
                    # The server's release is recorded in snowsky-disc-server (releases/<version>.json),
                    # which this repository's checks never read: its address and digest are the catalog's.
                    self.assertEqual(entry['source']['url'], 'https://github.com/eudj1n/snowsky-disc-server/releases/'
                                                             f'download/v{entry["version"]}/{name}')
                    continue
                self.assertEqual(entry['source']['url'], release.url(entry['version'], name))
                record = ROOT/'releases'/f'{entry["version"]}.json'
                self.assertTrue(record.is_file(), 'a release is recorded before the catalog names it')
                files = json.loads(record.read_text())['files']
                self.assertEqual((entry['source']['sha256'], entry['source']['size']), (files[name]['sha256'], files[name]['bytes']))

    def test_entries_are_checked(self):
        good = catalog.load()
        cases = {
            'name must match': lambda e: e.update(name='Disc Server'),
            'role must be': lambda e: e.update(role='daemon'),
            'sha256 must be': lambda e: e['source'].update(sha256='A' * 64),
            'url must be an https': lambda e: e['source'].update(url='http://example.com/x.zip'),
            'size must be': lambda e: e['source'].update(size=0),
            'verified must give': lambda e: e.update(verified={'date': '2026-10-03'}),
            'default must be': lambda e: e.update(default='yes'),
            'title must be': lambda e: e.update(title='x' * 33),
        }
        for message, damage in cases.items():
            with self.subTest(message):
                data = copy.deepcopy(good)
                damage(data['entries'][0])
                with self.assertRaisesRegex(catalog.CatalogError, message):
                    catalog.load(self.write(data))
        data = copy.deepcopy(good)
        data['entries'].append(copy.deepcopy(data['entries'][0]))
        with self.assertRaisesRegex(catalog.CatalogError, 'listed twice'):
            catalog.load(self.write(data))
        with self.assertRaisesRegex(catalog.CatalogError, 'a catalog of packages, not apps'):
            catalog.load(catalog.CATALOG, kind='apps')

    def test_a_servers_apps_catalog_has_the_same_form(self):
        page = b'a release zip of the page'
        apps = dict(schema=1, kind='apps', entries=[dict(
            name='Disc Player', version='2026.10.02-05a1422', api=1, license='MIT', default=True,
            source=dict(url=None, sha256=digest(page), size=len(page)),
            verified=dict(date='2026-10-03', acceptance='two_packages.py'))])
        self.assertEqual(catalog.load(self.write(apps), kind='apps')['entries'][0]['name'], 'Disc Player')
        del apps['entries'][0]['api']
        with self.assertRaisesRegex(catalog.CatalogError, 'api must name'):
            catalog.load(self.write(apps))

    def test_an_entry_is_taken_from_a_local_file_with_its_digest(self):
        folder = self.root/'pkg'
        (folder/'bin').mkdir(parents=True)
        (folder/'bin/run').write_text('#!/bin/sh\nexit 0\n')
        (folder/'bin/run').chmod(0o755)
        package.describe(folder, 'disc-server', '7', 'service', 'bin/run', profiles=[PROFILE])
        local = self.root/'local'
        local.mkdir()
        package.zip_package(folder, local/'server.zip')
        data = (local/'server.zip').read_bytes()
        entry = dict(name='disc-server', role='service', version='7', source=dict(url=None, sha256=digest(data), size=len(data)))
        (local/'other.zip').write_bytes(b'x' * len(data))
        fetched = catalog.fetch(entry, self.root/'out', [local])
        self.assertEqual(json.loads((fetched/'package.json').read_text())['version'], '7')
        entry['source']['sha256'] = '0' * 64
        with self.assertRaisesRegex(catalog.CatalogError, 'not published yet'):
            catalog.fetch(entry, self.root/'out2', [local], allow_download=True)
        entry['version'] = '8'
        entry['source']['sha256'] = digest(data)
        with self.assertRaisesRegex(catalog.CatalogError, 'the catalog names disc-server 8'):
            catalog.fetch(entry, self.root/'out3', [local])

    def test_a_published_archive_is_downloaded_checked_and_kept(self):
        folder = self.root/'src'
        (folder/'bin').mkdir(parents=True)
        (folder/'bin/run').write_text('#!/bin/sh\n')
        (folder/'bin/run').chmod(0o755)
        package.describe(folder, 'disc-server', '7', 'service', 'bin/run', profiles=['2.57'])
        published = self.root/'published'
        published.mkdir()
        package.zip_package(folder, published/'server.zip')
        data = (published/'server.zip').read_bytes()
        source = dict(url=(published/'server.zip').as_uri(), sha256=digest(data), size=len(data))
        entry = dict(name='disc-server', role='service', version='7', source=source)
        seen = []
        with self.assertRaisesRegex(catalog.NotLocal, 'downloads not allowed'):
            catalog.fetch(entry, self.root/'out0', [])
        fetched = catalog.fetch(entry, self.root/'out', [], allow_download=True, progress=lambda n, f: seen.append(f),
                                downloads=self.root/'downloads')
        self.assertEqual(json.loads((fetched/'package.json').read_text())['version'], '7')
        self.assertEqual(seen[-1], 1.0)
        self.assertEqual(catalog.find_local(source, [self.root/'downloads']), self.root/'downloads/server.zip', 'kept for the next run')
        with self.assertRaisesRegex(catalog.NotLocal, 'the download from .* failed'):
            catalog.obtain(dict(source, url=(published/'gone.zip').as_uri()), [], self.root, True)
        with self.assertRaisesRegex(catalog.CatalogError, 'does not match its catalog entry'):
            catalog.obtain(dict(source, sha256='0' * 64), [], self.root, True)

    def release(self, member, data):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
            info = tarfile.TarInfo(member)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        path = self.root/'release.tar.gz'
        path.write_bytes(buffer.getvalue())
        return path

    def test_diskos_is_assembled_from_its_release(self):
        binary = b'\x7fELF a stand-in for diskOS\'s UI'
        release = self.release('diskos-installer/payload/mq_ui', binary)
        archive = dict(url='https://example.com/diskos.tar.gz', sha256=digest(release.read_bytes()), size=release.stat().st_size)
        entry = dict(name='diskos', role='ui', version='1.2.0', title='diskOS', profiles=[PROFILE],
                     source=dict(recipe='diskos-release', archives=[archive], member='diskos-installer/payload/mq_ui',
                                 memberSha256=digest(binary)))
        folder = catalog.fetch(entry, self.root/'diskos', [release])
        manifest = json.loads((folder/'package.json').read_text())
        self.assertEqual((manifest['entry'], manifest['player'], manifest['title']), ('mq_ui', 'diskos/mq_ui', 'diskOS'))
        self.assertEqual((folder/'diskos/mq_ui').read_bytes(), binary)
        self.assertIn('/tmp/.diskos_boot_select', (folder/'mq_ui').read_text())
        package.check(folder, 'ui', PROFILE)
        entry['source']['memberSha256'] = '0' * 64
        with self.assertRaisesRegex(catalog.CatalogError, 'does not match its catalog entry'):
            catalog.fetch(entry, self.root/'diskos2', [release])


if __name__ == '__main__':
    unittest.main()
