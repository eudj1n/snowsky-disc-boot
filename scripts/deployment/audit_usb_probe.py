#!/usr/bin/env python3
"""Independently reconstruct a saved identity probe's USB session; never opens USB.

The probe (collect_rootfs.py acquire --mode rootfs-probe) reads the first blocks of the primary
rootfs, which tell the image a player holds (plan, stage 6: known by their digest). Checked from the
saved files alone against the reviewed profiles, the build and the SPL: every record, the marker and
data pages they had to be, the first blocks they make, each batch and the USB journal call by call
(usb_trace_audit.compare). The report (offline-review.json) gives the first blocks' digest the
installation review compares with the images known. Apart from usb_trace_audit it shares no code
with the acquisition."""
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from usb_trace_audit import (CLEAN, RECORD, Calls, batch_request, check_plan, check_record, check_session,
                             cli, compare, digest as sha, fingerprint as fp, profiles, ram_regions, require, split)

ROOT = Path(__file__).resolve().parents[2]


def audit(run, plan_file, build, diskos, root=ROOT):
    run = Path(run)
    r, p = check_session(run, plan_file, 'rootfs-probe-collected', root)
    require(p['operation'] == 'rootfs-probe', 'Not an identity probe plan')
    f = profiles(root, p['version'])
    reader, t, page, collector = f['reader'], f['transport'], f['page'], f['collector']
    ask = check_plan(p, f)
    main, ppb, writer = page['main_bytes'], page['chip']['pages_per_block'], page['writer']
    blocks, start = collector['probe_blocks'], writer['start_block']
    scope = dict(profile=collector, mode='rootfs-probe', first_page=start*ppb, end_page=(start+blocks)*ppb,
                 logical_blocks=blocks, session_budget_ms=collector['probe_budget_ms'])
    require(p['collector_policy_sha256'] == fp(scope) and p['page_policy_sha256'] == fp(page)
            and (p['first_page'], p['end_page_exclusive'], p['logical_blocks'], p['pages_per_block'])
            == (scope['first_page'], scope['end_page'], blocks, ppb), 'Probe scope differs')
    require(p['ram_regions'] == ram_regions(reader, collector), 'RAM regions differ')
    load_address = reader['load_address']
    require(p['payload_entry'] == load_address, 'Payload entry differs')

    build = Path(build)
    payload = (build/'identity.bin').read_bytes()
    require(sha((build/'build.json').read_bytes()) == p['build_sha256'] and sha(payload) == p['payload_sha256']
            and len(payload) == p['payload_bytes'], 'Probe build differs')
    spl = (Path(diskos)/'flash/disc_spl_lpddr3.bin').read_bytes()
    require(sha(spl) == p['spl_sha256'] and p['spl'] == dict(load=t['spl_load_address'], entry=t['spl_entry'],
            bytes=len(spl), diagnostic=t['diagnostic_address'], once_an_entry=True), 'SPL differs')

    nonce, raw = bytes.fromhex(r['nonce_hex']), (run/'records.bin').read_bytes()
    require(len(raw) == p['capture_bytes'] == (p['marker_reads']+p['data_reads'])*RECORD
            and sha(raw) == r['capture_sha256'], 'Capture differs')
    records = [raw[i:i+RECORD] for i in range(0, len(raw), RECORD)]
    histogram, batches, cursor = [0]*16, [], 0

    def batch(numbers):
        nonlocal cursor
        require(cursor+len(numbers) <= len(records), 'Capture ends early')
        values = []
        for k, n in enumerate(numbers):
            data, ecc = check_record(records[cursor+k], cursor+k, n, nonce, page, reader)
            histogram[ecc] += 1
            values.append(data)
        batches.append((cursor, len(numbers)))
        cursor += len(numbers)
        return values

    # Each block's first page twice, then every page of the first good blocks, batched apart.
    markers = [n for n in range(scope['first_page'], scope['end_page'], ppb) for _ in range(2)]
    require(len(markers) == p['marker_reads'], 'Marker reads differ')
    data = [v for part in split(markers) for v in batch(part)]
    good, bad = [], []
    for k in range(0, len(markers), 2):
        a, b = data[k][main], data[k+1][main]
        require(a == b, 'Ambiguous marker')
        (good if a == 255 else bad).append(markers[k]//ppb)
    mapping = good[:blocks]
    require(len(mapping) == blocks and r['bad_blocks'] == bad and r['logical_to_physical'] == mapping,
            'Block map differs')
    pages = [n for block in mapping for n in range(block*ppb, (block+1)*ppb)]
    require(len(pages) == p['data_reads'], 'Data reads differ')
    image = bytearray()
    for part in split(pages):
        for n, v in zip(part, batch(part)):
            require(n % ppb or v[main] == 255, 'Marker changed during the read')
            image += v[:main]
    image = bytes(image)
    require(image == (run/'logical-image.bin').read_bytes() and sha(image) == r['logical_image_sha256']
            and len(image) == p['logical_bytes'], 'First blocks differ')
    require(cursor == len(records) == r['records_completed'], 'Capture has extra records')
    require(histogram == r['ecc_histogram'], 'ECC histogram differs')
    require(len(batches) == r['batch_executions'] <= p['batch_limit']
            and len(list(run.glob('batch-*-request.bin'))) == len(batches), 'Batches differ')
    for index, (first, count) in enumerate(batches, 1):
        require(batch_request(records[first:first+count]) == (run/f'batch-{index:04}-request.bin').read_bytes(),
                f'Batch {index} request differs')

    entry = bytes.fromhex(r['entry_diagnostic'])
    skipped = entry == CLEAN
    require(r['spl_skipped'] == skipped and r['spl_execution_attempted'] == (not skipped)
            and r['ddr_diagnostic'] == list(struct.unpack('<5I', CLEAN)), 'DDR bring-up differs')
    answer = (run/'read-001.bin').read_bytes()
    require(answer.hex() in f['probe']['accepted_reply_hex'], 'Unreviewed CPU answer')
    calls = Calls(answer)

    def expected():
        yield from calls.bring_up(t, entry, spl)
        yield from calls.patterns(nonce, p['ram_regions'])
        yield from calls.write_compare(load_address, payload)
        for first, count in batches:
            yield from calls.batch(collector, load_address, records[first:first+count], ask, t['settle_ms'])

    rows = [json.loads(line) for line in (run/'transfers.jsonl').read_text().splitlines()]
    trace = compare(rows, expected(), run, t, p['protocol_call_limit'],
                    ([] if skipped else [t['spl_entry']])+[load_address]*len(batches), r['connection'])
    return dict(status='saved-probe-trace-matches', session_id=r['session_id'], calls=trace['calls'],
                raw_reads=trace['raw_reads'], raw_bytes=trace['read_bytes'], bulk_ram_writes=trace['bulk_writes'],
                spl_executions=0 if skipped else 1, batch_executions=len(batches), records=len(records),
                bad_blocks=bad, logical_to_physical=mapping, ecc_histogram=histogram, plan_sha256=fp(p),
                result_sha256=sha((run/'result.json').read_bytes()),
                journal_sha256=sha((run/'transfers.jsonl').read_bytes()), capture_sha256=sha(raw),
                metadata_sha256=p['metadata_sha256'], image_sha256=sha(image), image_bytes=len(image),
                audit_script_sha256=sha(Path(__file__).read_bytes()),
                new_device_access=False, nand_writes=False, active_boot_verified=False)


def main():
    return cli(audit, __doc__)


if __name__ == '__main__':
    raise SystemExit(main())
