#!/usr/bin/env python3
"""The player restarted from USB Boot (plan, stage 7); never writes NAND.

A separately approved session runs the restart payload (device/usbboot/restart.c, built with
build_identity.py --mode restart) from the player's RAM: it starts the watchdog as the pinned SPL
source's _machine_restart does, and the chip restarts from NAND into the system it holds, the cable
still connected. After an installation's write it runs in the same USB Boot entry, so the SPL is
not run again (ram_transport.bring_up).

  python3 scripts/deployment/restart_player.py plan --build <restart build> --diskos <diskOS files>
  python3 scripts/deployment/restart_player.py acquire --build ... --diskos ... --libusb ... \\
      --approved-plan-sha256 <plan's> --output <fresh folder>

After the execution one request is held for the ROM: the ROM leaving the bus is "player-restarted"
(the system's own start, its USB console, is the proof); the ROM answering is
"restart-not-observed" (the payload found no restart, stopped the watchdog and returned); neither is
"restart-uncertain".
"""
import argparse
import ctypes as C
from datetime import datetime, timezone
import json
from pathlib import Path
import struct
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from firmware_profile import fingerprint
from deployment import ram_transport as ram

check = ram.check
# The payload's own bound is a fraction of a second at the SPL's clocks (restart.h, RESTART_WAIT_TICKS).
WAIT_MS = 5000


def make_plan(base, cpu, reader, transport, inputs):
    p = ram.plan(base, cpu, reader, transport, {k: inputs[k] for k in ('spl', 'payload', 'build_sha256')}, 'ram-check')
    p.update(operation='restart', payload_entry=reader['load_address'], payload_bytes=len(inputs['payload']),
             ram_regions=[dict(name='code', address=reader['load_address'], bytes=reader['code_bytes'])],
             pattern_passes=0, pattern=None,
             restart=dict(watchdog='TCU 0xb0002000: TSCR WDTSC; WDT TCNT 0, TDR 2, TCSR prescale 64 and RTC, TCER 0 then 1',
                          source="diskOS's u-boot-xburst, arch/mips/cpu/xburst2/cpu.c _machine_restart", wait_ms=WAIT_MS),
             steps=['the DDR diagnostic; the SPL once an entry', 'the restart payload uploaded and compared',
                    'executed; one request held: the ROM leaves the bus, answers, or neither'],
             nand_commands=[], nand_writes=False, writer_executions=0)
    p['transport_sources_sha256']['scripts/deployment/restart_player.py'] = ram.probe.digest(Path(__file__))
    return p


def run(session, inputs, record):
    def upload(address, data):
        session.write(address, data)
        check(session.read(address, len(data)) == data, 'SPL SRAM readback mismatch')
    record['ddr_diagnostic'] = list(struct.unpack('<5I', ram.bring_up(session, inputs['spl'], record, upload)))
    entry, payload = record['plan']['payload_entry'], inputs['payload']
    session.write(entry, payload)
    check(session.read(entry, len(payload)) == payload, 'Restart payload readback mismatch')
    record['payload_ram_verified'] = True
    session.control(4, entry, 'restart_execution_attempted')
    # One request held for the ROM; an error other than a timeout is the device gone from the bus.
    answer = session.ask(WAIT_MS, tolerant=True)
    record['status'] = {True: 'restart-not-observed', False: 'player-restarted', None: 'restart-uncertain'}[answer]


def acquire(plan, approved, base, cpu, reader, transport, inputs, library, output,
            loader=C.CDLL, clock=time.monotonic, sleep=time.sleep):
    check(plan == make_plan(base, cpu, reader, transport, inputs) and approved == fingerprint(plan), 'Restart plan/inputs changed')
    output.mkdir(mode=0o700)
    nonce = uuid.uuid4().bytes
    record = dict(plan=plan, approved_plan_sha256=approved, session_id=str(uuid.uuid4()), nonce_hex=nonce.hex(),
                  started_at=datetime.now(timezone.utc).isoformat(), status='incomplete', physical_device_accessed=False,
                  usb_discovery_attempted=False, spl_execution_attempted=False, restart_execution_attempted=False,
                  payload_ram_verified=False, nand_writes=False, flash_ready=False)
    ram.save_json(output/'request.json', record)
    journal, session = None, None
    try:
        journal = ram.Journal(output)
        library = library.resolve(strict=True)
        ram.save_json(output/'dependency.json', dict(path=str(library), sha256=ram.probe.digest(library)))
        journal.event(phase='usb-discovery-attempt')
        session = ram.Session(ram.bind(loader(str(library))), cpu, dict(transport, session_budget_ms=transport['session_budget_ms']),
                              record, journal, clock, sleep)
        session.open()
        run(session, inputs, record)
    except (Exception, KeyboardInterrupt) as exc:
        record.update(status='failed', error=f'{type(exc).__name__}: {exc}')
    finally:
        # A player that restarted is gone from the bus: its interface cannot be released, which is no failure.
        if session and session.close() and record['status'] != 'player-restarted':
            record['status'] = 'failed'
        if journal:
            journal.file.close()
        record['finished_at'] = datetime.now(timezone.utc).isoformat()
        ram.save_json(output/'result.json', record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=('plan', 'acquire'))
    parser.add_argument('--version')
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--diskos', type=Path, required=True)
    parser.add_argument('--libusb', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--approved-plan-sha256')
    args = parser.parse_args()
    extras = (args.libusb, args.output, args.approved_plan_sha256)
    if (args.action == 'plan' and any(x is not None for x in extras)) or (args.action == 'acquire' and not all(extras)):
        parser.error('acquire requires libusb, a fresh output and the approved plan SHA-256; plan accepts none of them')
    try:
        base = ram.load_profile(args.version)
        cpu, reader = ram.load_probe_profile(base), ram.load_reader_profile(base)
        transport = ram.load_transport(base, reader)
        inputs = ram.prepare_inputs(base, cpu, reader, transport, args.build, args.diskos, 'restart')
        plan = make_plan(base, cpu, reader, transport, inputs)
        result = dict(plan=plan, plan_sha256=fingerprint(plan)) if args.action == 'plan' else acquire(
            plan, args.approved_plan_sha256, base, cpu, reader, transport, inputs, args.libusb, args.output)
        print(json.dumps(result, indent=2))
        return 1 if result.get('status') in ('failed', 'restart-uncertain') else 0
    except (Exception, KeyboardInterrupt) as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
