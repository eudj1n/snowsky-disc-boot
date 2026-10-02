#!/usr/bin/env python3
"""Classify differences in saved repeated NAND reads; no USB or hardware admission.

Exit zero means a report was produced, including non-parity differences. Region
labels describe documented addresses, not the cause or validity of returned data.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from firmware_profile import PROFILES, fingerprint, load_profile, load_reader_profile, require
from deployment.metadata_policy import load_metadata_policy
from deployment.nand_records import encode_request, decode_result
from deployment.review import regular

RECORD_BYTES = 48+4428
REGIONS = ['main', 'protected_metadata', 'ecc_parity', 'unprotected_metadata']


def validate_layout(layout, chip):
    require(layout.get('schema_version') == 1 and
            layout.get('chip_profile_sha256') == fingerprint(chip), 'OOB chip audit changed')
    regions = layout.get('regions', [])
    require(isinstance(regions, list) and [r.get('name') for r in regions] == REGIONS,
            'Invalid OOB region names/order')
    end = 0
    for region in regions:
        a, b = region.get('start'), region.get('end')
        require(type(a) is int and type(b) is int and a == end and a < b,
                'OOB regions must be contiguous and nonempty')
        end = b
    require(regions[0]['end'] == chip['page_bytes'] and
            end == chip['page_bytes']+chip['oob_bytes'], 'OOB region geometry mismatch')


def load_layout(chip, directory=PROFILES):
    # The selector comes from the validated chip profile, never a firmware branch.
    path = directory/'oob'/f'{chip["id"]}.json'
    regular(path, 8192)
    layout = json.loads(path.read_text())
    validate_layout(layout, chip)
    return layout


def audit(path, count, pages, nonce, base, reader, policy, layout, first_sequence=1):
    validate_layout(layout, policy['chip'])
    require(type(count) is int and 1 <= count <= 65536, 'Invalid bounded record count')
    require(isinstance(pages, list) and 1 <= len(pages) <= 64 and
            all(type(p) is int and 0 <= p < policy['total_pages'] for p in pages) and
            len(set(pages)) == len(pages), 'Invalid selected pages')
    require(type(first_sequence) is int and 1 <= first_sequence <= 0xffffffff-count+1,
            'Invalid sequence range')
    encode_request(2, nonce, first_sequence)
    require(regular(path, count*RECORD_BYTES) == count*RECORD_BYTES, 'Incomplete or extra records')
    regions = layout['regions']
    parity = regions[2]
    baselines, observations = {}, {p: [] for p in pages}
    capture_hash, ecc_histogram = hashlib.sha256(), [0]*16
    parity_hashes, alternating = Counter(), [None, None]
    period_two, changed, comparisons = True, set(), 0
    with path.open('rb') as source:
        for index in range(count):
            frame = source.read(RECORD_BYTES)
            require(len(frame) == RECORD_BYTES, 'Truncated record')
            capture_hash.update(frame)
            page = struct.unpack_from('<I', frame, 32)[0]
            require(page < policy['total_pages'], 'Physical page outside chip')
            sequence = first_sequence+index
            request = encode_request(2, nonce, sequence, page)
            require(frame[:48] == request, 'Wrong request/nonce/sequence')
            r = decode_result(frame[48:], request, regions[-1]['end'])
            require(all(0 <= r[k] <= 255 for k in ('status', 'feature', 'protect')),
                    'Invalid feature registers')
            ecc = (r['status'] & policy['ecc_mask']) >> policy['ecc_shift']
            require(not r['status'] & 1 and policy['ecc_admitted'] & (1 << ecc), 'Busy or untrusted ECC')
            require(0 < r['observed_id'] < 0xffffff and
                    r['observed_id'] & policy['id_mask'] == policy['expected_id'] and
                    r['feature'] & policy['feature_mask'] == policy['feature_value'],
                    'Chip/configuration mismatch')
            require(2 <= r['polls'] <= 2*reader['nand_polls'] and r['transfers'] == r['polls']+8,
                    'Invalid transfer counters')
            ecc_histogram[ecc] += 1
            data = r['data']
            digest = hashlib.sha256(data[parity['start']:parity['end']]).hexdigest()
            parity_hashes[digest] += 1
            if index < 2: alternating[index] = digest
            elif alternating[index % 2] != digest: period_two = False
            if page not in observations: continue
            if page not in baselines:
                baselines[page] = (sequence, data)
                continue
            comparisons += 1
            require(comparisons <= 256, 'Too many repeat comparisons; narrow the input selection')
            differences = {}
            for region in regions:
                a, b, name = region['start'], region['end'], region['name']
                offsets = [i-a for i in range(a, b) if data[i] != baselines[page][1][i]]
                if offsets: differences[name] = offsets; changed.add(name)
            observations[page].append(dict(sequence=sequence, changed_offsets=differences))
        require(not source.read(1), 'Unexpected trailing records')
    require(all(observations.values()), 'Each selected page requires at least two observations')
    return dict(status='offline-oob-classification', firmware_profile_sha256=fingerprint(base),
                reader_profile_sha256=fingerprint(reader), policy_sha256=fingerprint(policy),
                layout_sha256=fingerprint(layout), capture_sha256=capture_hash.hexdigest(),
                records=count, nonce_hex=nonce.hex(), first_sequence=first_sequence,
                regions=regions, ecc_histogram=ecc_histogram,
                pages=[dict(page=p, first_sequence=baselines[p][0], comparisons=observations[p]) for p in pages],
                changed_regions=sorted(changed), differences_confined_to_ecc_parity=changed == {'ecc_parity'},
                selected_repeats_byte_identical=not changed,
                parity_sha256_histogram=dict(sorted(parity_hashes.items())),
                parity_alternates_by_record=count > 2 and period_two and alternating[0] != alternating[1],
                physical_device_accessed=False, physical_provenance_verified=False,
                cause_established=False, raw_nand_verified=False, flash_ready=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version')
    parser.add_argument('--records', type=Path, required=True)
    parser.add_argument('--nonce', required=True)
    parser.add_argument('--count', type=int, required=True)
    parser.add_argument('--pages', type=int, nargs='+', required=True)
    parser.add_argument('--first-sequence', type=int, default=1)
    args = parser.parse_args()
    try:
        base = load_profile(args.version)
        reader = load_reader_profile(base)
        policy = load_metadata_policy(base, reader)
        report = audit(args.records, args.count, args.pages, bytes.fromhex(args.nonce), base,
                       reader, policy, load_layout(policy['chip']), args.first_sequence)
        print(json.dumps(report, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(2, f'OOB review failed: {error}\n')


if __name__ == '__main__':
    main()
