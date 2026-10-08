#!/usr/bin/env python3
"""Independently reconstruct a saved boot-evidence USB session; never opens USB.

The boot layer's bounded reads of the uboot and OTA partitions (boot_evidence.py acquire), checked
from the saved files alone against the reviewed profiles, the build and the SPL: every record, the
blocks and pages they had to be, each batch's request and result, and the USB journal call by call
(usb_trace_audit.compare). The report (offline-review.json) is the session's audit that
installation_review.py takes. Apart from the journal comparison it shares no code with the
acquisition: the profiles are read as files and the page and boot policies rebuilt from them."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from usb_trace_audit import compare, ready, require

ROOT = Path(__file__).resolve().parents[2]
# The batch ABI (device/acquisition): a 16-byte header, then 48 bytes a request and 4428 a result.
BATCH_PAGES, REQUEST, RESULT = 64, 48, 4428
REQUEST_BYTES, RESULT_BYTES = 16+REQUEST*BATCH_PAGES, 16+RESULT*BATCH_PAGES
CHUNK = 65536
CLEAN = struct.pack('<5I', 0xd1a6c0de, 9, 0, 0, 0)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fp(value):
    return sha(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())


def load(path):
    return json.loads(Path(path).read_text())


def profiles(root, version):
    """The reviewed profiles the plan names, and the page and boot policies rebuilt from them as
    boot_evidence.load_policy builds them."""
    d = root/'firmware'
    base = load(d/f'v{version}.json')
    named = {k: load(d/k/f'v{version}.json') for k in
             ('readers', 'transports', 'probes', 'completion', 'pages', 'kernels', 'boot', 'collectors')}
    reader, pages, kernel = named['readers'], named['pages'], named['kernels']
    chip, writer = load(d/'chips'/f'{kernel["chip"]}.json'), load(d/'writers'/f'{base["writer"]}.json')
    require(pages['reader_profile_sha256'] == fp(reader) and pages['kernel_profile_sha256'] == fp(kernel)
            and pages['chip_profile_sha256'] == fp(chip), 'Page profile links differ')
    ecc = chip['ecc']
    page = dict(profile=pages, kernel=kernel, chip=chip, writer=writer, page=pages['page'],
                expected_id=int.from_bytes(bytes.fromhex(chip['id_prefix_hex']), 'little'), id_mask=0xffff,
                main_bytes=chip['page_bytes'], oob_bytes=chip['oob_bytes'],
                total_pages=chip['blocks']*chip['pages_per_block'], feature_mask=pages['feature_mask'],
                feature_value=pages['feature_value'], ecc_mask=ecc['status_mask'], ecc_shift=ecc['status_shift'],
                ecc_admitted=(1 << (ecc['max_corrected']+1))-1)
    boot, buffers = named['boot'], named['collectors']
    require(boot['page_policy_sha256'] == fp(page) and buffers['policy_sha256'] == fp(page), 'Boot policy links differ')
    return dict(base=base, reader=reader, transport=named['transports'], probe=named['probes'],
                completion=named['completion'], page=page, policy=dict(profile=boot, page_policy=page, buffers=buffers))


def selector(data, boot):
    raw = data[:boot['selector_bytes']]
    for token, target in boot['selectors'].items():
        if raw == token.encode().ljust(len(raw), b' '):
            return dict(status='recognized-ota-selector', target=target, token=token, active_boot_verified=False)
    return dict(status='unknown-ota-selector', target=None, selector_sha256=sha(raw), active_boot_verified=False)


def audit(run, plan_file, build, diskos, root=ROOT):
    run = Path(run)
    r, q, approved = load(run/'result.json'), load(run/'request.json'), load(plan_file)
    p = r['plan']
    require(p == q['plan'] == approved['plan'] and fp(p) == approved['plan_sha256']
            == r['approved_plan_sha256'] == q['approved_plan_sha256'], 'Plan differs from the approved one')
    require(r['status'] == 'boot-evidence-collected' and r.get('error') is None and r['cleanup_errors'] == []
            and r['session_id'] == q['session_id'] and r['nonce_hex'] == q['nonce_hex'], 'Incomplete boot session')
    require(r['physical_device_accessed'] is True and r['ram_roundtrip_passed'] is True
            and r['page_execution_attempted'] is True and not any(r[k] for k in (
                'writer_execution_attempted', 'nand_writes', 'active_boot_verified', 'flash_ready')),
            'Boot session scope differs')
    require(p['operation'] == 'boot-evidence' and p['nand_writes'] is False and p['writer_executions'] == 0,
            'Not a read-only boot evidence plan')
    for name, digest in p['transport_sources_sha256'].items():
        require(sha((root/name).read_bytes()) == digest, f'Changed acquisition source: {name}')

    f = profiles(root, p['version'])
    reader, t, page, policy = f['reader'], f['transport'], f['page'], f['policy']
    boot, buffers = policy['profile'], policy['buffers']
    for key, value in (('firmware_profile_sha256', f['base']), ('probe_profile_sha256', f['probe']),
                       ('reader_profile_sha256', reader), ('transport_profile_sha256', t),
                       ('policy_sha256', policy)):
        require(p[key] == fp(value), f'Plan {key} differs from the reviewed profiles')
    main, oob, ppb = page['main_bytes'], page['oob_bytes'], page['chip']['pages_per_block']
    require(p['metadata'] == dict(page=page['page'], bytes=main+oob, policy_sha256=fp(page)), 'Metadata page differs')
    spans = [dict(name=x['name'], first_page=x['offset']//main, end_page=(x['offset']+x['size'])//main,
                  profile=buffers) for x in boot['partitions']]
    require(p['scopes'] == spans and [s['name'] for s in spans] == ['uboot', 'ota'], 'Evidence scopes differ')
    regions = [('code', reader['load_address'], reader['code_bytes']),
               ('stack', reader['stack_bottom'], reader['stack_top']-reader['stack_bottom']),
               ('request', reader['request_address'], REQUEST), ('result', reader['result_address'], RESULT),
               ('batch-request', buffers['request_address'], REQUEST_BYTES),
               ('batch-result', buffers['result_address'], RESULT_BYTES)]
    require(p['ram_regions'] == [dict(name=n, address=a, bytes=s) for n, a, s in regions], 'RAM regions differ')
    load_address = reader['load_address']
    require(p['payload_entry'] == load_address, 'Payload entry differs')
    ask = p.get('completion_ask', False)
    require(('completion_profile_sha256' not in p and not ask) or
            (p['completion_profile_sha256'] == fp(f['completion']) and ask == f['completion']['completion_ask']),
            'Completion profile differs')

    build = Path(build)
    require(sha((build/'build.json').read_bytes()) == p['build_sha256'], 'Build manifest differs')
    payloads = {n: (build/n/'identity.bin').read_bytes() for n in ('uboot', 'ota')}
    require({n: sha(v) for n, v in payloads.items()} == p['payload_sha256']
            and {n: len(v) for n, v in payloads.items()} == p['payload_bytes'], 'Boot payloads differ')
    spl = (Path(diskos)/'flash/disc_spl_lpddr3.bin').read_bytes()
    require(sha(spl) == p['spl_sha256'] and p['spl'] == dict(load=t['spl_load_address'], entry=t['spl_entry'],
            bytes=len(spl), diagnostic=t['diagnostic_address'], once_an_entry=True), 'SPL differs')

    nonce, raw = bytes.fromhex(r['nonce_hex']), (run/'records.bin').read_bytes()
    size = REQUEST+RESULT
    require(raw and len(raw) % size == 0 and sha(raw) == r['capture_sha256'], 'Capture differs')
    records = [raw[i:i+size] for i in range(0, len(raw), size)]
    histogram, batches, cursor = [0]*16, [], 0

    def framed(i, number):
        """Record i: the request for that page and the core's answer, checked as the reader admits
        it. Returns the page and its OOB."""
        req, b = records[i][:REQUEST], records[i][REQUEST:]
        require(req == struct.pack('<3I16s5I', 0x3151524e, 1, 2, nonce, i+1, number, 0, 0, 0),
                f'Record {i+1} request differs')
        w = struct.unpack_from('<19I', b)
        require(w[:4] == (0x3153524e, 1, 0x454e4f44, 0) and b[16:32] == nonce
                and w[8:12] == (i+1, 2, number, main+oob), f'Record {i+1} header differs')
        data = b[76:76+main+oob]
        require(w[12] == zlib.crc32(data) and not any(b[76+main+oob:]), f'Record {i+1} payload differs')
        require(0 < w[13] < 0xffffff and w[13] & page['id_mask'] == page['expected_id']
                and max(w[14:17]) <= 255 and not w[16] & 1
                and w[15] & page['feature_mask'] == page['feature_value']
                and 2 <= w[17] <= 2*reader['nand_polls'] and w[18] == w[17]+8, f'Record {i+1} NAND state differs')
        ecc = (w[16] & page['ecc_mask']) >> page['ecc_shift']
        require(page['ecc_admitted'] >> ecc & 1, f'Record {i+1} ECC is not admitted')
        histogram[ecc] += 1
        return data

    def batch(name, numbers):
        nonlocal cursor
        require(cursor+len(numbers) <= len(records), 'Capture ends early')
        values = [framed(cursor+k, n) for k, n in enumerate(numbers)]
        batches.append((name, cursor, len(numbers)))
        cursor += len(numbers)
        return values

    def split(numbers):
        return [numbers[k:k+BATCH_PAGES] for k in range(0, len(numbers), BATCH_PAGES)]

    # The pages the reads had to be: each block's first page twice, then (uboot) the metadata page
    # and every page of the good blocks.
    report = {}
    for scope in spans:
        name = scope['name']
        markers = [n for n in range(scope['first_page'], scope['end_page'], ppb) for _ in range(2)]
        data = [v for part in split(markers) for v in batch(name, part)]
        good, bad, first = [], [], {}
        for k in range(0, len(markers), 2):
            a, b, block = data[k], data[k+1], markers[k]//ppb
            require(a[main] == b[main], 'Ambiguous boot marker')
            if a[main] == 255:
                require(a[:main] == b[:main], 'Unstable first page')
                good.append(block)
                first[block] = a[:main]
            else:
                bad.append(block)
        require(good and r[name+'_good_blocks'] == good and r[name+'_bad_blocks'] == bad, f'{name} block map differs')
        report[name+'_bad_blocks'] = bad
        if name == 'uboot':
            require(page['page']//ppb in good, 'Metadata block is bad')
            metadata = batch(name, [page['page']])[0][:main]
            require(metadata == (run/'metadata-main.bin').read_bytes() and sha(metadata) == p['metadata_sha256'],
                    'Metadata differs')
            image = bytearray()
            for block in good:
                for part in split(list(range(block*ppb, (block+1)*ppb))):
                    for n, v in zip(part, batch(name, part)):
                        require(n % ppb or (v[main] == 255 and v[:main] == first[block]), 'Block changed during the read')
                        require(n != page['page'] or v[:main] == metadata, 'Metadata changed during the read')
                        image += v[:main]
            image = bytes(image)
            require(image == (run/'uboot-good-blocks.bin').read_bytes() and sha(image) == r['boot_image_sha256'],
                    'Boot image differs')
        else:
            selectors = {}
            for block, value in first.items():
                require((run/f'ota-block-{block}-main.bin').read_bytes() == value, 'OTA first page differs')
                selectors[str(block)] = selector(value, boot)
            require(r['ota_selectors'] == selectors and len(list(run.glob('ota-block-*-main.bin'))) == len(first),
                    'OTA selectors differ')
    require(cursor == len(records) == r['records_completed'] <= p['record_limit'], 'Capture has extra records')
    require(histogram == r['ecc_histogram'], 'ECC histogram differs')
    require(len(batches) == r['batch_executions'] <= p['batch_limit']
            and len(list(run.glob('batch-*-request.bin'))) == len(batches), 'Batches differ')

    entry = bytes.fromhex(r['entry_diagnostic'])
    skipped = entry == CLEAN
    require(r['spl_skipped'] == skipped and r['spl_execution_attempted'] == (not skipped)
            and r['ddr_diagnostic'] == list(struct.unpack('<5I', CLEAN)), 'DDR bring-up differs')
    # The ROM's CPU answer: the first call's, one of the reviewed ones; compare() checks every other.
    answer = (run/'read-001.bin').read_bytes()
    require(answer.hex() in f['probe']['accepted_reply_hex'], 'Unreviewed CPU answer')

    def ctrl(n, a=0):
        yield ('control', n, a, answer if n == 0 else None)

    def transfer(address, data, incoming):
        yield from ctrl(1, address)
        yield from ctrl(2, len(data))
        yield ('bulk', 129 if incoming else 1, len(data), data)

    def chunks(address, data):
        for off in range(0, len(data), CHUNK):
            yield address+off, data[off:off+CHUNK]

    def write_compare(address, data):
        for incoming in (False, True):
            for where, part in chunks(address, data):
                yield from transfer(where, part, incoming)

    def expected():
        # DDR's diagnostic first: this entry's clean one means no SPL again (ram_transport.bring_up).
        yield from ctrl(0)
        yield from transfer(t['diagnostic_address'], entry, True)
        if not skipped:
            yield from write_compare(t['spl_load_address'], spl)
            yield from ctrl(4, t['spl_entry'])
            yield from ctrl(0)
            yield from transfer(t['diagnostic_address'], CLEAN, True)
        for turn in range(2):
            for incoming in (False, True):
                for region in p['ram_regions']:
                    data = hashlib.shake_256(nonce+struct.pack('<I', region['address'])).digest(region['bytes'])
                    if turn:
                        data = bytes(v ^ 255 for v in data)
                    for where, part in chunks(region['address'], data):
                        yield from transfer(where, part, incoming)
        yield from write_compare(load_address, payloads['uboot'])
        current = 'uboot'
        for index, (name, first, count) in enumerate(batches, 1):
            if name != current:
                yield from write_compare(load_address, payloads[name])
                current = name
            own = records[first:first+count]
            request = (struct.pack('<4I', 0x3151424e, 1, count, 0)+b''.join(x[:REQUEST] for x in own)).ljust(REQUEST_BYTES, b'\0')
            require(request == (run/f'batch-{index:04}-request.bin').read_bytes(), f'Batch {index} request differs')
            result = (struct.pack('<4I', 0x3152424e, 1, count, 0)+b''.join(x[REQUEST:] for x in own)).ljust(RESULT_BYTES, b'\0')
            yield from write_compare(buffers['request_address'], request)
            yield from write_compare(buffers['result_address'], bytes(RESULT_BYTES))
            yield from ctrl(4, load_address)
            # The one CPU request asked at once and held when the plan asks, else after the settle.
            if ask:
                yield ready(t['settle_ms'], answer)
            else:
                yield from ctrl(0)
            for where, part in chunks(buffers['result_address'], result):
                yield from transfer(where, part, True)

    rows = [json.loads(line) for line in (run/'transfers.jsonl').read_text().splitlines()]
    trace = compare(rows, expected(), run, t, p['protocol_call_limit'],
                    ([] if skipped else [t['spl_entry']])+[load_address]*len(batches), r['connection'])
    return dict(status='saved-boot-trace-matches', session_id=r['session_id'], calls=trace['calls'],
                raw_reads=trace['raw_reads'], raw_bytes=trace['read_bytes'], bulk_ram_writes=trace['bulk_writes'],
                spl_executions=0 if skipped else 1, batch_executions=len(batches), records=len(records),
                **report, ecc_histogram=histogram, ota_selectors=r['ota_selectors'], plan_sha256=fp(p),
                result_sha256=sha((run/'result.json').read_bytes()),
                journal_sha256=sha((run/'transfers.jsonl').read_bytes()), capture_sha256=sha(raw),
                metadata_sha256=sha(metadata), boot_image_sha256=sha(image),
                audit_script_sha256=sha(Path(__file__).read_bytes()),
                new_device_access=False, nand_writes=False, active_boot_verified=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
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


if __name__ == '__main__':
    raise SystemExit(main())
