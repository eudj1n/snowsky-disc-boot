"""scripts/deployment/known_images.py and release.py record --image: the images a player may hold, known by
their first blocks (plan, stage 6: the user's installation without a history)."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
sys.path.insert(0, str(ROOT/'scripts/deployment'))
import known_images  # noqa: E402
import release  # noqa: E402

BLOCKS = 262144


def entry(seed, **extra):
    blocks = bytes([seed]) * BLOCKS
    return dict(bytes=BLOCKS * 2, sha256=hashlib.sha256(blocks * 2).hexdigest(),
                first_blocks_sha256=hashlib.sha256(blocks).hexdigest(), **extra)


EXERCISED = dict({k: k[0]*64 if k[0] in 'abcdef' else 'e'*64 for k in known_images.EXERCISED_DIGESTS},
                 image_bytes=2*BLOCKS, writer_entry=0x30, ram_regions=[dict(name='code', address=0, bytes=16)])


class KnownImagesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root/'images').mkdir()
        (self.root/'releases').mkdir()
        self.write_list(entry(1, name='stock.bin'), [entry(2, release='2.57.4', name='old.bin')])

    def write_list(self, stock, earlier, exercised=None):
        (self.root/'images/v2.57.json').write_text(json.dumps(dict(
            schema_version=1, version='2.57', first_blocks_bytes=BLOCKS, stock=stock, earlier=earlier,
            exercised=exercised or EXERCISED)))

    def load(self):
        return known_images.load('2.57', self.root/'images', self.root/'releases')

    def test_the_repositorys_list_names_stock_and_the_owners_images(self):
        known = known_images.load('2.57')
        self.assertEqual(known['stock']['rootfs_sha256'], '111e4dd7ee3d7ff91ba7e61181690be7ffd22bd1bbd13ad513f5f015ffb302ae')
        self.assertIn('615d16c7482c88920146108e1ff682678fafb7294cbd5b2db6b7b4bff6f3b59b', [i['sha256'] for i in known['images']])

    def test_the_probes_blocks_name_stock_a_release_or_nothing(self):
        (self.root/'releases/2.57.5.json').write_text(json.dumps(dict(
            version='2.57.5', firmware='2.57', image=entry(3, name='new.bin', first_blocks_bytes=BLOCKS))))
        known = self.load()
        self.assertEqual([i['release'] for i in known['images']], ['2.57.4', '2.57.5'])
        self.assertEqual(known_images.match(known, bytes([1]) * BLOCKS)[0], 'stock')
        self.assertEqual(known_images.match(known, bytes([3]) * BLOCKS)[1]['release'], '2.57.5')
        self.assertEqual(known_images.match(known, bytes([9]) * BLOCKS), (None, None))
        with self.assertRaises(known_images.KnownImagesError):
            known_images.match(known, bytes(10))

    def test_one_image_recorded_twice_is_one_image(self):
        """A release whose programs did not change builds the same image (2.57.5 after 2.57.4's rootless build)."""
        self.write_list(entry(1, name='stock.bin'), [entry(2, release='2.57.4', name='old.bin')])
        (self.root/'releases/2.57.5.json').write_text(json.dumps(dict(
            version='2.57.5', firmware='2.57', image=entry(2, name='same.bin', first_blocks_bytes=BLOCKS))))
        known = self.load()
        self.assertEqual([i['release'] for i in known['images']], ['2.57.4'])
        self.assertEqual(known_images.match(known, bytes([2]) * BLOCKS)[1]['release'], '2.57.4')

    def test_images_the_probe_could_not_tell_apart_are_refused(self):
        self.write_list(entry(1, name='stock.bin'), [entry(1, release='2.57.4', name='same.bin')])
        with self.assertRaisesRegex(known_images.KnownImagesError, 'tell them apart'):
            self.load()

    def test_the_exercised_write_abi_is_complete(self):
        self.assertEqual(self.load()['exercised'], EXERCISED)
        mine = known_images.load('2.57')['exercised']
        self.assertEqual(mine['image_bytes'], known_images.load('2.57')['stock']['bytes'])
        self.assertNotIn('note', mine)
        for change in (dict(writer_sha256='x'), dict(image_bytes=0), dict(ram_regions=[]),
                       dict(ram_regions=[dict(name='code', address=0)])):
            self.write_list(entry(1, name='stock.bin'), [], dict(EXERCISED, **change))
            with self.subTest(change=change), self.assertRaisesRegex(known_images.KnownImagesError, 'exercised'):
                self.load()

    def test_a_release_records_the_image_the_guest_ran(self):
        folder = self.root/'build'
        folder.mkdir()
        image = folder/'disc-boot-v257-review-only.bin'
        image.write_bytes(bytes([4]) * BLOCKS + bytes(BLOCKS))
        (folder/'report.json').write_text(json.dumps(dict(artifacts={image.name: dict(
            bytes=image.stat().st_size, sha256=hashlib.sha256(image.read_bytes()).hexdigest())})))
        recorded = release.image_of(folder)
        self.assertEqual(recorded['first_blocks_sha256'], hashlib.sha256(bytes([4]) * BLOCKS).hexdigest())
        self.assertEqual((recorded['first_blocks_bytes'], recorded['bytes']), (BLOCKS, 2 * BLOCKS))
        image.write_bytes(b'other')
        with self.assertRaisesRegex(release.ReleaseError, 'not the image its report names'):
            release.image_of(folder)


if __name__ == '__main__':
    unittest.main()
