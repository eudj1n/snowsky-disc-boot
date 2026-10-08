#!/usr/bin/env python3
"""Independently reconstruct a saved boot-evidence USB session; never opens USB.

The boot layer's bounded reads of the uboot and OTA partitions (boot_evidence.py acquire), checked
from the saved files alone against the reviewed profiles, the build and the SPL: every record, the
blocks and pages they had to be, each batch's request and result, and the USB journal call by call
(usb_trace_audit.compare). The report (offline-review.json) is the session's audit that
installation_review.py takes. Apart from usb_trace_audit it shares no code with the acquisition:
the profiles are read as files and the page and boot policies rebuilt from them."""
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from usb_trace_audit import (CLEAN, RECORD, Calls, batch_request, check_plan, check_record, check_session,
                             cli, compare, digest as sha, fingerprint as fp, profiles, ram_regions, require, split)

ROOT = Path(__file__).resolve().parents[2]


def selector(data, boot):
    raw = data[:boot['selector_bytes']]
    for token, target in boot['selectors'].items():
        if raw == token.encode().ljust(len(raw), b' '):
            return dict(status='recognized-ota-selector', target=target, token=token, active_boot_verified=False)
    return dict(status='unknown-ota-selector', target=None, selector_sha256=sha(raw), active_boot_verified=False)


def audit(run, plan_file, build, diskos, root=ROOT):
    run = Path(run)
    r, p = check_session(run, plan_file, 'boot-evidence-collected', root)
    require(p['operation'] == 'boot-evidence' and p['writer_executions'] == 0, 'Not a read-only boot evidence plan')
    f = profiles(root, p['version'])
    reader, t, page, policy = f['reader'], f['transport'], f['page'], f['boot']
    boot, buffers = policy['profile'], policy['buffers']
    ask = check_plan(p, f)
    require(p['policy_sha256'] == fp(policy), 'Plan policy_sha256 differs from the reviewed profiles')
    main, oob, ppb = page['main_bytes'], page['oob_bytes'], page['chip']['pages_per_block']
    require(p['metadata'] == dict(page=page['page'], bytes=main+oob, policy_sha256=fp(page)), 'Metadata page differs')
    spans = [dict(name=x['name'], first_page=x['offset']//main, end_page=(x['offset']+x['size'])//main,
                  profile=buffers) for x in boot['partitions']]
    require(p['scopes'] == spans and [s['name'] for s in spans] == ['uboot', 'ota'], 'Evidence scopes differ')
    require(p['ram_regions'] == ram_regions(reader, buffers), 'RAM regions differ')
    load_address = reader['load_address']
    require(p['payload_entry'] == load_address, 'Payload entry differs')

    build = Path(build)
    require(sha((build/'build.json').read_bytes()) == p['build_sha256'], 'Build manifest differs')
    payloads = {n: (build/n/'identity.bin').read_bytes() for n in ('uboot', 'ota')}
    require({n: sha(v) for n, v in payloads.items()} == p['payload_sha256']
            and {n: len(v) for n, v in payloads.items()} == p['payload_bytes'], 'Boot payloads differ')
    spl = (Path(diskos)/'flash/disc_spl_lpddr3.bin').read_bytes()
    require(sha(spl) == p['spl_sha256'] and p['spl'] == dict(load=t['spl_load_address'], entry=t['spl_entry'],
            bytes=len(spl), diagnostic=t['diagnostic_address'], once_an_entry=True), 'SPL differs')

    nonce, raw = bytes.fromhex(r['nonce_hex']), (run/'records.bin').read_bytes()
    require(raw and len(raw) % RECORD == 0 and sha(raw) == r['capture_sha256'], 'Capture differs')
    records = [raw[i:i+RECORD] for i in range(0, len(raw), RECORD)]
    histogram, batches, cursor = [0]*16, [], 0

    def batch(name, numbers):
        nonlocal cursor
        require(cursor+len(numbers) <= len(records), 'Capture ends early')
        values = []
        for k, n in enumerate(numbers):
            data, ecc = check_record(records[cursor+k], cursor+k, n, nonce, page, reader)
            histogram[ecc] += 1
            values.append(data)
        batches.append((name, cursor, len(numbers)))
        cursor += len(numbers)
        return values

    # The pages the reads had to be: each block's first page twice, then (uboot) the metadata page
    # and every page of the good blocks, a batch a block.
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
    for index, (_, first, count) in enumerate(batches, 1):
        require(batch_request(records[first:first+count]) == (run/f'batch-{index:04}-request.bin').read_bytes(),
                f'Batch {index} request differs')

    entry = bytes.fromhex(r['entry_diagnostic'])
    skipped = entry == CLEAN
    require(r['spl_skipped'] == skipped and r['spl_execution_attempted'] == (not skipped)
            and r['ddr_diagnostic'] == list(struct.unpack('<5I', CLEAN)), 'DDR bring-up differs')
    # The ROM's CPU answer: the first call's, one of the reviewed ones; compare() checks every other.
    answer = (run/'read-001.bin').read_bytes()
    require(answer.hex() in f['probe']['accepted_reply_hex'], 'Unreviewed CPU answer')
    calls = Calls(answer)

    def expected():
        yield from calls.bring_up(t, entry, spl)
        yield from calls.patterns(nonce, p['ram_regions'])
        yield from calls.write_compare(load_address, payloads['uboot'])
        current = 'uboot'
        for name, first, count in batches:
            if name != current:
                yield from calls.write_compare(load_address, payloads[name])
                current = name
            yield from calls.batch(buffers, load_address, records[first:first+count], ask, t['settle_ms'])

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
    return cli(audit, __doc__)


if __name__ == '__main__':
    raise SystemExit(main())
