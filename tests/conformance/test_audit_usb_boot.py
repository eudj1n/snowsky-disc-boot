"""The boot evidence audit against synthetic sessions of the fake ROM; no firmware, USB or sibling
repository."""
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from deployment import boot_evidence as boot
from deployment import installation_review as review
from test_boot_evidence import BootRom
from test_ram_transport import FakeClock
import test_kernel_review

spec = importlib.util.spec_from_file_location('audit_usb_boot', ROOT/'scripts/deployment/audit_usb_boot.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class BootAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = boot.ram.load_profile()
        self.reader = boot.ram.load_reader_profile(self.base)
        self.cpu = boot.ram.load_probe_profile(self.base)
        self.transport = boot.ram.load_transport(self.base, self.reader)
        # The reviewed profiles as they are: the audit reads them as files.
        self.policy = boot.load_policy(self.base, self.reader)
        fixture = test_kernel_review.KernelReviewTests()
        fixture.setUp()
        self.metadata = fixture.page()
        self.build = self.root/'build'
        payloads = {'uboot': b'boot payload', 'ota': b'ota! payload'}
        for name, data in payloads.items():
            (self.build/name).mkdir(parents=True)
            (self.build/name/'identity.bin').write_bytes(data)
        (self.build/'build.json').write_bytes(b'{"synthetic": true}\n')
        self.diskos = self.root/'diskos'
        (self.diskos/'flash').mkdir(parents=True)
        spl = bytes(range(256))*31+bytes(120)
        (self.diskos/'flash/disc_spl_lpddr3.bin').write_bytes(spl)
        self.inputs = dict(spl=spl, payload=payloads['uboot'], payloads=payloads, metadata=self.metadata,
                           build_sha256=boot.ram.sha((self.build/'build.json').read_bytes()),
                           completion=boot.ram.load_completion(self.base, self.transport))
        self.library = self.root/'fake-lib'
        self.library.write_bytes(b'never loaded')
        sync = patch.object(boot.os, 'fsync')
        sync.start()
        self.addCleanup(sync.stop)

    def session(self, fault=None, clean=False):
        out = self.root/'run'
        rom = BootRom(self.reader, self.transport, self.policy, self.metadata, fault)
        if clean:  # This entry's SPL already ran: its clean diagnostic is in TCSM.
            rom.put(self.transport['diagnostic_address'], audit.CLEAN)
        plan = boot.make_plan(self.base, self.cpu, self.reader, self.transport, self.policy, self.inputs)
        (self.root/'plan.json').write_text(json.dumps(dict(plan=plan, plan_sha256=boot.fingerprint(plan))))
        clock = FakeClock()
        result = boot.acquire(plan, boot.fingerprint(plan), self.base, self.cpu, self.reader, self.transport,
                              self.policy, self.inputs, self.library, out, loader=lambda _: rom,
                              clock=clock, sleep=clock.sleep)
        self.assertEqual(result['status'], 'boot-evidence-collected', result.get('error'))
        return out

    def check(self, out):
        return audit.audit(out, self.root/'plan.json', self.build, self.diskos)

    def test_session_matches_and_the_review_takes_it(self):
        out = self.session()
        started = time.monotonic()
        report = self.check(out)
        self.assertLess(time.monotonic()-started, 30)
        result = json.loads((out/'result.json').read_text())
        self.assertEqual(report['status'], 'saved-boot-trace-matches')
        self.assertEqual(report['records'], result['records_completed'])
        self.assertEqual(report['batch_executions'], result['batch_executions'])
        self.assertEqual((report['spl_executions'], report['uboot_bad_blocks'], report['ota_bad_blocks']), (1, [], []))
        self.assertEqual(report['boot_image_sha256'], result['boot_image_sha256'])
        self.assertLessEqual(report['calls'], result['plan']['protocol_call_limit'])
        self.assertEqual({s['target'] for s in report['ota_selectors'].values()}, {'rootfs'})
        self.assertFalse(report['nand_writes'] or report['new_device_access'] or report['active_boot_verified'])
        (out/'offline-review.json').write_text(json.dumps(report))
        found, pins = review.evidence(out, 'boot')
        self.assertEqual(pins['session_id'], report['session_id'])

    def test_an_entry_whose_spl_ran_skips_it(self):
        report = self.check(self.session(clean=True))
        self.assertEqual(report['spl_executions'], 0)

    def test_bad_boot_block_is_mapped_and_never_read_as_data(self):
        out = self.session('bad-boot')
        report = self.check(out)
        self.assertEqual(report['uboot_bad_blocks'], [1])
        self.assertEqual((out/'uboot-good-blocks.bin').stat().st_size, 15*64*2048)

    def test_unknown_selector_is_kept(self):
        report = self.check(self.session('unknown-selector'))
        self.assertEqual({s['status'] for s in report['ota_selectors'].values()}, {'unknown-ota-selector'})

    def test_changed_session_files_are_refused(self):
        out = self.session()
        def edit(name, change):
            path = out/name
            saved = path.read_bytes()
            path.write_bytes(change(saved))
            with self.assertRaises((ValueError, KeyError)):
                self.check(out)
            path.write_bytes(saved)
        flip = lambda at: (lambda b: b[:at]+bytes([b[at] ^ 1])+b[at+1:])
        result = json.loads((out/'result.json').read_text())
        def with_result(**changes):
            return lambda b: json.dumps(dict(json.loads(b), **changes)).encode()
        edit('records.bin', flip(4476*40+48+76+5))
        edit('read-001.bin', lambda b: b'X2001')
        edit('metadata-main.bin', flip(0))
        edit('uboot-good-blocks.bin', flip(2048*70))
        edit('batch-0002-request.bin', flip(3000))
        edit('transfers.jsonl', lambda b: b'\n'.join(b.splitlines()[:-2])+b'\n')
        edit('result.json', with_result(uboot_bad_blocks=[3]))
        edit('result.json', with_result(spl_skipped=True))
        edit('result.json', with_result(ota_selectors={k: dict(v, target='rootfs2') for k, v in result['ota_selectors'].items()}))
        edit('result.json', with_result(nand_writes=True))
        name = sorted(out.glob('read-*.bin'))[-1].name
        edit(name, flip(100))
        edit('request.json', with_result(nonce_hex='00'*16))
        # A record changed with the capture's digest to match: its own CRC refuses it.
        data = bytearray((out/'records.bin').read_bytes())
        data[4476*40+48+76+5] ^= 1
        (out/'records.bin').write_bytes(bytes(data))
        (out/'result.json').write_text(json.dumps(dict(result, capture_sha256=boot.ram.sha(bytes(data)))))
        with self.assertRaisesRegex(ValueError, 'Record 41 payload differs'):
            self.check(out)
        (self.build/'ota/identity.bin').write_bytes(b'ota? payload')
        with self.assertRaises(ValueError):
            self.check(out)

    def test_cli_writes_its_report_once(self):
        out = self.session()
        report = self.root/'offline-review.json'
        args = ['--run', str(out), '--plan', str(self.root/'plan.json'), '--build', str(self.build),
                '--diskos', str(self.diskos), '--output', str(report)]
        with patch.object(sys, 'argv', ['audit_usb_boot.py', *args]), patch('builtins.print'):
            self.assertEqual(audit.main(), 0)
            self.assertEqual(json.loads(report.read_text())['status'], 'saved-boot-trace-matches')
            self.assertEqual(audit.main(), 1)


if __name__ == '__main__':
    unittest.main()
