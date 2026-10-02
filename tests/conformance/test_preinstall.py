"""Synthetic stock-before-installation checks, distinct from exact image readback."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from deployment import preinstall as pi
from deployment import readback as rb


class PreinstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = rb.load_profile()
        self.reader = rb.load_reader_profile(self.base)
        self.policy = rb.load_metadata_policy(self.base, self.reader)
        self.content = b'hsqs'+bytes(range(256))*11+b'Z'*253  # Boundary inside a page.
        self.base.update(rootfs_size=len(self.content), rootfs_sha256=hashlib.sha256(self.content).hexdigest())
        self.policy['chip'].update(pages_per_block=2, blocks=32)
        self.policy['writer'].update(block_bytes=4096, start_block=3, logical_blocks=2, bad_block_reserve=2)
        self.policy['kernel']['metadata']['offset'] = 0
        self.metadata = (struct.pack('<2I', 0x646e616e, 1)+
                         struct.pack('<32s4I', b'rootfs', 16384, 12288, 0, 0)).ljust(2048, b'\xff')
        self.review = dict(schema_version=1, version=self.base['version'],
                           scope='stock-before-installation', firmware_profile_sha256=rb.fingerprint(self.base),
                           page_policy_sha256=rb.fingerprint(self.policy), tail_value=255,
                           physical_qualified=False)
        self.image = self.root/'restore.bin'
        self.image.write_bytes(self.content.ljust(8192, b'\0'))
        self.digest = hashlib.sha256(self.image.read_bytes()).hexdigest()
        self.path = self.root/'records.bin'
        self.nonce = bytes(range(16))
        self.plan = pi.make_plan(self.base, self.reader, self.policy, self.review, self.metadata, self.digest)

    def frame(self, page, sequence, data, ecc=0):
        request = rb.encode_request(2, self.nonce, sequence, page)
        q = struct.unpack('<12I', request)
        words = [0x3153524e, 1, 0x454e4f44, 0, *q[3:7], q[7], q[2], q[8],
                 len(data), zlib.crc32(data), 0x120b, 0x38, 0x10, ecc << 4, 2, 10]
        return request+struct.pack('<19I', *words)+data.ljust(4352, b'\0')

    def capture(self, tail=255, data=None, bad=(3,), ecc=0):
        data = data if data is not None else self.content.ljust(8192, bytes([tail]))
        frames = []
        for block in range(3, 7):
            marker = bytes(2048)+bytes([0 if block in bad else 255])+bytes(127)
            for _ in range(2): frames.append(self.frame(2*block, len(frames)+1, marker, ecc))
        mapping = [b for b in range(3, 7) if b not in bad][:2]
        for i, block in enumerate(mapping):
            for offset in range(2):
                start = (2*i+offset)*2048
                frames.append(self.frame(2*block+offset, len(frames)+1, data[start:start+2048]+b'\xff'*128, ecc))
        self.path.write_bytes(b''.join(frames))
        return frames

    def verify(self, **changes):
        args = dict(records_path=self.path, restore=self.image, expected_plan=self.plan,
                    base=self.base, reader=self.reader, policy=self.policy, review=self.review,
                    metadata=self.metadata, nonce=self.nonce)
        args.update(changes)
        return pi.verify(**args)

    def test_stock_prefix_and_entire_reviewed_tail_pass(self):
        self.capture(ecc=1)
        result = self.verify()
        self.assertEqual(result['status'], 'saved-stock-preinstall-content-matches')
        self.assertTrue(result['official_rootfs_match'])
        self.assertFalse(result['exact_restore_image_match'])
        self.assertEqual(result['logical_to_physical'], [4, 5])
        self.assertEqual(result['ecc_histogram'][1], 12)
        self.assertEqual(result['rootfs_sha256'], self.base['rootfs_sha256'])
        self.assertEqual(result['tail_bytes'], 8192-len(self.content))
        for key in ('flash_ready', 'physical_device_accessed', 'physical_provenance_verified',
                    'freshness_verified', 'active_boot_verified', 'postwrite_verified'):
            self.assertFalse(result[key])

    def test_strict_postwrite_still_rejects_ff_and_requires_zero_tail(self):
        strict = rb.make_plan(self.base, self.reader, self.policy, self.metadata, self.digest)
        self.capture()
        with self.assertRaisesRegex(ValueError, 'Image mismatch'):
            rb.verify(self.path, self.image, strict, self.base, self.reader, self.policy, self.metadata, self.nonce)
        self.capture(tail=0)
        with self.assertRaisesRegex(ValueError, 'tail'): self.verify()
        result = rb.verify(self.path, self.image, strict, self.base, self.reader, self.policy, self.metadata, self.nonce)
        self.assertEqual(result['status'], 'saved-logical-readback-matches')
        with self.assertRaisesRegex(ValueError, 'plan changed'): self.verify(expected_plan=strict)

    def test_prefix_boundary_last_byte_and_mixed_tail_are_checked(self):
        for pos in (0, len(self.content)-1, len(self.content), 4095, 8191):
            data = bytearray(self.content.ljust(8192, b'\xff')); data[pos] ^= 1
            self.capture(data=bytes(data))
            with self.subTest(pos=pos), self.assertRaises(ValueError): self.verify()

    def test_ecc_nonce_page_order_and_invalid_frames_rejected(self):
        for ecc in (9, 15):
            self.capture(ecc=ecc)
            with self.assertRaisesRegex(ValueError, 'ECC'): self.verify()
        self.capture()
        for nonce in (bytes(16), b'wrong', b'x'*16):
            with self.assertRaises(ValueError): self.verify(nonce=nonce)
        for index in (0, 8, 11):
            frames = self.capture(); frame = bytearray(frames[index]); frame[48+48] ^= 1
            frames[index] = bytes(frame); self.path.write_bytes(b''.join(frames))
            with self.assertRaises(ValueError): self.verify()
        frames = self.capture(); frames[8], frames[9] = frames[9], frames[8]
        self.path.write_bytes(b''.join(frames))
        with self.assertRaises(ValueError): self.verify()

    def test_full_record_count_and_regular_files_required(self):
        raw = b''.join(self.capture())
        for data in (raw[:-1], raw+raw[:4476]):
            self.path.write_bytes(data)
            with self.assertRaises(ValueError): self.verify()
        self.path.write_bytes(raw)
        for key, target in [('records_path', self.path), ('restore', self.image)]:
            link = self.root/(key+'.link'); link.symlink_to(target)
            with self.assertRaises(ValueError): self.verify(**{key: link})

    def test_restore_must_be_pinned_official_rootfs_with_zero_padding(self):
        self.capture()
        for pos in (0, len(self.content)-1, 8191):
            data = bytearray(self.content.ljust(8192, b'\0')); data[pos] ^= 1
            self.image.write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            plan = pi.make_plan(self.base, self.reader, self.policy, self.review, self.metadata, digest)
            with self.subTest(pos=pos), self.assertRaises(ValueError): self.verify(expected_plan=plan)

    def test_review_and_plan_drift_cannot_select_an_unreviewed_tail(self):
        self.capture()
        for key, value in [('tail_value', True), ('tail_value', -1), ('tail_value', 256),
                           ('scope', 'postwrite'), ('firmware_profile_sha256', '0'*64),
                           ('page_policy_sha256', '0'*64), ('physical_qualified', True)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.verify(review=dict(self.review, **{key:value}))
        with self.assertRaises(ValueError): self.verify(review=dict(self.review, tail_value=0))
        with self.assertRaises(ValueError): self.verify(expected_plan=dict(self.plan, rootfs_bytes=1))

    def test_future_firmware_and_new_tail_require_an_explicit_profile(self):
        self.capture(tail=0)
        base = dict(self.base, version='9.99')
        review = dict(self.review, version='9.99', firmware_profile_sha256=rb.fingerprint(base), tail_value=0)
        directory = self.root/'profiles'; (directory/'preinstall').mkdir(parents=True)
        (directory/'preinstall/v9.99.json').write_text(json.dumps(review))
        self.assertEqual(pi.load_review(base, self.policy, directory), review)
        plan = pi.make_plan(base, self.reader, self.policy, review, self.metadata, self.digest)
        result = self.verify(base=base, review=review, expected_plan=plan)
        self.assertTrue(result['exact_restore_image_match'])
        self.assertFalse(result['postwrite_verified'])
        with self.assertRaises(ValueError): pi.load_review(base, self.policy, ROOT/'firmware')

    def test_actual_review_loads_without_private_dependencies(self):
        base = rb.load_profile(); reader = rb.load_reader_profile(base)
        policy = rb.load_metadata_policy(base, reader)
        review = pi.load_review(base, policy)
        self.assertEqual(review['scope'], 'stock-before-installation')
        result = subprocess.run([sys.executable, str(ROOT/'scripts/deployment/preinstall.py'), '--help'],
                                capture_output=True, text=True, check=True)
        self.assertNotIn('--tail', result.stdout)


if __name__ == '__main__':
    unittest.main()
