#!/usr/bin/env python3
"""Offline plan or separately authorized RAM-only observation; never flashes NAND."""
import argparse
import ctypes as C
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import struct
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from firmware_profile import PROFILES, fingerprint, load_profile, load_probe_profile, load_reader_profile
from deployment import rom_probe as probe
from deployment.build_identity import inspect_elf, source_paths
from deployment.nand_records import encode_request, decode_result
from deployment.metadata_policy import load_metadata_policy
from deployment.collector_policy import load_collector_policy
from deployment.kernel_review import review_partitions

check = probe.check
RESULT_BYTES = 4428


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save_json(path, data):
    with path.open('x') as output:
        json.dump(data, output, indent=2)
        output.write('\n')
        output.flush()
        os.fsync(output.fileno())


def read_file(path, maximum):
    info = path.lstat()
    check(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= maximum, f'Invalid bounded input: {path.name}')
    raw = path.read_bytes()
    check(len(raw) == info.st_size, f'Input changed during read: {path.name}')
    return raw


def load_transport(base, reader, directory=PROFILES):
    p = json.loads(read_file(directory/'transports'/f'v{base["version"]}.json', 8192))
    check(p.get('schema_version') == 1 and p.get('version') == base['version']
          and p.get('rootfs_sha256') == base['rootfs_sha256']
          and p.get('reader_profile_sha256') == fingerprint(reader), 'Transport profile mismatch')
    check(p.get('protocol') == 'ingenic-rom-ram-v1' and p.get('physical_qualified') is False,
          'Unsupported transport protocol/qualification')
    for key in ('spl_load_address', 'spl_entry', 'diagnostic_address'):
        check(type(p.get(key)) is int and 0xb2401000 <= p[key] < 0xb2408000 and p[key] % 16 == 0,
              f'Invalid SRAM address: {key}')
    check(type(p.get('spl_bytes')) is int and 2048 < p['spl_bytes'] <= 16384
          and p['spl_load_address'] + p['spl_bytes'] <= 0xb2408000, 'Invalid SPL size')
    check(p['spl_load_address'] == 0xb2401000  # Linked SPL layout, not a firmware version check.
          and p['spl_entry'] == p['spl_load_address'] + 0x800
          and p['diagnostic_address'] == p['spl_load_address'] + 0x7c0,
          'Unsupported SPL parameter/entry layout')
    for key, low, high in [('control_timeout_ms', 100, 5000), ('bulk_timeout_ms', 100, 5000),
                           ('execution_timeout_ms', 100, 10000), ('settle_ms', 100, 5000),
                           ('session_budget_ms', 10000, 120000)]:
        check(type(p.get(key)) is int and low <= p[key] <= high, f'Invalid transport {key}')
    return p


def load_completion(base, transport, directory=PROFILES):
    """The host's ask after an execution (plan, stage 4c): whether the ROM is asked instead of the
    fixed wait, and how long the staging check may run. Apart from the transport profile, which every recorded session's evidence pins byte for
    byte (an installation's history would no longer match it), and from the installer's RAM
    contract, which installations keep identical."""
    p = json.loads(read_file(directory/'completion'/f'v{base["version"]}.json', 8192))
    check(p.get('schema_version') == 1 and p.get('version') == base['version']
          and p.get('transport_profile_sha256') == fingerprint(transport), 'Completion profile mismatch')
    check(type(p.get('completion_ask')) is bool, 'Invalid completion completion_ask')
    for key, low, high in [('staging_check_ms', 1000, 600000), ('staging_sample_ms', 1000, 120000)]:
        check(type(p.get(key)) is int and low <= p[key] <= high, f'Invalid completion {key}')
    sample = p.get('staging_sample_bytes')
    check(type(sample) is int and 0 < sample <= 16*1024*1024 and sample % 65536 == 0, 'Invalid staging hash sample')
    # What the player took (2026-10-07), for an installation's progress only.
    expected = p.get('expected_ms', {})
    check(type(expected) is dict and set(expected) <= {'staging_check', 'staging_sample', 'writer'}
          and all(type(v) is int and 0 < v <= 1800000 for v in expected.values()), 'Invalid completion expected_ms')
    return p


def prepare_inputs(base, cpu, reader, transport, build, diskos, mode='identity'):
    raw = read_file(build/'build.json', 65536)
    m = json.loads(raw)
    check(m.get('schema_version') == 1 and m.get('version') == base['version']
          and m.get('firmware_profile_sha256') == fingerprint(base)
          and m.get('reader_profile_sha256') == fingerprint(reader)
          and m.get('reader_profile') == reader, 'Build profile mismatch')
    page_policy = load_metadata_policy(base, reader) if mode in ('metadata', 'rootfs-probe', 'rootfs', 'rootfs-digest') else None
    collector = load_collector_policy(base, reader, mode) if mode.startswith('rootfs') else None
    # The staging check (plan, stage 4c) is memory only: no page policy, no collector, no NAND opcode.
    staging = mode == 'staging-check'
    check(m.get('collector_policy') == collector, 'Build collector policy mismatch')
    check(m.get('purpose', 'identity') == (mode if page_policy or staging else 'identity')
          and m.get('page_policy') == page_policy, 'Build purpose/page policy mismatch')
    check(m.get('physical_qualified') is False and m.get('device_access_performed') is False
          and m.get('nand_opcodes') == ([] if staging else ['0x9f', '0x0f'] + (['0x13', '0x0b'] if page_policy else []))
          and m.get('entry') == reader['load_address'],
          'Wrong build scope/entry')
    check(set(m.get('source_sha256', {})) == set(source_paths()), 'Build source set changed')
    for name, digest in m['source_sha256'].items():
        check(probe.digest(ROOT/name) == digest, f'Build source changed: {name}; rebuild')
    files = {'identity.elf': 1048576, 'identity.bin': reader['code_bytes'],
             'identity_layout.h': 8192, 'identity.ld': 8192}
    check(set(m.get('artifacts', {})) == set(files), 'Unexpected build artifacts')
    artifacts = {name: read_file(build/name, limit) for name, limit in files.items()}
    for name, content in artifacts.items():
        check(sha(content) == m['artifacts'][name], f'Build artifact changed: {name}')
    payload = artifacts['identity.bin']
    check(type(m.get('bytes')) is int and m['bytes'] == len(payload), 'Build size mismatch')
    check(inspect_elf(artifacts['identity.elf'], reader) == payload, 'ELF/raw payload mismatch')
    for name, digest in reader['source_pins'].items():
        check(probe.digest(diskos/name) == digest, f'External input changed: {name}')
    usb = diskos/'src/usbboot/usbboot.c'
    check(probe.digest(usb) == cpu['reference_source_sha256'], 'USB reference changed')
    spl = read_file(diskos/'flash/disc_spl_lpddr3.bin', transport['spl_bytes'])
    check(len(spl) == transport['spl_bytes']
          and sha(spl) == reader['source_pins']['flash/disc_spl_lpddr3.bin'], 'SPL bytes changed')
    # Fresh diagnostics cannot accidentally be satisfied by values embedded in the SPL padding.
    check(spl[0x7c0:0x7d4] == bytes(20), 'SPL diagnostic padding is not initially zero')
    result = dict(spl=spl, payload=payload, build_sha256=sha(raw))
    if page_policy: result['page_policy'] = page_policy
    if collector: result['collector_policy'] = collector
    return result


def regions(reader):
    return [('code', reader['load_address'], reader['code_bytes']),
            ('stack', reader['stack_bottom'], reader['stack_top'] - reader['stack_bottom']),
            ('request', reader['request_address'], 48), ('result', reader['result_address'], RESULT_BYTES)]


def plan(base, cpu, reader, transport, inputs, mode):
    check(mode in ('ram-check', 'identity', 'metadata'), 'Select a concrete RAM observation mode')
    page_policy = inputs.get('page_policy')
    check((mode == 'metadata') == (page_policy is not None), 'Wrong payload for selected mode')
    return dict(schema_version=1, operation=mode, version=base['version'],
                firmware_profile_sha256=fingerprint(base), probe_profile_sha256=fingerprint(cpu),
                reader_profile_sha256=fingerprint(reader), transport_profile_sha256=fingerprint(transport),
                build_sha256=inputs['build_sha256'], spl_sha256=sha(inputs['spl']),
                transport_sources_sha256={name:probe.digest(ROOT/name) for name in (
                    'scripts/deployment/ram_transport.py', 'scripts/deployment/rom_probe.py',
                    'scripts/deployment/nand_records.py')},
                payload_sha256=sha(inputs['payload']), target={'vid':cpu['vid'], 'pid':cpu['pid'], 'matches':1},
                metadata=(dict(page=page_policy['page'], bytes=page_policy['main_bytes']+page_policy['oob_bytes'],
                               policy_sha256=fingerprint(page_policy)) if page_policy else None),
                interface=0, bulk_out=1, bulk_in=129, vendor_requests=[0, 1, 2, 4],
                spl={'load':transport['spl_load_address'], 'entry':transport['spl_entry'],
                     'bytes':len(inputs['spl']), 'diagnostic':transport['diagnostic_address'],
                     # Run unless this entry's SPL left its clean diagnostic (bring_up, plan, stage 4c).
                     'once_an_entry':True},
                ram_regions=[dict(name=n, address=a, bytes=s) for n, a, s in regions(reader)],
                pattern_passes=2, pattern='SHAKE256(nonce + region-address); second pass bitwise complement',
                payload_entry=reader['load_address'] if mode != 'ram-check' else None,
                payload_bytes=len(inputs['payload']) if mode != 'ram-check' else 0,
                timeout_ms={key:transport[key] for key in ('control_timeout_ms', 'bulk_timeout_ms',
                            'execution_timeout_ms', 'settle_ms', 'session_budget_ms')},
                steps=['claim interface 0; exact CPU signature', 'upload and compare SPL in SRAM',
                       'execute SPL once; wait; CPU signature and DDR diagnostics',
                       'write all RAM regions then compare all; repeat with complemented patterns'] +
                      ([f'upload/compare {mode} code, fresh request and incomplete result',
                        f'execute {mode} once; wait; CPU signature; read and validate result']
                       if mode != 'ram-check' else []),
                nand_commands=(['0x9f', '0x0f', '0x13', '0x0b'] if page_policy else
                               ['0x9f', '0x0f'] if mode == 'identity' else []),
                nand_writes=False, retries=0, reconnect=False, physical_device_accessed=False,
                hardware_qualified=False, flash_ready=False)


def bind(lib):
    probe.bind(lib)
    signatures = {'libusb_claim_interface': (C.c_int, [C.c_void_p, C.c_int]),
                  'libusb_release_interface': (C.c_int, [C.c_void_p, C.c_int]),
                  'libusb_bulk_transfer': (C.c_int, [C.c_void_p, C.c_uint8, C.POINTER(C.c_uint8),
                                                   C.c_int, C.POINTER(C.c_int), C.c_uint])}
    for name, (result, args) in signatures.items():
        fn = getattr(lib, name)
        fn.restype, fn.argtypes = result, args
    return lib


LIBUSB_ERROR_TIMEOUT = -7


class Journal:
    def __init__(self, output):
        self.output, self.sequence = output, 0
        self.file = (output/'transfers.jsonl').open('x')

    def event(self, **fields):
        self.file.write(json.dumps(fields)+'\n')
        self.file.flush()
        os.fsync(self.file.fileno())

    def begin(self, kind, **fields):
        self.sequence += 1
        check(self.sequence <= 256, 'Transfer budget exhausted')
        self.event(phase='attempt', sequence=self.sequence, kind=kind, **fields)
        return self.sequence

    def finish(self, sequence, code, raw=None, **fields):
        if raw is not None:
            name = f'read-{sequence:03}.bin'
            with (self.output/name).open('xb') as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            fields.update(file=name, sha256=sha(raw), bytes=len(raw))
        self.event(phase='return', sequence=sequence, code=code, **fields)


class Session:
    def __init__(self, lib, cpu, config, record, journal, clock=time.monotonic, sleep=time.sleep):
        self.lib, self.cpu, self.config, self.record, self.journal = lib, cpu, config, record, journal
        self.clock, self.sleep = clock, sleep
        self.deadline = clock() + config['session_budget_ms']/1000
        self.context, self.handle = C.c_void_p(), C.c_void_p()
        self.devices = C.POINTER(C.c_void_p)()
        self.initialized = self.listed = self.opened = self.claimed = False

    def timeout(self, key):
        remaining = math.floor((self.deadline - self.clock())*1000)
        check(remaining > 0, 'Session transfer deadline expired')
        return min(self.config[key], remaining)

    def open(self):
        self.record['usb_discovery_attempted'] = True
        self.record['physical_device_accessed'] = None
        check(self.lib.libusb_init(C.byref(self.context)) == 0, 'USB initialization failed')
        self.initialized = True
        count = self.lib.libusb_get_device_list(self.context, C.byref(self.devices))
        check(count >= 0, 'USB enumeration failed')
        self.listed = True
        check(count <= 4096, 'Unexpected device count')
        matches = []
        for i in range(count):
            d = probe.Descriptor()
            check(self.lib.libusb_get_device_descriptor(self.devices[i], C.byref(d)) == 0,
                  'Cannot establish unique target')
            if (d.vid, d.pid) == (self.cpu['vid'], self.cpu['pid']):
                check(d.length == 18 and d.type == 1, 'Malformed device descriptor')
                matches.append(self.devices[i])
        check(len(matches) == 1, f'Expected one ROM device; found {len(matches)}')
        device = matches[0]
        ports = (C.c_uint8 * 7)()
        count = self.lib.libusb_get_port_numbers(device, ports, len(ports))
        check(0 < count <= 7, 'Cannot record USB port path')
        self.record['connection'] = dict(bus=self.lib.libusb_get_bus_number(device),
            address=self.lib.libusb_get_device_address(device), ports=list(ports)[:count])
        self.timeout('control_timeout_ms')
        check(self.lib.libusb_open(device, C.byref(self.handle)) == 0 and self.handle.value, 'USB open failed')
        self.opened = True
        self.record['physical_device_accessed'] = True
        self.journal.event(phase='connection', **self.record['connection'])
        check(self.lib.libusb_claim_interface(self.handle, 0) == 0, 'Cannot claim interface 0; no detach attempted')
        self.claimed = True

    def close(self):
        errors = []
        if self.claimed:
            try:
                code = self.lib.libusb_release_interface(self.handle, 0)
                if code != 0: errors.append(f'Interface release failed: {code}')
            except Exception as exc: errors.append(str(exc))
        for enabled, fn, args in [(self.opened, self.lib.libusb_close, (self.handle,)),
                                 (self.listed, self.lib.libusb_free_device_list, (self.devices, 1)),
                                 (self.initialized, self.lib.libusb_exit, (self.context,))]:
            if enabled:
                try: fn(*args)
                except Exception as exc: errors.append(str(exc))
        self.record['cleanup_errors'] = errors
        return errors

    def control(self, request, parameter=0, execution_field=None):
        check(request in (0, 1, 2, 4) and type(parameter) is int and 0 <= parameter <= 0xffffffff,
              'Forbidden ROM control operation')
        timeout = self.timeout('execution_timeout_ms' if request == 4 else 'control_timeout_ms')
        incoming = request == 0
        check(not incoming or parameter == 0, 'Unexpected CPU parameter')
        data = (C.c_uint8 * 8)() if incoming else None
        seq = self.journal.begin('control', request=request, parameter=parameter, timeout_ms=timeout)
        if execution_field is not None: self.record[execution_field] = True
        code = self.lib.libusb_control_transfer(self.handle, 0xc0 if incoming else 0x40, request,
            parameter >> 16, parameter & 0xffff, data, 8 if incoming else 0, timeout)
        raw = bytes(data[:max(0, min(code, 8))]) if incoming else None
        self.journal.finish(seq, code, raw)
        self.timeout('control_timeout_ms')
        if incoming:
            probe.match_reply(self.cpu, code, raw.hex())
        else:
            check(code == 0, f'Control {request} failed: {code}; outcome uncertain, no retry')
        return raw

    def ask(self, timeout_ms, tolerant=False):
        """A CPU-info request while a payload may still run, its timeout the whole wait: the
        controller holds it and the ROM answers it once the payload has returned. Read-only. Returns
        True for the answer, None for a timeout (the whole wait went by), False for another error
        (tolerant only; otherwise it stops the session)."""
        remaining = math.floor((self.deadline - self.clock())*1000)
        check(remaining > 0, 'Session transfer deadline expired')
        timeout = min(timeout_ms, remaining)
        data = (C.c_uint8 * 8)()
        seq = self.journal.begin('control', request=0, parameter=0, timeout_ms=timeout, poll=True)
        code = self.lib.libusb_control_transfer(self.handle, 0xc0, 0, 0, 0, data, 8, timeout)
        if code == LIBUSB_ERROR_TIMEOUT or (tolerant and code < 0):
            self.journal.finish(seq, code)
            return None if code == LIBUSB_ERROR_TIMEOUT else False
        raw = bytes(data[:max(0, min(code, 8))])
        self.journal.finish(seq, code, raw)
        probe.match_reply(self.cpu, code, raw.hex())
        return True

    def wait_ready(self, limit_ms=None, ask=None, tolerant=False):
        """After an execution: one CPU-info request whose timeout is the whole wait (limit_ms,
        settle_ms by default), ended by the ROM's answer once the payload has returned, which also
        tells how long it ran. Never a request given up while the payload runs: on the player the
        ROM keeps every abandoned request and takes them after the payload, and the next request
        then fails (2026-10-07: 18 asks of 50 ms, then the address request timed out). tolerant
        (the writer's wait, where only the ROM's answer is an outcome): an ask that fails at once
        is followed by the rest of the wait and one more request. ask False: the fixed wait and one
        request, as before. Returns the milliseconds, or None."""
        ask = self.config.get('completion_ask', False) if ask is None else ask
        limit = self.config['settle_ms'] if limit_ms is None else limit_ms
        # The whole wait must fit the session, asked or not: asking only ends it earlier.
        check(self.clock() + limit/1000 < self.deadline, 'Insufficient settle budget')
        if not ask:
            self.sleep(limit/1000)
            self.control(0)
            return None
        started = self.clock()
        answer = self.ask(limit, tolerant)
        if answer:
            return round((self.clock() - started)*1000)
        rest = limit/1000 - (self.clock() - started)
        if answer is False and rest > 0.001:
            # Failed at once: the rest of the wait, then one request as the fixed wait made it.
            self.sleep(rest)
            if self.ask(self.config['control_timeout_ms'], tolerant):
                return round((self.clock() - started)*1000)
        check(False, 'The payload did not return within the wait; no retry')

    def bulk(self, length, outgoing=None):
        check(type(length) is int and 0 < length <= 65536, 'Invalid bounded RAM transfer')
        incoming = outgoing is None
        check(incoming or (isinstance(outgoing, bytes) and len(outgoing) == length), 'Wrong outgoing data size')
        data = (C.c_uint8 * length)() if incoming else (C.c_uint8 * length).from_buffer_copy(outgoing)
        count = C.c_int()
        timeout = self.timeout('bulk_timeout_ms')
        seq = self.journal.begin('bulk', endpoint=129 if incoming else 1, bytes=length, timeout_ms=timeout,
                                 outgoing_sha256=None if incoming else sha(outgoing))
        code = self.lib.libusb_bulk_transfer(self.handle, 129 if incoming else 1, data, length,
                                           C.byref(count), timeout)
        raw = bytes(data[:max(0, min(count.value, length))]) if incoming else None
        self.journal.finish(seq, code, raw, transferred=count.value)
        check(code == 0 and count.value == length,
              f'Bulk failed/short: code={code}, bytes={count.value}/{length}; no continuation')
        self.timeout('control_timeout_ms')
        return raw

    def check_range(self, address, length):
        check(type(address) is int and type(length) is int and 0 < length <= 65536,
              'Invalid RAM range')
        ranges = [(self.config['spl_load_address'], self.config['spl_bytes'])]
        ranges += [(p['address'], p['bytes']) for p in self.record['plan']['ram_regions']]
        check(any(start <= address and address + length <= start + size for start, size in ranges),
              'Transfer is outside the reviewed RAM regions')

    def write(self, address, data):
        check(isinstance(data, bytes), 'Expected immutable RAM bytes')
        self.check_range(address, len(data))
        self.control(1, address)
        self.control(2, len(data))
        self.bulk(len(data), data)

    def read(self, address, length):
        self.check_range(address, length)
        self.control(1, address)
        self.control(2, length)
        return self.bulk(length)

    def execute(self, address, field):
        expected = {'spl_execution_attempted': self.config['spl_entry'],
                    ('page_execution_attempted' if self.record['plan']['operation'] == 'metadata'
                     else 'identity_execution_attempted'): self.record['plan']['payload_entry']}
        check(address is not None and field in expected and address == expected[field],
              'Entry is outside the reviewed execution scope')
        self.control(4, address, field)  # Attempt is recorded immediately before the USB call.
        delay = self.config['settle_ms']/1000
        check(self.clock() + delay < self.deadline, 'Insufficient settle budget')
        self.sleep(delay)
        self.control(0)


CLEAN_DDR = struct.pack('<5I', 0xd1a6c0de, 9, 0, 0, 0)


def bring_up(session, spl, record, upload):
    """DDR up, with the SPL once a USB Boot entry (plan, stage 4c): the diagnostic the SPL leaves in
    TCSM is read first, and the clean one means this entry's SPL ran and DDR is up, so it is not run
    again (a second SPL re-runs the DDR bring-up, whose training failed in 3 of 6 re-runs, both after
    the writer). Otherwise (a fresh entry: what the power-on left there) the SPL is uploaded and
    compared (upload), run, and its diagnostic must be clean. The RAM passes that follow check DDR
    either way. Returns the diagnostic."""
    p = session.config
    session.control(0)
    found = session.read(p['diagnostic_address'], 20)
    record['entry_diagnostic'] = found.hex()
    record['spl_skipped'] = found == CLEAN_DDR
    # A write without a history belongs to the USB Boot entry of its evidence (plan, stage 6): that
    # entry's SPL ran already, so a fresh entry stops here, before any SPL.
    check(record['spl_skipped'] or not record.get('plan', {}).get('same_entry_required'),
          'This write belongs to the USB Boot entry its evidence was read in, and the player has left it: start again')
    if record['spl_skipped']:
        return found
    upload(p['spl_load_address'], spl)
    session.execute(p['spl_entry'], 'spl_execution_attempted')
    diag = session.read(p['diagnostic_address'], 20)
    check(diag == CLEAN_DDR, 'SPL did not report clean DDR completion')
    return diag


def observe(session, reader, inputs, mode, nonce, record):
    def upload(address, data):
        session.write(address, data)
        check(session.read(address, len(data)) == data, 'SPL SRAM readback mismatch')
    diag = struct.unpack('<5I', bring_up(session, inputs['spl'], record, upload))
    record['ddr_diagnostic'] = dict(zip(('magic', 'stage', 'first_failure', 'status', 'failures'), diag))
    for turn in range(2):
        patterns = []
        for name, address, length in regions(reader):
            data = hashlib.shake_256(nonce + struct.pack('<I', address)).digest(length)
            if turn: data = bytes(x ^ 255 for x in data)
            patterns.append((name, address, data))
            session.write(address, data)
        for name, address, data in patterns:
            check(session.read(address, len(data)) == data, f'RAM mismatch: {name}, pass {turn+1}')
    record['ram_roundtrip_passed'] = True
    if mode == 'ram-check':
        record['status'] = 'ram-roundtrip-observed'
        return
    policy = inputs.get('page_policy') if mode == 'metadata' else None
    request = encode_request(2 if policy else 1, nonce, 1, policy['page'] if policy else 0)
    with (session.journal.output/f'{mode}-request.bin').open('xb') as f:
        f.write(request)
        f.flush()
        os.fsync(f.fileno())
    for address, data in [(reader['load_address'], inputs['payload']),
                          (reader['request_address'], request), (reader['result_address'], bytes(RESULT_BYTES))]:
        session.write(address, data)
        check(session.read(address, len(data)) == data, 'Payload input readback mismatch')
    session.execute(reader['load_address'], 'page_execution_attempted' if policy else 'identity_execution_attempted')
    raw = session.read(reader['result_address'], RESULT_BYTES)
    decoded = decode_result(raw, request, policy['main_bytes']+policy['oob_bytes'] if policy else 0)
    if policy:
        check((decoded['observed_id'] & policy['id_mask']) == policy['expected_id']
              and 0 < decoded['observed_id'] < 0xffffff
              and 2 <= decoded['polls'] <= 2*reader['nand_polls']
              and decoded['transfers'] == decoded['polls']+8
              and all(0 <= decoded[k] <= 255 for k in ('protect', 'feature', 'status'))
              and not decoded['status'] & 1
              and (decoded['feature'] & policy['feature_mask']) == policy['feature_value']
              and policy['ecc_admitted'] & (1 << ((decoded['status'] & policy['ecc_mask']) >> policy['ecc_shift'])),
              'Untrusted metadata page identity/ECC/configuration/counters')
        data = decoded.pop('data')
        record['page_observation'] = decoded
        for name, part in [('metadata-main.bin', data[:policy['main_bytes']]),
                           ('metadata-oob.bin', data[policy['main_bytes']:])]:
            with (session.journal.output/name).open('xb') as f:
                f.write(part); f.flush(); os.fsync(f.fileno())
        record['metadata_layout'] = review_partitions(data[:policy['main_bytes']], policy['kernel'],
                                                    policy['chip'], policy['writer'])
        record['status'] = 'nand-metadata-observed'
        return
    check(0 < decoded['observed_id'] < 0xffffff and 1 <= decoded['polls'] <= reader['nand_polls']
          and decoded['transfers'] == decoded['polls'] + 4
          and all(0 <= decoded[k] <= 255 for k in ('protect', 'feature', 'status'))
          and not decoded['status'] & 1, 'Impossible identity observation')
    decoded.pop('data')
    record['identity'] = decoded
    record['status'] = 'nand-identity-observed'


def acquire(plan_data, approved, cpu, reader, config, inputs, library, output,
            loader=C.CDLL, clock=time.monotonic, sleep=time.sleep):
    check(approved == fingerprint(plan_data), 'Approved plan fingerprint mismatch')
    policy = inputs.get('page_policy')
    expected_metadata = (dict(page=policy['page'], bytes=policy['main_bytes']+policy['oob_bytes'],
                              policy_sha256=fingerprint(policy)) if policy else None)
    check((plan_data['operation'] == 'metadata') == (policy is not None)
          and plan_data.get('metadata') == expected_metadata, 'Metadata plan/policy mismatch')
    check(plan_data['operation'] in ('ram-check', 'identity', 'metadata')
          and plan_data['probe_profile_sha256'] == fingerprint(cpu)
          and plan_data['reader_profile_sha256'] == fingerprint(reader)
          and plan_data['transport_profile_sha256'] == fingerprint(config)
          and plan_data['build_sha256'] == inputs['build_sha256']
          and plan_data['spl_sha256'] == sha(inputs['spl'])
          and plan_data['payload_sha256'] == sha(inputs['payload']), 'Plan inputs changed')
    output.mkdir(mode=0o700)
    nonce = uuid.uuid4().bytes
    record = dict(plan=plan_data, approved_plan_sha256=approved, session_id=str(uuid.uuid4()),
                  nonce_hex=nonce.hex(), started_at=datetime.now(timezone.utc).isoformat(), status='incomplete',
                  physical_device_accessed=False, usb_discovery_attempted=False,
                  spl_execution_attempted=False, identity_execution_attempted=False,
                  page_execution_attempted=False,
                  ram_roundtrip_passed=False, hardware_qualified=False, flash_ready=False,
                  transport_source_sha256=probe.digest(Path(__file__)),
                  probe_source_sha256=probe.digest(Path(probe.__file__)))
    save_json(output/'request.json', record)
    journal, session = None, None
    try:
        journal = Journal(output)
        library = library.resolve(strict=True)
        check(stat.S_ISREG(library.stat().st_mode), 'libusb must be a regular file')
        save_json(output/'dependency.json', dict(path=str(library), sha256=probe.digest(library)))
        journal.event(phase='usb-discovery-attempt')
        session = Session(bind(loader(str(library))), cpu, config, record, journal, clock, sleep)
        session.open()
        observe(session, reader, inputs, plan_data['operation'], nonce, record)
    except (Exception, KeyboardInterrupt) as exc:
        record.update(status='failed', error=f'{type(exc).__name__}: {exc}')
    finally:
        if session and session.close(): record['status'] = 'failed'
        if journal: journal.file.close()
        record['finished_at'] = datetime.now(timezone.utc).isoformat()
        save_json(output/'result.json', record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'acquire'))
    parser.add_argument('--mode', choices=('ram-check', 'identity', 'metadata'), required=True)
    parser.add_argument('--version')
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--diskos', type=Path, required=True)
    parser.add_argument('--libusb', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--approved-plan-sha256')
    args = parser.parse_args()
    extras = (args.libusb, args.output, args.approved_plan_sha256)
    if (args.action == 'plan' and any(x is not None for x in extras)) or (
            args.action == 'acquire' and not all(x is not None for x in extras)):
        parser.error('acquire requires libusb, fresh output and approved-plan-sha256; plan accepts none of them')
    try:
        base = load_profile(args.version)
        cpu, reader = load_probe_profile(base), load_reader_profile(base)
        config = load_transport(base, reader)
        inputs = prepare_inputs(base, cpu, reader, config, args.build, args.diskos, args.mode)
        intent = plan(base, cpu, reader, config, inputs, args.mode)
        if args.action == 'plan':
            result = dict(plan=intent, plan_sha256=fingerprint(intent))
        else:
            result = acquire(intent, args.approved_plan_sha256, cpu, reader, config, inputs,
                             args.libusb, args.output)
        print(json.dumps(result, indent=2))
        return 1 if result.get('status') == 'failed' else 0
    except (Exception, KeyboardInterrupt) as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
