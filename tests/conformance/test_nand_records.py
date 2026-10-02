"""Cross-language synthetic NAND records; never loads a firmware or USB library."""
import importlib.util
import os
from pathlib import Path
import struct
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('nand_records', ROOT/'scripts/deployment/nand_records.py')
records = importlib.util.module_from_spec(spec);spec.loader.exec_module(records)


class NandRecordTests(unittest.TestCase):
    def setUp(self):
        binary = Path(os.environ.get('DISC_NAND_TEST_BINARY', ROOT/'build/host/nand-reader-test'))
        self.raw = subprocess.check_output([str(binary), '--emit-fixture'], timeout=3)
        self.nonce = struct.pack('<4I', 1, 2, 3, 4)
        self.request = records.encode_request(2, self.nonce, 9, 127)

    def test_c_record_preserves_full_page_oob_and_identity(self):
        result = records.decode_result(self.raw, self.request, 2112)
        self.assertEqual(result['data'], bytes((i+127) % 256 for i in range(2112)))
        self.assertEqual(result['observed_id'], 0x563412)
        self.assertEqual(result['page'], 127)
        self.assertFalse(result['hardware_qualified'])

    def test_stale_nonce_sequence_operation_and_physical_page_rejected(self):
        for request in (records.encode_request(2, b'x'*16, 9, 127),
                        records.encode_request(2, self.nonce, 10, 127),
                        records.encode_request(2, self.nonce, 9, 126),
                        records.encode_request(1, self.nonce, 9)):
            with self.assertRaises(ValueError):records.decode_result(self.raw, request, 2112)

    def test_short_corrupt_incomplete_and_failed_results_rejected(self):
        bad = [self.raw[:-1], self.raw+b'x']
        for offset in (0, 4, 8, 12, 44, 48, 76, 76+2048, len(self.raw)-1):
            raw = bytearray(self.raw);raw[offset] ^= 1;bad.append(bytes(raw))
        for raw in bad:
            with self.assertRaises(ValueError):records.decode_result(raw, self.request, 2112)
        with self.assertRaises(ValueError):records.decode_result(self.raw, self.request, 2048)

    def test_request_bounds_and_reserved_words(self):
        for op, nonce, seq, page in ((3,self.nonce,0,0), (2,b'\0'*16,0,0), (2,b'x',0,0),
                                     (2,self.nonce,-1,0), (2,self.nonce,0,0x1000000),
                                     (1,self.nonce,0,1), (True,self.nonce,0,0)):
            with self.assertRaises(ValueError):records.encode_request(op, nonce, seq, page)
        bad = bytearray(self.request);bad[-1] = 1
        with self.assertRaises(ValueError):records.decode_result(self.raw, bytes(bad), 2112)


if __name__ == '__main__':unittest.main()
