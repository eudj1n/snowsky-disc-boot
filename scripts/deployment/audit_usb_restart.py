#!/usr/bin/env python3
"""Independently reconstruct a saved restart session (restart_player.py acquire); never opens USB.

Checked from the saved files alone against the reviewed profiles, the build and the SPL: the plan,
the payload and the SPL by their digests, and the USB journal call by call (usb_trace_audit.compare):
the entry's DDR diagnostic (the SPL only when it is not clean), the payload written and read back,
its execution, and the one request held after it, with the outcome the result names. The report
(offline-review.json) is the session's audit. Apart from usb_trace_audit it shares no code with the
session."""
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from usb_trace_audit import (CLEAN, Calls, ask, check_plan, cli, compare, digest as sha, fingerprint as fp, load,
                             profiles, require)

ROOT = Path(__file__).resolve().parents[2]
OUTCOMES = {'player-restarted': 'gone', 'restart-not-observed': 'answer', 'restart-uncertain': 'timeout'}


def audit(run, plan_file, build, diskos, root=ROOT):
    run = Path(run)
    r, q, approved = load(run/'result.json'), load(run/'request.json'), load(plan_file)
    p = r['plan']
    require(p == q['plan'] == approved['plan'] and fp(p) == approved['plan_sha256']
            == r['approved_plan_sha256'] == q['approved_plan_sha256'], 'Plan differs from the approved one')
    require(r['status'] in OUTCOMES and r.get('error') is None and r['session_id'] == q['session_id']
            and r['nonce_hex'] == q['nonce_hex'], 'Incomplete restart session')
    require(r['physical_device_accessed'] is True and r['payload_ram_verified'] is True
            and r['restart_execution_attempted'] is True and r['nand_writes'] is False and r['flash_ready'] is False
            and p['operation'] == 'restart' and p['nand_writes'] is False and p['writer_executions'] == 0,
            'Restart session scope differs')
    # Released and closed as usual unless the player left the bus.
    require(r['cleanup_errors'] == [] or r['status'] == 'player-restarted', 'Cleanup failed')
    for name, value in p['transport_sources_sha256'].items():
        require(sha((Path(root)/name).read_bytes()) == value, f'Changed acquisition source: {name}')
    f = profiles(root, p['version'])
    reader, t = f['reader'], f['transport']
    check_plan(p, f)
    load_address = reader['load_address']
    require(p['ram_regions'] == [dict(name='code', address=load_address, bytes=reader['code_bytes'])]
            and p['payload_entry'] == load_address, 'RAM regions differ')
    build = Path(build)
    payload = (build/'identity.bin').read_bytes()
    manifest = json.loads((build/'build.json').read_text())
    require(sha((build/'build.json').read_bytes()) == p['build_sha256'] and manifest.get('purpose') == 'restart'
            and manifest.get('nand_opcodes') == [] and sha(payload) == p['payload_sha256']
            and len(payload) == p['payload_bytes'], 'Restart build differs')
    spl = (Path(diskos)/'flash/disc_spl_lpddr3.bin').read_bytes()
    require(sha(spl) == p['spl_sha256'] and p['spl'] == dict(load=t['spl_load_address'], entry=t['spl_entry'],
            bytes=len(spl), diagnostic=t['diagnostic_address'], once_an_entry=True), 'SPL differs')
    entry = bytes.fromhex(r['entry_diagnostic'])
    skipped = entry == CLEAN
    require(r['spl_skipped'] == skipped and r['spl_execution_attempted'] == (not skipped)
            and r.get('ddr_diagnostic', list(struct.unpack('<5I', CLEAN))) == list(struct.unpack('<5I', CLEAN)),
            'DDR bring-up differs')
    answer = (run/'read-001.bin').read_bytes()
    require(answer.hex() in f['probe']['accepted_reply_hex'], 'Unreviewed CPU answer')
    calls = Calls(answer)

    def expected():
        yield from calls.bring_up(t, entry, spl)
        yield from calls.write_compare(load_address, payload)
        yield from calls.ctrl(4, load_address)
        yield ask(p['restart']['wait_ms'], OUTCOMES[r['status']], answer)

    rows = [json.loads(line) for line in (run/'transfers.jsonl').read_text().splitlines()]
    trace = compare(rows, expected(), run, t, 64, ([] if skipped else [t['spl_entry']]) + [load_address], r['connection'])
    return dict(status='saved-restart-trace-matches', outcome=r['status'], session_id=r['session_id'], calls=trace['calls'],
                raw_reads=trace['raw_reads'], spl_executions=0 if skipped else 1, plan_sha256=fp(p),
                result_sha256=sha((run/'result.json').read_bytes()), journal_sha256=sha((run/'transfers.jsonl').read_bytes()),
                payload_sha256=sha(payload), audit_script_sha256=sha(Path(__file__).read_bytes()),
                new_device_access=False, nand_writes=False)


def main():
    return cli(audit, __doc__)


if __name__ == '__main__':
    raise SystemExit(main())
