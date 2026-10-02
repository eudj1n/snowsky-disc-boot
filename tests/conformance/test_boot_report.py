"""Firmware-free shell fixtures; production has no runtime fixture path option."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('candidate_report', ROOT/'scripts/deployment/build_candidate.py')
builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)


class BootReportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name in ('run', 'proc/net', 'proc/sys/kernel/random', 'sys/class/udc/controller', 'sys/kernel/config/usb_gadget', 'card'):
            (self.root/name).mkdir(parents=True, exist_ok=True)
        self.card = self.root/'card'; self.marker = self.card/'.disc'/'dev'/'boot-report'
        self.output = self.card/'.disc'/'dev'/'boot-report.txt'
        self.marker.parent.mkdir(parents=True)
        self.marker.write_bytes(b'DISC_WEB_LOCAL_BOOT_REPORT\n')
        (self.root/'proc/mounts').write_text(f'/dev/mmcblk0p1 {self.card} exfat rw 0 0\n/dev/root / squashfs ro 0 0\n')
        (self.root/'proc/net/tcp').write_text('0: 0100007F:1EBE 00000000:0000 0A\n')
        (self.root/'proc/sys/kernel/random/boot_id').write_text('synthetic-boot-id\n')
        (self.root/'run/disc-usb.log').write_text('usb hook entered\nnot found\nusb helper exit=127\n')
        (self.root/'run/disc-boot').mkdir()
        (self.root/'run/disc-boot/boot.json').write_text('{"mode":"platform","reason":"default"}\n')
        (self.root/'sys/class/udc/controller/state').write_text('not attached\n')
        # Native host commands provide the BusyBox applet interface. Actual
        # stock BusyBox syntax/behavior is checked in disposable integration.
        self.bb = self.root/'bb'; self.bb.write_text('#!/bin/sh\nexec "$@"\n'); self.bb.chmod(0o755)
        self.profile = dict(sd_mount=str(self.card), sd_source='/dev/mmcblk0p1', udc='controller',
                            boot_report=dict(delay_seconds=0, wait_seconds=1, max_bytes=16384))
        self.script = self.root/'report.sh'

    def execute(self):
        source = builder.boot_report_script(self.profile)
        for key, value in [('BB',self.bb),('RUN',self.root/'run'),('PROC',self.root/'proc'),('SYS',self.root/'sys')]:
            import re
            source = re.sub(r'^'+key+r'=.*$',key+"='"+str(value)+"'",source,flags=re.M)
        self.script.write_text(source)
        return subprocess.run(['/bin/sh',str(self.script)],capture_output=True,text=True,timeout=5)

    def test_report_survives_native_launch_failure_without_usb_marker(self):
        result = self.execute(); self.assertEqual(result.returncode,0,result.stderr)
        report = self.output.read_bytes()
        self.assertIn(b'usb helper exit=127',report)
        self.assertIn(b'invalid_or_missing_pid',report)
        self.assertIn(b'[boot_decision]\n{"mode":"platform","reason":"default"}',report)
        self.assertIn(b'[boot_service]\nunavailable',report)
        self.assertIn(b'0100007F:1EBE 0A',report)
        self.assertIn(b'synthetic-boot-id',report)
        self.assertIn(('usb_profile_sha256='+builder.fingerprint(self.profile)).encode(),report)
        self.assertTrue(report.endswith(b'END_DISC_WEB_BOOT_REPORT_V1\n'))
        self.assertLessEqual(len(report),16384)

    def test_marker_is_separate_exact_regular_opt_in(self):
        self.marker.write_bytes(b'DISC_WEB_LOCAL_ROOT_CONSOLE\n')
        self.assertEqual(self.execute().returncode,0); self.assertFalse(self.output.exists())

    def test_symlink_marker_refused(self):
        other=self.root/'ack'; other.write_bytes(self.marker.read_bytes()); self.marker.unlink(); self.marker.symlink_to(other)
        self.assertEqual(self.execute().returncode,0); self.assertFalse(self.output.exists())

    def test_missing_or_wrong_mount_refused(self):
        (self.root/'proc/mounts').write_text(f'/dev/other {self.card} exfat rw 0 0\n')
        self.assertEqual(self.execute().returncode,0); self.assertFalse(self.output.exists())

    def test_stock_gadget_prevents_card_write(self):
        (self.root/'sys/kernel/config/usb_gadget/storage_demo').mkdir()
        self.assertEqual(self.execute().returncode,0); self.assertFalse(self.output.exists())

    def test_marker_revoked_after_snapshot_prevents_publish(self):
        self.bb.write_text('#!/bin/sh\nif [ "$1" = grep ]; then rm -f "'+str(self.marker)+'"; fi\nexec "$@"\n')
        self.assertNotEqual(self.execute().returncode,0); self.assertFalse(self.output.exists())

    def test_stock_gadget_arrival_after_snapshot_prevents_publish(self):
        gadget=self.root/'sys/kernel/config/usb_gadget/storage_demo'
        self.bb.write_text('#!/bin/sh\nif [ "$1" = grep ]; then mkdir "'+str(gadget)+'"; fi\nexec "$@"\n')
        self.assertNotEqual(self.execute().returncode,0); self.assertFalse(self.output.exists())

    def test_existing_report_is_preserved(self):
        self.output.write_bytes(b'earlier evidence')
        self.assertEqual(self.execute().returncode,0); self.assertEqual(self.output.read_bytes(),b'earlier evidence')

    def test_symlink_output_never_changes_target(self):
        target=self.root/'target'; target.write_bytes(b'untouched'); self.output.symlink_to(target)
        self.assertEqual(self.execute().returncode,0); self.assertEqual(target.read_bytes(),b'untouched')

    def test_fifo_output_does_not_block(self):
        os.mkfifo(self.output)
        self.assertEqual(self.execute().returncode,0)

    def test_duplicate_run_does_not_rewrite_report(self):
        self.assertEqual(self.execute().returncode,0); before=self.output.read_bytes()
        (self.root/'run/disc-usb.log').write_text('changed')
        self.assertEqual(self.execute().returncode,0); self.assertEqual(self.output.read_bytes(),before)

    def test_large_log_is_bounded_and_complete(self):
        (self.root/'run/disc-usb.log').write_bytes(b'x'*100000)
        self.assertEqual(self.execute().returncode,0)
        report=self.output.read_bytes(); self.assertLessEqual(len(report),16384)
        self.assertTrue(report.endswith(b'END_DISC_WEB_BOOT_REPORT_V1\n'))
        self.assertNotIn(b'x'*4097,report)

    def test_truncated_report_is_never_published(self):
        self.profile['boot_report']['max_bytes']=128
        self.assertNotEqual(self.execute().returncode,0); self.assertFalse(self.output.exists())

    def test_generated_hook_observes_failure_independently(self):
        profile={**self.profile,'startup_seconds':30,'session_seconds':900}
        hook=builder.usb_hook(profile)
        self.assertIn('/bin/sh /opt/disc-boot/boot-report.sh',hook)
        self.assertIn('usb helper exit=%s',hook)
        self.assertNotIn('usb-console',builder.boot_report_script(profile))
        self.assertNotIn('@',builder.boot_report_script(profile))


if __name__=='__main__': unittest.main()
