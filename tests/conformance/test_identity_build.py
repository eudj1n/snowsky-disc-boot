"""Firmware-free RAM layout/ELF checks; no Docker, USB or sibling checkout."""
import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from firmware_profile import load_profile, load_reader_profile
from deployment.build_identity import prepare, inspect_elf


class IdentityBuildTests(unittest.TestCase):
    def setUp(self):
        self.base = load_profile((ROOT/'firmware/active-version').read_text().strip())
        self.profile = load_reader_profile(self.base)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'readers').mkdir()

    def load(self, p, base=None):
        (self.root/'readers'/f'v{p["version"]}.json').write_text(json.dumps(p))
        return load_reader_profile(base or self.base, self.root)

    def test_new_version_layout_and_generated_addresses(self):
        p = copy.deepcopy(self.profile)
        base = {**self.base, 'version': '9.99', 'rootfs_sha256': 'b'*64}
        p.update(version=base['version'], rootfs_sha256=base['rootfs_sha256'],
                 request_address=0xa1800000, result_address=0xa1900000)
        self.assertEqual(self.load(p, base), p)
        prepare(p, self.root)
        self.assertIn('#define IDENTITY_REQUEST 0xa1800000', (self.root/'identity_layout.h').read_text())
        self.assertIn('BSS needs an explicit initialization path', (self.root/'identity.ld').read_text())

    def test_invalid_profiles(self):
        for key, value in [('rootfs_sha256', 'a'*64), ('physical_qualified', True),
                           ('protocol', 'writer'), ('code_bytes', 65536), ('stack_top', 0xa0c08000),
                           ('load_address', 0x80c00000), ('request_address', True),
                           ('result_address', 0xa1fffff0), ('sfc_polls', 800001),
                           ('nand_polls', 0), ('id_address_bytes', True), ('source_pins', {})]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.load({**self.profile, key: value})

    def test_all_region_overlaps_and_alignment(self):
        for key in ('load_address', 'stack_bottom', 'request_address'):
            for delta in (0, 16):
                with self.subTest(key=key, delta=delta), self.assertRaises(ValueError):
                    self.load({**self.profile, 'result_address': self.profile[key]+delta})
        with self.assertRaises(ValueError):
            self.load({**self.profile, 'request_address': self.profile['result_address']+4096})
        with self.assertRaises(ValueError):
            self.load({**self.profile, 'load_address': self.profile['load_address']+1})

    def elf(self):
        data = bytearray(88)
        data[:7] = b'\x7fELF\x01\x01\x01'
        addr = self.profile['load_address']
        struct.pack_into('<HHIIIIIHHHHHH', data, 16, 2, 8, 1, addr, 52, 0, 0, 52, 32, 1, 0, 0, 0)
        struct.pack_into('<8I', data, 52, 1, 84, addr, addr, 4, 4, 7, 16)
        data[84:] = b'abcd'
        return data

    def test_elf_exact_entry_and_payload(self):
        self.assertEqual(inspect_elf(self.elf(), self.profile), b'abcd')

    def test_elf_corruption_bounds_interpreter_and_uninitialized_memory(self):
        for offset, value in [(18, 3), (24, self.profile['load_address']+4), (28, 999),
                              (52, 3), (52, 2), (56, 100), (60, 0x80c00000), (64, 0),
                              (68, 40000), (72, 8), (76, 6)]:
            data = self.elf()
            struct.pack_into('<I', data, offset, value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                inspect_elf(data, self.profile)
        for length in (0, 51, 83, 87):
            with self.subTest(length=length), self.assertRaises(ValueError):
                inspect_elf(self.elf()[:length], self.profile)


if __name__ == '__main__':
    unittest.main()
