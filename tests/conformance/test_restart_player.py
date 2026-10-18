"""The player's restart from USB Boot (plan, stage 7) on a fake ROM, and its journal's audit: the
device leaving the bus after the restart payload starts, the ROM answering when no restart came, and
silence; the SPL once an entry. Never opens USB."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from deployment import restart_player as restart  # noqa: E402
from test_ram_transport import FakeClock, Rom  # noqa: E402

spec = importlib.util.spec_from_file_location('audit_usb_restart', ROOT/'scripts/deployment/audit_usb_restart.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
LIBUSB_ERROR_NO_DEVICE = -4


class RestartRom(Rom):
    """The ROM until the restart payload starts at the code region; then, as the watchdog resets the
    chip, the device is gone ('gone'), or the payload returns and the ROM answers ('returns'), or
    nothing answers within the wait ('silent')."""

    def __init__(self, reader, config, outcome='gone'):
        super().__init__(reader, config)
        self.outcome, self.started = outcome, False

    def call(self, name, args):
        if name == 'exit' and self.outcome == 'gone-stuck':
            # libusb 1.0.30 on macOS: libusb_exit deadlocked as the player detached (2026-10-18).
            self.events.append(name)
            threading.Event().wait()
        if self.started and self.outcome in ('gone', 'gone-stuck') and name in ('control_transfer', 'bulk_transfer', 'release_interface'):
            self.events.append(name)
            return LIBUSB_ERROR_NO_DEVICE
        if self.started and self.outcome == 'silent' and name == 'control_transfer':
            self.events.append(name)
            return -7
        if name == 'control_transfer' and args[2] == 4 and args[3] << 16 | args[4] == self.reader['load_address']:
            self.calls.append((name, args))
            self.events.append(name)
            self.execute_addresses.append(self.reader['load_address'])
            self.started = True
            return 0
        return super().call(name, args)


class RestartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        ram = restart.ram
        self.base = ram.load_profile()
        self.reader = ram.load_reader_profile(self.base)
        self.cpu = ram.load_probe_profile(self.base)
        self.transport = ram.load_transport(self.base, self.reader)
        self.build = self.root/'build'
        self.build.mkdir()
        (self.build/'identity.bin').write_bytes(b'synthetic restart payload')
        (self.build/'build.json').write_text(json.dumps(dict(purpose='restart', nand_opcodes=[])))
        self.diskos = self.root/'diskos'
        (self.diskos/'flash').mkdir(parents=True)
        spl = bytes(range(256)) * 31 + bytes(120)
        (self.diskos/'flash/disc_spl_lpddr3.bin').write_bytes(spl)
        self.inputs = dict(spl=spl, payload=(self.build/'identity.bin').read_bytes(),
                           build_sha256=ram.sha((self.build/'build.json').read_bytes()))
        self.library = self.root/'fake-library'
        self.library.write_bytes(b'never loaded')
        self.index = 0
        sync = patch.object(ram.os, 'fsync')
        sync.start()
        self.addCleanup(sync.stop)

    def session(self, outcome='gone', clean=False, close_seconds=restart.CLOSE_SECONDS):
        rom = RestartRom(self.reader, self.transport, outcome)
        if clean:  # After the installation's write, in its entry: the SPL ran already.
            rom.put(self.transport['diagnostic_address'], audit.CLEAN)
        plan = restart.make_plan(self.base, self.cpu, self.reader, self.transport, self.inputs)
        self.index += 1
        out = self.root/f'run-{self.index}'
        (self.root/f'plan-{self.index}.json').write_text(json.dumps(dict(plan=plan, plan_sha256=restart.fingerprint(plan))))
        clock = FakeClock()
        result = restart.acquire(plan, restart.fingerprint(plan), self.base, self.cpu, self.reader, self.transport, self.inputs,
                                 self.library, out, loader=lambda _: rom, clock=clock, sleep=clock.sleep, close_seconds=close_seconds)
        return result, out, rom

    def test_a_close_the_usb_library_never_returns_from_is_bounded(self):
        """The second player's write (2026-10-18): the player left the bus, libusb_exit never returned, and
        the installer waited for a result that was never written. The close has a bound; the result says so."""
        self.addCleanup(restart.PENDING_CLOSES.clear)
        result, out, rom = self.session('gone-stuck', clean=True, close_seconds=0.2)
        self.assertEqual(result['status'], 'player-restarted', result.get('error'))
        self.assertEqual(len(result['cleanup_errors']), 1)
        self.assertIn('did not return from the close', result['cleanup_errors'][0])
        self.assertEqual(len(restart.PENDING_CLOSES), 1)
        self.assertTrue((out/'result.json').is_file(), 'the result is written although the close hangs')
        self.assertEqual(json.loads((out/'result.json').read_text())['status'], 'player-restarted')
        self.assertEqual(self.check(out)['outcome'], 'player-restarted')

    def check(self, out):
        return audit.audit(out, self.root/f'plan-{self.index}.json', self.build, self.diskos)

    def test_the_player_leaves_the_bus_after_the_restart_payload(self):
        result, out, rom = self.session(clean=True)
        self.assertEqual(result['status'], 'player-restarted', result.get('error'))
        self.assertEqual(rom.execute_addresses, [self.reader['load_address']], 'no SPL in the entry of the write')
        self.assertTrue(result['payload_ram_verified'] and result['restart_execution_attempted'])
        self.assertFalse(result['nand_writes'])
        report = self.check(out)
        self.assertEqual((report['status'], report['outcome'], report['spl_executions']),
                         ('saved-restart-trace-matches', 'player-restarted', 0))

    def test_a_fresh_entry_runs_the_spl_first(self):
        result, out, rom = self.session()
        self.assertEqual(result['status'], 'player-restarted', result.get('error'))
        self.assertEqual(rom.execute_addresses, [self.transport['spl_entry'], self.reader['load_address']])
        self.assertEqual(self.check(out)['spl_executions'], 1)

    def test_no_restart_and_silence_are_told_apart(self):
        result, out, _ = self.session('returns', clean=True)
        self.assertEqual((result['status'], result['cleanup_errors']), ('restart-not-observed', []))
        self.assertEqual(self.check(out)['outcome'], 'restart-not-observed')
        result, out, _ = self.session('silent', clean=True)
        self.assertEqual(result['status'], 'restart-uncertain')
        self.assertEqual(self.check(out)['outcome'], 'restart-uncertain')

    def test_the_audit_refuses_a_changed_outcome_or_journal(self):
        result, out, _ = self.session(clean=True)
        saved = (out/'result.json').read_text()
        (out/'result.json').write_text(json.dumps(dict(json.loads(saved), status='restart-not-observed')))
        with self.assertRaisesRegex(ValueError, 'Cleanup failed|Held ask|Saved read'):
            self.check(out)
        (out/'result.json').write_text(saved)
        journal = (out/'transfers.jsonl').read_text().splitlines()
        (out/'transfers.jsonl').write_text('\n'.join(journal[:-2]) + '\n')
        with self.assertRaisesRegex(ValueError, 'Missing USB call'):
            self.check(out)

    def test_a_changed_plan_never_reaches_usb(self):
        plan = restart.make_plan(self.base, self.cpu, self.reader, self.transport, self.inputs)
        with self.assertRaisesRegex(restart.ram.probe.ProbeError, 'Restart plan/inputs changed'):
            restart.acquire(plan, '0'*64, self.base, self.cpu, self.reader, self.transport, self.inputs, self.library,
                            self.root/'never', loader=lambda _: self.fail('USB'))
        self.assertFalse((self.root/'never').exists())


if __name__ == '__main__':
    unittest.main()
