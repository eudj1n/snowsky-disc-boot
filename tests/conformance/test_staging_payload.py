"""The staging check's logic (device/usbboot/staging.c, plan, stage 4c), compiled for this
computer with the request's DRAM addresses mapped into a buffer of the test's own."""
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
IMAGE = 0xa1000000
HARNESS = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
static uint8_t *base;
#define STAGING_POINTER(address) ((uintptr_t)(base + ((address) - 0xa1000000u)))
#include "staging.c"
int main(void) {
    struct staging_request q;
    uint32_t length;
    if (fread(&q, sizeof(q), 1, stdin) != 1 || fread(&length, 4, 1, stdin) != 1) return 2;
    base = aligned_alloc(64, length ? length : 64);
    if (!base || fread(base, 1, length, stdin) != length) return 3;
    struct staging_result r;
    staging_run(&q, &r);
    fwrite(&r, sizeof(r), 1, stdout);
    fwrite(base, 1, length, stdout);
    return 0;
}
'''
REQUEST, RESULT = 0x51475453, 0x52475453
NONCE = bytes(range(16))


def request(op, address=IMAGE, length=4096, seed=0, magic=REQUEST, version=1, reserved=(0, 0)):
    return struct.pack('<6I16s2I', magic, version, op, address, length, seed, NONCE, *reserved)


@unittest.skipUnless(shutil.which('cc'), 'needs a C compiler')
class StagingPayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        folder = Path(cls.temp.name)
        (folder/'harness.c').write_text(HARNESS)
        cls.binary = folder/'harness'
        subprocess.run(['cc', '-std=c11', '-O1', '-Wall', '-Wextra', '-Werror', '-I', str(ROOT/'device/usbboot'),
                        str(folder/'harness.c'), '-o', str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_payload(self, q, memory):
        out = subprocess.run([str(self.binary)], input=q + struct.pack('<I', len(memory)) + memory,
                             capture_output=True, check=True).stdout
        fields = struct.unpack_from('<4I16s4I32s', out)
        return dict(zip(('magic', 'version', 'op', 'code', 'nonce', 'address', 'length', 'checked', 'bad', 'sha256'),
                        fields)), out[80:]

    def test_the_staged_image_is_hashed_on_the_player(self):
        for length in (64, 4096, 65536 + 64):
            with self.subTest(length=length):
                data = os.urandom(length)
                r, memory = self.run_payload(request(2, length=length), data)
                self.assertEqual((r['magic'], r['version'], r['op'], r['code']), (RESULT, 1, 2, 0))
                self.assertEqual((r['nonce'], r['address'], r['length'], r['checked'], r['bad']), (NONCE, IMAGE, length, length, 0))
                self.assertEqual(r['sha256'], hashlib.sha256(data).digest())
                self.assertEqual(memory, data, 'hashing leaves the image as it is')

    def test_the_image_region_is_checked_with_a_pattern_and_its_complement(self):
        length = 8192
        r, memory = self.run_payload(request(1, length=length, seed=0x12345678), bytes(length))
        self.assertEqual((r['magic'], r['op'], r['code'], r['checked'], r['bad']), (RESULT, 1, 0, 2 * length // 4, 0))
        self.assertEqual(r['sha256'], bytes(32))
        # The region keeps the complement of the seed's xorshift32 sequence, the last pass written.
        x, words = 0x12345678, []
        for _ in range(length // 4):
            x ^= (x << 13) & 0xffffffff; x ^= x >> 17; x ^= (x << 5) & 0xffffffff
            words.append(x ^ 0xffffffff)
        self.assertEqual(memory, struct.pack(f'<{length // 4}I', *words))

    def test_a_request_out_of_its_rules_is_refused_and_touches_nothing(self):
        data = os.urandom(4096)
        for q in (request(2, magic=0), request(2, version=2), request(3), request(2, length=4000), request(2, length=0),
                  request(2, address=IMAGE + 2), request(1, seed=0), request(2, seed=1), request(2, reserved=(1, 0)),
                  request(2, address=0x9ffff000), request(2, address=0xa7fff000, length=8192)):
            with self.subTest(q=q.hex()):
                r, memory = self.run_payload(q, data)
                self.assertEqual((r['magic'], r['code'], r['checked']), (RESULT, 1, 0))
                self.assertEqual(memory, data)


if __name__ == '__main__':
    unittest.main()
