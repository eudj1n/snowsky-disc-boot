#!/usr/bin/env python3
"""Offline stock-content acceptance before installation, never post-write approval.

Compare pinned official rootfs bytes and every byte of a separately reviewed
stock tail. Does not alter the exact-image verifier or authorize device access.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from firmware_profile import PROFILES, fingerprint, load_profile, load_reader_profile, require
from deployment import readback
from deployment.metadata_policy import load_metadata_policy
from deployment.review import check_image, regular


def validate_review(base, policy, review):
    require(review.get('schema_version') == 1 and review.get('version') == base['version']
            and review.get('scope') == 'stock-before-installation'
            and review.get('physical_qualified') is False, 'Invalid pre-installation review scope')
    require(review.get('firmware_profile_sha256') == fingerprint(base)
            and review.get('page_policy_sha256') == fingerprint(policy), 'Pre-installation audit changed')
    require(type(review.get('tail_value')) is int and 0 <= review['tail_value'] <= 255,
            'Invalid reviewed tail byte')


def load_review(base, policy, directory=PROFILES):
    path = directory/'preinstall'/f'v{base["version"]}.json'
    require(path.exists(), 'Unreviewed firmware pre-installation policy')
    regular(path, 8192)
    review = json.loads(path.read_text())
    validate_review(base, policy, review)
    return review


def make_plan(base, reader, policy, review, metadata, restore_sha256):
    validate_review(base, policy, review)
    plan = readback.make_plan(base, reader, policy, metadata, restore_sha256)
    require(type(base['rootfs_size']) is int and 4 <= base['rootfs_size'] <= plan['image_bytes'],
            'Official rootfs exceeds writer capacity')
    require(plan['logical_blocks']*plan['pages_per_block']*plan['main_bytes'] == plan['image_bytes'],
            'Writer and reader geometry disagree')
    return dict(plan, operation='offline-stock-preinstall', review_sha256=fingerprint(review),
                rootfs_bytes=base['rootfs_size'], rootfs_sha256=base['rootfs_sha256'],
                tail_bytes=plan['image_bytes']-base['rootfs_size'], tail_value=review['tail_value'])


def verify(records_path, restore, expected_plan, base, reader, policy, review, metadata,
           nonce, first_sequence=1):
    plan = make_plan(base, reader, policy, review, metadata, expected_plan['image_sha256'])
    require(plan == expected_plan, 'Pre-installation plan changed')
    checked = check_image(restore, plan['image_bytes'], plan['image_sha256'], plan['rootfs_bytes'])
    require(checked['contentSha256'] == plan['rootfs_sha256'], 'Restore is not pinned official rootfs')
    position = 0
    content_hash, tail_hash, reference_hash = hashlib.sha256(), hashlib.sha256(), hashlib.sha256()
    with restore.open('rb') as source:
        def compare(data, logical, number):
            nonlocal position
            expected = source.read(plan['main_bytes'])
            require(len(expected) == len(data), 'Short restore image during comparison')
            reference_hash.update(expected)
            take = max(0, min(len(data), plan['rootfs_bytes']-position))
            require(data[:take] == expected[:take], f'Official rootfs mismatch at physical page {number}')
            require(data[take:] == bytes([plan['tail_value']])*(len(data)-take),
                    f'Unreviewed stock tail at physical page {number}')
            content_hash.update(data[:take]); tail_hash.update(data[take:])
            position += len(data)
        observed = readback._scan_records(records_path, plan, reader, policy, nonce, first_sequence, compare)
        require(not source.read(1) and position == plan['image_bytes'], 'Incomplete stock comparison')
    require(reference_hash.hexdigest() == plan['image_sha256'], 'Restore changed during comparison')
    require(content_hash.hexdigest() == plan['rootfs_sha256'], 'Official rootfs hash mismatch')
    # Keep the observed complete image hash separate from the reviewed restore hash.
    observed_hash = observed.pop('image_sha256')
    return dict(status='saved-stock-preinstall-content-matches', plan_sha256=fingerprint(plan),
                rootfs_bytes=plan['rootfs_bytes'], rootfs_sha256=content_hash.hexdigest(),
                tail_bytes=plan['tail_bytes'], tail_value=plan['tail_value'], tail_sha256=tail_hash.hexdigest(),
                observed_image_sha256=observed_hash, restore_image_sha256=plan['image_sha256'],
                official_rootfs_match=True, exact_restore_image_match=observed_hash == plan['image_sha256'],
                postwrite_verified=False, **observed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'verify'))
    parser.add_argument('--version')
    parser.add_argument('--metadata-page', type=Path, required=True)
    parser.add_argument('--restore', type=Path, required=True)
    parser.add_argument('--restore-sha256', required=True)
    parser.add_argument('--records', type=Path)
    parser.add_argument('--nonce-hex')
    args = parser.parse_args()
    if (args.action == 'verify' and (args.records is None or args.nonce_hex is None)) or (
            args.action == 'plan' and (args.records is not None or args.nonce_hex is not None)):
        parser.error('verify requires records/nonce; plan accepts neither')
    try:
        base = load_profile(args.version); reader = load_reader_profile(base)
        policy = load_metadata_policy(base, reader); review = load_review(base, policy)
        require(regular(args.metadata_page, policy['main_bytes']) == policy['main_bytes'], 'Invalid metadata page')
        metadata = args.metadata_page.read_bytes()
        plan = make_plan(base, reader, policy, review, metadata, args.restore_sha256)
        checked = check_image(args.restore, plan['image_bytes'], plan['image_sha256'], plan['rootfs_bytes'])
        require(checked['contentSha256'] == plan['rootfs_sha256'], 'Restore is not pinned official rootfs')
        result = dict(plan=plan, plan_sha256=fingerprint(plan)) if args.action == 'plan' else verify(
            args.records, args.restore, plan, base, reader, policy, review, metadata, bytes.fromhex(args.nonce_hex))
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps(dict(status='rejected', error=str(exc), flash_ready=False)))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
