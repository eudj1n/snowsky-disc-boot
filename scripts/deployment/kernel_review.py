#!/usr/bin/env python3
"""Offline review of a pinned OTA kernel and optional saved NAND metadata page.

No device access, firmware execution or flash admission. A decoded table does
not prove its physical origin, active boot slot, ECC state or bad-block map.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from firmware_profile import PROFILES, fingerprint, load_profile, load_writer, require, sha
from deployment.chip_review import load_chip
from deployment.review import regular

# disc-boot's key read at power-on (device/boot/boot.c, read_keys): port B's PxPIN, active low.
BOOT_KEY_PORT = 0x10010100
BOOT_KEYS = {'vol-up-key': 13, 'play-key': 15}
KEY_NAMES = {'vol-up-key': 'volume_up', 'vol-down-key': 'volume_down', 'play-key': 'play'}


def load_kernel_profile(firmware, writer, directory=PROFILES):
    path = directory/'kernels'/f'v{firmware["version"]}.json'
    regular(path, 16384)
    p = json.loads(path.read_text())
    require(p.get('schema_version') == 1 and p.get('version') == firmware['version'] and
            p.get('rootfs_sha256') == firmware['rootfs_sha256'], 'Kernel/firmware profile mismatch')
    require(p.get('writer_profile_sha256') == fingerprint(writer), 'Kernel/writer audit changed')
    for key in ('ximage_sha256', 'kernel_sha256'):
        require(sha(p.get(key)), f'Invalid {key}')
    for key in ('ximage_bytes', 'kernel_bytes', 'gzip_offset', 'dtb_bytes'):
        require(type(p.get(key)) is int and 0 < p[key] <= 32*1024*1024, f'Invalid {key}')
    for key in ('kernel_base', 'chip_entry_address', 'dtb_address'):
        require(type(p.get(key)) is int and 0 <= p[key] <= 0xffffffff, f'Invalid {key}')
    require(p.get('page_reads_admitted') is False and p.get('writes_admitted') is False,
            'Offline kernel review cannot admit hardware operations')
    m = p.get('metadata', {})
    require(m.get('format') == 'ingenic-nand-partitions-v1', 'Unknown partition format')
    require(type(m.get('offset')) is int and 0 <= m['offset'] <= 0xffffffff,
            'Invalid metadata offset')
    require(type(m.get('max_partitions')) is int and 1 <= m['max_partitions'] <= 32,
            'Invalid partition count bound')
    require(m.get('magic') == 0x646e616e and isinstance(m.get('target'), str) and m['target'],
            'Invalid partition magic/target')
    return p


def extract_kernel(image, profile):
    require(len(image) == profile['ximage_bytes'] and
            hashlib.sha256(image).hexdigest() == profile['ximage_sha256'], 'Unreviewed xImage')
    require(len(image) >= 64, 'Short uImage header')
    magic, hcrc, _, size, _, _, dcrc, os_, arch, kind, compression, _ = struct.unpack_from('>7I4B32s', image)
    header = image[:4] + bytes(4) + image[8:64]
    require(magic == 0x27051956 and zlib.crc32(header) == hcrc, 'Invalid uImage header CRC/magic')
    require((os_, arch, kind, compression) == (5, 5, 2, 0), 'Expected Linux/MIPS uncompressed wrapper')
    require(size == len(image)-64 and zlib.crc32(image[64:]) == dcrc, 'Invalid uImage payload size/CRC')
    offset = profile['gzip_offset']
    require(64 <= offset < len(image), 'Invalid reviewed gzip offset')
    decoder = zlib.decompressobj(31)
    kernel = decoder.decompress(image[offset:], profile['kernel_bytes']+1)
    require(decoder.eof and not decoder.unconsumed_tail and len(kernel) == profile['kernel_bytes'],
            'Truncated/oversized kernel stream')
    require(hashlib.sha256(kernel).hexdigest() == profile['kernel_sha256'], 'Kernel fingerprint mismatch')
    return kernel


def dtb_properties(blob):
    """Read bounded FDT properties; reject ambiguous paths/properties and framing."""
    require(len(blob) >= 40, 'Short FDT header')
    magic, total, structure, strings, _, version, compatible, _, string_size, struct_size = struct.unpack_from('>10I', blob)
    require(magic == 0xd00dfeed and total == len(blob) and version == 17 and compatible <= 17,
            'Unsupported FDT header')
    require(40 <= structure < structure+struct_size <= strings and strings+string_size <= total,
            'Invalid FDT ranges')
    end = structure+struct_size
    names = blob[strings:strings+string_size]
    pos, stack, nodes, props = structure, [], set(), {}
    while pos+4 <= end:
        token, = struct.unpack_from('>I', blob, pos)
        pos += 4
        if token == 1:
            stop = blob.find(b'\0', pos, end)
            require(stop >= pos, 'Unterminated FDT node')
            name = blob[pos:stop].decode('ascii')
            require('/' not in name and (bool(name) if stack else not name), 'Invalid FDT node name')
            stack.append(name)
            require(len(stack) <= 64, 'FDT nesting limit')
            path = '/'.join(stack) or '/'
            require(path not in nodes, 'Duplicate FDT node')
            nodes.add(path)
            pos = (stop+4) & ~3
        elif token == 2:
            require(bool(stack), 'Unbalanced FDT node')
            stack.pop()
        elif token == 3:
            require(bool(stack) and pos+8 <= end, 'Invalid FDT property')
            length, name_offset = struct.unpack_from('>2I', blob, pos)
            pos += 8
            require(pos+length <= end and name_offset < len(names), 'FDT property out of bounds')
            stop = names.find(b'\0', name_offset)
            require(stop > name_offset, 'Invalid FDT property name')
            key = ('/'.join(stack) or '/', names[name_offset:stop].decode('ascii'))
            require(key not in props, 'Duplicate FDT property')
            props[key] = blob[pos:pos+length]
            pos = (pos+length+3) & ~3
        elif token == 4:
            pass
        elif token == 9:
            require(not stack and '/' in nodes and not any(blob[pos:end]), 'Incomplete FDT tree')
            return props
        else:
            raise ValueError('Unknown FDT token')
    raise ValueError('Missing FDT end token')


def key_pins(props):
    """Resolve the stock key node's GPIOs to port registers; refuse a move of disc-boot's keys."""
    def word(value):
        require(value is not None and len(value) == 4, 'Unreviewed key GPIO controller')
        return struct.unpack('>I', value)[0]
    nodes = [n for (n, k), v in props.items() if k == 'compatible' and v == b'x2000-key\0']
    require(len(nodes) == 1 and props.get((nodes[0], 'status')) == b'okay\0',
            'Expected one enabled x2000-key node; repeat the key audit')
    handles = [(v, n) for (n, k), v in props.items() if k == 'phandle']
    ports = dict(handles)
    require(len(ports) == len(handles), 'Duplicate FDT phandle')
    keys = {}
    for prop, name in KEY_NAMES.items():
        cells = props.get((nodes[0], prop))
        require(cells is not None and len(cells) == 16, f'Unreviewed {prop} specifier')
        port = ports.get(cells[:4], '')
        parent, _, leaf = port.rpartition('/')
        require(re.fullmatch('gp[a-z]', leaf) is not None and props.get((port, 'gpio-controller')) == b'' and
                word(props.get((port, '#gpio-cells'))) == 3, f'{prop} is not on a reviewed GPIO port')
        require(props.get((parent, 'compatible'), b'').startswith(b'ingenic,') and
                len(props.get((parent, 'reg'), b'')) >= 4, f'{prop} is not on a reviewed GPIO port')
        bit = struct.unpack_from('>I', cells, 4)[0]
        require(bit < min(32, word(props.get((port, 'ingenic,num-gpios')))), f'{prop} pin outside its port')
        address = (struct.unpack_from('>I', props[(parent, 'reg')])[0] +
                   (ord(leaf[2])-ord('a'))*word(props.get((parent, 'ingenic,regs-offset'))))
        keys[name] = dict(port=f'{address:#010x}', bit=bit)
    for prop, bit in BOOT_KEYS.items():
        require(keys[KEY_NAMES[prop]] == dict(port=f'{BOOT_KEY_PORT:#010x}', bit=bit),
                f'Kernel {prop} is not the pin disc-boot reads; review read_keys')
    return keys


def inspect_kernel(kernel, profile, chip):
    def at(address, size):
        offset = address-profile['kernel_base']
        require(0 <= offset <= len(kernel)-size, 'Kernel address outside image')
        return kernel[offset:offset+size]
    device_id, name_address, params_address = struct.unpack('<3I', at(profile['chip_entry_address'], 12))
    require(device_id == bytes.fromhex(chip['id_prefix_hex'])[1], 'Kernel device ID mismatch')
    name_bytes = at(name_address, 32)
    require(b'\0' in name_bytes, 'Unterminated kernel chip name')
    name = name_bytes.split(b'\0', 1)[0].decode('ascii').strip()
    geometry = struct.unpack('<4I', at(params_address, 16))
    expected = (chip['page_bytes'], chip['page_bytes']*chip['pages_per_block'], chip['oob_bytes'],
                chip['page_bytes']*chip['pages_per_block']*chip['blocks'])
    require(geometry == expected, 'Kernel/chip geometry mismatch')
    props = dtb_properties(at(profile['dtb_address'], profile['dtb_bytes']))
    node = profile['sfc_node']
    require(props.get((node, 'status')) == b'okay\0' and
            props.get((node, 'ingenic,use_ofpart_info')) == b'\0' and
            props.get((node, 'ingenic,spiflash_param_offset')) == bytes(4),
            'Unreviewed SFC partition source; repeat driver audit')
    return dict(kernel_chip_name=name, geometry=list(geometry), partition_source='nand-metadata',
                metadata_offset=profile['metadata']['offset'],
                metadata_page=profile['metadata']['offset']//chip['page_bytes'], keys=key_pins(props))


def review_partitions(page, profile, chip, writer):
    """Parse one main-data page, never OOB or a claimed live partition table."""
    require(len(page) == chip['page_bytes'], 'Expected exactly one main-data page')
    m = profile['metadata']
    start = m['offset'] % chip['page_bytes']
    require(start+8 <= len(page), 'Partition header crosses page boundary')
    magic, count = struct.unpack_from('<2I', page, start)
    require(magic == m['magic'] and 1 <= count <= m['max_partitions'], 'Invalid partition magic/count')
    require(start+8+count*48 <= len(page), 'Partition records cross page boundary')
    block = chip['page_bytes']*chip['pages_per_block']
    capacity = block*chip['blocks']
    parts, names = [], set()
    for i in range(count):
        raw_name, size, offset, flags, manager = struct.unpack_from('<32s4I', page, start+8+i*48)
        require(b'\0' in raw_name, 'Unterminated partition name')
        name = raw_name.split(b'\0', 1)[0].decode('ascii')
        require(name and all(c.isalnum() or c in '_-' for c in name) and name not in names,
                'Invalid/duplicate partition name')
        names.add(name)
        require(size > 0 and size % block == 0 and offset % block == 0 and offset+size <= capacity,
                'Partition outside chip, empty, unaligned or unresolved size sentinel')
        require(manager in (0, 1), 'Unknown partition manager mode')
        parts.append(dict(name=name, offset=offset, size=size, mask_flags=flags, manager_mode=manager))
    ordered = sorted(parts, key=lambda p: p['offset'])
    require(all(a['offset']+a['size'] <= b['offset'] for a, b in zip(ordered, ordered[1:])),
            'Overlapping partitions')
    targets = [p for p in parts if p['name'] == m['target']]
    require(len(targets) == 1, 'Target partition missing')
    target = targets[0]
    begin = writer['start_block']*writer['block_bytes']
    end = (writer['start_block']+writer['logical_blocks']+writer['bad_block_reserve'])*writer['block_bytes']
    require(writer['block_bytes'] == block and begin == target['offset'] and
            end <= target['offset']+target['size'], 'Writer scan must start at and fit inside target')
    require(target['manager_mode'] == 0 and target['mask_flags'] == 0, 'Unreviewed target manager/flags')
    return dict(partitions=parts, target=m['target'], writer_range=[begin, end],
                page_sha256=hashlib.sha256(page).hexdigest(), writer_range_fits=True,
                active_boot_verified=False, physical_provenance_verified=False, flash_ready=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version')
    parser.add_argument('--ximage', required=True, type=Path, help='Decrypted OTA xImage; never executed')
    parser.add_argument('--metadata-page', type=Path, help='Optional saved main-data page, no live acquisition')
    args = parser.parse_args()
    try:
        firmware = load_profile(args.version)
        writer = load_writer(firmware['writer'])
        profile = load_kernel_profile(firmware, writer)
        chip = load_chip(profile['chip'])
        regular(args.ximage, profile['ximage_bytes'])
        kernel = extract_kernel(args.ximage.read_bytes(), profile)
        report = inspect_kernel(kernel, profile, chip)
        report.update(status='offline-kernel-reviewed', firmware_version=firmware['version'],
                      kernel_profile_sha256=fingerprint(profile), kernel_sha256=profile['kernel_sha256'],
                      ximage_sha256=profile['ximage_sha256'], physical_device_accessed=False,
                      page_reads_admitted=False, flash_ready=False)
        if args.metadata_page:
            regular(args.metadata_page, chip['page_bytes'])
            report['saved_metadata'] = review_partitions(args.metadata_page.read_bytes(), profile, chip, writer)
        print(json.dumps(report, indent=2))
    except (ValueError, OSError, KeyError, TypeError, struct.error, zlib.error) as error:
        parser.exit(1, f'Kernel review refused: {error}\n')


if __name__ == '__main__':
    main()
