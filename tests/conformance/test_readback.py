"""Synthetic saved-page streams; no USB, firmware, sibling repository or Docker."""
import copy
import hashlib
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from deployment import readback as rb


class ReadbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = rb.load_profile((ROOT/'firmware/active-version').read_text().strip())
        self.reader = rb.load_reader_profile(self.base)
        self.policy = rb.load_metadata_policy(self.base, self.reader)
        # Small geometry exercises the same ordering with distinct blocks/pages.
        self.policy['chip'].update(pages_per_block=2, blocks=32)
        self.policy['writer'].update(block_bytes=4096, start_block=3, logical_blocks=2, bad_block_reserve=2)
        self.policy['kernel']['metadata']['offset'] = 0
        self.metadata = (struct.pack('<2I', 0x646e616e, 1) +
                         struct.pack('<32s4I', b'rootfs', 16384, 12288, 0, 0)).ljust(2048, b'\xff')
        self.image = self.root/'image.bin'
        self.image.write_bytes(b''.join(bytes([n])*2048 for n in range(1, 5)))
        self.digest = hashlib.sha256(self.image.read_bytes()).hexdigest()
        self.plan = rb.make_plan(self.base, self.reader, self.policy, self.metadata, self.digest)
        self.nonce = bytes(range(16))
        self.path = self.root/'records.bin'

    def frame(self, page, sequence, data, status=0):
        request = rb.encode_request(2, self.nonce, sequence, page)
        q = struct.unpack('<12I', request)
        words = [0x3153524e, 1, 0x454e4f44, 0, *q[3:7], q[7], q[2], q[8],
                 len(data), zlib.crc32(data), 0x120b, 0x38, 0x10, status, 2, 10]
        return request+struct.pack('<19I', *words)+data.ljust(4352, b'\0')

    def capture(self, bad=(3,), status=0):
        frames, sequence = [], 1
        for block in range(3, 7):
            data = bytes(2048)+bytes([0 if block in bad else 255])+bytes(127)
            for _ in range(2):
                frames.append(self.frame(2*block, sequence, data, status)); sequence += 1
        good = [block for block in range(3, 7) if block not in bad][:2]
        for logical, block in enumerate(good):
            for offset in range(2):
                data = bytes([1+2*logical+offset])*2048+b'\xff'*128
                frames.append(self.frame(2*block+offset, sequence, data, status)); sequence += 1
        self.path.write_bytes(b''.join(frames))
        return frames

    def verify(self, **changes):
        args = dict(records_path=self.path, image=self.image, expected_plan=self.plan,
                    base=self.base, reader=self.reader, policy=self.policy,
                    metadata=self.metadata, nonce=self.nonce)
        args.update(changes)
        return rb.verify(**args)

    def change_word(self, frames, index, word, value):
        data = bytearray(frames[index])
        struct.pack_into('<I', data, 48+4*word, value)
        frames[index] = bytes(data)
        self.path.write_bytes(b''.join(frames))

    def replace_data(self, frames, index, offset, value):
        data = bytearray(frames[index])
        data[48+76+offset] = value
        struct.pack_into('<I', data, 48+48, zlib.crc32(data[48+76:48+76+2176]))
        frames[index] = bytes(data)
        self.path.write_bytes(b''.join(frames))

    def test_maps_good_blocks_and_compares_entire_image(self):
        for bad, mapping in [((), [3, 4]), ((3,), [4, 5]), ((4,), [3, 5]),
                             ((3, 4), [5, 6]), ((6,), [3, 4])]:
            with self.subTest(bad=bad):
                self.capture(bad)
                result = self.verify()
                self.assertEqual(result['logical_to_physical'], mapping)
                self.assertEqual(result['bad_blocks'], list(bad))
                self.assertEqual(result['image_sha256'], self.digest)
                self.assertEqual(result['records'], 12)
                for field in ('physical_device_accessed', 'physical_provenance_verified',
                              'freshness_verified', 'active_boot_verified', 'flash_ready'):
                    self.assertFalse(result[field])

    def test_all_ecc_values_including_corrected_and_marker_errors(self):
        for ecc in range(16):
            with self.subTest(ecc=ecc):
                self.capture(status=ecc << 4)
                if ecc <= 8:
                    self.assertEqual(self.verify()['ecc_histogram'][ecc], 12)
                else:
                    with self.assertRaisesRegex(ValueError, 'ECC'): self.verify()

    def test_marker_disagreement_changed_marker_and_wrong_image_page(self):
        for index, offset, value, message in [(1, 2048, 255, 'Ambiguous'),
                                             (8, 2048, 0, 'Marker changed'),
                                             (9, 2047, 99, 'Image mismatch')]:
            frames = self.capture()
            self.replace_data(frames, index, offset, value)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.verify()

    def test_every_truncation_boundary_and_extra_records_fail(self):
        data = b''.join(self.capture())
        for count in list(range(12))+[13]:
            self.path.write_bytes(data[:count*rb.RECORD_BYTES] if count < 12 else data+data[:rb.RECORD_BYTES])
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, 'records'): self.verify()
        self.path.write_bytes(data[:-1])
        with self.assertRaises(ValueError): self.verify()

    def test_duplicate_reordered_stale_requests_and_wrong_physical_page(self):
        for index in (0, 1, 7, 8, 11):
            frames = self.capture()
            data = bytearray(frames[index]); data[32] ^= 1
            frames[index] = bytes(data); self.path.write_bytes(b''.join(frames))
            with self.assertRaisesRegex(ValueError, 'request'): self.verify()
        for mode in ('duplicate', 'swap'):
            frames = self.capture()
            if mode == 'duplicate': frames[9] = frames[8]
            else: frames[8], frames[9] = frames[9], frames[8]
            self.path.write_bytes(b''.join(frames))
            with self.assertRaises(ValueError): self.verify()
        self.capture()
        for nonce in (bytes(16), b'a'*16, b'a'):
            with self.assertRaises(ValueError): self.verify(nonce=nonce)
        for seq in (0, True, 0xffffffff):
            with self.assertRaises(ValueError): self.verify(first_sequence=seq)

    def test_invalid_results_never_become_bad_block_guesses(self):
        for word, value in [(0, 0), (2, 0), (3, 8), (4, 0), (10, 1), (11, 0),
                            (12, 0), (13, 0), (13, 0xffffff), (14, 256),
                            (15, 0), (15, 0x50), (16, 1), (16, 0x100),
                            (17, 1), (17, 2*self.reader['nand_polls']+1), (18, 9)]:
            frames = self.capture()
            self.change_word(frames, 0, word, value)
            with self.subTest(word=word, value=value), self.assertRaises(ValueError): self.verify()

    def test_not_enough_good_blocks(self):
        frames = self.capture()
        for index in range(6): self.replace_data(frames, index, 2048, 0)
        with self.assertRaisesRegex(ValueError, 'Insufficient'): self.verify()

    def test_plan_image_policy_and_partition_changes_rejected(self):
        self.capture()
        for plan in (dict(self.plan, first_block=4), dict(self.plan, capture_bytes=1)):
            with self.assertRaisesRegex(ValueError, 'plan changed'): self.verify(expected_plan=plan)
        policy = copy.deepcopy(self.policy); policy['ecc_admitted'] = 1
        with self.assertRaises(ValueError): self.verify(policy=policy)
        with self.assertRaises(ValueError): self.verify(metadata=b'bad!'+self.metadata[4:])
        for content in (b'wrong', self.image.read_bytes()[:-1]+b'X'):
            self.image.write_bytes(content)
            with self.assertRaises(ValueError): self.verify()

    def test_future_version_has_no_version_branch(self):
        self.capture()
        base = dict(self.base, version='9.99')
        plan = rb.make_plan(base, self.reader, self.policy, self.metadata, self.digest)
        self.assertNotEqual(rb.fingerprint(plan), rb.fingerprint(self.plan))
        self.assertEqual(self.verify(base=base, expected_plan=plan)['image_sha256'], self.digest)

    def test_symlink_capture_or_image_refused(self):
        self.capture()
        for field, target in [('records_path', self.path), ('image', self.image)]:
            link = self.root/(field+'.link'); link.symlink_to(target)
            with self.assertRaises(ValueError): self.verify(**{field: link})


if __name__ == '__main__':
    unittest.main()
