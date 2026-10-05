"""Offline image invariants, using synthetic bytes; no firmware or devices."""
import importlib.util
import os
from pathlib import Path
import struct
import subprocess
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
                                         'opt/disc-boot/disc-usb-console', 'opt/disc-boot/guard/rm',
                                         'opt/disc-boot/mq_player', 'opt/disc-boot/mq_ui', 'sbin/mq_player', 'sbin/mq_ui'])
        self.assertEqual(files['opt/disc-boot/mq_ui'], ('link', 'disc-boot'))
        self.assertEqual(files['opt/disc-boot/mq_player'], ('link', 'disc-boot'))
        self.assertIn("card='/selected/card'", files['opt/disc-boot/guard/rm'][0].decode())
        self.assertTrue(all(mode == 0o755 for data, mode in files.values() if data != 'link'))
        for gone in ('disc-service', 'S99disc-web', 'disc-web/', 'image.json', '/app', '/catalog'):
            self.assertFalse(any(gone in name for name in files), gone)
        hook = files['etc/init.d/S99disc-usb'][0].decode()
        self.assertIn('/opt/disc-boot/disc-usb-console --sd-mount /selected/card --sd-source /dev/selected1 --udc controller', hook)
        early = files['etc/init.d/S22disc-boot'][0].decode()
        self.assertIn('/opt/disc-boot/disc-boot early --profile 2.57 --card /selected/card --card-source /dev/selected1', early)
        self.assertIn('log=/usr/data/disc-boot/boot.log', early)
        start = files['etc/init.d/S99disc-boot'][0].decode()
        self.assertIn('start)\n    /opt/disc-boot/disc-boot start', start)
        self.assertIn('stop) /opt/disc-boot/disc-boot stop', start)
        import subprocess
        for name in ('etc/init.d/S22disc-boot', 'etc/init.d/S99disc-boot', 'etc/init.d/S99disc-usb', 'sbin/mq_ui',
                     'sbin/mq_player', 'opt/disc-boot/guard/rm', 'opt/disc-boot/boot-report.sh'):
            with self.subTest(name=name):
                checked = subprocess.run(['sh', '-n'], input=files[name][0], capture_output=True)
                self.assertEqual(checked.returncode, 0, checked.stderr)

    LOG = ['log=/usr/data/disc-boot/boot.log; up=; read -r up _ 2>/dev/null </proc/uptime',
           '{ [ ! -f $log ] || [ "$(wc -c <$log)" -lt 262144 ]; } 2>/dev/null && '
           'echo "$up NAME$([ -f /run/disc-boot/ui-launch ] && echo \' ui-launch\')" 2>/dev/null >>$log']

    def test_the_ui_wrapper_leaves_stock_unless_the_boot_layer_chose_a_package(self):
        wrapper = candidate.ui_wrapper()
        lines = [line for line in wrapper.splitlines() if line and not line.startswith('#')]
        # Every start under the bare name stock's watch loop looks for: pgrep -x matches argv[0].
        self.assertEqual(lines, ['PATH=/opt/disc-boot/guard:$PATH; export PATH',
                                 *(line.replace('NAME', 'mq_ui') for line in self.LOG),
                                 'named=; (exec -a true true) 2>/dev/null && named=1',
                                 'if [ -f /run/disc-boot/ui-launch ] && [ -x /opt/disc-boot/mq_ui ]; then',
                                 '  [ -n "$named" ] && exec -a mq_ui /opt/disc-boot/mq_ui "$@"',
                                 '  exec /opt/disc-boot/mq_ui "$@"',
                                 'fi',
                                 '[ -n "$named" ] && exec -a mq_ui /usr/bin/mq_ui "$@"',
                                 'exec /usr/bin/mq_ui "$@"'])

    def test_the_player_wrapper_puts_the_guard_first_and_ends_in_stock_player(self):
        wrapper = candidate.player_wrapper()
        lines = [line for line in wrapper.splitlines() if line and not line.startswith('#')]
        # Started here, stock's player marks that a player ran in this boot (it runs the watchdog).
        self.assertEqual(lines, ['PATH=/opt/disc-boot/guard:$PATH; export PATH',
                                 *(line.replace('NAME', 'mq_player') for line in self.LOG),
                                 'named=; (exec -a true true) 2>/dev/null && named=1',
                                 'if [ -f /run/disc-boot/ui-launch ] && [ ! -f /run/disc-boot/ui/fallback ] && '
                                 '[ -x /opt/disc-boot/mq_player ]; then',
                                 '  [ -n "$named" ] && exec -a mq_player /opt/disc-boot/mq_player "$@"',
                                 '  exec /opt/disc-boot/mq_player "$@"',
                                 'fi',
                                 'true 2>/dev/null >/run/disc-boot/player-ran',
                                 '[ -n "$named" ] && exec -a mq_player /usr/bin/mq_player "$@"',
                                 'exec /usr/bin/mq_player "$@"'])
        self.assertEqual(candidate.GUARD.rsplit('/', 1), ['opt/disc-boot/guard', 'rm'])

    def test_the_card_guard_needs_a_plain_mount_point(self):
        for mount in ('/tmp/sdcard/', '/tmp//sdcard', 'tmp/sdcard', '/'):
            with self.subTest(mount), self.assertRaises(ValueError):
                candidate.card_guard({'sd_mount': mount})

    def guard(self, command, cwd=None):
        """A command through sh with the rendered guard first in PATH, as stock's player runs it."""
        guard = self.root/'guard'
        guard.mkdir(exist_ok=True)
        (guard/'rm').write_text(candidate.card_guard({'sd_mount': str(self.card)}))
        (guard/'rm').chmod(0o755)
        env = dict(os.environ, PATH=f'{guard}:{os.environ["PATH"]}')
        return subprocess.run(['sh', '-c', command], env=env, cwd=cwd or self.root, capture_output=True, timeout=30)

    def card_tree(self):
        return sorted(str(p.relative_to(self.card)) for p in self.card.rglob('*')) if self.card.exists() else None

    def test_the_card_guard_refuses_the_mount_point_and_passes_the_rest(self):
        # The real path: macOS's temporary folder is reached through a link.
        self.root = self.root.resolve()
        self.card = self.root/'mnt/card'
        full = ['a', 'folder', 'folder/b']
        def fill():
            (self.card/'folder').mkdir(parents=True, exist_ok=True)
            (self.card/'a').write_text('a'); (self.card/'folder/b').write_text('b')
        fill()
        (self.root/'alias').symlink_to(self.card)
        refused = [f'rm -rf {self.card}', f'rm -rf {self.card}/', f'rm -rf {self.root}/mnt//card', f'rm -rf -- {self.card}',
                   f'rm -rf {self.card}/folder/..', f'rm -rf {self.root}/mnt', 'rm -rf card', f'rm -rf {self.root}/alias',
                   f'rm -r -f {self.card} {self.root}/elsewhere']
        for command in refused:
            with self.subTest(command):
                result = self.guard(command, cwd=self.root/'mnt')
                self.assertEqual((result.returncode, self.card_tree()), (0, full), result.stderr)
        # What lies on the card, and anything elsewhere, goes to the real rm.
        (self.root/'elsewhere').mkdir()
        self.assertEqual(self.guard(f'rm -rf {self.root}/elsewhere').returncode, 0)
        self.assertFalse((self.root/'elsewhere').exists())
        self.assertEqual(self.guard(f'rm -rf {self.card}/folder').returncode, 0)
        self.assertEqual(self.card_tree(), ['a'])
        self.assertEqual(self.guard(f'rm {self.card}/a').returncode, 0)
        self.assertNotEqual(self.guard(f'rm {self.card}/missing').returncode, 0, 'the real rm answers')
        # An empty mount point (the card unmounted) goes, as stock meant; a non-empty one stays.
        fill()
        self.assertEqual(self.guard(f'rm -rf {self.card}').returncode, 0)
        self.assertEqual(self.card_tree(), full)
        subprocess.run(['/bin/rm', '-rf', str(self.card/'a'), str(self.card/'folder')], check=True)
        self.assertEqual(self.guard(f'rm -rf {self.card}').returncode, 0)
        self.assertFalse(self.card.exists())

    def test_the_fixture_build_of_the_boot_program_cannot_be_packaged(self):
        path=self.elf();candidate.check_boot_binary(path)
        path.write_bytes(path.read_bytes()+b'DISC_BOOT_FIXTURE_ROOT')
        with self.assertRaises(ValueError):candidate.check_boot_binary(path)

    def test_only_listed_additions_and_no_stock_changes(self):
        files = self.payload()
        before = {'usr/bin/mq_ui':{'sha256':'original'}, 'opt':{}, 'etc':{}, 'etc/init.d':{}}
        additions = candidate.additions_of(files, before)
        # Folders stock lacks are additions too; existing ones are not.
        self.assertEqual(additions, set(files) | {'opt/disc-boot', 'opt/disc-boot/guard', 'sbin'})
        after = {**before, **{key:{} for key in additions}}
        candidate.check_delta(before, after, additions)
        for corrupted in (dict(after,extra={}), {k:v for k,v in after.items() if k!='usr/bin/mq_ui'},
                          {**after,'usr/bin/mq_ui':{'sha256':'replaced'}},
                          {k:v for k,v in after.items() if k!='opt/disc-boot/boot-report.sh'}):
            with self.assertRaises(ValueError):candidate.check_delta(before, corrupted, additions)

    LISTING = ('drwxr-xr-x 0/0                     270 2026-09-08 13:50 \n'
               'drwxr-xr-x 0/0                    1037 2026-09-09 09:14 /bin\n'
               'lrwxrwxrwx 0/0                       7 2026-09-08 13:19 /bin/ash -> busybox\n'
               '-rwsr-xr-x 0/0                  800000 2026-09-08 13:19 /bin/busybox\n'
               'crw-rw-rw- 0/0                   1,  3 2026-09-08 13:19 /dev/null\n'
               'drwxr-xr-x 1001/1001                 3 2026-09-08 13:19 /run/dbus\n'
               '-rw-rw-r-- 0/0                      12 2026-09-08 13:19 /etc/a file\n'
               'drwxrwxrwt 0/0                       3 2026-09-08 13:19 /tmp\n')

    def test_a_squashfs_listing_keeps_every_mode_bit_and_owner(self):
        entries = candidate.parse_listing(self.LISTING)
        self.assertEqual(entries['/'], ('d', 'drwxr-xr-x', '0/0', None, None))
        self.assertEqual(entries['/bin/ash'], ('l', 'l', '0/0', '7', 'busybox'))
        self.assertEqual(entries['/bin/busybox'], ('-', '-rwsr-xr-x', '0/0', '800000', None))
        self.assertEqual(entries['/dev/null'], ('c', 'crw-rw-rw-', '0/0', '1,3', None))
        self.assertEqual(entries['/run/dbus'][2], '1001/1001')
        self.assertEqual(entries['/etc/a file'][1], '-rw-rw-r--')
        with self.assertRaises(ValueError):
            candidate.parse_listing('-rwxr-xr-x root/root 1 2026-09-08 13:19 /bin/x\n')

    def test_the_image_keeps_stock_entries_exactly(self):
        """The second write's image (2026-10-05): built on a macOS share, stock's setuid BusyBox
        became 0755, group-writable files lost the bit and every owner became root."""
        stock = candidate.parse_listing(self.LISTING)
        added = 'drwxr-xr-x 0/0 3 2026-10-05 10:00 /opt/disc-boot\n-rwxr-xr-x 0/0 9 2026-10-05 10:00 /opt/disc-boot/disc-boot\n'
        built = candidate.parse_listing(self.LISTING.replace('1037 2026-09-09', '1100 2026-10-05') + added)
        candidate.check_listing(stock, built, {'opt/disc-boot', 'opt/disc-boot/disc-boot'})
        for old, new in (('-rwsr-xr-x 0/0', '-rwxr-xr-x 0/0'), ('1001/1001', '0/0     '), ('-rw-rw-r--', '-rw-r--r--'),
                         ('drwxrwxrwt', 'drwxrwxrwx'), ('-> busybox', '-> /bin/busybox'), ('/dev/null', '/dev/zero')):
            with self.subTest(change=new):
                with self.assertRaises(ValueError):
                    candidate.check_listing(stock, candidate.parse_listing(self.LISTING.replace(old, new) + added),
                                            {'opt/disc-boot', 'opt/disc-boot/disc-boot'})
        with self.assertRaises(ValueError):
            candidate.check_listing(stock, built, {'opt/disc-boot'})

    def test_a_file_system_that_changes_the_unpacked_tree_is_refused(self):
        tree = self.root/'tree'
        (tree/'bin').mkdir(parents=True)
        (tree/'bin/tool').write_bytes(b'x' * 5)
        (tree/'bin/tool').chmod(0o775)
        (tree/'bin/sh').symlink_to('tool')
        owner = f'{os.getuid()}/{os.getgid()}'
        stock = candidate.parse_listing(f'drwxr-xr-x {owner} 30 2026-09-08 13:50 \n'
                                        f'{candidate.stat.filemode((tree/"bin").stat().st_mode)} {owner} 40 2026-09-08 13:50 /bin\n'
                                        f'lrwxrwxrwx {owner} 4 2026-09-08 13:50 /bin/sh -> tool\n'
                                        f'-rwxrwxr-x {owner} 5 2026-09-08 13:50 /bin/tool\n')
        candidate.check_extraction(stock, candidate.tree_listing(tree))
        (tree/'bin/tool').chmod(0o755)
        with self.assertRaises(ValueError):
            candidate.check_extraction(stock, candidate.tree_listing(tree))

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
