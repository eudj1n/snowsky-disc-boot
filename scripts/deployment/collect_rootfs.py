#!/usr/bin/env python3
"""Separately authorized, bounded rootfs collection; never writes NAND or retries."""
import argparse
from datetime import datetime, timezone
import ctypes as C
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deployment import ram_transport as ram
from deployment.collector_policy import REQUEST_BYTES, RESULT_BYTES, MAX_PAGES, DIGEST_RESULT_BYTES, DIGEST_RECORD_BYTES
from deployment.kernel_review import review_partitions
from deployment.nand_records import encode_request, decode_result, decode_digest

check, fingerprint = ram.check, ram.fingerprint


def result_bytes(scope):
    """The batch result's RAM: 128 bytes a page by digest (plan, stage 4b), 4428 in full."""
    return DIGEST_RESULT_BYTES if scope.get('mode') == 'rootfs-digest' else RESULT_BYTES


def buffers(inputs):
    p = inputs['collector_policy']['profile']
    return [('batch-request', p['request_address'], REQUEST_BYTES),
            ('batch-result', p['result_address'], result_bytes(inputs['collector_policy']))]


def chunks(address, data):
    for offset in range(0, len(data), 65536):
        yield address+offset, data[offset:offset+65536]


def make_plan(base, cpu, reader, config, inputs, metadata):
    page, scope = inputs['page_policy'], inputs['collector_policy']
    layout = review_partitions(metadata, page['kernel'], page['chip'], page['writer'])
    # Reuse the exact reviewed ROM bootstrap fields; this is a distinct plan.
    bootstrap = ram.plan(base, cpu, reader, config, {k:v for k,v in inputs.items()
                                                  if k not in ('page_policy', 'collector_policy')}, 'ram-check')
    ppb = page['chip']['pages_per_block']
    scan = (scope['end_page']-scope['first_page'])//ppb
    markers, data = 2*scan, scope['logical_blocks']*ppb
    batches = math.ceil(markers/MAX_PAGES)+math.ceil(data/MAX_PAGES)
    regions = ram.regions(reader)+buffers(inputs)
    digest = scope.get('mode') == 'rootfs-digest'
    # Three calls a 64 KiB chunk (the request and the result written and compared, the result
    # read) and two to run the batch: 53 for the full result, 17 for a digest batch.
    per_batch = 3*(2*math.ceil(REQUEST_BYTES/65536)+3*math.ceil(result_bytes(scope)/65536))+2
    bootstrap.update(operation=scope['mode'], collector_policy_sha256=fingerprint(scope),
        page_policy_sha256=fingerprint(page), metadata_sha256=layout['page_sha256'],
        first_page=scope['first_page'], end_page_exclusive=scope['end_page'],
        logical_blocks=scope['logical_blocks'], pages_per_block=ppb,
        marker_reads=markers, data_reads=data, batch_limit=batches, batch_pages=MAX_PAGES,
        capture_bytes=(markers+data)*(48+(DIGEST_RECORD_BYTES if digest else 4428)),
        logical_bytes=data*(32 if digest else page['main_bytes']),
        protocol_call_limit=12+12*sum(math.ceil(n/65536) for _,_,n in regions)+6+batches*per_batch,
        payload_entry=reader['load_address'], payload_bytes=len(inputs['payload']),
        ram_regions=[dict(name=n, address=a, bytes=s) for n,a,s in regions],
        nand_commands=['0x9f', '0x0f', '0x13', '0x0b'],
        steps=['SPL SRAM comparison; one SPL execution; CPU/DDR checks',
               'two pattern passes across all six RAM regions; upload/compare batch payload once',
               'paired first-page marker reads across the compiled range',
               'all pages of the selected good blocks; retain records and logical image; return to ROM'] if not digest else
              ['SPL SRAM comparison; one SPL execution; CPU/DDR checks',
               'two pattern passes across all six RAM regions; upload/compare the digest payload once',
               'paired first-page marker reads across the compiled range, by digest record',
               'all pages of the selected good blocks by digest; retain records and page digests; return to ROM'])
    bootstrap['timeout_ms'] = dict(bootstrap['timeout_ms'], session_budget_ms=scope['session_budget_ms'])
    bootstrap['transport_sources_sha256'].update({name:ram.probe.digest(ram.ROOT/name) for name in (
        'scripts/deployment/collect_rootfs.py', 'scripts/deployment/collector_policy.py')})
    return bootstrap


class Journal(ram.Journal):
    def __init__(self, output, limit):
        super().__init__(output)
        self.limit = limit

    def begin(self, kind, **fields):
        check(self.sequence < self.limit, 'Collector protocol budget exhausted')
        self.sequence += 1
        self.event(phase='attempt', sequence=self.sequence, kind=kind, **fields)
        return self.sequence


class Session(ram.Session):
    def execute_batch(self):
        check(self.record['batch_executions'] < self.record['plan']['batch_limit'], 'Batch budget exhausted')
        self.record['batch_executions'] += 1
        self.control(4, self.record['plan']['payload_entry'], 'page_execution_attempted')
        delay = self.config['settle_ms']/1000
        check(self.clock()+delay < self.deadline, 'Insufficient batch settle budget')
        self.sleep(delay)
        self.control(0)


def write_compare(session, address, data):
    for where, part in chunks(address, data): session.write(where, part)
    for where, part in chunks(address, data):
        check(session.read(where, len(part)) == part, 'Collector RAM comparison failed')


def bootstrap(session, inputs, nonce, record):
    p = session.config
    session.control(0)
    write_compare(session, p['spl_load_address'], inputs['spl'])
    session.execute(p['spl_entry'], 'spl_execution_attempted')
    diag = struct.unpack('<5I', session.read(p['diagnostic_address'], 20))
    check(diag == (0xd1a6c0de, 9, 0, 0, 0), 'SPL DDR diagnostic failed')
    record['ddr_diagnostic'] = list(diag)
    for turn in range(2):
        patterns = []
        for r in record['plan']['ram_regions']:
            data = hashlib.shake_256(nonce+struct.pack('<I', r['address'])).digest(r['bytes'])
            if turn: data = bytes(x ^ 255 for x in data)
            patterns.append((r['address'], data))
            for where, part in chunks(r['address'], data): session.write(where, part)
        for address, data in patterns:
            for where, part in chunks(address, data):
                check(session.read(where, len(part)) == part, 'Collector RAM pattern mismatch')
    record['ram_roundtrip_passed'] = True
    write_compare(session, record['plan']['payload_entry'], inputs['payload'])


def collect(session, reader, inputs, nonce, record):
    policy, scope = inputs['page_policy'], inputs['collector_policy']
    config, plan = scope['profile'], record['plan']
    digest_mode = scope.get('mode') == 'rootfs-digest'
    size, item_bytes = result_bytes(scope), (DIGEST_RECORD_BYTES if digest_mode else 4428)
    magic = 0x3244424e if digest_mode else 0x3152424e
    sequence, captured = 1, 0
    digest, image_digest = hashlib.sha256(), hashlib.sha256()
    histogram = [0]*16
    folder = session.journal.output
    logical = 'logical-digests.bin' if digest_mode else 'logical-image.bin'
    with (folder/'records.bin').open('xb') as stream, (folder/logical).open('xb') as image:
        def batch(pages):
            nonlocal sequence, captured
            check(0 < len(pages) <= MAX_PAGES and all(scope['first_page'] <= p < scope['end_page'] for p in pages),
                  'Page outside collector scope')
            requests = [encode_request(2, nonce, sequence+i, p) for i,p in enumerate(pages)]
            request = (struct.pack('<4I', 0x3151424e, 1, len(pages), 0)+b''.join(requests)).ljust(REQUEST_BYTES, b'\0')
            # Retain the exact intent before any USB call for this batch.
            with (folder/f'batch-{record["batch_executions"]+1:04}-request.bin').open('xb') as f:
                f.write(request); f.flush(); os.fsync(f.fileno())
            write_compare(session, config['request_address'], request)
            write_compare(session, config['result_address'], bytes(size))
            session.execute_batch()
            raw = b''.join(session.read(config['result_address']+offset, min(65536, size-offset))
                           for offset in range(0, size, 65536))
            check(struct.unpack_from('<4I', raw) == (magic, 1, len(pages), 0), 'Incomplete or failed batch')
            check(not any(raw[16+len(pages)*item_bytes:]), 'Nonzero unused batch results')
            decoded = []
            for i,q in enumerate(requests):
                result = raw[16+i*item_bytes:16+(i+1)*item_bytes]
                d = (decode_digest if digest_mode else decode_result)(result, q, policy['main_bytes']+policy['oob_bytes'])
                check(0 <= d['status'] <= 255 and 0 <= d['feature'] <= 255 and 0 <= d['protect'] <= 255
                      and not d['status'] & 1 and (d['feature'] & policy['feature_mask']) == policy['feature_value']
                      and 0 < d['observed_id'] < 0xffffff
                      and d['observed_id'] & policy['id_mask'] == policy['expected_id']
                      and 2 <= d['polls'] <= 2*reader['nand_polls'] and d['transfers'] == d['polls']+8,
                      'Untrusted batch page ID/configuration/counters')
                ecc = (d['status'] & policy['ecc_mask']) >> policy['ecc_shift']
                check(policy['ecc_admitted'] & (1 << ecc), 'Untrusted batch page ECC')
                # By digest the marker is the OOB's first byte and the page its main bytes' SHA-256.
                decoded.append((d['oob_head'][0], d['main_sha256']) if digest_mode else d['data']); histogram[ecc] += 1
            # Only fully validated batches enter the record stream. Failed raw
            # reads remain in the durable USB journal, including partial batches.
            for i,q in enumerate(requests):
                item = q+raw[16+i*item_bytes:16+(i+1)*item_bytes]
                stream.write(item); digest.update(item)
            stream.flush(); os.fsync(stream.fileno())
            sequence += len(pages); captured += len(pages)
            record['records_completed'] = captured
            return decoded

        ppb, main = plan['pages_per_block'], policy['main_bytes']
        marker = (lambda value: value[0]) if digest_mode else (lambda value: value[main])
        content = (lambda value: value[1]) if digest_mode else (lambda value: value[:main])
        marker_pages = [page for page in range(scope['first_page'], scope['end_page'], ppb) for _ in range(2)]
        good, bad = [], []
        for offset in range(0, len(marker_pages), MAX_PAGES):
            numbers = marker_pages[offset:offset+MAX_PAGES]
            values = batch(numbers)
            for i in range(0, len(numbers), 2):
                a, b = marker(values[i]), marker(values[i+1])
                check(a == b, f'Ambiguous marker at block {numbers[i]//ppb}')
                (good if a == 255 else bad).append(numbers[i]//ppb)
        check(len(good) >= scope['logical_blocks'], 'Insufficient good blocks')
        mapping = good[:scope['logical_blocks']]
        record.update(bad_blocks=bad, logical_to_physical=mapping)
        # Profile geometry currently uses 64 pages/block, matching batch capacity.
        pending = []
        def consume(pages):
            data = batch(pages)
            for number, value in zip(pages, data):
                check(number % ppb != 0 or marker(value) == 255, 'Marker changed during data pass')
                image.write(content(value)); image_digest.update(content(value))
            image.flush(); os.fsync(image.fileno())
        for block in mapping:
            for number in range(block*ppb, (block+1)*ppb):
                pending.append(number)
                if len(pending) == MAX_PAGES: consume(pending); pending = []
        if pending: consume(pending)
    check(captured == plan['marker_reads']+plan['data_reads'], 'Capture count mismatch')
    if digest_mode:
        record.update(status='rootfs-digest-collected', capture_sha256=digest.hexdigest(),
                      logical_digests_sha256=image_digest.hexdigest(), ecc_histogram=histogram,
                      image_match_verified=False, active_boot_verified=False)
        return
    record.update(status='rootfs-probe-collected' if scope['mode'] == 'rootfs-probe' else 'rootfs-collected',
                  capture_sha256=digest.hexdigest(), logical_image_sha256=image_digest.hexdigest(),
                  ecc_histogram=histogram, image_match_verified=False, active_boot_verified=False)


def acquire(plan, approved, base, cpu, reader, config, inputs, metadata, library, output,
            loader=C.CDLL, clock=time.monotonic, sleep=time.sleep):
    check(plan == make_plan(base, cpu, reader, config, inputs, metadata) and approved == fingerprint(plan),
          'Collector approval/inputs changed')
    output.mkdir(mode=0o700)
    nonce = uuid.uuid4().bytes
    record = dict(plan=plan, approved_plan_sha256=approved, session_id=str(uuid.uuid4()), nonce_hex=nonce.hex(),
                  started_at=datetime.now(timezone.utc).isoformat(), status='incomplete', batch_executions=0,
                  records_completed=0, physical_device_accessed=False, usb_discovery_attempted=False,
                  spl_execution_attempted=False, page_execution_attempted=False, ram_roundtrip_passed=False,
                  hardware_qualified=False, flash_ready=False)
    ram.save_json(output/'request.json', record)
    journal, session = None, None
    try:
        journal = Journal(output, plan['protocol_call_limit'])
        ram.read_file(library, 16*1024*1024)
        ram.save_json(output/'dependency.json', dict(path=str(library.resolve()), sha256=ram.probe.digest(library)))
        journal.event(phase='usb-discovery-attempt')
        limits = dict(config, session_budget_ms=inputs['collector_policy']['session_budget_ms'])
        session = Session(ram.bind(loader(str(library))), cpu, limits, record, journal, clock, sleep)
        session.open()
        bootstrap(session, inputs, nonce, record)
        collect(session, reader, inputs, nonce, record)
    except (Exception, KeyboardInterrupt) as exc:
        record.update(status='failed', error=f'{type(exc).__name__}: {exc}')
    finally:
        if session and session.close(): record['status'] = 'failed'
        if journal: journal.file.close()
        record['finished_at'] = datetime.now(timezone.utc).isoformat()
        ram.save_json(output/'result.json', record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'acquire'))
    parser.add_argument('--mode', choices=('rootfs-probe', 'rootfs', 'rootfs-digest'), required=True)
    parser.add_argument('--version')
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--diskos', type=Path, required=True)
    parser.add_argument('--metadata-page', type=Path, required=True)
    parser.add_argument('--libusb', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--approved-plan-sha256')
    args = parser.parse_args()
    extra = (args.libusb, args.output, args.approved_plan_sha256)
    if (args.action == 'plan' and any(x is not None for x in extra)) or (args.action == 'acquire' and not all(extra)):
        parser.error('acquire requires libusb, fresh output and approved plan hash; plan accepts none')
    try:
        base = ram.load_profile(args.version)
        cpu, reader = ram.load_probe_profile(base), ram.load_reader_profile(base)
        config = ram.load_transport(base, reader)
        inputs = ram.prepare_inputs(base, cpu, reader, config, args.build, args.diskos, args.mode)
        metadata = ram.read_file(args.metadata_page, inputs['page_policy']['main_bytes'])
        plan = make_plan(base, cpu, reader, config, inputs, metadata)
        result = dict(plan=plan, plan_sha256=fingerprint(plan)) if args.action == 'plan' else acquire(
            plan, args.approved_plan_sha256, base, cpu, reader, config, inputs, metadata, args.libusb, args.output)
        print(json.dumps(result, indent=2))
        return 1 if result.get('status') == 'failed' else 0
    except (Exception, KeyboardInterrupt) as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__': raise SystemExit(main())
