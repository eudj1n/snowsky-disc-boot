"""The boot program (docs/contract.md) through its fixture build, in a temporary root.

Packages are shell scripts; keys, the card's mount and stock's UI are files.
Timings are shortened (confirmation after 1 s) through the fixture's variables.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location('boot_builder', ROOT/'scripts/deployment/build_candidate.py')
BUILDER = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BUILDER)
BINARY = Path(os.environ.get('DISC_BOOT_FIXTURE_BINARY', ROOT/'build/host/disc-boot-fixture'))
PRODUCTION = Path(os.environ.get('DISC_BOOT_BINARY', ROOT/'build/host/disc-boot'))
# The fixture's own file when BINARY is a wrapper (an emulated MIPS build).
FIXTURE_FILE = Path(os.environ.get('DISC_BOOT_FIXTURE_FILE', BINARY))

GOOD = '''trap 'exit 0' TERM
: > "$DISC_BOOT_RUN/ready"
while :; do sleep 0.1; done
'''


class BootTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        for name in ('run', 'usr/data', 'usr/bin', 'sbin', 'tmp/sdcard', 'proc', 'fixture', 'out'):
            (self.root/name).mkdir(parents=True, exist_ok=True)
        (self.root/'proc/mounts').write_text('/dev/root / squashfs ro 0 0\n')
        self.env = dict(os.environ, DISC_BOOT_FIXTURE_ROOT=str(self.root),
                        DISC_BOOT_FIXTURE_TIMING='confirm=1,grace=1,window=30,backoff=0.1,card=2,ui=30')
        self.data = self.root/'usr/data/disc-boot'
        self.run_dir = self.root/'run/disc-boot'
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.boot('stop')
        subprocess.run(['pkill', '-f', str(self.root)], capture_output=True)

    def boot(self, *args, check=False):
        return subprocess.run([str(BINARY), *args], env=self.env, capture_output=True, text=True, timeout=30, check=check)

    def early(self, keys=None):
        path = self.root/'fixture/keys'
        if keys is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(keys)
        self.boot('early', '--profile', '2.57', '--card', '/tmp/sdcard', '--card-source', '/dev/mmcblk0p1', check=True)
        return json.loads((self.run_dir/'boot.json').read_text())

    def package(self, directory, script, name='disc-server', version='1', role='service', entry=None, edit=None, extra=None):
        directory.mkdir(parents=True, exist_ok=True)
        entry = entry or ('bin/mq_ui' if role == 'ui' else 'bin/run')
        files = {entry: ('#!/bin/sh\n' + script, 0o755), **(extra or {})}
        listed = {}
        for path, (text, mode) in files.items():
            target = directory/path
            target.parent.mkdir(parents=True, exist_ok=True)
            data = text.encode()
            target.write_bytes(data)
            target.chmod(mode)
            listed[path] = dict(size=len(data), sha256=hashlib.sha256(data).hexdigest(), mode=f'{mode:04o}')
        manifest = dict(schema=1, name=name, version=version, role=role, bootApi=1, arch='fixture',
                        profiles=['2.57'], entry=entry, args=[], ready=5, files=listed)
        if edit:
            edit(manifest)
        (directory/'package.json').write_text(json.dumps(manifest))
        return manifest

    def install(self, role, slot, script, confirmed=False, previous=None, **kwargs):
        manifest = self.package(self.data/role/slot, script, role=role, **kwargs)
        self.state(role, slot, confirmed, previous)
        return manifest

    def state(self, role, current, confirmed=False, previous=None):
        """The role's state as boot writes it: a previous slot keeps its package.json's fingerprint."""
        (self.data/role).mkdir(parents=True, exist_ok=True)
        (self.data/role/'state.json').write_text(json.dumps(dict(schema=1, current=current, confirmed=confirmed, previous=previous,
                                                                 previousManifest=self.fingerprint(role, previous))))

    def fingerprint(self, role, slot):
        path = self.data/role/str(slot)/'package.json'
        return hashlib.sha256(path.read_bytes()).hexdigest() if slot and path.exists() else None

    def role_state(self, role):
        return json.loads((self.data/role/'state.json').read_text())

    def status(self, role):
        try:
            return json.loads((self.run_dir/f'{role}.json').read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def wait_status(self, role, state, timeout=15):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            current = self.status(role)
            if current and current['state'] == state:
                return current
            time.sleep(0.05)
        self.fail(f'{role} never reached {state}: {self.status(role)}; log: {self.log()}')

    def wait_for(self, condition, role='service', timeout=15):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            current = self.status(role)
            if current and condition(current):
                return current
            time.sleep(0.05)
        self.fail(f'{role}: {self.status(role)}; log: {self.log()}')

    def log(self):
        path = self.run_dir/'boot.log'
        return path.read_text() if path.exists() else ''

    def global_state(self):
        return json.loads((self.data/'state.json').read_text())

    # Modes and the boot-loop guard

    def test_modes_follow_the_default_and_the_keys(self):
        self.assertEqual([self.early()[k] for k in ('mode', 'reason')], ['platform', 'default'])
        self.assertFalse(self.early()['keys']['read'])
        self.assertEqual([self.early('volume-up')[k] for k in ('mode', 'reason')], ['stock', 'key'])
        self.assertEqual([self.early('play')[k] for k in ('mode', 'reason')], ['platform', 'recovery'])
        self.assertEqual([self.early('volume-up play')[k] for k in ('mode', 'reason')], ['platform', 'recovery'])
        (self.data).mkdir(parents=True, exist_ok=True)
        (self.data/'state.json').write_text('{"schema":1,"default":"stock","unconfirmed":0}')
        self.assertEqual([self.early('')[k] for k in ('mode', 'reason')], ['stock', 'default'])
        self.assertEqual([self.early('volume-up')[k] for k in ('mode', 'reason')], ['platform', 'key'])

    def test_unconfirmed_platform_boots_fall_back_to_stock(self):
        self.early()
        self.assertFalse((self.data/'state.json').exists(), 'nothing installed, nothing counted')
        self.install('service', 'a', GOOD)
        for count in (1, 2, 3):
            self.assertEqual(self.early()['mode'], 'platform')
            self.assertEqual(self.global_state()['unconfirmed'], count)
        boot = self.early()
        self.assertEqual((boot['mode'], boot['reason']), ('stock', 'boot-loop'))
        self.assertEqual(self.global_state()['unconfirmed'], 3, 'a stock boot is not counted')
        # The key still chooses: Volume Up from the platform default means stock, Play means recovery.
        self.assertEqual(self.early('play')['mode'], 'platform')

    # Manifests

    def verify(self, directory, role='service'):
        result = self.boot('verify', role, str(directory), '--profile', '2.57')
        return result.returncode, json.loads(result.stdout)

    def test_a_package_is_what_its_manifest_lists(self):
        good = self.root/'pkg'
        self.package(good, GOOD, extra={'lib/libx.so': ('x', 0o644)})
        self.assertEqual(self.verify(good), (0, dict(ok=True, name='disc-server', version='1', bytes=len('#!/bin/sh\n' + GOOD) + 1)))
        cases = {
            'does not match its sha256': lambda d: (d/'bin/run').write_text('#!/bin/sh\n' + GOOD.replace('0.1', '0.2')),
            'has 3 bytes': lambda d: (d/'lib/libx.so').write_text('xyz'),
            'is not listed': lambda d: (d/'extra').write_text('unlisted'),
            'not a regular file': lambda d: (d/'link').symlink_to(d/'bin/run'),
            'has mode': lambda d: (d/'lib/libx.so').chmod(0o600),
        }
        for message, damage in cases.items():
            with self.subTest(message):
                directory = self.root/'case'
                subprocess.run(['rm', '-rf', str(directory)])
                self.package(directory, GOOD, extra={'lib/libx.so': ('x', 0o644)})
                damage(directory)
                code, result = self.verify(directory)
                self.assertEqual(code, 1)
                self.assertIn(message, result['error'])

    def test_manifest_bounds_and_fit(self):
        def big(m):
            m['files'] = {f'f{i}': dict(size=1, sha256='0'*64, mode='0644') for i in range(257)}
        cases = {
            'name must match': lambda m: m.update(name='Disc Server'),
            'role must be': lambda m: m.update(role='daemon'),
            'bootApi must be': lambda m: m.update(bootApi=0),
            'needs boot API 2': lambda m: m.update(bootApi=2),
            'built for mips32el': lambda m: m.update(arch='mips32el-linux-static'),
            'does not support firmware profile 2.57': lambda m: m.update(profiles=['2.58']),
            'ready must be': lambda m: m.update(ready=121),
            'unsafe path': lambda m: m['files'].update({'../escape': dict(size=1, sha256='0'*64, mode='0644')}),
            'files must list 1-256': big,
            'exceeds 32 MiB': lambda m: m['files'].update({'big': dict(size=33*1024*1024, sha256='0'*64, mode='0644')}),
            'mode 0755 or 0644': lambda m: m['files']['bin/run'].update(mode='0777'),
            'entry must be a listed file with mode 0755': lambda m: m.update(entry='bin/other'),
            "role is service, not ui": None,
        }
        for message, edit in cases.items():
            with self.subTest(message):
                directory = self.root/'case'
                subprocess.run(['rm', '-rf', str(directory)])
                self.package(directory, GOOD, edit=edit)
                code, result = self.verify(directory, 'ui' if edit is None else 'service')
                self.assertEqual(code, 1, result)
                self.assertIn(message, result['error'])
        directory = self.root/'ui'
        self.package(directory, GOOD, role='ui', entry='bin/launch')
        self.assertIn('named mq_ui', self.verify(directory, 'ui')[1]['error'])
        # A repeated key is refused rather than one of them believed.
        text = (directory/'package.json').read_text()
        (directory/'package.json').write_text(text[:-1] + ',"name":"other"}')
        self.assertIn('name must match', self.verify(directory, 'ui')[1]['error'])

    # The service's lifecycle

    def test_a_service_is_confirmed_with_its_environment(self):
        script = 'env > "$DISC_BOOT_DATA/env.txt"\nps -o nice= -p $$ > "$DISC_BOOT_DATA/nice.txt"\n' + GOOD
        self.install('service', 'a', script)
        self.early()
        self.assertEqual(self.global_state()['unconfirmed'], 1)
        self.boot('start', check=True)
        status = self.wait_status('service', 'confirmed')
        self.assertEqual((status['name'], status['version'], status['slot'], status['confirmed']), ('disc-server', '1', 'a', True))
        self.assertEqual(self.role_state('service'), dict(schema=1, current='a', confirmed=True, previous=None, previousManifest=None))
        self.assertEqual(self.global_state()['unconfirmed'], 0)
        env = dict(line.split('=', 1) for line in (self.data/'data/disc-server/env.txt').read_text().splitlines() if '=' in line)
        slot = str(self.data/'service/a')
        self.assertEqual(env['DISC_BOOT_ROLE'], 'service')
        self.assertEqual(env['DISC_BOOT_PROFILE'], '2.57')
        self.assertEqual(env['DISC_BOOT_SLOT'], slot)
        self.assertEqual(env['DISC_BOOT_INACTIVE'], str(self.data/'service/b'))
        self.assertEqual(env['DISC_BOOT_REQUEST'], str(self.data/'service/request'))
        self.assertEqual(env['DISC_BOOT_DATA'], str(self.data/'data/disc-server'))
        self.assertEqual(env['DISC_BOOT_RUN'], str(self.run_dir/'service'))
        self.assertEqual(env['DISC_BOOT_CARD'], str(self.root/'tmp/sdcard'))
        self.assertTrue(env['LD_LIBRARY_PATH'].startswith(slot + '/lib:/usr/lib:'))
        self.assertNotIn('DISC_BOOT_FIXTURE_ROOT', env, 'a package starts from a clean environment')
        self.assertEqual(int((self.data/'data/disc-server/nice.txt').read_text()), 5)
        self.boot('stop')
        self.wait_status('service', 'stopped')
        self.assertFalse((self.run_dir/'supervisor.pid').exists())

    def test_a_failing_new_version_gives_way_to_the_previous_one(self):
        self.package(self.data/'service/a', GOOD, version='1')
        self.install('service', 'b', 'exit 3\n', version='2', previous='a')
        self.early()
        self.boot('start', check=True)
        status = self.wait_status('service', 'confirmed')
        self.assertEqual((status['version'], status['slot']), ('1', 'a'))
        self.assertEqual(self.role_state('service'), dict(schema=1, current='a', confirmed=True, previous=None, previousManifest=None))

    def test_a_failing_first_version_stops_and_says_why(self):
        self.install('service', 'a', 'exit 3\n')
        self.early()
        self.boot('start', check=True)
        self.assertIn('exited before its confirmation', self.wait_status('service', 'failed')['note'])
        self.install('service', 'a', 'trap "exit 0" TERM\nwhile :; do sleep 0.1; done\n', edit=lambda m: m.update(ready=1))
        (self.run_dir/'service.json').unlink()
        self.boot('start', check=True)
        self.assertIn('not ready in time', self.wait_status('service', 'failed')['note'])

    def test_a_confirmed_service_restarts_within_bounds(self):
        self.install('service', 'a', 'echo run >> "$DISC_BOOT_DATA/runs"\n: > "$DISC_BOOT_RUN/ready"\nsleep 0.3\nexit 1\n', confirmed=True)
        self.early()
        self.boot('start', check=True)
        self.assertIn('restarted too often', self.wait_status('service', 'failed')['note'])
        self.assertEqual(len((self.data/'data/disc-server/runs').read_text().split()), 4)

    def test_an_update_is_staged_activated_and_confirmed(self):
        staged = self.root/'staged'
        self.package(staged, GOOD, version='2')
        update = f'''if [ ! -e "$DISC_BOOT_DATA/updated" ]; then
  : > "$DISC_BOOT_DATA/updated"
  : > "$DISC_BOOT_RUN/ready"
  cp -Rp "{staged}/." "$DISC_BOOT_INACTIVE/"
  printf '{{"action":"activate"}}' > "$DISC_BOOT_REQUEST"
  exit 0
fi
''' + GOOD
        self.install('service', 'a', update, version='1', confirmed=True)
        self.early()
        self.boot('start', check=True)
        status = self.wait_status('service', 'confirmed')
        self.assertEqual((status['version'], status['slot'], status['lastRequest']), ('2', 'b', 'activated disc-server 2'))
        self.assertEqual(self.role_state('service'), dict(schema=1, current='b', confirmed=True, previous='a',
                                                          previousManifest=self.fingerprint('service', 'a')))
        self.assertEqual(status['previous'], dict(slot='a', name='disc-server', version='1', manifest=self.fingerprint('service', 'a')))
        # Asked to go back, it returns to the confirmed previous version.
        (self.data/'service/request').write_text('{"action":"rollback"}')
        subprocess.run(['pkill', '-f', str(self.data/'service/b')])
        status = self.wait_for(lambda s: s['slot'] == 'a' and s['state'] == 'confirmed')
        self.assertEqual((status['version'], status['lastRequest']), ('1', 'rolled back to disc-server 1'))
        self.assertEqual(self.role_state('service'), dict(schema=1, current='a', confirmed=True, previous=None, previousManifest=None))

    def test_a_rollback_returns_only_to_the_version_confirmed_there(self):
        # An update staged into the inactive slot replaces the previous version: no rollback to it,
        # neither asked for nor after a failure of the tentative version.
        staged = self.root/'staged'
        self.package(staged, GOOD, version='3')
        stage = f'cp -Rp "{staged}/." "$DISC_BOOT_INACTIVE/"\n'
        on_demand = f'''trap 'exit 0' TERM
: > "$DISC_BOOT_RUN/ready"
while :; do
  if [ -e "$DISC_BOOT_DATA/stage" ]; then rm "$DISC_BOOT_DATA/stage"; {stage}  fi
  sleep 0.1
done
'''
        self.package(self.data/'service/a', GOOD, version='1')
        self.install('service', 'b', on_demand, version='2', confirmed=True, previous='a')
        self.early()
        self.boot('start', check=True)
        status = self.wait_status('service', 'confirmed')
        self.assertEqual(status['previous']['version'], '1')
        (self.data/'data/disc-server').mkdir(parents=True, exist_ok=True)
        (self.data/'data/disc-server/stage').touch()
        until = time.monotonic() + 10
        while json.loads((self.data/'service/a/package.json').read_text())['version'] != '3' and time.monotonic() < until:
            time.sleep(0.05)
        (self.data/'service/request').write_text('{"action":"rollback"}')
        subprocess.run(['pkill', '-f', str(self.data/'service/b')])
        status = self.wait_for(lambda s: s['lastRequest'] is not None and s['state'] == 'confirmed')
        self.assertEqual((status['slot'], status['version'], status['lastRequest'], status['previous']),
                         ('b', '2', 'rollback refused: the previous version was replaced', None))
        self.boot('stop', check=True)
        # A tentative version whose fallback was replaced stops and says why, rather than run the stage.
        self.install('service', 'b', stage + 'exit 3\n', version='2', previous='a')
        self.package(self.data/'service/a', GOOD, version='1')
        self.state('service', 'b', previous='a')
        (self.run_dir/'service.json').unlink()
        self.boot('start', check=True)
        status = self.wait_status('service', 'failed')
        self.assertEqual((status['slot'], status['note']), ('b', 'exited before its confirmation'))
        self.assertEqual(json.loads((self.data/'service/a/package.json').read_text())['version'], '3')

    def test_a_broken_update_is_refused_and_the_current_version_runs_on(self):
        update = '''if [ ! -e "$DISC_BOOT_DATA/updated" ]; then
  : > "$DISC_BOOT_DATA/updated"
  printf '{"schema":1}' > "$DISC_BOOT_INACTIVE/package.json"
  printf '{"action":"activate"}' > "$DISC_BOOT_REQUEST"
  exit 0
fi
''' + GOOD
        self.install('service', 'a', update, confirmed=True)
        self.early()
        self.boot('start', check=True)
        status = self.wait_status('service', 'confirmed')
        self.assertEqual(status['slot'], 'a')
        self.assertTrue(status['lastRequest'].startswith('activate refused: name must match'), status)
        self.assertEqual(status['failures'], 0)

    def test_default_and_remove_requests(self):
        script = '''if [ ! -e "$DISC_BOOT_DATA/asked" ]; then
  : > "$DISC_BOOT_DATA/asked"
  printf '{"action":"default","mode":"stock"}' > "$DISC_BOOT_REQUEST"
  exit 0
fi
printf '{"action":"remove","purge":true}' > "$DISC_BOOT_REQUEST"
exit 0
'''
        self.install('service', 'a', script, confirmed=True)
        self.early()
        self.boot('start', check=True)
        status = self.wait_status('service', 'absent')
        self.assertEqual(status['lastRequest'], 'removed with its data')
        self.assertEqual(self.global_state()['default'], 'stock')
        self.assertFalse((self.data/'service/a').exists())
        self.assertFalse((self.data/'data/disc-server').exists())
        self.assertFalse((self.data/'service/state.json').exists())

    def test_the_package_log_is_capped(self):
        self.install('service', 'a', 'head -c 100000 /dev/zero | tr "\\0" x\n' + GOOD)
        self.early()
        self.boot('start', check=True)
        self.wait_status('service', 'confirmed')
        self.assertLessEqual((self.run_dir/'service/log').stat().st_size, 65536)

    def test_an_unreadable_state_runs_nothing(self):
        self.install('service', 'a', GOOD)
        (self.data/'service/state.json').write_text('{"schema":1,"current":"c"}')
        (self.data/'state.json').write_text('not json')
        boot = self.early()
        self.assertEqual((boot['mode'], boot['stateReadable']), ('platform', False))
        self.boot('start', check=True)
        self.assertIn('unreadable', self.wait_status('service', 'failed')['note'])

    def test_stock_mode_starts_nothing(self):
        self.install('service', 'a', GOOD)
        self.early('volume-up')
        self.boot('start', check=True)
        self.assertEqual(self.status('service')['state'], 'stock-mode')
        self.assertFalse((self.run_dir/'supervisor.pid').exists())

    # Recovery from the card

    def staged(self, role):
        return self.root/'tmp/sdcard/.disc/boot/install'/role

    def test_play_installs_the_staged_packages(self):
        (self.root/'proc/mounts').write_text('/dev/mmcblk0p1 /tmp/sdcard exfat rw 0 0\n')
        self.package(self.staged('service'), GOOD, version='7')
        broken = self.staged('ui')
        self.package(broken, GOOD, role='ui', name='other-ui')
        (broken/'bin/mq_ui').write_text('changed')
        self.early('play')
        self.boot('start', check=True)
        status = self.wait_status('service', 'confirmed')
        self.assertEqual((status['version'], status['slot']), ('7', 'a'))
        result = json.loads((self.root/'tmp/sdcard/.disc/boot/result.json').read_text())
        self.assertEqual(result['roles']['service'], dict(installed=True, note='installed disc-server 7'))
        self.assertFalse(result['roles']['ui']['installed'])
        self.assertIn('bin/mq_ui has 7 bytes', result['roles']['ui']['note'])
        self.assertFalse(self.staged('service').exists())
        self.assertTrue(broken.exists(), 'a refused package stays on the card')
        self.assertEqual(oct((self.data/'service/a/bin/run').stat().st_mode & 0o777), '0o755')

    def test_recovery_needs_the_expected_card(self):
        (self.root/'proc/mounts').write_text('/dev/other /tmp/sdcard exfat rw 0 0\n')
        self.package(self.staged('service'), GOOD)
        self.early('play')
        self.boot('start', check=True)
        self.wait_status('service', 'absent')
        self.assertTrue(self.staged('service').exists())
        self.assertFalse((self.root/'tmp/sdcard/.disc/boot/result.json').exists())
        self.assertIn('the card is not mounted', self.log())

    def test_nothing_is_taken_from_the_card_without_play(self):
        (self.root/'proc/mounts').write_text('/dev/mmcblk0p1 /tmp/sdcard exfat rw 0 0\n')
        self.package(self.staged('service'), GOOD)
        self.early()
        self.boot('start', check=True)
        time.sleep(0.5)
        self.assertTrue(self.staged('service').exists())
        self.assertFalse((self.data/'service').exists())

    # The ui role

    def launch(self):
        """Stock's start of its UI: the image's /sbin/mq_ui wrapper, with the fixture's paths."""
        launcher = self.root/'opt/disc-boot/mq_ui'
        if not launcher.exists():
            launcher.parent.mkdir(parents=True, exist_ok=True)
            launcher.symlink_to(BINARY)
        wrapper = self.root/'sbin/mq_ui'
        text = BUILDER.ui_wrapper()
        for path in ('/run/disc-boot/ui-launch', '/opt/disc-boot/mq_ui', '/usr/bin/mq_ui'):
            text = text.replace(path, str(self.root) + path)
        wrapper.write_text(text)
        wrapper.chmod(0o755)
        return subprocess.Popen(['/bin/sh', str(wrapper)], env=self.env, start_new_session=True)

    def stock_ui(self):
        stock = self.root/'usr/bin/mq_ui'
        stock.write_text(f'#!/bin/sh\necho stock >> "{self.root}/out/ui"\n')
        stock.chmod(0o755)

    def runs(self):
        path = self.root/'out/ui'
        return path.read_text().split() if path.exists() else []

    def test_the_launcher_runs_the_ui_package_and_confirms_it(self):
        self.stock_ui()
        self.install('ui', 'a', f'echo package >> "{self.root}/out/ui"\n: > "$DISC_BOOT_RUN/ready"\nsleep 2.5\n', name='other-ui')
        self.early()
        self.launch().wait(timeout=10)
        self.assertEqual(self.runs(), ['package'])
        status = self.wait_status('ui', 'confirmed')
        self.assertEqual((status['name'], status['slot']), ('other-ui', 'a'))
        self.assertTrue(self.role_state('ui')['confirmed'])
        self.early('volume-up')
        self.launch().wait(timeout=10)
        self.assertEqual(self.runs(), ['package', 'stock'])

    def test_stock_runs_without_the_boot_program_when_no_package_was_chosen(self):
        self.stock_ui()
        self.install('ui', 'a', f'echo package >> "{self.root}/out/ui"\n', name='other-ui')
        self.early('volume-up')
        self.assertFalse((self.run_dir/'ui-launch').exists())
        # Even a launcher that cannot run is never reached: stock's UI starts from the wrapper alone.
        self.launch().wait(timeout=10)
        (self.root/'opt/disc-boot/mq_ui').unlink()
        (self.root/'opt/disc-boot/mq_ui').write_text('#!/bin/sh\nexit 99\n')
        self.launch().wait(timeout=10)
        self.assertEqual(self.runs(), ['stock', 'stock'])
        self.assertIsNone(self.status('ui'), 'the boot program never ran')
        self.early()
        self.assertEqual((self.run_dir/'ui-launch').read_text(), 'ui\n')

    def test_without_a_ui_package_stock_runs(self):
        self.stock_ui()
        self.early()
        self.launch().wait(timeout=10)
        self.assertEqual(self.runs(), ['stock'])
        self.assertFalse((self.run_dir/'ui-launch').exists())
        self.assertIsNone(self.status('ui'), 'no package: the wrapper starts stock on its own')

    def test_a_crashing_ui_package_falls_back_to_stock(self):
        self.stock_ui()
        self.install('ui', 'a', f'echo package >> "{self.root}/out/ui"\nexit 1\n', name='other-ui')
        self.early()
        for _ in range(5):
            self.launch().wait(timeout=10)
        self.assertEqual(self.runs(), ['package', 'package', 'package', 'stock', 'stock'])
        self.assertEqual(self.status('ui')['state'], 'fallback')

    def test_a_crashing_new_ui_gives_way_to_the_previous_one(self):
        self.stock_ui()
        self.package(self.data/'ui/a', f'echo old >> "{self.root}/out/ui"\nexit 1\n', role='ui', name='other-ui')
        self.install('ui', 'b', f'echo new >> "{self.root}/out/ui"\nexit 1\n', name='other-ui', previous='a')
        self.early()
        for _ in range(4):
            self.launch().wait(timeout=10)
        self.assertEqual(self.runs(), ['new', 'new', 'new', 'old'])
        self.assertEqual(self.role_state('ui')['current'], 'a')

    def test_a_ui_request_applies_at_its_next_start(self):
        self.stock_ui()
        self.install('ui', 'a', f'echo package >> "{self.root}/out/ui"\nprintf \'{{"action":"remove"}}\' > "$DISC_BOOT_REQUEST"\n', name='other-ui', confirmed=True)
        self.early()
        self.launch().wait(timeout=10)
        self.launch().wait(timeout=10)
        self.assertEqual(self.runs(), ['package', 'stock'])
        self.assertFalse((self.data/'ui/a').exists())

    def test_status_gathers_the_parts(self):
        self.early()
        status = json.loads(self.boot('status', check=True).stdout)
        self.assertEqual(status['boot']['mode'], 'platform')
        self.assertIsNone(status['service'])

    def test_the_production_build_has_no_fixture_switches(self):
        data = PRODUCTION.read_bytes()
        self.assertNotIn(b'DISC_BOOT_FIXTURE', data)
        self.assertNotIn(b'/fixture/keys', data)
        self.assertIn(b'DISC_BOOT_FIXTURE', FIXTURE_FILE.read_bytes())


if __name__ == '__main__':
    unittest.main()
