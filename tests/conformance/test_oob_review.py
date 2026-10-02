"""Offline OOB classification with synthetic records only."""
import copy
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
from deployment import oob_review as ob


class OobReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = ob.load_profile()
        self.reader = ob.load_reader_profile(self.base)
        self.policy = ob.load_metadata_policy(self.base, self.reader)
        self.layout = ob.load_layout(self.policy['chip'])
        self.nonce = bytes(range(16))
        self.path = self.root/'records.bin'

    def frame(self, page, sequence, data, status=0):
        request = ob.encode_request(2, self.nonce, sequence, page)
        q = struct.unpack('<12I', request)
        words = [0x3153524e, 1, 0x454e4f44, 0, *q[3:7], q[7], q[2], q[8],
                 len(data), zlib.crc32(data), 0x120b, 0x38, 0x10, status, 2, 10]
        return request+struct.pack('<19I', *words)+data.ljust(4352, b'\0')

    def capture(self, changes=(), pages=(10, 10, 11, 10), status=0):
        frames = []
        for index, page in enumerate(pages):
            data = bytearray(bytes([page])*2048+b'\xff'*128)
            if index == 1:
                for offset in changes: data[offset] ^= 1
            frames.append(self.frame(page, index+1, bytes(data), status))
        self.path.write_bytes(b''.join(frames))
        return frames

    def audit(self, **changes):
        args = dict(path=self.path, count=4, pages=[10], nonce=self.nonce,
                    base=self.base, reader=self.reader, policy=self.policy, layout=self.layout)
        args.update(changes)
        return ob.audit(**args)

    def test_parity_changes_are_classified_without_qualification(self):
        self.capture(range(2112, 2164))
        result = self.audit()
        self.assertEqual(result['changed_regions'], ['ecc_parity'])
        self.assertTrue(result['differences_confined_to_ecc_parity'])
        self.assertFalse(result['selected_repeats_byte_identical'])
        self.assertEqual(result['pages'][0]['comparisons'][0]['changed_offsets']['ecc_parity'], list(range(52)))
        self.assertEqual(result['pages'][0]['comparisons'][1]['changed_offsets'], {})
        for flag in ('physical_device_accessed', 'physical_provenance_verified',
                     'cause_established', 'raw_nand_verified', 'flash_ready'):
            self.assertFalse(result[flag])

    def test_every_region_boundary_is_classified(self):
        for offset, name, relative in [(0, 'main', 0), (2047, 'main', 2047),
                                      (2048, 'protected_metadata', 0), (2111, 'protected_metadata', 63),
                                      (2112, 'ecc_parity', 0), (2163, 'ecc_parity', 51),
                                      (2164, 'unprotected_metadata', 0), (2175, 'unprotected_metadata', 11)]:
            with self.subTest(offset=offset):
                self.capture([offset])
                result = self.audit()
                self.assertEqual(result['pages'][0]['comparisons'][0]['changed_offsets'], {name: [relative]})
                self.assertEqual(result['differences_confined_to_ecc_parity'], name == 'ecc_parity')

    def test_alternating_parity_is_an_observation_not_a_cause(self):
        frames = []
        for index in range(4):
            data = bytes(2112)+bytes([index % 2])*52+bytes(12)
            frames.append(self.frame(10, index+1, data))
        self.path.write_bytes(b''.join(frames))
        result = self.audit()
        self.assertTrue(result['parity_alternates_by_record'])
        self.assertEqual(sorted(result['parity_sha256_histogram'].values()), [2, 2])
        self.assertFalse(result['cause_established'])
        self.capture([2112])
        self.assertFalse(self.audit()['parity_alternates_by_record'])
        self.capture()
        self.assertFalse(self.audit()['parity_alternates_by_record'])

    def test_comparison_report_is_bounded(self):
        self.capture(pages=[10]*258)
        with self.assertRaisesRegex(ValueError, 'Too many repeat'):
            self.audit(count=258)

    def test_equal_reads_and_missing_repeats(self):
        self.capture()
        result = self.audit()
        self.assertTrue(result['selected_repeats_byte_identical'])
        self.assertFalse(result['differences_confined_to_ecc_parity'])
        for pages in ([11], [12], [], [10, 10], list(range(65))):
            with self.subTest(pages=pages), self.assertRaises(ValueError): self.audit(pages=pages)

    def test_truncated_extra_symlink_and_bounded_inputs(self):
        raw = b''.join(self.capture())
        for data in (b'', raw[:-1], raw+raw[:4476]):
            self.path.write_bytes(data)
            with self.assertRaises(ValueError): self.audit()
        self.path.write_bytes(raw)
        link = self.root/'link'; link.symlink_to(self.path)
        with self.assertRaises(ValueError): self.audit(path=link)
        for count in (0, True, 65537):
            with self.assertRaises(ValueError): self.audit(count=count)
        for sequence in (0, True, 0xffffffff):
            with self.assertRaises(ValueError): self.audit(first_sequence=sequence)

    def test_all_records_validated_including_unselected_pages(self):
        for word, value in [(0, 0), (2, 0), (3, 8), (4, 0), (10, 1), (11, 0),
                            (12, 0), (13, 0), (14, 256), (15, 0x50), (16, 1),
                            (16, 256), (17, 1), (18, 9)]:
            frames = self.capture()
            frame = bytearray(frames[2]); struct.pack_into('<I', frame, 48+4*word, value)
            frames[2] = bytes(frame); self.path.write_bytes(b''.join(frames))
            with self.subTest(word=word), self.assertRaises(ValueError): self.audit()
        for ecc in range(16):
            self.capture(status=ecc << 4)
            if ecc <= 8: self.assertEqual(self.audit()['ecc_histogram'][ecc], 4)
            else:
                with self.assertRaises(ValueError): self.audit()

    def test_nonce_order_padding_and_page_bounds(self):
        for mode in ('nonce', 'order', 'padding', 'page'):
            frames = self.capture()
            if mode == 'nonce': self.nonce = b'x'*16
            elif mode == 'order': frames[0], frames[1] = frames[1], frames[0]
            elif mode == 'padding': frames[0] = frames[0][:-1]+b'x'
            else: frames[0] = self.frame(self.policy['total_pages'], 1, bytes(2176))
            self.path.write_bytes(b''.join(frames))
            with self.subTest(mode=mode), self.assertRaises(ValueError): self.audit()
        with self.assertRaises(ValueError): self.audit(nonce=bytes(16))

    def test_layout_drift_gaps_overlap_and_geometry(self):
        self.capture()
        for mode in ('pin', 'gap', 'overlap', 'end', 'name', 'geometry'):
            layout = copy.deepcopy(self.layout)
            if mode == 'pin': layout['chip_profile_sha256'] = '0'*64
            elif mode == 'gap': layout['regions'][1]['start'] += 1
            elif mode == 'overlap': layout['regions'][1]['start'] -= 1
            elif mode == 'end': layout['regions'][-1]['end'] += 1
            elif mode == 'name': layout['regions'][0]['name'] = 'ecc_parity'
            else: layout['regions'][0]['end'] -= 1; layout['regions'][1]['start'] -= 1
            with self.subTest(mode=mode), self.assertRaises(ValueError): self.audit(layout=layout)

    def test_cli_and_firmware_identity_are_explicit(self):
        self.capture([2112])
        result = subprocess.run([sys.executable, str(ROOT/'scripts/deployment/oob_review.py'),
                                 '--version', self.base['version'], '--records', str(self.path),
                                 '--nonce', self.nonce.hex(), '--count', '4', '--pages', '10'],
                                text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), self.audit())
        future = self.audit(base=dict(self.base, version='9.99'))
        self.assertNotEqual(future['firmware_profile_sha256'], self.audit()['firmware_profile_sha256'])


if __name__ == '__main__':
    unittest.main()
