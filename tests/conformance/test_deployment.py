"""Offline image invariants, using synthetic bytes; no firmware or devices."""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('candidate',ROOT/'scripts/deployment/build_candidate.py')
candidate=importlib.util.module_from_spec(spec);spec.loader.exec_module(candidate)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def elf(self,ptype=1,tag=0,fp_abi=3,abiflags=True):
        count=(1 if ptype==1 else 2)+(1 if abiflags else 0)
        data=bytearray(256);data[:7]=b'\x7fELF\x01\x01\x01'
        offset=160
        struct.pack_into('<HHIIIIIHHH',data,16,2,8,1,0,52,0,0x1007,52,32,count)
        struct.pack_into('<8I',data,52,1,offset,0,0,16,16,5,4)
        header=84
        if ptype!=1:struct.pack_into('<8I',data,header,ptype,offset,0,0,16,16,5,4);header+=32
        if abiflags:
            struct.pack_into('<8I',data,header,0x70000003,200,0,0,24,24,4,8)
            struct.pack_into('<HBBBBBBIIII',data,200,0,1,0,32,32,0,fp_abi,0,0,0,0)
        struct.pack_into('<II',data,offset,tag,0)
        path=self.root/'binary';path.write_bytes(data);return path

    def test_mips_executable_and_wrong_architecture(self):
        path=self.elf();info=candidate.check_elf(path)
        self.assertEqual((info['bytes'],info['fpAbi']),(256,'soft'))
        data=bytearray(path.read_bytes());data[18]=62;path.write_bytes(data)
        with self.assertRaises(ValueError):candidate.check_elf(path)

    def test_hard_float_or_unflagged_native_executable_rejected(self):
        # Linux 4.4.94 runs FP branch delay slots from a user-stack trampoline
        # while emulating the FPU; the hard-float build died there on the first
        # WebSocket. Double (1) and single (2) hard-float ABIs must not ship.
        for fp_abi in (1,2):
            with self.assertRaisesRegex(ValueError,'soft-float'):candidate.check_elf(self.elf(fp_abi=fp_abi))
        with self.assertRaisesRegex(ValueError,'soft-float'):candidate.check_elf(self.elf(abiflags=False))

    def test_interpreter_dependencies_and_truncation_rejected(self):
        # A dependency-free dynamic table is legal in a static PIE. Include a
        # load segment so rejection cannot pass solely because it is missing.
        candidate.check_elf(self.elf(2,0))
        for kind,tag in [(3,0),(2,1)]:
            with self.assertRaises(ValueError):candidate.check_elf(self.elf(kind,tag))
        path=self.elf();path.write_bytes(path.read_bytes()[:100])
        with self.assertRaises(ValueError):candidate.check_elf(path)

    def test_wrong_stock_refused(self):
        path=self.root/'stock';path.write_bytes(b'hsqs'+b'0'*100)
        with self.assertRaises(ValueError):candidate.check_stock(path, {'rootfs_size':8,'rootfs_sha256':'0'*64})

    def test_padding_preserves_bytes_and_never_truncates_or_overwrites(self):
        src=self.root/'src';src.write_bytes(b'hsqs1234');out=self.root/'out'
        candidate.pad(src,out,16);self.assertEqual(out.read_bytes(),b'hsqs1234'+b'\0'*8)
        with self.assertRaises(FileExistsError):candidate.pad(src,out,16)
        with self.assertRaises(ValueError):candidate.pad(src,self.root/'oversize',4)
        self.assertFalse((self.root/'oversize').exists())

    def payload(self):
        usb = dict(sd_mount='/selected/card', sd_source='/dev/selected1', udc='controller',
                   startup_seconds=30, session_seconds=900,
                   boot_report=dict(delay_seconds=0, wait_seconds=1, max_bytes=16384))
        return candidate.payload({'version': '2.57'}, usb, self.elf(), self.elf())

    def test_the_image_adds_the_boot_layer_and_no_package(self):
        files = self.payload()
        self.assertEqual(sorted(files), ['etc/init.d/S22disc-boot', 'etc/init.d/S99disc-boot', 'etc/init.d/S99disc-usb',
                                         'opt/disc-boot/boot-report.sh', 'opt/disc-boot/disc-boot',
                                         'opt/disc-boot/disc-usb-console', 'opt/disc-boot/mq_ui', 'sbin/mq_ui'])
        self.assertEqual(files['opt/disc-boot/mq_ui'], ('link', 'disc-boot'))
        self.assertTrue(all(mode == 0o755 for data, mode in files.values() if data != 'link'))
        for gone in ('disc-service', 'S99disc-web', 'disc-web/', 'image.json', '/app', '/catalog'):
            self.assertFalse(any(gone in name for name in files), gone)
        hook = files['etc/init.d/S99disc-usb'][0].decode()
        self.assertIn('/opt/disc-boot/disc-usb-console --sd-mount /selected/card --sd-source /dev/selected1 --udc controller', hook)
        early = files['etc/init.d/S22disc-boot'][0].decode()
        self.assertIn('/opt/disc-boot/disc-boot early --profile 2.57 --card /selected/card --card-source /dev/selected1', early)
        start = files['etc/init.d/S99disc-boot'][0].decode()
        self.assertIn('start) /opt/disc-boot/disc-boot start', start)
        self.assertIn('stop) /opt/disc-boot/disc-boot stop', start)
        import subprocess
        for name in ('etc/init.d/S22disc-boot', 'etc/init.d/S99disc-boot', 'etc/init.d/S99disc-usb', 'sbin/mq_ui', 'opt/disc-boot/boot-report.sh'):
            with self.subTest(name=name):
                checked = subprocess.run(['sh', '-n'], input=files[name][0], capture_output=True)
                self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_the_ui_wrapper_leaves_stock_unless_the_boot_layer_chose_a_package(self):
        wrapper = candidate.ui_wrapper()
        lines = [line for line in wrapper.splitlines() if line and not line.startswith('#')]
        self.assertEqual(lines, ['[ -f /run/disc-boot/ui-launch ] && [ -x /opt/disc-boot/mq_ui ] && exec /opt/disc-boot/mq_ui "$@"',
                                 'exec /usr/bin/mq_ui "$@"'])

    def test_the_fixture_build_of_the_boot_program_cannot_be_packaged(self):
        path=self.elf();candidate.check_boot_binary(path)
        path.write_bytes(path.read_bytes()+b'DISC_BOOT_FIXTURE_ROOT')
        with self.assertRaises(ValueError):candidate.check_boot_binary(path)

    def test_only_listed_additions_and_no_stock_changes(self):
        files = self.payload()
        before = {'usr/bin/mq_ui':{'sha256':'original'}, 'opt':{}, 'etc':{}, 'etc/init.d':{}}
        additions = candidate.additions_of(files, before)
        # Folders stock lacks are additions too; existing ones are not.
        self.assertEqual(additions, set(files) | {'opt/disc-boot', 'sbin'})
        after = {**before, **{key:{} for key in additions}}
        candidate.check_delta(before, after, additions)
        for corrupted in (dict(after,extra={}), {k:v for k,v in after.items() if k!='usr/bin/mq_ui'},
                          {**after,'usr/bin/mq_ui':{'sha256':'replaced'}},
                          {k:v for k,v in after.items() if k!='opt/disc-boot/boot-report.sh'}):
            with self.assertRaises(ValueError):candidate.check_delta(before, corrupted, additions)

    def test_inventory_preserves_symlink_without_reading_target(self):
        outside=self.root/'outside';outside.write_text('not firmware')
        tree=self.root/'tree';tree.mkdir();(tree/'link').symlink_to(outside)
        result=candidate.inventory(tree)
        self.assertEqual(result['link']['target'],str(outside));self.assertNotIn('sha256',result['link'])

    def test_usb_fixture_binary_cannot_be_packaged(self):
        path=self.elf();candidate.check_usb_binary(path)
        path.write_bytes(path.read_bytes()+b'--fixture-root')
        with self.assertRaises(ValueError):candidate.check_usb_binary(path)

if __name__=='__main__':unittest.main()
