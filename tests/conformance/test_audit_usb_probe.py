"""The identity probe's audit against synthetic sessions of the fake ROM; no firmware, USB or
sibling repository."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from deployment import collect_rootfs as collect
from deployment.collector_policy import load_collector_policy
from test_collect_rootfs import BatchRom
from test_ram_transport import FakeClock
import test_kernel_review

spec = importlib.util.spec_from_file_location('audit_usb_probe', ROOT/'scripts/deployment/audit_usb_probe.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class ProbeAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        ram = collect.ram
        self.base = ram.load_profile()
        self.cpu = ram.load_probe_profile(self.base)
        self.reader = ram.load_reader_profile(self.base)
        self.config = ram.load_transport(self.base, self.reader)
        self.policy = ram.load_metadata_policy(self.base, self.reader)
        self.scope = load_collector_policy(self.base, self.reader, 'rootfs-probe')
        self.build = self.root/'build'
        self.build.mkdir()
        (self.build/'identity.bin').write_bytes(b'synthetic probe payload')
        (self.build/'build.json').write_bytes(b'{"synthetic": true}\n')
        self.diskos = self.root/'diskos'
        (self.diskos/'flash').mkdir(parents=True)
        spl = bytes(range(256))*31+bytes(120)
        (self.diskos/'flash/disc_spl_lpddr3.bin').write_bytes(spl)
        self.inputs = dict(spl=spl, payload=(self.build/'identity.bin').read_bytes(),
                           build_sha256=ram.sha((self.build/'build.json').read_bytes()),
                           page_policy=self.policy, collector_policy=self.scope,
                           completion=ram.load_completion(self.base, self.config))
        fixture = test_kernel_review.KernelReviewTests()
        fixture.setUp()
        self.metadata = fixture.page()
        self.library = self.root/'lib'
        self.library.write_bytes(b'fake')
        sync = patch.object(collect.os, 'fsync')
        sync.start()
        self.addCleanup(sync.stop)

    def session(self, clean=False):
        rom = BatchRom(self.reader, self.config, self.scope)
        if clean:  # This entry's SPL already ran: its clean diagnostic is in TCSM.
            rom.put(self.config['diagnostic_address'], audit.CLEAN)
        plan = collect.make_plan(self.base, self.cpu, self.reader, self.config, self.inputs, self.metadata)
        (self.root/'plan.json').write_text(json.dumps(dict(plan=plan, plan_sha256=collect.fingerprint(plan))))
        out, clock = self.root/'run', FakeClock()
        result = collect.acquire(plan, collect.fingerprint(plan), self.base, self.cpu, self.reader, self.config,
                                 self.inputs, self.metadata, self.library, out, loader=lambda _: rom,
                                 clock=clock, sleep=clock.sleep)
        self.assertEqual(result['status'], 'rootfs-probe-collected', result.get('error'))
        return out

    def check(self, out):
        return audit.audit(out, self.root/'plan.json', self.build, self.diskos)

    def test_session_matches_and_gives_the_first_blocks(self):
        out = self.session()
        report = self.check(out)
        result = json.loads((out/'result.json').read_text())
        self.assertEqual(report['status'], 'saved-probe-trace-matches')
        self.assertEqual(report['image_sha256'], result['logical_image_sha256'])
        self.assertEqual(report['image_bytes'], self.scope['logical_blocks']*64*2048)
        self.assertEqual((report['spl_executions'], report['records'], report['batch_executions']), (1, 132, 3))
        self.assertLessEqual(report['calls'], result['plan']['protocol_call_limit'])
        self.assertFalse(report['nand_writes'] or report['new_device_access'])

    def test_an_entry_whose_spl_ran_skips_it(self):
        self.assertEqual(self.check(self.session(clean=True))['spl_executions'], 0)

    def test_changed_session_files_are_refused(self):
        out = self.session()
        def edit(name, change, message):
            path = out/name
            saved = path.read_bytes()
            path.write_bytes(change(saved))
            with self.assertRaisesRegex(ValueError, message):
                self.check(out)
            path.write_bytes(saved)
        flip = lambda at: (lambda b: b[:at]+bytes([b[at] ^ 1])+b[at+1:])
        result = json.loads((out/'result.json').read_text())
        def with_result(**changes):
            return lambda b: json.dumps(dict(json.loads(b), **changes)).encode()
        edit('logical-image.bin', flip(5000), 'First blocks differ')
        edit('records.bin', flip(4476*10+200), 'Capture differs')
        edit('batch-0003-request.bin', flip(100), 'Batch 3 request differs')
        edit('transfers.jsonl', lambda b: b'\n'.join(b.splitlines()[:-2])+b'\n', 'Missing USB call')
        edit('result.json', with_result(logical_to_physical=[80, 82]), 'Block map differs')
        edit('result.json', with_result(spl_skipped=True), 'DDR bring-up differs')
        edit('result.json', with_result(status='rootfs-collected'), 'Incomplete session')
        edit(sorted(out.glob('read-*.bin'))[-1].name, flip(7), 'Saved read .* bytes differ')
        # A record changed with the capture's digest to match: its own CRC refuses it.
        data = bytearray((out/'records.bin').read_bytes())
        data[4476*10+48+76+5] ^= 1
        (out/'records.bin').write_bytes(bytes(data))
        (out/'result.json').write_text(json.dumps(dict(result, capture_sha256=collect.ram.sha(bytes(data)))))
        with self.assertRaisesRegex(ValueError, 'Record 11 payload differs'):
            self.check(out)

    def test_cli_writes_its_report_once(self):
        out = self.session()
        report = self.root/'offline-review.json'
        args = ['--run', str(out), '--plan', str(self.root/'plan.json'), '--build', str(self.build),
                '--diskos', str(self.diskos), '--output', str(report)]
        with patch.object(sys, 'argv', ['audit_usb_probe.py', *args]), patch('builtins.print'):
            self.assertEqual(audit.main(), 0)
            self.assertEqual(json.loads(report.read_text())['status'], 'saved-probe-trace-matches')
            self.assertEqual(audit.main(), 1)


if __name__ == '__main__':
    unittest.main()
