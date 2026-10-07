#!/usr/bin/env python3
"""Offline identity or fixed metadata-page RAM build; never opens USB."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from firmware_profile import fingerprint, load_profile, load_reader_profile, require
from deployment.metadata_policy import load_metadata_policy
from deployment.collector_policy import load_collector_policy


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_paths():
    return sorted(str(x.relative_to(ROOT)) for x in (ROOT/'device/acquisition').glob('*.[chS]')) + [
        'scripts/deployment/build_identity.py', 'scripts/deployment/link_identity.sh', 'scripts/firmware_profile.py',
        'scripts/deployment/metadata_policy.py', 'scripts/deployment/kernel_review.py',
        'scripts/deployment/chip_review.py', 'scripts/deployment/review.py',
        'scripts/deployment/collector_policy.py']


def prepare(p, output, page_policy=None, collector=None):
    fields = {'STACK_TOP': 'stack_top', 'REQUEST': 'request_address', 'RESULT': 'result_address',
              'SFC_POLLS': 'sfc_polls', 'NAND_POLLS': 'nand_polls', 'EXTAL_MHZ': 'extal_mhz',
              'TARGET_MHZ': 'target_mhz', 'ID_ADDRESS_BYTES': 'id_address_bytes'}
    (output/'identity_layout.h').write_text(''.join(
        f'#define IDENTITY_{name} 0x{p[key]:x}\n' for name, key in fields.items()))
    if page_policy is not None:
        with (output/'identity_layout.h').open('a') as header:
            if collector:
                for name, value in [('ROOTFS_FIRST_PAGE', collector['first_page']),
                                    ('ROOTFS_END_PAGE', collector['end_page']),
                                    ('BATCH_REQUEST_ADDRESS', collector['profile']['request_address']),
                                    ('BATCH_RESULT_ADDRESS', collector['profile']['result_address'])]:
                    header.write(f'#define {name} 0x{value:x}\n')
                if collector['mode'] == 'rootfs-digest':
                    header.write('#define ROOTFS_DIGEST 1\n')
            else:
                header.write(f'#define METADATA_PAGE {page_policy["page"]}\n')
            for key in ('expected_id', 'id_mask', 'main_bytes', 'oob_bytes', 'total_pages',
                        'feature_mask', 'feature_value', 'ecc_mask', 'ecc_shift', 'ecc_admitted'):
                header.write(f'#define PAGE_{key.upper()} 0x{page_policy[key]:x}\n')
    (output/'identity.ld').write_text(f'''ENTRY(_start)
PHDRS {{ image PT_LOAD FLAGS(7); }}
SECTIONS {{
  . = 0x{p['load_address']:x};
  .text : {{ KEEP(*(.text.entry)) *(.text*) }} :image
  .rodata : {{ *(.rodata*) }} :image
  .data : {{ *(.data*) *(.sdata*) }} :image
  .reginfo : {{ *(.reginfo) }} :image
  .MIPS.abiflags : {{ *(.MIPS.abiflags) }} :image
  .bss (NOLOAD) : {{ *(.bss*) *(.sbss*) *(COMMON) }} :image
  .got : {{ *(.got*) }} :image
  ASSERT(SIZEOF(.bss) == 0, "BSS needs an explicit initialization path")
  ASSERT(SIZEOF(.got) == 0, "Unexpected GOT")
  ASSERT(. <= 0x{p['load_address'] + p['code_bytes']:x}, "RAM code budget exceeded")
  /DISCARD/ : {{ *(.comment) *(.note*) *(.pdr) *(.mdebug*) *(.eh_frame*) }}
}}
''')


def inspect_elf(data, p):
    require(len(data) >= 52 and data[:7] == b'\x7fELF\x01\x01\x01', 'Expected ELF32 little endian')
    h = struct.unpack_from('<HHIIIIIHHHHHH', data, 16)
    kind, machine, version, entry, offset = h[:5]
    require((kind, machine, version, entry) == (2, 8, 1, p['load_address']), 'Wrong MIPS executable/entry')
    size, count = h[8:10]
    require(size == 32 and 1 <= count <= 8 and offset >= 52 and offset + count*size <= len(data),
            'Invalid program headers')
    loads = []
    for i in range(count):
        typ, off, va, pa, filesz, memsz, flags, align = struct.unpack_from('<8I', data, offset+i*size)
        require(typ not in (2, 3), 'Dynamic linking/interpreter is forbidden')
        if typ != 1:
            continue
        require(va == pa == p['load_address'] and 0 < filesz == memsz <= p['code_bytes']
                and off + filesz <= len(data) and flags & 1, 'Invalid loaded RAM segment')
        loads.append(data[off:off+filesz])
    require(len(loads) == 1, 'Expected one initialized load segment')
    return loads[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version')
    parser.add_argument('--mode', choices=('identity', 'metadata', 'rootfs-probe', 'rootfs', 'rootfs-digest'), default='identity')
    parser.add_argument('--diskos', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    base = load_profile(args.version)
    p = load_reader_profile(base)
    page_policy = load_metadata_policy(base, p) if args.mode != 'identity' else None
    collector = load_collector_policy(base, p, args.mode) if args.mode.startswith('rootfs') else None
    for name, sha in p['source_pins'].items():
        require(digest(args.diskos/name) == sha, f'External input changed: {name}')
    output = args.output.resolve()
    require(any(output.is_relative_to(ROOT/name) for name in ('build', 'work')),
            'Output must be inside ignored build/ or work/')
    output.mkdir(parents=True, exist_ok=False)
    prepare(p, output, page_policy, collector)
    image = os.environ.get('DISC_TOOLCHAIN_IMAGE', 'diskos-ui-builder')
    image_id = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', image], text=True).strip()
    subprocess.run(['docker', 'run', '--rm', '--platform', 'linux/amd64', '--network', 'none',
                    '-v', f'{ROOT}:/src:ro', '-v', f'{output}:/out', '-w', '/out', image_id,
                    'sh', '/src/scripts/deployment/link_identity.sh'], check=True)
    binary = (output/'identity.bin').read_bytes()
    require(inspect_elf((output/'identity.elf').read_bytes(), p) == binary, 'ELF/raw image mismatch')
    report = {'schema_version': 1, 'version': base['version'], 'firmware_profile_sha256': fingerprint(base),
              'purpose': args.mode, 'page_policy': page_policy, 'collector_policy': collector,
              'reader_profile_sha256': fingerprint(p), 'reader_profile': p, 'toolchain_image': image_id,
              'source_sha256': {name: digest(ROOT/name) for name in source_paths()},
              'artifacts': {n: digest(output/n) for n in ('identity.elf', 'identity.bin', 'identity_layout.h', 'identity.ld')},
              'bytes': len(binary), 'entry': p['load_address'],
              'nand_opcodes': ['0x9f', '0x0f'] + (['0x13', '0x0b'] if page_policy else []),
              'physical_qualified': False, 'device_access_performed': False}
    (output/'build.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'output': str(output), 'bytes': len(binary), 'physical_qualified': False}))


if __name__ == '__main__':
    main()
