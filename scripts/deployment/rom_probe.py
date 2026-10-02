#!/usr/bin/env python3
"""Plan offline, or explicitly acquire one CPU-info reply. No RAM/NAND operations.

Acquisition requires separately authorized physical access. Import and plan never
load libusb. The profile selects VID/PID and timeout, not arbitrary USB requests.
"""
import argparse
import ctypes as C
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
import stat
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from firmware_profile import load_profile, load_probe_profile, fingerprint


class Descriptor(C.Structure):
    _fields_ = [('length', C.c_uint8), ('type', C.c_uint8), ('usb', C.c_uint16),
                ('device_class', C.c_uint8), ('subclass', C.c_uint8), ('protocol', C.c_uint8),
                ('packet_size', C.c_uint8), ('vid', C.c_uint16), ('pid', C.c_uint16),
                ('device', C.c_uint16), ('manufacturer', C.c_uint8), ('product', C.c_uint8),
                ('serial', C.c_uint8), ('configurations', C.c_uint8)]


class ProbeError(Exception):
    pass


def check(ok, message):
    if not ok:
        raise ProbeError(message)


def bind(lib):
    """Bind only enumeration/open/close and the fixed control-IN primitive."""
    ptr = C.c_void_p
    signatures = {
        'libusb_init': (C.c_int, [C.POINTER(ptr)]),
        'libusb_exit': (None, [ptr]),
        'libusb_get_device_list': (C.c_ssize_t, [ptr, C.POINTER(C.POINTER(ptr))]),
        'libusb_free_device_list': (None, [C.POINTER(ptr), C.c_int]),
        'libusb_get_device_descriptor': (C.c_int, [ptr, C.POINTER(Descriptor)]),
        'libusb_get_bus_number': (C.c_uint8, [ptr]),
        'libusb_get_device_address': (C.c_uint8, [ptr]),
        'libusb_get_port_numbers': (C.c_int, [ptr, C.POINTER(C.c_uint8), C.c_int]),
        'libusb_open': (C.c_int, [ptr, C.POINTER(ptr)]),
        'libusb_close': (None, [ptr]),
        'libusb_control_transfer': (C.c_int, [ptr, C.c_uint8, C.c_uint8,
            C.c_uint16, C.c_uint16, C.POINTER(C.c_uint8), C.c_uint16, C.c_uint]),
    }
    for name, (result, args) in signatures.items():
        fn = getattr(lib, name)
        fn.restype, fn.argtypes = result, args
    return lib


def request(profile):
    return dict(requestType=0xc0, request=0, value=0, index=0,
                length=8, timeoutMs=profile['timeout_ms'], attempts=1)


def match_reply(profile, code, reply_hex):
    check(type(code) is int and 0 < code <= 8, f'Invalid CPU transfer result: {code}')
    check(isinstance(reply_hex, str) and re.fullmatch(r'[0-9a-f]+', reply_hex)
          and len(reply_hex) == code * 2, 'CPU reply length/encoding mismatch')
    check(reply_hex in profile['accepted_reply_hex'], 'CPU reply does not match a reviewed signature')
    return reply_hex


def plan(firmware, profile):
    return dict(schemaVersion=1, operation='rom-cpu-info',
                requestedFirmwareVersion=firmware['version'],
                firmwareProfileSha256=fingerprint(firmware), probeProfileSha256=fingerprint(profile),
                protocolSourceSha256=profile['reference_source_sha256'],
                target=dict(vid=profile['vid'], pid=profile['pid'], requiredMatches=1),
                transfer=request(profile), physicalDeviceAccessed=False,
                acceptedReplyHex=profile['accepted_reply_hex'],
                ramCodeUploaded=False, nandAccessed=False, flashReady=False,
                scope='CPU reply only; no firmware, unique device, NAND or boot-slot verification')


def query(lib, profile, result):
    """No retry, interface claim/detach, reset, bulk transfer or OUT request."""
    context, handle = C.c_void_p(), C.c_void_p()
    devices = C.POINTER(C.c_void_p)()
    initialized = listed = opened = False
    result['vendorRequestAttempted'] = False
    result['deviceOpened'] = False
    try:
        # libusb initialization itself may discover USB devices. Before a target
        # opens, do not claim that discovery errors prove no physical access.
        result['usbDiscoveryAttempted'] = True
        result['physicalDeviceAccessed'] = None
        code = lib.libusb_init(C.byref(context))
        check(code == 0, f'libusb initialization failed: {code}')
        initialized = True
        count = lib.libusb_get_device_list(context, C.byref(devices))
        check(count >= 0, f'USB enumeration failed: {count}')
        listed = True
        check(count <= 4096, 'Unexpected device count')
        matches = []
        for index in range(count):
            desc = Descriptor()
            code = lib.libusb_get_device_descriptor(devices[index], C.byref(desc))
            check(code == 0, f'Cannot establish unique target: descriptor error {code}')
            if (desc.vid, desc.pid) == (profile['vid'], profile['pid']):
                check(desc.length == 18 and desc.type == 1, 'Malformed device descriptor')
                matches.append(devices[index])
        result['matchingDevices'] = len(matches)
        check(len(matches) == 1, f'Expected exactly one ROM device; found {len(matches)}')
        device = matches[0]
        ports = (C.c_uint8 * 7)()
        count = lib.libusb_get_port_numbers(device, ports, len(ports))
        check(0 < count <= len(ports), 'Cannot record physical USB port path')
        result['connection'] = dict(bus=lib.libusb_get_bus_number(device),
                                    address=lib.libusb_get_device_address(device), ports=list(ports)[:count])
        code = lib.libusb_open(device, C.byref(handle))
        check(code == 0 and handle.value, f'Cannot open selected ROM device: {code}')
        opened = True
        result['deviceOpened'] = True
        result['physicalDeviceAccessed'] = True
        data = (C.c_uint8 * 8)()
        result['vendorRequestAttempted'] = True
        code = lib.libusb_control_transfer(handle, 0xc0, 0, 0, 0, data, 8, profile['timeout_ms'])
        result['transferResult'] = code
        result['replyHex'] = bytes(data[:max(0, min(code, 8))]).hex()
        match_reply(profile, code, result['replyHex'])
        result['cpuSignatureMatched'] = True
        result['status'] = 'cpu-signature-matched'
    finally:
        if opened:
            lib.libusb_close(handle)
        if listed:
            lib.libusb_free_device_list(devices, 1)
        if initialized:
            lib.libusb_exit(context)


def write_json(path, data):
    with path.open('x') as output:
        json.dump(data, output, indent=2)
        output.write('\n')


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def review_result(path, expected_sha256, firmware, profile):
    """Reassess retained bytes, not the device. Never rewrite the source report."""
    check(isinstance(expected_sha256, str) and re.fullmatch('[0-9a-f]{64}', expected_sha256),
          'Supply the independently retained result SHA-256')
    info = path.lstat()
    check(stat.S_ISREG(info.st_mode) and info.st_size <= 65536, 'Expected bounded regular result file')
    raw = path.read_bytes()
    check(hashlib.sha256(raw).hexdigest() == expected_sha256, 'Saved result fingerprint mismatch')
    saved = json.loads(raw)
    check(saved['schemaVersion'] == 1 and saved['operation'] == 'rom-cpu-info', 'Wrong result schema/operation')
    check(saved['requestedFirmwareVersion'] == firmware['version']
          and saved['firmwareProfileSha256'] == fingerprint(firmware), 'Saved firmware selection mismatch')
    check(saved['protocolSourceSha256'] == profile['reference_source_sha256']
          and saved['target'] == plan(firmware, profile)['target']
          and saved['transfer'] == request(profile), 'Saved protocol/target/request mismatch')
    check(type(saved['matchingDevices']) is int and saved['matchingDevices'] == 1
          and all(saved.get(k) is True for k in ('vendorRequestAttempted', 'deviceOpened', 'physicalDeviceAccessed')),
          'No completed single-device observation')
    check(all(saved.get(k) is False for k in ('ramCodeUploaded', 'nandAccessed', 'flashReady')),
          'Unexpected acquisition scope claims')
    check(saved['status'] in ('failed', 'cpu-reply-observed', 'cpu-signature-matched'), 'Incomplete observation')
    check(str(uuid.UUID(saved['sessionId'])) == saved['sessionId'], 'Invalid saved session')
    start = datetime.fromisoformat(saved['startedAt'])
    end = datetime.fromisoformat(saved['finishedAt'])
    check(start.utcoffset() is not None and end.utcoffset() is not None and end >= start, 'Invalid acquisition times')
    for field in ('probeSourceSha256', 'probeProfileSha256'):
        check(isinstance(saved[field], str) and re.fullmatch('[0-9a-f]{64}', saved[field]), 'Missing original provenance')
    reply = match_reply(profile, saved['transferResult'], saved['replyHex'])
    return dict(schemaVersion=1, status='saved-cpu-signature-matched',
                originalStatus=saved['status'], sourceResultSha256=expected_sha256,
                sourceSessionId=saved['sessionId'], sourceProbeProfileSha256=saved['probeProfileSha256'],
                reviewedProbeProfileSha256=fingerprint(profile), firmwareProfileSha256=fingerprint(firmware),
                replyHex=reply, cpuSignatureMatched=True, cpuIdentityVerified=False,
                freshnessAuthenticated=False, physicalDeviceAccessed=False,
                ramCodeUploaded=False, nandAccessed=False, flashReady=False,
                scope='Offline signature assessment only; original acquisition status is preserved')


def acquire(firmware, profile, library, output, loader=C.CDLL):
    # Exclusive output reservation and evidence preparation precede library load.
    output.mkdir(mode=0o700)
    record = dict(plan(firmware, profile), sessionId=str(uuid.uuid4()),
                  startedAt=datetime.now(timezone.utc).isoformat(),
                  probeSourceSha256=digest(Path(__file__)), status='incomplete',
                  cpuIdentityVerified=False, freshnessAuthenticated=False,
                  cpuSignatureMatched=False,
                  vendorRequestAttempted=False, usbDiscoveryAttempted=False, deviceOpened=False)
    write_json(output/'request.json', record)
    try:
        library = library.resolve(strict=True)
        check(stat.S_ISREG(library.stat().st_mode), 'libusb must be a regular library file')
        record['library'] = dict(path=str(library), sha256=digest(library))
        # Persist the selected dependency even if the process dies in native code.
        write_json(output/'dependency.json', record['library'])
        query(bind(loader(str(library))), profile, record)
    except (Exception, KeyboardInterrupt) as exc:
        record['status'] = 'failed'
        record['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        record['finishedAt'] = datetime.now(timezone.utc).isoformat()
        write_json(output/'result.json', record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'acquire', 'review'))
    parser.add_argument('--version', help='Reviewed profile; does not identify firmware on USB')
    parser.add_argument('--libusb', type=Path, help='Explicit trusted local libusb library; acquire only')
    parser.add_argument('--output', type=Path, help='Fresh private evidence directory; acquire/review only')
    parser.add_argument('--result', type=Path, help='Previously saved result; review only')
    parser.add_argument('--sha256', help='Independently retained result hash; review only')
    args = parser.parse_args()
    supplied = {k for k in ('libusb', 'output', 'result', 'sha256') if getattr(args, k) is not None}
    expected = dict(plan=set(), acquire={'libusb', 'output'}, review={'result', 'sha256', 'output'})
    if supplied != expected[args.action]:
        parser.error(f'{args.action} requires exactly these extra arguments: {sorted(expected[args.action])}')
    try:
        firmware = load_profile(args.version)
        profile = load_probe_profile(firmware)
        if args.action == 'plan':
            result = plan(firmware, profile)
        elif args.action == 'acquire':
            result = acquire(firmware, profile, args.libusb, args.output)
        else:
            result = review_result(args.result, args.sha256, firmware, profile)
            args.output.mkdir(mode=0o700)
            write_json(args.output/'review.json', result)
        print(json.dumps(result, indent=2))
        return int(result.get('status') == 'failed')
    except (OSError, ValueError, KeyError, TypeError, ProbeError) as exc:
        print(json.dumps(dict(status='rejected', error=str(exc), flashReady=False)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
