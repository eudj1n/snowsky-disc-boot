"""CI-safe filesystem/PTY fixture. Never opens USB or a real configfs mount."""
import os
from pathlib import Path
import pty
import select
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(os.environ.get('DISC_USB_FIXTURE_BINARY', ROOT/'build/host/usb-console-fixture'))
ACK = b'DISC_WEB_LOCAL_ROOT_CONSOLE\n'


class UsbConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.processes = []
        self.addCleanup(self.stop_all)
        for name in ('proc', 'run', 'tmp/sdcard', 'dev', 'sys/class/udc/controller', 'sys/kernel/config/usb_gadget'):
            (self.root/name).mkdir(parents=True, exist_ok=True)
        (self.root/'proc/mounts').write_text('/dev/mmcblk0p1 /tmp/sdcard exfat rw 0 0\n')
        (self.root/'sys/class/udc/controller/state').write_text('configured\n')
        self.marker = self.root/'tmp/sdcard/.disc/dev/usb-console'
        self.marker.parent.mkdir(parents=True)
        self.marker.write_bytes(ACK)
        self.base = self.root/'sys/kernel/config/usb_gadget'
        self.gadget = self.base/'disc_web_debug'
        self.master, self.slave = pty.openpty()
        self.addCleanup(os.close, self.master)
        self.addCleanup(os.close, self.slave)
        (self.root/'dev/ttyGS0').symlink_to(os.ttyname(self.slave))

    def stop_all(self):
        for proc in self.processes:
            if proc.poll() is None:proc.terminate()
            try:proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill();proc.communicate(timeout=3)

    def start(self, session=10, udc='controller', cwd=None):
        proc = subprocess.Popen([str(FIXTURE), '--fixture-root', str(self.root),
            '--sd-mount', '/tmp/sdcard', '--sd-source', '/dev/mmcblk0p1', '--udc', udc,
            '--startup-seconds', '1', '--session-seconds', str(session)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
        self.processes.append(proc)
        return proc

    def wait(self, predicate):
        end = time.monotonic()+5
        while not predicate():
            if select.select([self.master], [], [], 0)[0]:
                try:os.read(self.master, 4096)
                except OSError:pass
            self.assertLess(time.monotonic(), end, 'Fixture deadline')
            time.sleep(.02)

    def running(self, proc):
        self.wait(lambda:(self.gadget/'UDC').exists())
        self.assertIsNone(proc.poll())

    def finished(self, proc):
        self.wait(lambda:proc.poll() is not None)
        _, log = proc.communicate(timeout=5)
        self.assertEqual(proc.returncode, 0, log)
        self.assertFalse(self.gadget.exists(), log)
        return log

    def test_absent_wrong_and_symlink_markers_never_create_gadget(self):
        for mode in ('absent', 'wrong', 'symlink'):
            with self.subTest(mode=mode):
                if self.marker.exists() or self.marker.is_symlink():self.marker.unlink()
                if mode == 'wrong':self.marker.write_bytes(b'yes\n')
                if mode == 'symlink':
                    target = self.root/'ack';target.write_bytes(ACK);self.marker.symlink_to(target)
                self.assertIn(b'not opted in', self.finished(self.start()))

    def test_directory_is_not_a_mounted_card_and_source_must_match(self):
        for mounts in ('', '/dev/other /tmp/sdcard exfat rw 0 0\n', '/dev/mmcblk0p1 /tmp/sdcard tmpfs rw 0 0\n'):
            (self.root/'proc/mounts').write_text(mounts)
            self.assertIn(b'startup deadline', self.finished(self.start()))

    def test_existing_stock_gadget_is_preserved(self):
        stock = self.base/'storage_demo';stock.mkdir();(stock/'UDC').write_text('controller')
        self.assertIn(b'stock USB owns port', self.finished(self.start()))
        self.assertEqual((stock/'UDC').read_text(), 'controller')

    def test_multiple_controllers_refused(self):
        (self.root/'sys/class/udc/other').mkdir()
        self.assertIn(b'startup deadline', self.finished(self.start()))

    def test_sole_controller_with_different_name_never_binds(self):
        # Reproduce the observed name mismatch with synthetic sysfs, no device.
        (self.root/'sys/class/udc/controller').rename(self.root/'sys/class/udc/13500000.otg_new')
        log=self.finished(self.start(udc='13500000.otg'))
        self.assertIn(b'mounted=1 controller=0',log)
        self.assertIn(b'startup deadline',log)
        self.assertNotIn(b'console started',log)

    def test_configured_controller_name_with_suffix_binds_exactly(self):
        name='13500000.otg_new'
        (self.root/'sys/class/udc/controller').rename(self.root/'sys/class/udc'/name)
        proc=self.start(udc=name);self.running(proc)
        self.assertEqual((self.gadget/'UDC').read_text(),name)
        (self.root/'run/disc-usb.stop').touch()
        self.finished(proc)

    def test_console_executes_over_pty_and_exit_cleans_own_gadget(self):
        proc = self.start();self.running(proc)
        proof = self.root/'proof'
        os.write(self.master, f"printf verified > '{proof}'\n".encode())
        self.wait(lambda:proof.exists() and proof.read_text() == 'verified')
        os.write(self.master, b'exit\n')
        self.assertIn(b'console exited', self.finished(proc))

    def test_acm_target_resolves_from_process_cwd_before_configfs_link(self):
        # configfs resolves the supplied target from the caller's cwd at link
        # creation, unlike ordinary filesystems which store an unchecked string.
        cwd=self.root/'unrelated-cwd';cwd.mkdir()
        proc=self.start(cwd=cwd);self.running(proc)
        target=Path(os.readlink(self.gadget/'configs/c.1/acm.0'))
        self.assertEqual((cwd/target).resolve(),(self.gadget/'functions/acm.0').resolve())
        self.assertTrue((cwd/target).is_dir())
        (self.root/'run/disc-usb.stop').touch();self.finished(proc)

    def test_duplicate_start_does_not_rebind_or_reset_stop_request(self):
        proc = self.start();self.running(proc)
        before = (self.gadget/'UDC').stat().st_mtime_ns
        duplicate = self.start();duplicate.communicate(timeout=3)
        self.assertEqual(duplicate.returncode, 0)
        self.assertEqual((self.gadget/'UDC').stat().st_mtime_ns, before)
        (self.root/'run/disc-usb.stop').touch()
        self.finished(proc)

    def test_an_unmounted_card_is_no_revocation(self):
        """Stock's player unmounts the card and mounts it again at every start of the pair: the
        session goes on through that, and ends when the card comes back without the marker."""
        (self.root/'proc/mounts').write_text('/dev/mmcblk0p1 /tmp/sdcard exfat rw 0 0\n')
        (self.root/'sys/class/udc/controller/state').write_text('configured\n')
        proc = self.start(); self.running(proc)
        # The session, not only the gadget: the start checks the marker on a mounted card after the gadget
        # is bound, so an unmount before the shell runs is a start refused (under the suite's load, 2026-10-08).
        live = self.root/'live'
        os.write(self.master, f"printf live > '{live}'\n".encode())
        self.wait(lambda: live.exists())
        (self.root/'proc/mounts').write_text('')
        time.sleep(1.5)
        self.assertIsNone(proc.poll(), 'the session ended while the card was only unmounted')
        self.marker.unlink()
        (self.root/'proc/mounts').write_text('/dev/mmcblk0p1 /tmp/sdcard exfat rw 0 0\n')
        self.finished(proc)

    def test_marker_removal_unbind_and_stock_arrival_revoke(self):
        for trigger in ('marker', 'unbind', 'disconnect', 'stock'):
            with self.subTest(trigger=trigger):
                self.marker.write_bytes(ACK)
                (self.root/'proc/mounts').write_text('/dev/mmcblk0p1 /tmp/sdcard exfat rw 0 0\n')
                (self.root/'sys/class/udc/controller/state').write_text('configured\n')
                proc = self.start();self.running(proc)
                if trigger == 'marker':self.marker.unlink()
                if trigger == 'unbind':(self.gadget/'UDC').write_text('\n')
                if trigger == 'disconnect':(self.root/'sys/class/udc/controller/state').write_text('not attached\n')
                if trigger == 'stock':(self.base/'uac_demo').mkdir()
                self.finished(proc)
                if trigger == 'stock':self.assertTrue((self.base/'uac_demo').is_dir())

    def test_session_deadline_and_signal_stop(self):
        proc = self.start(session=1);self.running(proc);self.finished(proc)
        proc = self.start();self.running(proc);proc.terminate();self.finished(proc)

    def test_partial_setup_failure_removes_only_owned_objects(self):
        (self.root/'run/disc-usb.fail-attribute').touch()
        self.assertIn(b'gadget setup failed', self.finished(self.start()))
        self.assertTrue(self.base.is_dir())

    def test_unconfigured_controller_never_opens_console(self):
        (self.root/'sys/class/udc/controller/state').write_text('not attached\n')
        self.assertNotIn(b'console started', self.finished(self.start()))

    def test_production_binary_rejects_fixture_option(self):
        binary = Path(os.environ.get('DISC_USB_PRODUCTION_BINARY', ROOT/'build/host/disc-usb-console'))
        # All normal arguments are valid: failure must be the forbidden fixture
        # option, not simply the absence of required production arguments.
        result = subprocess.run([str(binary), '--fixture-root', str(self.root),
            '--sd-mount', '/tmp/sdcard', '--sd-source', '/dev/mmcblk0p1',
            '--udc', 'controller', '--startup-seconds', '1', '--session-seconds', '1'], timeout=5)
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__':unittest.main()
