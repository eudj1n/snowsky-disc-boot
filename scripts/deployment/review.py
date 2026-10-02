#!/usr/bin/env python3
"""Read local images and reference bytes only. Never enumerate USB or run helpers.

Exit 0 means the offline review passed, NEVER permission/readiness to flash.
A consistent debug record cannot establish its freshness or physical bootability.
"""
import argparse
import hashlib
import json
from pathlib import Path
import stat
import struct

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from firmware_profile import load_profile, load_writer, load_usb_profile, load_os_profile, fingerprint, artifact_names


def require(condition, message):
    if not condition:
        raise ValueError(message)


def regular(path, limit):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_size <= limit,
            f'Expected bounded regular file, not a symlink/device: {path}')
    return info.st_size


def check_pin(path, expected):
    regular(path, 1024*1024)
    data = path.read_bytes()
    require(hashlib.sha256(data).hexdigest() == expected, f'Reference changed: {path}')
    return {'bytes': len(data), 'sha256': expected}


def writer_capacity(data, writer):
    blocks = writer['logical_blocks']
    offset = writer['capacity_instruction_offset']
    require(len(data) >= offset+4, 'Short writer')
    instruction, = struct.unpack_from('<I', data, offset)
    require(instruction & writer['capacity_instruction_mask'] == writer['capacity_instruction_value'], 'Unknown writer instruction layout')
    capacity = instruction & 0xffff
    # Larger writers are unsafe too: they read beyond our staged image.
    require(capacity == blocks, 'Writer must exactly match the profile logical block count')
    return capacity


def check_image(path, expected_bytes, expected_sha, content_bytes):
    require(4 <= content_bytes <= expected_bytes, 'Invalid content length')
    require(regular(path, expected_bytes) == expected_bytes, 'Wrong image size')
    full = hashlib.sha256()
    content = hashlib.sha256()
    position = 0
    with path.open('rb') as source:
        require(source.read(4) == b'hsqs', 'Expected squashfs magic')
        source.seek(0)
        while chunk := source.read(1024*1024):
            full.update(chunk)
            take = max(0, min(len(chunk), content_bytes-position))
            content.update(chunk[:take])
            require(not any(chunk[take:]), 'Nonzero image padding')
            position += len(chunk)
    require(position == expected_bytes and full.hexdigest() == expected_sha,
            'Image hash/length mismatch')
    return {'bytes': position, 'sha256': full.hexdigest(),
            'contentSha256': content.hexdigest(), 'zeroPadding': True}


def check_debug(raw, usbboot_exit, writer):
    blocks, start = writer['logical_blocks'], writer['start_block']
    reserve = writer['bad_block_reserve']
    end = start + blocks + reserve
    require(usbboot_exit == 0, 'Host transfer did not finish successfully; result unknown')
    require(len(raw) == 1024, 'Expected exactly 1024 bytes of writer readback')
    w = struct.unpack('<256I', raw)
    require((w[0], w[9], w[16]) == (0x4004e005, 0x55555555, 0x600df10c),
            'Missing writer success/DONE markers; result failed or unknown')
    require((w[1], w[5], w[6]) == (blocks, start, blocks), 'Wrong progress/start/capacity')
    require(w[3] == w[4] == w[7] == w[8] == 0 and w[15] & 0x10 and w[21] & 0x10,
            'NAND initialization/ECC/write status mismatch')
    require(w[12] == 0x73717368, 'Source was not squashfs')
    require(w[20] <= reserve, 'Bad-block list exceeds reviewed capacity')
    bad = list(w[40:40+w[20]])
    require(bad == sorted(set(bad)) and all(start <= b < end for b in bad),
            'Invalid bad-block scan list')
    require(start+blocks <= w[11] <= end, 'Physical end outside writer range')
    skipped = sum(b < w[11] for b in bad)
    require(w[10] == skipped and w[11]-start == blocks+skipped,
            'Bad-block mapping/progress inconsistent')
    require(w[11]-1 not in bad, 'Writer cannot finish on a skipped bad block')
    require(w[17] <= blocks and ((w[17] == 0 and w[18] == 0)
            or (w[17] > 0 and 2 <= w[18] <= writer['max_tries'])), 'Invalid retry counters')
    return {'status': 'consistent-writer-record', 'freshnessVerified': False,
            'bootVerified': False, 'logicalBlocks': w[1], 'physicalEndExclusive': w[11],
            'badBlocks': bad, 'skipped': skipped, 'retried': w[17], 'worstTries': w[18]}


def review(artifacts, diskos, profile, writer):
    image_bytes = writer['block_bytes'] * writer['logical_blocks']
    regular(artifacts/'report.json', 65536)
    report = json.loads((artifacts/'report.json').read_text())
    variant = report.get('variant', 'companion')
    candidate, restore = artifact_names(profile, variant)
    if variant == 'product':
        require('usbDiagnostic' not in report and report.get('webroot', {}).get('rawMode') is False,
                'The product image carries no USB diagnostics or raw mode')
    if variant == 'usb-engineering':
        usb = load_usb_profile(profile)
        require(report['usbDiagnostic']['profileSha256'] == fingerprint(usb)
                and report['usbDiagnostic']['optInRequired'] is True
                and report['usbDiagnostic']['physicalQualified'] is False,
                'Engineering USB profile/qualification mismatch')
    # Images since the device-facts stage pin the OS profile their hook arguments came from.
    webroot = report.get('webroot') or {}
    if 'osProfileSha256' in webroot:
        require(webroot['osProfileSha256'] == fingerprint(load_os_profile(profile)), 'OS profile fingerprint mismatch')
    require(report['status'] == 'offline-verified' and report['product'] == profile['product']
            and report['firmware'] == profile['main_os_version'] and report['fullRoundTrip'] is True,
            'Expected completed offline build for selected profile')
    require(report['profileSha256'] == fingerprint(profile)
            and report['writerProfileSha256'] == fingerprint(writer), 'Build profile fingerprint mismatch')
    require(report['hardwareQualified'] is False and report['flashAuthorized'] is False,
            'Unexpected readiness claims in review-only build')
    require(report['writerFormatBytes'] == image_bytes and report['stockBytes'] == profile['rootfs_size']
            and report['stockSha256'] == profile['rootfs_sha256'], 'Wrong firmware/image format')
    require(set(report['artifacts']) == {candidate, restore}, 'Unexpected image set')
    images = {}
    for name, length in ((candidate, report['packedBytes']), (restore, profile['rootfs_size'])):
        entry = report['artifacts'][name]
        require(entry['bytes'] == image_bytes, 'Unexpected artifact size')
        images[name] = check_image(artifacts/name, image_bytes, entry['sha256'], length)
    require(images[restore]['contentSha256'] == profile['rootfs_sha256'], 'Restore is not pinned stock')
    pins = {name:check_pin(diskos/name, sha) for name, sha in writer['source_pins'].items()}
    capacity = writer_capacity((diskos/writer['writer_file']).read_bytes(), writer)
    return {
        'status': 'offline-review-passed', 'flashReady': False, 'physicalDeviceAccessed': False,
        'images': images, 'variant': variant, 'referenceFiles': pins, 'writerLogicalBlocks': capacity,
        'writerAssumptions': {'blockBytes': writer['block_bytes'], 'startBlock': writer['start_block'],
                              'physicalEndExclusive': writer['start_block']+capacity+writer['bad_block_reserve'],
                              'offsetBytes': writer['start_block']*writer['block_bytes'],
                              'endOffsetExclusive': (writer['start_block']+capacity+writer['bad_block_reserve'])*writer['block_bytes']},
        'blockers': ['Actual NAND identity, geometry and active rootfs mapping unverified',
                     'Live backup and independent readback route not implemented',
                     'Native macOS USB helper bundle not qualified',
                     'Diagnostic bootstrap and native process management not qualified',
                     'No separately authorized physical installation/recovery test'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', help='Reviewed firmware profile selector')
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--diskos', type=Path, required=True)
    parser.add_argument('--debug', type=Path, help='Optional saved writer record, never acquired here')
    parser.add_argument('--usbboot-exit', type=int)
    args = parser.parse_args()
    try:
        require((args.debug is None) == (args.usbboot_exit is None),
                '--debug and --usbboot-exit must be supplied together')
        profile = load_profile(args.version)
        writer = load_writer(profile['writer'])
        result = review(args.artifacts, args.diskos, profile, writer)
        if args.debug is not None:
            regular(args.debug, 1024)
            result['writerRecord'] = check_debug(args.debug.read_bytes(), args.usbboot_exit, writer)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'rejected', 'flashReady': False, 'error': str(exc)}, indent=2))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
