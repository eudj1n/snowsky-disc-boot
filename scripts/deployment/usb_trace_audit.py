"""Offline USB journal comparison and reconstruction shared by the independent audits.

Nothing here imports the acquisition: the profiles are read as files, the records and the calls
are rebuilt from the formats themselves."""
import argparse
import hashlib
import json
from pathlib import Path
import stat
import sys
import struct
import zlib

LIBUSB_ERROR_TIMEOUT = -7
# The batch ABI (device/usbboot): a 16-byte header, then 48 bytes a request and 4428 a result.
BATCH_PAGES, REQUEST, RESULT = 64, 48, 4428
REQUEST_BYTES, RESULT_BYTES = 16+REQUEST*BATCH_PAGES, 16+RESULT*BATCH_PAGES
RECORD = REQUEST+RESULT
CHUNK = 65536
CLEAN = struct.pack('<5I', 0xd1a6c0de, 9, 0, 0, 0)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fingerprint(value):
    return digest(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())


def load(path):
    return json.loads(Path(path).read_text())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def ready(limit_ms, answer, tolerant=False):
    """The expected ask after an execution (plan, stage 4c): one CPU-info request, its timeout at most
    the whole wait, answered. tolerant (the writer's wait): one that failed at once may be followed,
    after the rest of the wait, by one more, answered."""
    return ('ready', limit_ms, answer, tolerant)


def compare(rows, expected, run: Path, transport, call_limit, executions, connection):
    require(rows[:2] == [{'phase': 'usb-discovery-attempt'},
                         {'phase': 'connection', **connection}], 'Discovery/connection differs')
    require((len(rows) - 2) % 2 == 0, 'Unpaired USB journal row')
    pairs = iter(zip(rows[2::2], rows[3::2]))
    counts = dict(calls=0, reads=0, read_bytes=0, writes=0)
    actual_executions = []

    def take(kind):
        counts['calls'] += 1
        calls = counts['calls']
        require(calls <= call_limit, 'USB call budget exceeded')
        pair = next(pairs, None)
        require(pair is not None, f'Missing USB call {calls}')
        attempt, reply = pair
        require(attempt.get('sequence') == reply.get('sequence') == calls and
                attempt.get('phase') == 'attempt' and reply.get('phase') == 'return' and
                attempt.get('kind') == kind, f'USB call {calls} order/kind differs')
        return calls, attempt, reply

    def saved_read(calls, reply, data):
        name = reply.get('file')
        require(name == f'read-{calls:03}.bin' and reply.get('bytes') == len(data),
                f'Saved read {calls} name/length differs')
        saved = run / name
        require(stat.S_ISREG(saved.lstat().st_mode), f'Saved read {calls} is not regular')
        actual = saved.read_bytes()
        require(actual == data and digest(actual) == reply.get('sha256'),
                f'Saved read {calls} bytes differ')
        counts['reads'] += 1
        counts['read_bytes'] += len(actual)

    for item in expected:
        if item[0] == 'ready':
            _, limit, answer, tolerant = item
            for turn in range(2 if tolerant else 1):
                calls, attempt, reply = take('control')
                require(attempt.get('request') == 0 and attempt.get('parameter') == 0 and attempt.get('poll') is True
                        and 0 < attempt.get('timeout_ms', 0) <= limit, f'Completion ask {calls} differs')
                if tolerant and turn == 0 and reply.get('code', 0) < 0 and reply.get('code') != LIBUSB_ERROR_TIMEOUT:
                    require('file' not in reply, f'Unsolicited read file at call {calls}')
                    continue
                require(reply.get('code') == len(answer), f'Completion ask {calls} answer differs')
                saved_read(calls, reply, answer)
                break
            else:
                raise ValueError('The ROM did not answer within the wait')
            continue
        kind, number, parameter, data = item
        calls, attempt, reply = take(kind)
        require('poll' not in attempt, f'Unexpected completion poll at call {calls}')
        if kind == 'control':
            require(attempt.get('request') == number and attempt.get('parameter') == parameter and
                    reply.get('code') == (5 if number == 0 else 0),
                    f'Control call {calls} differs')
            limit = transport['execution_timeout_ms' if number == 4 else 'control_timeout_ms']
            incoming = number == 0
            if number == 4:
                actual_executions.append(parameter)
        elif kind == 'bulk':
            require(attempt.get('endpoint') == number and attempt.get('bytes') == parameter and
                    reply.get('code') == 0 and reply.get('transferred') == parameter,
                    f'Bulk call {calls} differs')
            limit = transport['bulk_timeout_ms']
            incoming = number == 129
            require(attempt.get('outgoing_sha256') == (None if incoming else digest(data)),
                    f'Bulk source {calls} differs')
            if not incoming:
                counts['writes'] += 1
        else:
            raise ValueError(f'Unexpected USB kind {kind}')
        require(0 < attempt.get('timeout_ms', 0) <= limit,
                f'USB call {calls} timeout differs')
        if incoming:
            saved_read(calls, reply, data)
        else:
            require('file' not in reply, f'Unsolicited read file at call {calls}')
    require(next(pairs, None) is None and counts['calls'] > 0, 'Extra or empty USB journal')
    require(actual_executions == executions, 'Executed unexpected RAM entry')
    require(len(list(run.glob('read-*.bin'))) == counts['reads'], 'Unaccounted saved USB reads')
    return dict(calls=counts['calls'], raw_reads=counts['reads'], read_bytes=counts['read_bytes'],
                bulk_writes=counts['writes'], executions=actual_executions)


def profiles(root, version):
    """The reviewed profiles of a firmware version, read as files, and the page and boot policies
    rebuilt from them as the acquisition's loaders build them (metadata_policy, boot_evidence)."""
    d = Path(root)/'firmware'
    base = load(d/f'v{version}.json')
    named = {k: load(d/k/f'v{version}.json') for k in
             ('readers', 'transports', 'probes', 'completion', 'pages', 'kernels', 'boot', 'collectors')}
    reader, pages, kernel = named['readers'], named['pages'], named['kernels']
    chip, writer = load(d/'chips'/f'{kernel["chip"]}.json'), load(d/'writers'/f'{base["writer"]}.json')
    require(pages['reader_profile_sha256'] == fingerprint(reader) and pages['kernel_profile_sha256'] == fingerprint(kernel)
            and pages['chip_profile_sha256'] == fingerprint(chip), 'Page profile links differ')
    ecc = chip['ecc']
    page = dict(profile=pages, kernel=kernel, chip=chip, writer=writer, page=pages['page'],
                expected_id=int.from_bytes(bytes.fromhex(chip['id_prefix_hex']), 'little'), id_mask=0xffff,
                main_bytes=chip['page_bytes'], oob_bytes=chip['oob_bytes'],
                total_pages=chip['blocks']*chip['pages_per_block'], feature_mask=pages['feature_mask'],
                feature_value=pages['feature_value'], ecc_mask=ecc['status_mask'], ecc_shift=ecc['status_shift'],
                ecc_admitted=(1 << (ecc['max_corrected']+1))-1)
    boot, collector = named['boot'], named['collectors']
    require(boot['page_policy_sha256'] == fingerprint(page) and collector['policy_sha256'] == fingerprint(page),
            'Boot/collector policy links differ')
    return dict(base=base, reader=reader, transport=named['transports'], probe=named['probes'],
                completion=named['completion'], page=page, collector=collector,
                boot=dict(profile=boot, page_policy=page, buffers=collector))


def ram_regions(reader, buffers):
    """A batch session's RAM: the reader's four regions and the collector's batch buffers."""
    regions = [('code', reader['load_address'], reader['code_bytes']),
               ('stack', reader['stack_bottom'], reader['stack_top']-reader['stack_bottom']),
               ('request', reader['request_address'], REQUEST), ('result', reader['result_address'], RESULT),
               ('batch-request', buffers['request_address'], REQUEST_BYTES),
               ('batch-result', buffers['result_address'], RESULT_BYTES)]
    return [dict(name=n, address=a, bytes=s) for n, a, s in regions]


def check_plan(p, f):
    """The plan's profile fingerprints against the files."""
    pairs = [('firmware_profile_sha256', f['base']), ('probe_profile_sha256', f['probe']),
             ('reader_profile_sha256', f['reader']), ('transport_profile_sha256', f['transport'])]
    for key, value in pairs:
        require(p[key] == fingerprint(value), f'Plan {key} differs from the reviewed profiles')
    ask = p.get('completion_ask', False)
    require(('completion_profile_sha256' not in p and not ask) or
            (p['completion_profile_sha256'] == fingerprint(f['completion']) and ask == f['completion']['completion_ask']),
            'Completion profile differs')
    return ask


def check_session(run, plan_file, status, root):
    """A saved session against its approved plan, and the acquisition's sources it pinned."""
    r, q, approved = load(Path(run)/'result.json'), load(Path(run)/'request.json'), load(plan_file)
    p = r['plan']
    require(p == q['plan'] == approved['plan'] and fingerprint(p) == approved['plan_sha256']
            == r['approved_plan_sha256'] == q['approved_plan_sha256'], 'Plan differs from the approved one')
    require(r['status'] == status and r.get('error') is None and r['cleanup_errors'] == []
            and r['session_id'] == q['session_id'] and r['nonce_hex'] == q['nonce_hex'], 'Incomplete session')
    require(r['physical_device_accessed'] is True and r['ram_roundtrip_passed'] is True
            and r['page_execution_attempted'] is True and p['nand_writes'] is False
            and not any(r.get(k) for k in ('writer_execution_attempted', 'nand_writes', 'active_boot_verified', 'flash_ready')),
            'Session scope differs')
    for name, value in p['transport_sources_sha256'].items():
        require(digest((Path(root)/name).read_bytes()) == value, f'Changed acquisition source: {name}')
    return r, p


def check_record(record, index, number, nonce, page, reader):
    """Record index (from 0): the request for that page and the core's answer, checked as the
    reader admits it. Returns the page with its OOB, and its ECC state."""
    req, b = record[:REQUEST], record[REQUEST:]
    size = page['main_bytes']+page['oob_bytes']
    require(req == struct.pack('<3I16s5I', 0x3151524e, 1, 2, nonce, index+1, number, 0, 0, 0),
            f'Record {index+1} request differs')
    w = struct.unpack_from('<19I', b)
    require(w[:4] == (0x3153524e, 1, 0x454e4f44, 0) and b[16:32] == nonce
            and w[8:12] == (index+1, 2, number, size), f'Record {index+1} header differs')
    data = b[76:76+size]
    require(w[12] == zlib.crc32(data) and not any(b[76+size:]), f'Record {index+1} payload differs')
    require(0 < w[13] < 0xffffff and w[13] & page['id_mask'] == page['expected_id']
            and max(w[14:17]) <= 255 and not w[16] & 1
            and w[15] & page['feature_mask'] == page['feature_value']
            and 2 <= w[17] <= 2*reader['nand_polls'] and w[18] == w[17]+8, f'Record {index+1} NAND state differs')
    ecc = (w[16] & page['ecc_mask']) >> page['ecc_shift']
    require(page['ecc_admitted'] >> ecc & 1, f'Record {index+1} ECC is not admitted')
    return data, ecc


def split(numbers):
    return [numbers[k:k+BATCH_PAGES] for k in range(0, len(numbers), BATCH_PAGES)]


class Calls:
    """The calls a reviewed RAM session makes, as audit items: answer is the ROM's CPU answer."""

    def __init__(self, answer):
        self.answer = answer

    def ctrl(self, n, a=0):
        yield ('control', n, a, self.answer if n == 0 else None)

    def transfer(self, address, data, incoming):
        yield from self.ctrl(1, address)
        yield from self.ctrl(2, len(data))
        yield ('bulk', 129 if incoming else 1, len(data), data)

    @staticmethod
    def chunks(address, data):
        for off in range(0, len(data), CHUNK):
            yield address+off, data[off:off+CHUNK]

    def write_compare(self, address, data):
        for incoming in (False, True):
            for where, part in self.chunks(address, data):
                yield from self.transfer(where, part, incoming)

    def bring_up(self, transport, entry, spl):
        """DDR's diagnostic first: this entry's clean one means no SPL again (ram_transport.bring_up)."""
        yield from self.ctrl(0)
        yield from self.transfer(transport['diagnostic_address'], entry, True)
        if entry != CLEAN:
            yield from self.write_compare(transport['spl_load_address'], spl)
            yield from self.ctrl(4, transport['spl_entry'])
            yield from self.ctrl(0)
            yield from self.transfer(transport['diagnostic_address'], CLEAN, True)

    def patterns(self, nonce, regions):
        """Two passes over every region, each written in full before it is compared."""
        for turn in range(2):
            for incoming in (False, True):
                for region in regions:
                    data = hashlib.shake_256(nonce+struct.pack('<I', region['address'])).digest(region['bytes'])
                    if turn:
                        data = bytes(v ^ 255 for v in data)
                    for where, part in self.chunks(region['address'], data):
                        yield from self.transfer(where, part, incoming)

    def batch(self, buffers, entry, records, ask, settle_ms):
        """One batch: its request and a zero result written and compared, the payload run, the ROM
        asked at once and held when the plan asks (else after the settle), the result read."""
        result = (struct.pack('<4I', 0x3152424e, 1, len(records), 0)+b''.join(x[REQUEST:] for x in records)).ljust(RESULT_BYTES, b'\0')
        yield from self.write_compare(buffers['request_address'], batch_request(records))
        yield from self.write_compare(buffers['result_address'], bytes(RESULT_BYTES))
        yield from self.ctrl(4, entry)
        if ask:
            yield ready(settle_ms, self.answer)
        else:
            yield from self.ctrl(0)
        for where, part in self.chunks(buffers['result_address'], result):
            yield from self.transfer(where, part, True)


def batch_request(records):
    """A batch's request as the host wrote it (batch-NNNN-request.bin), from its saved records."""
    count = len(records)
    return (struct.pack('<4I', 0x3151424e, 1, count, 0)+b''.join(x[:REQUEST] for x in records)).ljust(REQUEST_BYTES, b'\0')


def cli(audit, description):
    """An audit's command line: the session, its approved plan, the build and diskOS's files; the
    report written once to --output."""
    parser = argparse.ArgumentParser(description=description)
    for name in ('run', 'plan', 'build', 'diskos', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    try:
        report = audit(args.run, args.plan, args.build, args.diskos)
        with args.output.open('x') as stream:
            json.dump(report, stream, indent=2)
            stream.write('\n')
    except (ValueError, KeyError, OSError) as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0
