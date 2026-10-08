#!/usr/bin/env python3
"""Offline logical-image verification from saved request/result pairs; no USB.

Input order: two first-page marker observations per physical scan block, then
all pages of the selected good blocks in logical order. Successful verification
does not authenticate capture provenance, freshness, active boot or installation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from firmware_profile import fingerprint, load_profile, load_reader_profile, sha
from deployment.metadata_policy import load_metadata_policy
from deployment.kernel_review import review_partitions
from deployment.nand_records import encode_request, decode_result, decode_digest, DIGEST_BYTES
from deployment.review import regular, require

REQUEST_BYTES, RESULT_BYTES = 48, 4428
RECORD_BYTES = REQUEST_BYTES + RESULT_BYTES
DIGEST_RECORD = REQUEST_BYTES + DIGEST_BYTES   # a page read by digest (plan, stage 4b)


def image_digest(path, size, expected):
    require(sha(expected), 'Expected a reviewed image SHA-256')
    require(regular(path, size) == size, 'Image must exactly match writer capacity')
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while chunk := source.read(1024*1024):
            digest.update(chunk)
    require(digest.hexdigest() == expected, 'Image fingerprint mismatch')


def make_plan(base, reader, policy, metadata, image_sha256, digest=False, probe_blocks=None):
    """The capture of the writer's range a comparison expects. probe_blocks: the identity probe's
    (collect_rootfs --mode rootfs-probe): only the first blocks, no reserve, and what they hold is
    not known before (image_sha256 None)."""
    require(sha(image_sha256) if probe_blocks is None else image_sha256 is None and not digest,
            'Expected a reviewed image SHA-256')
    writer, chip = policy['writer'], policy['chip']
    layout = review_partitions(metadata, policy['kernel'], chip, writer)
    first = writer['start_block']
    blocks, reserve = writer['logical_blocks'], writer['bad_block_reserve']
    if probe_blocks is not None:
        require(type(probe_blocks) is int and 0 < probe_blocks <= blocks, 'Invalid probe size')
        blocks, reserve = probe_blocks, 0
    count = blocks+reserve
    ppb = chip['pages_per_block']
    require(chip['factory_marker'] == dict(page_in_block=0, column=chip['page_bytes'], good_value=255),
            'Unsupported marker mapping')
    require(all(type(n) is int and n > 0 for n in (blocks, ppb, count))
            and type(reserve) is int and reserve >= 0 and type(first) is int and first >= 0
            and first+count <= chip['blocks'], 'Invalid readback range')
    records = 2*count+blocks*ppb
    record = DIGEST_RECORD if digest else RECORD_BYTES
    operation = 'offline-first-blocks' if probe_blocks else 'offline-logical-digest-readback' if digest else 'offline-logical-readback'
    return dict(schema_version=1, operation=operation,
                firmware_profile_sha256=fingerprint(base), reader_profile_sha256=fingerprint(reader),
                policy_sha256=fingerprint(policy), metadata_sha256=layout['page_sha256'],
                image_sha256=image_sha256, image_bytes=blocks*writer['block_bytes'],
                first_block=first, end_block_exclusive=first+count, logical_blocks=blocks,
                pages_per_block=ppb, main_bytes=chip['page_bytes'], oob_bytes=chip['oob_bytes'],
                marker_reads=2*count, data_reads=blocks*ppb,
                record_bytes=record, records=records, capture_bytes=records*record,
                physical_device_accessed=False, flash_ready=False)


def _scan_records(records_path, plan, reader, policy, nonce, first_sequence, compare_page, digest=False):
    """Shared record/mapping validation; callers must enforce their content contract.

    This returns observed hashes only, not an image acceptance status.
    """
    require(isinstance(nonce, bytes) and len(nonce) == 16 and any(nonce), 'Expected nonzero 128-bit nonce')
    require(type(first_sequence) is int and 1 <= first_sequence
            and first_sequence+plan['records']-1 <= 0xffffffff, 'Invalid sequence range')
    require(regular(records_path, plan['capture_bytes']) == plan['capture_bytes'],
            'Incomplete or extra readback records')
    capture_digest, logical_digest = hashlib.sha256(), hashlib.sha256()
    ecc_histogram, bad, good = [0]*16, [], []
    sequence = first_sequence
    with records_path.open('rb') as capture:
        size = DIGEST_RECORD if digest else RECORD_BYTES
        def page(number):
            nonlocal sequence
            record = capture.read(size)
            require(len(record) == size, f'Truncated record at sequence {sequence}')
            capture_digest.update(record)
            request = encode_request(2, nonce, sequence, number)
            require(record[:REQUEST_BYTES] == request,
                    f'Wrong request/nonce/order at sequence {sequence}, page {number}')
            result = (decode_digest if digest else decode_result)(record[REQUEST_BYTES:], request, plan['main_bytes']+plan['oob_bytes'])
            status, feature = result['status'], result['feature']
            require(0 <= status <= 255 and 0 <= feature <= 255 and 0 <= result['protect'] <= 255,
                    f'Invalid registers at page {number}')
            ecc = (status & policy['ecc_mask']) >> policy['ecc_shift']
            require(not status & 1 and policy['ecc_admitted'] & (1 << ecc),
                    f'Busy or untrusted ECC at page {number}')
            require((feature & policy['feature_mask']) == policy['feature_value']
                    and 0 < result['observed_id'] < 0xffffff
                    and result['observed_id'] & policy['id_mask'] == policy['expected_id'],
                    f'Chip/configuration mismatch at page {number}')
            require(2 <= result['polls'] <= 2*reader['nand_polls']
                    and result['transfers'] == result['polls']+8, f'Invalid counters at page {number}')
            ecc_histogram[ecc] += 1
            sequence += 1
            # By digest a page is its marker byte and its main bytes' SHA-256.
            return bytes([result['oob_head'][0]]) + result['main_sha256'] if digest else result['data']

        main, ppb = plan['main_bytes'], plan['pages_per_block']
        if digest:
            main = 0      # the marker byte leads, the SHA-256 follows
        for block in range(plan['first_block'], plan['end_block_exclusive']):
            a, b = page(block*ppb)[main], page(block*ppb)[main]
            require(a == b, f'Ambiguous marker at block {block}')
            (good if a == 255 else bad).append(block)
        require(len(good) >= plan['logical_blocks'], 'Insufficient good blocks in writer range')
        mapping = good[:plan['logical_blocks']]
        for logical, physical in enumerate(mapping):
            for offset in range(ppb):
                number = physical*ppb+offset
                data = page(number)
                require(offset != 0 or data[main] == 255, f'Marker changed at block {physical}')
                content = data[1:] if digest else data[:main]
                compare_page(content, logical, number)
                logical_digest.update(content)
        require(not capture.read(1), 'Unexpected trailing bytes')
    require(sequence == first_sequence+plan['records'], 'Wrong record count')
    return dict(capture_sha256=capture_digest.hexdigest(), image_sha256=logical_digest.hexdigest(),
                nonce_hex=nonce.hex(), first_sequence=first_sequence, records=plan['records'],
                bad_blocks=bad, logical_to_physical=mapping,
                mapping_sha256=fingerprint(mapping), ecc_histogram=ecc_histogram,
                physical_device_accessed=False, physical_provenance_verified=False,
                freshness_verified=False, active_boot_verified=False, flash_ready=False)


def verify(records_path, image, expected_plan, base, reader, policy, metadata, nonce, first_sequence=1):
    plan = make_plan(base, reader, policy, metadata, expected_plan['image_sha256'])
    require(plan == expected_plan, 'Readback plan changed')
    image_digest(image, plan['image_bytes'], plan['image_sha256'])
    with image.open('rb') as source:
        def compare(data, logical, number):
            expected = source.read(plan['main_bytes'])
            require(len(expected) == plan['main_bytes'] and data == expected,
                    f'Image mismatch at logical block {logical}, physical page {number}')
        result = _scan_records(records_path, plan, reader, policy, nonce, first_sequence, compare)
        require(not source.read(1), 'Unexpected trailing bytes')
    require(result['image_sha256'] == plan['image_sha256'], 'Image changed during comparison')
    return dict(status='saved-logical-readback-matches', plan_sha256=fingerprint(plan), **result)


def first_blocks(records_path, base, reader, policy, metadata, nonce, blocks):
    """The identity probe's first blocks, recomputed from its saved records (plan, stage 6): every
    record checked as a readback's, the blocks' marker pairs, then the pages of the first good
    blocks. Returns their SHA-256 for the images known to be compared with; no image is assumed."""
    plan = make_plan(base, reader, policy, metadata, None, probe_blocks=blocks)
    result = _scan_records(records_path, plan, reader, policy, nonce, 1, lambda *page: None)
    return dict(status='saved-first-blocks-read', plan_sha256=fingerprint(plan), image_bytes=plan['image_bytes'], **result)


def verify_digest(records_path, image, expected_plan, base, reader, policy, metadata, nonce, first_sequence=1):
    """The image's every page, by the SHA-256 of its main bytes, against a digest capture."""
    plan = make_plan(base, reader, policy, metadata, expected_plan['image_sha256'], digest=True)
    require(plan == expected_plan, 'Readback plan changed')
    image_digest(image, plan['image_bytes'], plan['image_sha256'])
    with image.open('rb') as source:
        def compare(digest, logical, number):
            expected = source.read(plan['main_bytes'])
            require(len(expected) == plan['main_bytes'] and digest == hashlib.sha256(expected).digest(),
                    f'Image mismatch at logical block {logical}, physical page {number}')
        result = _scan_records(records_path, plan, reader, policy, nonce, first_sequence, compare, digest=True)
        require(not source.read(1), 'Unexpected trailing bytes')
    result['digests_sha256'] = result.pop('image_sha256')
    return dict(status='saved-logical-digest-matches', plan_sha256=fingerprint(plan), image_sha256=plan['image_sha256'], **result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'verify'))
    parser.add_argument('--version')
    parser.add_argument('--metadata-page', type=Path, required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--image-sha256', required=True)
    parser.add_argument('--records', type=Path)
    parser.add_argument('--nonce-hex')
    parser.add_argument('--first-sequence', type=int, default=1)
    parser.add_argument('--digest', action='store_true', help='A capture read by digest (plan, stage 4b)')
    args = parser.parse_args()
    if (args.action == 'verify' and (args.records is None or args.nonce_hex is None)) or (
            args.action == 'plan' and (args.records is not None or args.nonce_hex is not None or args.first_sequence != 1)):
        parser.error('verify requires records and nonce; plan accepts no capture arguments')
    try:
        base = load_profile(args.version)
        reader = load_reader_profile(base)
        policy = load_metadata_policy(base, reader)
        require(regular(args.metadata_page, policy['main_bytes']) == policy['main_bytes'], 'Invalid metadata page')
        metadata = args.metadata_page.read_bytes()
        plan = make_plan(base, reader, policy, metadata, args.image_sha256, digest=args.digest)
        image_digest(args.image, plan['image_bytes'], args.image_sha256)
        result = dict(plan=plan, plan_sha256=fingerprint(plan)) if args.action == 'plan' else (verify_digest if args.digest else verify)(
            args.records, args.image, plan, base, reader, policy, metadata,
            bytes.fromhex(args.nonce_hex), args.first_sequence)
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps(dict(status='rejected', error=str(exc), flash_ready=False)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
