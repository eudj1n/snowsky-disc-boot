"""Synthetic offline writer/image review; never import or execute USB tooling."""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('review', ROOT/'scripts/deployment/review.py')
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


class DeploymentReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.writer_profile = review.load_writer('diskos-my-write5-768')

    def writer(self, blocks=768, opcode=0x24020000):
        data = bytearray(0x410)
        struct.pack_into('<I', data, 0x40c, opcode | blocks)
        return bytes(data)

    def record(self, changes=None, bad=()):
        words = [0]*256
        for index, value in {0:0x4004e005, 1:768, 5:80, 6:768, 9:0x55555555,
                             10:len(bad), 11:848+len(bad), 12:0x73717368,
                             15:0x10, 16:0x600df10c, 20:len(bad), 21:0x10}.items():
            words[index] = value
        for i, block in enumerate(bad):
            words[40+i] = block
        for index, value in (changes or {}).items():
            words[index] = value
        return struct.pack('<256I', *words)

    def test_exact_capacity_required_before_write(self):
        self.assertEqual(review.writer_capacity(self.writer(), self.writer_profile), 768)
        for blocks in (0, 580, 769, 1024):
            with self.subTest(blocks=blocks), self.assertRaises(ValueError):
                review.writer_capacity(self.writer(blocks), self.writer_profile)

    def test_unknown_or_short_writer_refused(self):
        for data in (b'', self.writer()[:-1], self.writer(opcode=0x34020000)):
            with self.assertRaises(ValueError):
                review.writer_capacity(data, self.writer_profile)

    def test_valid_records_with_and_without_skipped_blocks(self):
        for bad in ((), (383,), (80, 81)):
            result = review.check_debug(self.record(bad=bad), 0, self.writer_profile)
            self.assertEqual(result['status'], 'consistent-writer-record')
            self.assertFalse(result['freshnessVerified'])
            self.assertEqual(result['skipped'], len(bad))
        # Bad blocks beyond the written span belong to the scan, not skipped.
        review.check_debug(self.record({10:0, 11:848}, bad=(900,)), 0, self.writer_profile)

    def test_exit_failure_short_poisoned_and_extra_readback_refused(self):
        for data, rc in ((self.record(), 1), (self.record(), -9), (b'\xee'*1024, 0),
                         (self.record()[:-1], 0), (self.record()+b'\0', 0)):
            with self.subTest(rc=rc, size=len(data)), self.assertRaises(ValueError):
                review.check_debug(data, rc, self.writer_profile)

    def test_success_sentinel_alone_is_insufficient(self):
        for index, value in ((0,0), (1,767), (3,1), (4,1), (5,81), (6,580),
                             (6,769), (7,1), (8,1), (9,0), (11,913), (12,0),
                             (15,0), (16,0xdead0003), (20,65), (21,0)):
            with self.subTest(index=index, value=value), self.assertRaises(ValueError):
                review.check_debug(self.record({index:value}), 0, self.writer_profile)

    def test_inconsistent_bad_block_mapping_refused(self):
        for bad, changes in (((79,), {}), ((912,), {}), ((383,383), {}),
                             ((384,383), {}), ((848,), {}), ((383,), {10:0}),
                             ((383,), {11:848}), ((), {10:1})):
            with self.subTest(bad=bad, changes=changes), self.assertRaises(ValueError):
                review.check_debug(self.record(changes, bad), 0, self.writer_profile)

    def test_retry_counters_are_bounded(self):
        review.check_debug(self.record({17:1, 18:2}), 0, self.writer_profile)
        for changes in ({17:769,18:2}, {17:1,18:7}, {17:1,18:0}, {17:0,18:2}):
            with self.assertRaises(ValueError):
                review.check_debug(self.record(changes), 0, self.writer_profile)

    def test_image_hash_size_magic_and_padding(self):
        path = self.root/'image'
        data = b'hsqsdata'+b'\0'*8
        path.write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        result = review.check_image(path, 16, sha, 8)
        self.assertEqual(result['contentSha256'], hashlib.sha256(data[:8]).hexdigest())
        for size, digest, content in ((15,sha,8), (16,'0'*64,8), (16,sha,17), (16,sha,4)):
            with self.assertRaises(ValueError):
                review.check_image(path, size, digest, content)
        path.write_bytes(b'bad!'+data[4:])
        with self.assertRaises(ValueError):
            review.check_image(path, 16, hashlib.sha256(path.read_bytes()).hexdigest(), 8)

    def test_symlink_image_refused(self):
        path = self.root/'real'
        path.write_bytes(b'hsqs')
        link = self.root/'link'
        link.symlink_to(path)
        with self.assertRaises(ValueError):
            review.check_image(link, 4, hashlib.sha256(b'hsqs').hexdigest(), 4)

    def test_reference_pin_detects_changed_bytes(self):
        path = self.root/'source'
        path.write_bytes(b'reviewed')
        pin = hashlib.sha256(b'reviewed').hexdigest()
        review.check_pin(path, pin)
        path.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            review.check_pin(path, pin)

    def test_engineering_review_pins_opt_in_and_separate_artifacts(self):
        stock, candidate = b'hsqsorig', b'hsqsmod!'
        sha = lambda data: hashlib.sha256(data).hexdigest()
        profile = dict(version='9.99', product='SYNTHETIC', main_os_version=999,
                       rootfs_size=8, rootfs_sha256=sha(stock))
        writer = dict(self.writer_profile, block_bytes=8, logical_blocks=2,
                      writer_file='writer', source_pins={'writer':sha(self.writer(2))})
        (self.root/'writer').write_bytes(self.writer(2))
        usb = dict(version='9.99', rootfs_sha256=sha(stock))
        names = review.artifact_names(profile, 'usb-engineering')
        artifacts = {}
        for name, content in zip(names, (candidate, stock)):
            data = content + b'\0'*8
            (self.root/name).write_bytes(data)
            artifacts[name] = dict(bytes=16, sha256=sha(data))
        diagnostic = dict(profileSha256=review.fingerprint(usb), optInRequired=True,
                          physicalQualified=False)
        report = dict(variant='usb-engineering', usbDiagnostic=diagnostic,
                      status='offline-verified', product='SYNTHETIC', firmware=999,
                      fullRoundTrip=True, profileSha256=review.fingerprint(profile),
                      writerProfileSha256=review.fingerprint(writer), hardwareQualified=False,
                      flashAuthorized=False, writerFormatBytes=16, stockBytes=8,
                      stockSha256=sha(stock), packedBytes=8, artifacts=artifacts)
        def run():
            (self.root/'report.json').write_text(json.dumps(report))
            return review.review(self.root, self.root, profile, writer)
        with patch.object(review, 'load_usb_profile', return_value=usb):
            self.assertFalse(run()['flashReady'])
            for field, invalid in (('profileSha256','0'*64), ('optInRequired',False),
                                   ('physicalQualified',True)):
                previous = diagnostic[field]; diagnostic[field] = invalid
                with self.subTest(field=field), self.assertRaises(ValueError):run()
                diagnostic[field] = previous
            report['variant'] = 'companion'
            with self.assertRaises(ValueError):run()


if __name__ == '__main__':
    unittest.main()
