#!/usr/bin/env python3
"""Compare saved identity bytes with a documented chip and pinned writer audit.

Offline only. Exit zero means a comparison was produced, never flash readiness.
Inputs are local evidence, not authenticated device identity or a journal audit.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from firmware_profile import PROFILES, fingerprint, load_profile, load_writer, require
from deployment.nand_records import decode_result
from deployment.review import check_pin, regular, writer_capacity


def load_chip(name, directory=PROFILES):
    require(isinstance(name, str) and re.fullmatch('[a-z0-9-]+', name), 'Invalid chip selector')
    path = directory/'chips'/f'{name}.json'
    regular(path, 16384)
    p = json.loads(path.read_text())
    require(p.get('schema_version') == 1 and p.get('id') == name, 'Invalid chip schema/identity')
    require(isinstance(p.get('id_prefix_hex'), str) and
            re.fullmatch('[a-f0-9]{4}', p['id_prefix_hex']), 'Expected two documented ID bytes')
    for key in ('page_bytes', 'oob_bytes', 'pages_per_block', 'blocks'):
        require(type(p.get(key)) is int and 0 < p[key] <= 0x1000000, f'Invalid chip {key}')
    require(p.get('page_reads_admitted') is False and p.get('writes_admitted') is False,
            'This review format cannot admit hardware operations')
    audit = p.get('writer_audit', {})
    require(isinstance(audit.get('profile_sha256'), str) and
            re.fullmatch('[a-f0-9]{64}', audit['profile_sha256']), 'Missing writer audit pin')
    for key in ('reads_ecc_status', 'checks_chip_id', 'attempts_ecc_disable'):
        require(type(audit.get(key)) is bool, f'Missing writer audit {key}')
    return p


def compare(request, raw, chip, writer):
    require(fingerprint(writer) == chip['writer_audit']['profile_sha256'],
            'Writer changed; repeat chip/source audit before comparison')
    identity = decode_result(raw, request, 0)
    require(0 <= identity['observed_id'] <= 0xffffff, 'Identity exceeds three wire bytes')
    wire = identity['observed_id'].to_bytes(3, 'little')
    require(wire[:2].hex() == chip['id_prefix_hex'], 'Unknown chip ID for selected profile')
    for key in ('protect', 'feature', 'status'):
        require(0 <= identity[key] <= 255, f'Invalid feature register {key}')
    require(not identity['status'] & 1, 'Identity reports a busy chip')
    require(chip['page_reads_admitted'] is False and chip['writes_admitted'] is False,
            'Review does not admit hardware operations')
    audit = chip['writer_audit']
    block_bytes = chip['page_bytes'] * chip['pages_per_block']
    end = writer['start_block'] + writer['logical_blocks'] + writer['bad_block_reserve']
    checks = {
        'page_geometry_matches': (chip['page_bytes'] == audit['page_bytes'] and
                                  chip['pages_per_block'] == audit['pages_per_block'] and
                                  block_bytes == writer['block_bytes']),
        'factory_marker_location_matches': chip['factory_marker'] == audit['factory_marker'],
        'scan_fits_documented_chip': end <= chip['blocks'],
        'writer_checks_chip_id': audit['checks_chip_id'],
        'writer_reads_ecc_status': audit['reads_ecc_status'],
        'ecc_disable_assumption_matches': not (audit['attempts_ecc_disable'] and chip['ecc']['always_on']),
    }
    identity.pop('data')
    return {
        'status': 'offline-chip-comparison',
        'chip_family': chip['family'], 'id_wire_hex': wire.hex(),
        'matched_id_bytes': 2, 'identity': identity,
        'request_sha256': hashlib.sha256(request).hexdigest(),
        'result_sha256': hashlib.sha256(raw).hexdigest(),
        'chip_profile_sha256': fingerprint(chip),
        'writer_profile_sha256': fingerprint(writer),
        'documented_geometry': {key: chip[key] for key in
                                ('page_bytes', 'oob_bytes', 'pages_per_block', 'blocks')},
        'writer_byte_range': [writer['start_block'] * writer['block_bytes'], end * writer['block_bytes']],
        'checks': checks,
        'remaining_review': [
            'Confirm stock partition/boot mapping and bad-block translation.',
            'Resolve always-on ECC marker semantics and per-read ECC error handling.',
            'Review program-load/write-enable ordering for this chip.',
            'Bind any future installation to fresh identity, exact images and recovery procedure.',
        ],
        'physical_device_accessed': False, 'physical_provenance_verified': False,
        'page_reads_admitted': False, 'hardware_qualified': False, 'flash_ready': False,
    }


def bounded_bytes(path, size):
    require(regular(path, size) == size, f'Wrong saved record length: {path}')
    return path.read_bytes()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version')
    parser.add_argument('--chip', required=True)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--result', type=Path, required=True)
    parser.add_argument('--diskos', type=Path, required=True)
    args = parser.parse_args()
    try:
        firmware = load_profile(args.version)
        writer = load_writer(firmware['writer'])
        chip = load_chip(args.chip)
        sources = {name: check_pin(args.diskos/name, digest)
                   for name, digest in writer['source_pins'].items()}
        writer_capacity((args.diskos/writer['writer_file']).read_bytes(), writer)
        result = compare(bounded_bytes(args.request, 48), bounded_bytes(args.result, 4428), chip, writer)
        result.update(firmware_version=firmware['version'], firmware_profile_sha256=fingerprint(firmware),
                      writer_sources=sources)
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(1, f'Chip review refused: {error}\n')


if __name__ == '__main__':
    main()
