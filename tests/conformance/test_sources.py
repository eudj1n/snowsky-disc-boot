"""scripts/sources.py: diskOS's files the reviewed tools read, by the profiles' pins (plan, stage 6)."""
import hashlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
import sources  # noqa: E402


class SourcesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.content = {'flash/spl.bin': b'spl', 'src/usbboot/usbboot.c': b'int main;'}
        self.files = {p: hashlib.sha256(d).hexdigest() for p, d in self.content.items()}
        patcher = mock.patch.object(sources, 'diskos_files', return_value=self.files)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.asked = []

    def opener(self, served=None):
        served = served or self.content

        def open_url(url):
            self.asked.append(url)
            path = url.split(sources.load()['revision'] + '/', 1)[1]
            return io.BytesIO(served[path])
        return open_url

    def test_a_copy_with_the_pinned_digests_is_taken_where_it_is(self):
        checkout = self.root/'checkout'
        for path, data in self.content.items():
            (checkout/path).parent.mkdir(parents=True, exist_ok=True)
            (checkout/path).write_bytes(data)
        self.assertEqual(sources.fetch('2.57', [checkout], self.root/'cache', allow_download=False), checkout)
        (checkout/'flash/spl.bin').write_bytes(b'other')
        self.assertEqual(sources.differs(checkout, self.files), ['flash/spl.bin'])
        with self.assertRaisesRegex(sources.SourceError, 'no copy of diskOS'):
            sources.fetch('2.57', [checkout], self.root/'cache', allow_download=False)

    def test_fetched_file_by_file_from_the_revision_checked_and_kept(self):
        got = sources.fetch('2.57', (), self.root/'cache', opener=self.opener())
        revision = sources.load()['revision']
        self.assertEqual(got, self.root/'cache'/f'diskos-{revision[:7]}')
        self.assertEqual(sources.differs(got, self.files), [])
        self.assertTrue(all(url.startswith(f'https://raw.githubusercontent.com/b0hemia/diskos/{revision}/') for url in self.asked))
        self.asked.clear()
        self.assertEqual(sources.fetch('2.57', (), self.root/'cache', allow_download=False), got)
        self.assertEqual(self.asked, [], 'the kept copy is used again')

    def test_a_file_that_does_not_match_its_pin_keeps_nothing(self):
        with self.assertRaisesRegex(sources.SourceError, 'does not match its pin'):
            sources.fetch('2.57', (), self.root/'cache', opener=self.opener({**self.content, 'flash/spl.bin': b'evil'}))
        self.assertEqual(sorted(p.name for p in (self.root/'cache').iterdir()), [])


class PinnedFilesTests(unittest.TestCase):
    def test_the_profiles_pin_the_six_files_the_tools_read(self):
        files = sources.diskos_files('2.57')
        self.assertEqual(sorted(files), ['diskos_installer/flasher.py', 'flash/disc_spl_lpddr3.bin', 'flash/my_write5.c',
                                         'flash/my_write5_dram.bin', 'spl-src/uboot-xburst-lpddr3-src.tar.gz',
                                         'src/usbboot/usbboot.c'])
        self.assertEqual(len(sources.load()['revision']), 40)


if __name__ == '__main__':
    unittest.main()
