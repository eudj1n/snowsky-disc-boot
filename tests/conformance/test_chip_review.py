"""Firmware-free chip/writer review tests; no USB or sibling checkout required."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'scripts'))
from deployment import chip_review as review
from deployment.nand_records import DATA_MAX, DONE, RESULT_MAGIC, encode_request


class ChipReviewTests(unittest.TestCase):
    def setUp(self):
        self.chip = review.load_chip('xt26g02c')
        self.writer = review.load_writer('diskos-my-write5-768')
        self.request = encode_request(1, bytes(range(16)), 7)

    def record(self, changes=None):
        q = struct.unpack('<12I', self.request)
        words = [RESULT_MAGIC, 1, DONE, 0, *q[3:7], q[7], 1, 0, 0, 0,
                 0x00120b, 0x38, 0x10, 0, 1, 5]
        for index, value in (changes or {}).items():
            words[index] = value
        return struct.pack('<19I', *words) + bytes(DATA_MAX)

    def run_review(self, raw=None):
        return review.compare(self.request, self.record() if raw is None else raw,
                              self.chip, self.writer)

    def test_documented_matches_do_not_admit_hardware(self):
        result = self.run_review()
        for key in ('page_geometry_matches', 'factory_marker_location_matches', 'scan_fits_documented_chip'):
            self.assertTrue(result['checks'][key])
        for key in ('writer_checks_chip_id', 'writer_reads_ecc_status', 'ecc_disable_assumption_matches'):
            self.assertFalse(result['checks'][key])
        self.assertEqual(result['writer_byte_range'], [0xa00000, 0x7200000])
        for key in ('physical_device_accessed', 'physical_provenance_verified',
                    'page_reads_admitted', 'hardware_qualified', 'flash_ready'):
            self.assertFalse(result[key])
        self.assertEqual(result['result_sha256'], hashlib.sha256(self.record()).hexdigest())

    def test_only_two_id_bytes_are_documented(self):
        self.assertEqual(self.run_review(self.record({13: 0xab120b}))['matched_id_bytes'], 2)
        for value in (0x00e20b, 0x0012c8, 0x0100120b):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.run_review(self.record({13: value}))

    def test_stale_failed_or_wrong_operation_refused(self):
        for changes in ({4: 999}, {8: 8}, {9: 2}, {2: 0}, {3: 1}, {11: 1}, {12: 1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.run_review(self.record(changes))

    def test_invalid_registers_or_busy_chip_refused(self):
        for changes in ({14: 256}, {15: 256}, {16: 256}, {16: 1}):
            with self.assertRaises(ValueError):
                self.run_review(self.record(changes))

    def test_truncated_extra_or_dirty_payload_refused(self):
        raw = self.record()
        for invalid in (raw[:-1], raw+b'\0', raw[:-1]+b'\1'):
            with self.assertRaises(ValueError):
                self.run_review(invalid)

    def test_changed_writer_requires_new_source_audit(self):
        self.writer['logical_blocks'] += 1
        with self.assertRaisesRegex(ValueError, 'Writer changed'):
            self.run_review()

    def test_geometry_marker_and_capacity_mismatches_are_visible(self):
        self.chip.update(page_bytes=4096, blocks=100)
        self.chip['factory_marker']['page_in_block'] = 1
        result = self.run_review()
        for key in ('page_geometry_matches', 'factory_marker_location_matches', 'scan_fits_documented_chip'):
            self.assertFalse(result['checks'][key])
        self.assertFalse(result['flash_ready'])

    def test_profile_cannot_promote_hardware(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'chips').mkdir()
            for key in ('page_reads_admitted', 'writes_admitted'):
                profile = copy.deepcopy(self.chip)
                profile[key] = True
                (root/'chips/xt26g02c.json').write_text(json.dumps(profile))
                with self.assertRaises(ValueError):
                    review.load_chip('xt26g02c', root)

    def test_unknown_or_traversal_selector_refused(self):
        for name in ('../xt26g02c', '/tmp/chip', 'unknown-chip'):
            with self.assertRaises((ValueError, OSError)):
                review.load_chip(name)

    def test_saved_records_must_be_bounded_regular_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'record'
            path.write_bytes(self.request)
            link = Path(directory)/'link'
            link.symlink_to(path)
            self.assertEqual(review.bounded_bytes(path, 48), self.request)
            for target, size in ((link, 48), (path, 49), (path, 47)):
                with self.assertRaises(ValueError):
                    review.bounded_bytes(target, size)

    def test_cli_with_synthetic_future_firmware_and_source_pins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'request').write_bytes(self.request)
            (root/'result').write_bytes(self.record())
            binary = bytearray(0x410)
            struct.pack_into('<I', binary, 0x40c, 0x24020300)
            (root/'writer').write_bytes(binary)
            self.writer.update(writer_file='writer', source_pins={'writer': hashlib.sha256(binary).hexdigest()})
            self.chip['writer_audit']['profile_sha256'] = review.fingerprint(self.writer)
            argv = ['chip_review.py', '--version', '9.99', '--chip', 'xt26g02c',
                    '--request', str(root/'request'), '--result', str(root/'result'), '--diskos', str(root)]
            with patch.object(sys, 'argv', argv), \
                    patch.object(review, 'load_profile', return_value={'version':'9.99', 'writer':'fixture'}) as load, \
                    patch.object(review, 'load_writer', return_value=self.writer), \
                    patch.object(review, 'load_chip', return_value=self.chip):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    review.main()
                load.assert_called_once_with('9.99')
                self.assertEqual(json.loads(output.getvalue())['firmware_version'], '9.99')
                (root/'writer').write_bytes(b'changed')
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                    review.main()
                self.assertEqual(error.exception.code, 1)


if __name__ == '__main__':
    unittest.main()
