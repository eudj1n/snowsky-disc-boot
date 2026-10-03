"""Synthetic OTA/container, FDT and partition fixtures; no firmware/device inputs."""
import copy
import gzip
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'scripts'))
from deployment import kernel_review as review


def digest(data):
    return hashlib.sha256(data).hexdigest()


def uimage(kernel):
    payload = bytes(16)+gzip.compress(kernel, mtime=0)
    header = struct.pack('>7I4B32s', 0x27051956, 0, 0, len(payload), 0x80000000,
                         0x80000000, zlib.crc32(payload), 5, 5, 2, 0, b'SYNTHETIC')
    header = header[:4]+struct.pack('>I', zlib.crc32(header))+header[8:]
    return header+payload


def fdt(properties, nodes=None):
    """The `/sfc` node with `properties`, then `nodes`: {name: (properties, children)}."""
    def word(n):
        return struct.pack('>I', n)
    def padded(data):
        return data+bytes(-len(data) % 4)
    names = b''
    def node(name, props, children):
        nonlocal names
        out = word(1)+padded(name.encode()+b'\0')
        for key, data in props:
            out += word(3)+word(len(data))+word(len(names))+padded(data)
            names += key.encode()+b'\0'
        for child, (child_props, grandchildren) in children.items():
            out += node(child, child_props, grandchildren)
        return out+word(2)
    tree = node('', [], {'sfc': (properties, {}), **(nodes or {})})+word(9)
    strings = 56+len(tree)
    return struct.pack('>10I', 0xd00dfeed, strings+len(names), 56, strings, 40,
                       17, 16, 0, len(names), len(tree))+bytes(16)+tree+names


def gpio(handle, pin, flags=0):
    return struct.pack('>4I', handle, pin, 0, flags)


def key_nodes(keys=None, offset=0x100, pinctrl='ingenic,x2000-pinctrl'):
    """The stock key node and its GPIO ports as the reviewed kernel lays them out."""
    def port(handle):
        return ([('gpio-controller', b''), ('#gpio-cells', struct.pack('>I', 3)),
                 ('ingenic,num-gpios', struct.pack('>I', 32)), ('phandle', struct.pack('>I', handle))], {})
    keys = dict({'vol-up-key': gpio(9, 13), 'vol-down-key': gpio(9, 14), 'play-key': gpio(9, 15)},
                **(keys or {}))
    return {'apb': ([], {'pinctrl@0x10010000': (
                [('compatible', pinctrl.encode()+b'\0'), ('reg', struct.pack('>2I', 0x10010000, 0x1000)),
                 ('ingenic,regs-offset', struct.pack('>I', offset))],
                {'gpa': port(8), 'gpb': port(9), 'gpe': port(10)})}),
            'x2000_key': ([('status', b'okay\0'), ('compatible', b'x2000-key\0'),
                           *((k, v) for k, v in keys.items() if v is not None),
                           ('tf-int', gpio(9, 6, 2))], {})}


class KernelReviewTests(unittest.TestCase):
    def setUp(self):
        self.firmware = review.load_profile('2.57')
        self.writer = review.load_writer(self.firmware['writer'])
        self.profile = review.load_kernel_profile(self.firmware, self.writer)
        self.chip = review.load_chip(self.profile['chip'])

    def page(self, changes=None, count=None):
        # Historical layout is a synthetic parser fixture, not a device capture.
        records, offset = [], 0
        for name, mib in [('uboot', 2), ('kernel', 8), ('rootfs', 128), ('kernel2', 8),
                          ('rootfs2', 25), ('ota', 1), ('mac', 1), ('userdata', 83)]:
            records.append([name.encode(), mib*1024*1024, offset, 0, 0])
            offset += mib*1024*1024
        for index, values in (changes or {}).items():
            for column, value in values.items():
                records[index][column] = value
        data = struct.pack('<2I', 0x646e616e, len(records) if count is None else count)
        data += b''.join(struct.pack('<32s4I', *row) for row in records)
        return data.ljust(self.chip['page_bytes'], b'\xff')

    def parse(self, page):
        return review.review_partitions(page, self.profile, self.chip, self.writer)

    def test_synthetic_layout_fits_without_admitting_writes(self):
        report = self.parse(self.page())
        self.assertEqual(report['writer_range'], [0xa00000, 0x7200000])
        self.assertTrue(report['writer_range_fits'])
        self.assertEqual(report['partitions'][2]['name'], 'rootfs')
        for key in ('active_boot_verified', 'physical_provenance_verified', 'flash_ready'):
            self.assertFalse(report[key])

    def test_bad_header_count_and_page_length(self):
        invalid = [b'bad!'+self.page()[4:], self.page()[:-1], self.page()+b'\0']
        invalid += [self.page(count=n) for n in (0, 33, 0x10000008, 0xffffffff)]
        for data in invalid:
            with self.assertRaises(ValueError):
                self.parse(data)

    def test_bad_names_and_missing_target(self):
        for name in (b'kernel', b'X'*32, b'root/fs', b'root\x01fs', b'\xff', b'', b'another'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.parse(self.page({2: {0: name}}))

    def test_zero_unaligned_overflow_and_sentinel_extents(self):
        for fields in ({1: 0}, {1: 1}, {1: 0xffffffff}, {2: 1}, {2: 0xfffe0000}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.parse(self.page({7: fields}))

    def test_overlap_is_not_accepted_as_target_containment(self):
        with self.assertRaisesRegex(ValueError, 'Overlapping'):
            self.parse(self.page({3: {2: 10*1024*1024}}))

    def test_writer_must_start_at_target_and_include_reserve(self):
        for field, value in (('start_block', 81), ('logical_blocks', 1024), ('block_bytes', 65536)):
            writer = dict(self.writer, **{field: value})
            with self.subTest(field=field), self.assertRaises(ValueError):
                review.review_partitions(self.page(), self.profile, self.chip, writer)

    def test_unknown_manager_and_target_flags_refused(self):
        for changes in ({2: {4: 1}}, {2: {3: 0x400}}, {7: {4: 2}}):
            with self.assertRaises(ValueError):
                self.parse(self.page(changes))
        self.assertTrue(self.parse(self.page({7: {4: 1}}))['writer_range_fits'])

    def test_no_implicit_cross_page_fetch(self):
        self.profile['metadata']['offset'] += self.chip['page_bytes']-4
        with self.assertRaisesRegex(ValueError, 'header crosses'):
            self.parse(self.page())
        self.profile['metadata']['offset'] -= 12
        page = bytearray(self.page())
        struct.pack_into('<2I', page, self.chip['page_bytes']-16, 0x646e616e, 8)
        with self.assertRaisesRegex(ValueError, 'records cross'):
            self.parse(bytes(page))

    def test_future_profile_selection_and_stale_audits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'kernels').mkdir()
            firmware = dict(self.firmware, version='9.99')
            profile = dict(self.profile, version='9.99')
            path = root/'kernels/v9.99.json'
            path.write_text(json.dumps(profile))
            self.assertEqual(review.load_kernel_profile(firmware, self.writer, root)['version'], '9.99')
            for key, value in [('rootfs_sha256', '0'*64), ('writer_profile_sha256', '0'*64),
                               ('writes_admitted', True), ('page_reads_admitted', True),
                               ('kernel_bytes', 1 << 30)]:
                path.write_text(json.dumps(dict(profile, **{key: value})))
                with self.subTest(key=key), self.assertRaises(ValueError):
                    review.load_kernel_profile(firmware, self.writer, root)

    def test_uimage_checks_and_bounded_decompression(self):
        kernel = b'SYNTHETIC KERNEL'*100
        image = uimage(kernel)
        profile = dict(ximage_bytes=len(image), ximage_sha256=digest(image), gzip_offset=80,
                       kernel_bytes=len(kernel), kernel_sha256=digest(kernel))
        self.assertEqual(review.extract_kernel(image, profile), kernel)
        for altered in (image[:-1], image[:-1]+bytes([image[-1] ^ 1]), b'bad!'+image[4:]):
            with self.assertRaises(ValueError):
                review.extract_kernel(altered, profile)
        for offset in (4, 30, 65):
            corrupted = bytearray(image)
            corrupted[offset] ^= 1
            # Even if the outer hash is re-pinned, CRC/header checks remain.
            with self.assertRaises(ValueError):
                review.extract_kernel(bytes(corrupted), dict(profile, ximage_sha256=digest(corrupted)))
        for changes in ({'kernel_bytes': 1}, {'kernel_bytes': len(kernel)+1},
                        {'kernel_sha256': '0'*64}, {'gzip_offset': len(image)}):
            with self.assertRaises(ValueError):
                review.extract_kernel(image, dict(profile, **changes))

    def test_fdt_properties_and_malformed_input(self):
        blob = fdt([('status', b'okay\0')])
        self.assertEqual(review.dtb_properties(blob)[('/sfc', 'status')], b'okay\0')
        malformed = [blob[:-1], b'bad!'+blob[4:], fdt([('status', b'a'), ('status', b'b')])]
        bad = bytearray(blob)
        struct.pack_into('>I', bad, 56, 99)
        malformed.append(bytes(bad))
        bad = bytearray(blob)
        struct.pack_into('>I', bad, 76, 0xffffffff)  # Property length.
        malformed.append(bytes(bad))
        for data in malformed:
            with self.assertRaises(ValueError):
                review.dtb_properties(data)

    def test_kernel_chip_table_and_partition_source(self):
        properties = [('status', b'okay\0'), ('ingenic,use_ofpart_info', b'\0'),
                      ('ingenic,spiflash_param_offset', bytes(4))]
        tree = fdt(properties, key_nodes())
        kernel = bytearray(256)+tree
        base = 0x80010000
        struct.pack_into('<3I', kernel, 0, 0x12, base+32, base+64)
        kernel[32:48] = b'SYNTHETIC CHIP\0\0'
        struct.pack_into('<4I', kernel, 64, 2048, 131072, 128, 268435456)
        profile = dict(self.profile, kernel_base=base, chip_entry_address=base,
                       dtb_address=base+256, dtb_bytes=len(tree), sfc_node='/sfc')
        report = review.inspect_kernel(bytes(kernel), profile, self.chip)
        self.assertEqual(report['metadata_page'], 11)
        self.assertEqual(report['keys']['play'], {'port': '0x10010100', 'bit': 15})
        for offset, value in ((0, 0xe2), (8, base+len(kernel)+4), (64, 4096)):
            altered = bytearray(kernel)
            struct.pack_into('<I', altered, offset, value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                review.inspect_kernel(bytes(altered), profile, self.chip)
        tree = fdt([('status', b'okay\0'), ('ingenic,use_ofpart_info', b'\1'),
                    ('ingenic,spiflash_param_offset', bytes(4))], key_nodes())
        with self.assertRaisesRegex(ValueError, 'partition source'):
            review.inspect_kernel(bytes(kernel[:256])+tree, profile, self.chip)


    def test_key_pins_are_the_ones_disc_boot_reads(self):
        def pins(nodes):
            return review.key_pins(review.dtb_properties(fdt([], nodes)))
        self.assertEqual(pins(key_nodes()), {
            'volume_up': {'port': '0x10010100', 'bit': 13},
            'volume_down': {'port': '0x10010100', 'bit': 14},
            'play': {'port': '0x10010100', 'bit': 15}})
        # Volume Down is reported, not read by disc-boot: it may move.
        self.assertEqual(pins(key_nodes({'vol-down-key': gpio(10, 3)}))['volume_down'],
                         {'port': '0x10010400', 'bit': 3})
        moved = [key_nodes({'play-key': gpio(9, 14)}), key_nodes({'play-key': gpio(10, 15)}),
                 key_nodes({'vol-up-key': gpio(8, 13)}), key_nodes(offset=0x200)]
        for nodes in moved:
            with self.subTest(nodes=nodes), self.assertRaisesRegex(ValueError, 'disc-boot reads'):
                pins(nodes)
        broken = [key_nodes({'play-key': None}), key_nodes({'play-key': gpio(9, 15)[:12]}),
                  key_nodes({'play-key': gpio(7, 15)}), key_nodes({'play-key': gpio(9, 32)}),
                  key_nodes(pinctrl='vendor,pinctrl'), {}]
        disabled = key_nodes()
        disabled['x2000_key'][0][0] = ('status', b'disabled\0')
        twice = dict(key_nodes(), second_key=key_nodes()['x2000_key'])
        for nodes in broken+[disabled, twice]:
            with self.subTest(nodes=nodes), self.assertRaises(ValueError):
                pins(nodes)

    def test_review_mirrors_disc_boot_key_read(self):
        source = (Path(__file__).resolve().parents[2]/'device/src/boot.c').read_text()
        body = source[source.index('static int read_keys('):]
        body = body[:body.index('\n}\n')]
        base = int(re.search(r'MAP_SHARED, fd, (0x[0-9a-f]+)\)', body)[1], 16)
        offset = int(re.search(r'\(char \*\)map \+ (0x[0-9a-f]+)\)', body)[1], 16)
        self.assertEqual(base+offset, review.BOOT_KEY_PORT)
        for name, prop in (('volume_up', 'vol-up-key'), ('play', 'play-key')):
            bit = int(re.search(rf'\*{name} = !\(\(gpb >> (\d+)\) & 1u\)', body)[1])
            self.assertEqual(bit, review.BOOT_KEYS[prop], name)


if __name__ == '__main__':
    unittest.main()
