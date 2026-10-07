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
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from firmware_profile import load_profile  # noqa: E402
# The active reviewed firmware profile; the tests hold for whichever one is selected.
PROFILE = load_profile()['version']
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
        self.env = dict(os.environ, DISC_TEST_LEAK='1', DISC_BOOT_FIXTURE_ROOT=str(self.root),
                        DISC_BOOT_FIXTURE_TIMING='confirm=1,grace=1,window=30,backoff=0.1,card=2,ui=30,menu=2,pair=0.3')
        self.data = self.root/'usr/data/disc-boot'
        self.run_dir = self.root/'run/disc-boot'
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.boot('stop')
        subprocess.run(['pkill', '-f', str(self.root)], capture_output=True)

    def boot(self, *args, check=False):
        return subprocess.run([str(BINARY), *args], env=self.env, capture_output=True, text=True, timeout=30, check=check)

    def package(self, directory, script, name='disc-server', version='1', role='service', entry=None, edit=None, extra=None):
        directory.mkdir(parents=True, exist_ok=True)
        entry = entry or ('bin/run' if role == 'service' else 'bin/mq_ui')
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
                        profiles=[PROFILE], entry=entry, args=[], ready=5, files=listed)
        if edit:
            edit(manifest)
        (directory/'package.json').write_text(json.dumps(manifest))
        return manifest

    @staticmethod
    def domain(role, name):
        """Where boot keeps a package: service and menu by role, each ui package under its name."""
        return f'ui/{name}' if role == 'ui' else role

    def install(self, role, slot, script, confirmed=False, previous=None, **kwargs):
        domain = self.domain(role, kwargs.get('name', 'disc-server'))
        manifest = self.package(self.data/domain/slot, script, role=role, **kwargs)
        self.state(domain, slot, confirmed, previous)
        return manifest

    def state(self, domain, current, confirmed=False, previous=None):
        """A domain's state as boot writes it: a previous slot keeps its package.json's fingerprint."""
        (self.data/domain).mkdir(parents=True, exist_ok=True)
        (self.data/domain/'state.json').write_text(json.dumps(dict(schema=1, current=current, confirmed=confirmed, previous=previous,
                                                                   previousManifest=self.fingerprint(domain, previous))))

    def fingerprint(self, domain, slot):
        path = self.data/domain/str(slot)/'package.json'
        return hashlib.sha256(path.read_bytes()).hexdigest() if slot and path.exists() else None

    def role_state(self, domain):
        return json.loads((self.data/domain/'state.json').read_text())

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
        result = self.boot('verify', role, str(directory), '--profile', PROFILE)
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
            'only a ui package brings a player launcher': lambda m: m.update(player='bin/run'),
            'player must be a listed relative path': lambda m: m.update(player='/bin/run'),
            'title must be 1-32 printable ASCII': lambda m: m.update(title='x' * 33),
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
        self.package(directory, GOOD, role='ui', edit=lambda m: m.update(player='bin/player'))
        self.assertIn('player must be a listed file with mode 0755', self.verify(directory, 'ui')[1]['error'])
        self.package(directory, GOOD, role='ui', entry='bin/launch')
        self.assertIn('named mq_ui', self.verify(directory, 'ui')[1]['error'])
        # A repeated key is refused rather than one of them believed.
        text = (directory/'package.json').read_text()
        (directory/'package.json').write_text(text[:-1] + ',"name":"other"}')
        self.assertIn('name must match', self.verify(directory, 'ui')[1]['error'])

    # The service's lifecycle

    def test_a_service_is_confirmed_with_its_environment(self):
        # Which descriptors above 2 are open: a duplicate of one succeeds only when it is.
        script = ('env > "$DISC_BOOT_DATA/env.txt"\nps -o nice= -p $$ > "$DISC_BOOT_DATA/nice.txt"\n'
                  'for fd in 3 4 5 6 7 8 9; do { true >&$fd; } 2>/dev/null && echo $fd; done > "$DISC_BOOT_DATA/fds.txt"\n' + GOOD)
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
        self.assertEqual(env['DISC_BOOT_PROFILE'], PROFILE)
        self.assertEqual(env['DISC_BOOT_SLOT'], slot)
        self.assertEqual(env['DISC_BOOT_INACTIVE'], str(self.data/'service/b'))
        self.assertEqual(env['DISC_BOOT_REQUEST'], str(self.data/'service/request'))
        self.assertEqual(env['DISC_BOOT_DATA'], str(self.data/'data/disc-server'))
        self.assertEqual(env['DISC_BOOT_RUN'], str(self.run_dir/'service'))
        self.assertEqual(env['DISC_BOOT_CARD'], str(self.root/'tmp/sdcard'))
        self.assertEqual(env['DISC_BOOT_PROGRAM'], str(BINARY.resolve()))
        self.assertTrue(env['LD_LIBRARY_PATH'].startswith(slot + '/lib:/usr/lib:'))
        self.assertNotIn('DISC_TEST_LEAK', env, 'a package starts from a clean environment')
        # The fixture's root only, so that the boot program a package runs (verify) sees the same world.
        self.assertEqual(env['DISC_BOOT_FIXTURE_ROOT'], str(self.root))
        self.assertEqual(int((self.data/'data/disc-server/nice.txt').read_text()), 5)
        self.assertEqual((self.data/'data/disc-server/fds.txt').read_text(), '', 'only stdin, stdout and stderr are open')
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
        broken = self.staged('ui/other-ui')
        self.package(broken, GOOD, role='ui', name='other-ui')
        (broken/'bin/mq_ui').write_text('changed')
        self.early('play')
        self.boot('start', check=True)
        status = self.wait_status('service', 'confirmed')
        self.assertEqual((status['version'], status['slot']), ('7', 'a'))
        result = json.loads((self.root/'tmp/sdcard/.disc/boot/result.json').read_text())
        self.assertEqual(result['roles']['service'], dict(installed=True, note='installed disc-server 7'))
        self.assertFalse(result['roles']['ui']['other-ui']['installed'])
        self.assertIn('bin/mq_ui has 7 bytes', result['roles']['ui']['other-ui']['note'])
        self.assertFalse(self.staged('service').exists())
        self.assertTrue(broken.exists(), 'a refused package stays on the card')
        self.assertEqual(oct((self.data/'service/a/bin/run').stat().st_mode & 0o777), '0o755')

    def test_play_with_a_ui_package_stops_the_ui_stock_started(self):
        # The card comes after stock's UI: the recovery stops that UI by its name, as stock's watch
        # loop finds it, so the loop starts the launcher (and with it the package).
        (self.root/'proc/mounts').write_text('/dev/mmcblk0p1 /tmp/sdcard exfat rw 0 0\n')
        self.package(self.staged('ui/other-ui'), GOOD, role='ui', name='other-ui')
        stock_ui = subprocess.Popen(['sleep', '60'])
        self.addCleanup(stock_ui.kill)
        other = subprocess.Popen(['sleep', '60'])
        self.addCleanup(other.kill)
        for process, name in ((stock_ui, 'mq_ui'), (other, 'mq_player')):
            (self.root/f'proc/{process.pid}').mkdir()
            (self.root/f'proc/{process.pid}/comm').write_text(name + '\n')
        self.early('play')
        self.boot('start', check=True)
        self.assertEqual(stock_ui.wait(timeout=15), -signal.SIGTERM)
        self.assertIsNone(other.poll(), 'only the UI is stopped')
        self.assertTrue((self.run_dir/'ui-launch').exists())
        result = json.loads((self.root/'tmp/sdcard/.disc/boot/result.json').read_text())
        self.assertEqual(result['roles']['ui'], {'other-ui': dict(installed=True, note='installed other-ui 1')})
        self.assertEqual(self.global_state()['ui'], 'other-ui', 'the first ui package installed becomes the default')

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

    def fixture_wrapper(self, text, name):
        """An image's wrapper with its paths under the fixture's root."""
        for path in ('/run/disc-boot', '/opt/disc-boot', '/usr/bin/', '/usr/data'):
            text = text.replace(path, str(self.root) + path)
        return text

    def launch(self):
        """Stock's start of its UI: the image's /sbin/mq_ui wrapper, with the fixture's paths."""
        launcher = self.root/'opt/disc-boot/mq_ui'
        if not launcher.exists():
            launcher.parent.mkdir(parents=True, exist_ok=True)
            launcher.symlink_to(BINARY)
        wrapper = self.root/'sbin/mq_ui'
        text = self.fixture_wrapper(BUILDER.ui_wrapper(), 'mq_ui')
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
        self.assertTrue(self.role_state('ui/other-ui')['confirmed'])
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
        self.package(self.data/'ui/other-ui/a', f'echo old >> "{self.root}/out/ui"\nexit 1\n', role='ui', name='other-ui')
        self.install('ui', 'b', f'echo new >> "{self.root}/out/ui"\nexit 1\n', name='other-ui', previous='a')
        self.early()
        for _ in range(4):
            self.launch().wait(timeout=10)
        self.assertEqual(self.runs(), ['new', 'new', 'new', 'old'])
        self.assertEqual(self.role_state('ui/other-ui')['current'], 'a')

    def test_a_ui_request_applies_at_its_next_start(self):
        self.stock_ui()
        self.install('ui', 'a', f'echo package >> "{self.root}/out/ui"\nprintf \'{{"action":"remove"}}\' > "$DISC_BOOT_REQUEST"\n', name='other-ui', confirmed=True)
        self.early()
        self.launch().wait(timeout=10)
        self.launch().wait(timeout=10)
        self.assertEqual(self.runs(), ['package', 'stock'])
        self.assertFalse((self.data/'ui/other-ui').exists())

    # Several UIs and the boot menu

    def set_global(self, **fields):
        self.data.mkdir(parents=True, exist_ok=True)
        current = self.global_state() if (self.data/'state.json').exists() else dict(schema=1, default='platform', unconfirmed=0)
        current.update(fields)
        (self.data/'state.json').write_text(json.dumps(current))

    def choice(self):
        return json.loads((self.run_dir/'ui/choice.json').read_text())

    def two_uis(self, confirmed=True):
        for name in ('alpha', 'beta'):
            self.install('ui', 'a', f'echo {name} >> "{self.root}/out/ui"\n: > "$DISC_BOOT_RUN/ready"\nsleep 2.5\n',
                         name=name, confirmed=confirmed)

    def menu(self, script, confirmed=False, **kwargs):
        return self.install('menu', 'a', f'echo menu >> "{self.root}/out/ui"\n' + script, name='disc-menu', confirmed=confirmed, **kwargs)

    def test_the_status_names_each_package_s_project_page(self):
        # The manager links what the status names (snowsky-disc-server "Player software"); a page
        # that breaks the tools' rule is left out, never a reason to refuse the package.
        self.stock_ui()
        page = 'https://github.com/b0hemia/diskos'
        for name, homepage in (('alpha', page), ('beta', 'http://example.com/beta')):
            self.install('ui', 'a', f'echo {name} >> "{self.root}/out/ui"\n: > "$DISC_BOOT_RUN/ready"\nsleep 2.5\n',
                         name=name, confirmed=True, edit=lambda m, h=homepage: m.update(homepage=h))
        self.set_global(ui='alpha')
        self.early()
        self.launch().wait(timeout=10)
        status = self.wait_status('ui', 'confirmed')
        self.assertEqual((status['name'], status['homepage']), ('alpha', page))
        self.assertEqual([(i['name'], i['homepage']) for i in status['installed']], [('alpha', page), ('beta', None)])

    def test_the_default_chooses_among_several_uis(self):
        self.stock_ui()
        self.two_uis()
        self.set_global(ui='beta')
        self.early()
        self.assertEqual(self.choice(), dict(schema=1, ui='beta', by='default', menu=False, note=''))
        self.launch().wait(timeout=10)
        self.assertEqual(self.runs(), ['beta'])
        status = self.wait_status('ui', 'confirmed')
        self.assertEqual([(i['name'], i['slot']) for i in status['installed']], [('alpha', 'a'), ('beta', 'a')])
        self.assertEqual(status['choice']['ui'], 'beta')
        # Without a default the first installed runs; a default that is gone gives stock's UI, and
        # with nothing of ours to start the boot program stays out of the UI's way.
        self.set_global(ui=None)
        self.early()
        self.assertEqual(self.choice()['ui'], 'alpha')
        self.set_global(ui='gamma')
        self.early()
        self.assertEqual(self.choice(), dict(schema=1, ui='stock', by='fallback', menu=False, note='gamma is not installed'))
        self.assertFalse((self.run_dir/'ui-launch').exists())
        self.set_global(ui='stock')
        self.early()
        self.assertEqual((self.choice()['ui'], self.choice()['by']), ('stock', 'default'))
        self.assertFalse((self.run_dir/'ui-launch').exists())

    def test_next_chooses_one_boot_and_requests_are_checked(self):
        self.two_uis()
        self.install('service', 'a', GOOD, confirmed=True)
        self.set_global(ui='alpha')
        # A running service's request about the choice applies at the next boot, before anything starts.
        (self.data/'service/request').write_text('{"action":"ui-next","ui":"beta"}')
        self.early()
        self.assertEqual((self.choice()['ui'], self.choice()['by']), ('beta', 'next'))
        self.assertFalse((self.data/'service/request').exists())
        self.assertIsNone(self.global_state()['next'])
        self.early()
        self.assertEqual((self.choice()['ui'], self.choice()['by']), ('alpha', 'default'))
        for request, why in (('{"action":"ui-default","ui":"gamma"}', 'ui-default refused: gamma is not installed'),
                             ('{"action":"ui-default","ui":"Bad Name"}', 'ui-default refused: ui must name an installed ui package or stock'),
                             ('{"action":"ui-remove","ui":"alpha"}', 'ui-remove refused: only the service or the menu removes a ui package')):
            with self.subTest(request):
                (self.data/'ui/beta/request').write_text(request)
                self.set_global(unconfirmed=0)  # each boot here counts; none runs long enough to confirm
                self.early()
                self.assertIn(f'ui/beta request: {why}', self.boot_log_of_early())
        (self.data/'service/request').write_text('{"action":"ui-default","ui":"beta"}')
        self.set_global(unconfirmed=0)
        self.early()
        self.assertEqual((self.global_state()['ui'], self.choice()['ui']), ('beta', 'beta'))

    def boot_log_of_early(self):
        return self.last_early.stderr

    def early(self, keys=None):
        path = self.root/'fixture/keys'
        if keys is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(keys)
        self.last_early = self.boot('early', '--profile', PROFILE, '--card', '/tmp/sdcard', '--card-source', '/dev/mmcblk0p1', check=True)
        return json.loads((self.run_dir/'boot.json').read_text())

    def test_ui_remove_from_the_service_goes_before_any_ui_starts(self):
        self.two_uis()
        self.install('service', 'a', GOOD, confirmed=True)
        self.set_global(ui='alpha', next='alpha')
        (self.data/'data/alpha').mkdir(parents=True)
        (self.data/'service/request').write_text('{"action":"ui-remove","ui":"alpha","purge":true}')
        self.early()
        self.assertFalse((self.data/'ui/alpha').exists())
        self.assertFalse((self.data/'data/alpha').exists())
        self.assertEqual((self.global_state()['ui'], self.global_state()['next']), ('stock', None))
        self.assertEqual(self.choice()['ui'], 'stock')
        self.assertTrue((self.data/'ui/beta/a').exists())

    def test_a_menu_that_exits_gets_the_pair_restarted_into_its_choice(self):
        self.stock_ui(); self.stock_player()
        self.two_uis()
        self.set_global(ui='alpha')
        self.menu(f'cp "$DISC_BOOT_STATUS/ui/choices.json" "{self.root}/out/choices.json"\n'
                  'printf \'{"ui":"beta"}\' > "$DISC_BOOT_RUN/choice"\n')
        self.early()
        self.assertEqual(self.choice(), dict(schema=1, ui='alpha', by='default', menu=True, note=''))
        self.assertTrue((self.run_dir/'ui-launch').exists())
        self.launch().wait(timeout=10)
        self.assertEqual(self.runs(), ['menu'])
        offered = json.loads((self.root/'out/choices.json').read_text())
        self.assertEqual(offered['default'], 'alpha')
        self.assertEqual([e['ui'] for e in offered['entries']], ['alpha', 'beta', 'stock'])
        # A player already ran in this boot (it runs the watchdog): stock's player starts beside the
        # menu at once, never a package's launcher while the choice is open.
        (self.run_dir/'player-ran').write_text('\n')
        self.launch_player().wait(timeout=10)
        self.assertEqual((self.player_status()['launch'], self.player_status()['note']), ('stock', 'the menu is choosing'))
        self.launch().wait(timeout=10)
        self.assertEqual(self.runs(), ['menu', 'beta'])
        self.assertEqual(self.choice(), dict(schema=1, ui='beta', by='menu', menu=False, note=''))
        menu = self.status('menu')
        self.assertEqual((menu['state'], menu['name'], menu['version']), ('answered', 'disc-menu', '1'), 'the status names the menu')
        self.assertTrue(self.role_state('menu')['confirmed'], 'a valid answer confirms a tentative menu')
        # The last answer becomes the default (owner, 2026-10-07): the menu starts on it next time.
        self.assertEqual(json.loads((self.data/'state.json').read_text())['ui'], 'beta')
        self.launch_player().wait(timeout=10)
        self.assertEqual(self.player_status()['note'], 'the ui package brings no player launcher')
        self.early()
        self.assertEqual(self.choice(), dict(schema=1, ui='beta', by='default', menu=True, note=''))
        # next skips the menu.
        self.set_global(next='alpha')
        self.early()
        self.assertEqual((self.choice()['ui'], self.choice()['menu']), ('alpha', False))

    def ui_with_player(self, name):
        extra = {'bin/player': (f'#!/bin/sh\necho "launcher $DISC_BOOT_ROLE" >> "{self.root}/out/player"\n'
                                f'exec "{self.root}/usr/bin/mq_player" "$@"\n', 0o755)}
        self.install('ui', 'a', f'echo {name} >> "{self.root}/out/ui"\n: > "$DISC_BOOT_RUN/ready"\nsleep 2.5\n',
                     name=name, confirmed=True, extra=extra, edit=lambda m: m.update(player='bin/player'))

    def test_the_menu_hands_over_without_a_restart(self):
        self.stock_ui(); self.stock_player()
        self.install('ui', 'a', f'echo alpha >> "{self.root}/out/ui"\n', name='alpha', confirmed=True,
                     edit=lambda m: m.update(title='Alpha UI'))
        self.ui_with_player('beta')
        self.set_global(ui='alpha')
        self.menu(f'cp "$DISC_BOOT_STATUS/ui/choices.json" "{self.root}/out/choices.json"\n'
                  'printf \'{"ui":"beta"}\' > "$DISC_BOOT_RUN/choice"\nexec "$DISC_BOOT_LAUNCHER"\n')
        self.early()
        # The boot's first start of the pair: no player ran yet, so the player waits for the choice.
        player = self.launch_player()
        until = time.monotonic() + 5
        while not (self.run_dir/'ui/player.json').exists() and time.monotonic() < until:
            time.sleep(0.05)
        self.assertEqual(self.player_status()['launch'], 'waiting')
        self.assertIsNone(player.poll())
        # The menu answers and hands over in its own process: the chosen UI starts at once.
        self.launch().wait(timeout=10)
        self.assertEqual(self.runs(), ['menu', 'beta'])
        # The offer: each package's title (else its name) and version, stock's UI with the firmware's.
        offered = json.loads((self.root/'out/choices.json').read_text())['entries']
        self.assertEqual(offered, [dict(ui='alpha', title='Alpha UI', version='1', confirmed=True),
                                   dict(ui='beta', title='beta', version='1', confirmed=True),
                                   dict(ui='stock', version=PROFILE)])
        player.wait(timeout=10)
        self.assertEqual(self.players(), ['launcher ui', f'stock ui {self.root}/opt/disc-boot/guard'])
        self.assertEqual({k: self.player_status()[k] for k in ('launch', 'name')}, {'launch': 'package', 'name': 'beta'})
        self.assertEqual((self.choice()['ui'], self.choice()['by']), ('beta', 'menu'))
        self.assertEqual(self.wait_status('ui', 'confirmed')['name'], 'beta')
        self.assertTrue((self.run_dir/'player-ran').exists())
        # The persistent boot log (it survives a reset): each decision with its uptime, in order.
        lines = [line.split(' ', 1)[1] for line in (self.data/'boot.log').read_text().splitlines()]
        wanted = ['player waiting: the menu is choosing', 'choice alpha by ', 'menu asking 1', 'menu answered',
                  'ui/beta starting 1', 'player follows the UI by', 'player package beta', 'ui/beta confirmed 1']
        found = [next((i for i, line in enumerate(lines) if w in line), -1) for w in wanted]
        self.assertTrue(all(i >= 0 for i in found), (wanted, lines))
        # Stock's order after the menu's choice: the UI first, the player behind it.
        self.assertLess(found[wanted.index('menu answered')], found[wanted.index('player follows the UI by')])
        self.assertTrue(any('mq_ui launcher' in line for line in lines), lines)

    def test_the_pair_s_output_reaches_the_boot_log_at_its_next_start(self):
        """What a program in the UI's place wrote (stock's UI keeps no log of its own) is in the boot
        log at the pair's next start, with stock's queues; the program's output goes to RUN_DIR/out."""
        self.stock_ui()
        self.install('ui', 'a', f'echo package >> "{self.root}/out/ui"\necho "cannot open the player queue"\n'
                                'echo "second line" >&2\n', name='other-ui')
        (self.root/'dev/mqueue').mkdir(parents=True)
        (self.root/'dev/mqueue/ui').write_text('QSIZE:0          NOTIFY:0     SIGNO:0     NOTIFY_PID:0     \n')
        self.early()
        self.launch().wait(timeout=10)
        self.assertIn('cannot open the player queue', (self.run_dir/'out/mq_ui.log').read_text())
        self.launch().wait(timeout=10)
        log = (self.data/'boot.log').read_text()
        self.assertIn('mq_ui before said: cannot open the player queue', log)
        self.assertIn('mq_ui before said: second line', log)
        self.assertIn('mqueue ui QSIZE:0', log)
        self.assertEqual(self.runs(), ['package', 'package'])

    def test_stock_s_process_lock_and_stuck_processes_reach_the_boot_log(self):
        """Stock's pair serialises on a flock of /usr/data/fiio/process_lock.txt (blocking): its holder
        and waiters, and processes in uninterruptible sleep, are in the boot log at each start."""
        self.stock_ui()
        self.install('ui', 'a', f'echo package >> "{self.root}/out/ui"\n', name='other-ui')
        lock = self.root/'usr/data/fiio/process_lock.txt'
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text('')
        inode = lock.stat().st_ino
        proc = self.root/'proc'
        for pid, name, state in ((1234, 'mq_player', 'S'), (1235, 'mq_ui', 'S'), (4321, 'cp', 'D')):
            (proc/str(pid)).mkdir(parents=True, exist_ok=True)
            (proc/str(pid)/'stat').write_text(f'{pid} ({name}) {state} 1 1 1 0\n')
        (proc/'locks').write_text(f'1: FLOCK  ADVISORY  WRITE 1234 00:0e:{inode} 0 EOF\n'
                                  f'1: -> FLOCK  ADVISORY  WRITE 1235 00:0e:{inode} 0 EOF\n'
                                  '2: POSIX  ADVISORY  WRITE 999 00:0e:1 0 EOF\n')
        self.early()
        self.launch().wait(timeout=10)
        log = (self.data/'boot.log').read_text()
        self.assertIn('process_lock holds by 1234 (mq_player S)', log)
        self.assertIn('process_lock waits by 1235 (mq_ui S)', log)
        self.assertIn('uninterruptible 4321 (cp D)', log)
        self.assertNotIn('by 999', log)

    def test_the_boot_log_stops_at_its_cap(self):
        self.stock_ui()
        self.install('ui', 'a', f'echo package >> "{self.root}/out/ui"\n', name='other-ui')
        self.early()
        log = self.data/'boot.log'
        log.write_bytes(b'x' * 262144)
        self.launch().wait(timeout=10)
        self.assertEqual(self.runs(), ['package'])
        self.assertEqual(log.stat().st_size, 262144, 'a full log is left as it is, by the wrappers and the boot program')
        log.unlink()
        self.launch().wait(timeout=10)
        self.assertIn('choice other-ui', log.read_text())

    def test_after_a_player_ran_the_pair_restarts_for_the_package_player(self):
        self.stock_ui(); self.stock_player()
        self.ui_with_player('beta')
        self.set_global(ui='beta')
        self.menu('printf \'{"ui":"beta"}\' > "$DISC_BOOT_RUN/choice"\nexec "$DISC_BOOT_LAUNCHER"\n')
        self.early()
        # Stock's player started before the menu in this boot (after a recovery, say): the wrapper marks it.
        (self.run_dir/'ui-launch').unlink()
        self.launch_player().wait(timeout=10)
        self.assertTrue((self.run_dir/'player-ran').exists())
        (self.run_dir/'ui-launch').write_text('ui\n')
        self.assertEqual(self.launch().wait(timeout=10), 0)
        self.assertEqual(self.runs(), ['menu'], 'the launcher leaves the restart to stock\'s loop')
        self.assertEqual(self.status('ui')['note'], 'the pair restarts for the package\'s player')
        self.launch().wait(timeout=10)
        self.launch_player().wait(timeout=10)
        self.assertEqual(self.runs(), ['menu', 'beta'])
        self.assertEqual(self.player_status()['launch'], 'package')

    def test_a_failing_menu_gives_way_to_the_default(self):
        self.stock_ui()
        self.two_uis()
        self.set_global(ui='alpha')
        self.menu('printf \'{"ui":"gamma"}\' > "$DISC_BOOT_RUN/choice"\n')
        self.early()
        for _ in range(3):
            self.launch().wait(timeout=10)
        self.assertEqual(self.runs(), ['menu', 'menu', 'alpha'])
        choice = self.choice()
        self.assertEqual((choice['ui'], choice['by'], choice['menu']), ('alpha', 'default', False))
        self.assertIn('the menu failed 2 times', choice['note'])
        self.assertIn('gamma, which is not installed', choice['note'])
        self.menu('exit 0\n')
        self.early()
        for _ in range(3):
            self.launch().wait(timeout=10)
        self.assertEqual(self.runs()[3:], ['menu', 'menu', 'alpha'])
        self.assertIn('exited without an answer', self.choice()['note'])

    def test_a_menu_that_never_answers_is_stopped(self):
        self.stock_ui()
        self.two_uis()
        self.set_global(ui='beta')
        self.menu('while :; do sleep 0.1; done\n')
        self.early()
        started = time.monotonic()
        self.assertEqual(self.launch().wait(timeout=15), -signal.SIGKILL)
        self.assertGreater(time.monotonic() - started, 1.5)
        self.assertEqual(self.choice()['note'], 'the menu did not answer in time')
        self.launch().wait(timeout=10)
        self.assertEqual(self.runs(), ['menu', 'beta'])

    def test_stock_chosen_in_the_menu_clears_the_boot_count(self):
        self.stock_ui()
        self.two_uis()
        self.menu('printf \'{"ui":"stock"}\' > "$DISC_BOOT_RUN/choice"\n', confirmed=True)
        self.early()
        self.assertEqual(self.global_state()['unconfirmed'], 1)
        self.launch().wait(timeout=10)
        self.launch().wait(timeout=10)
        self.assertEqual(self.runs(), ['menu', 'stock'])
        self.assertEqual(self.status('ui')['state'], 'stock-ui')
        self.assertEqual(self.global_state()['unconfirmed'], 0, 'nothing of ours runs: no boot-loop count')

    def test_play_installs_several_uis_and_a_menu(self):
        (self.root/'proc/mounts').write_text('/dev/mmcblk0p1 /tmp/sdcard exfat rw 0 0\n')
        for name in ('beta', 'alpha'):
            self.package(self.staged(f'ui/{name}'), GOOD, role='ui', name=name)
        self.package(self.staged('ui/gamma'), GOOD, role='ui', name='other')
        self.package(self.staged('menu'), GOOD, role='menu', name='disc-menu')
        (self.staged('ui')/'package.json').write_text('{}')
        self.early('play')
        self.boot('start', check=True)
        until = time.monotonic() + 15
        path = self.root/'tmp/sdcard/.disc/boot/result.json'
        while not path.exists() and time.monotonic() < until:
            time.sleep(0.05)
        roles = json.loads(path.read_text())['roles']
        self.assertEqual(roles['menu'], dict(installed=True, note='installed disc-menu 1'))
        self.assertEqual(roles['ui']['alpha'], dict(installed=True, note='installed alpha 1'))
        self.assertEqual(roles['ui']['beta'], dict(installed=True, note='installed beta 1'))
        self.assertEqual(roles['ui']['gamma'], dict(installed=False, note='refused: the folder is named gamma, the package other'))
        self.assertFalse(roles['ui']['package.json']['installed'])
        self.assertEqual(self.global_state()['ui'], 'alpha', 'the first installed, by name, becomes the default')
        self.assertEqual((self.choice()['ui'], self.choice()['menu']), ('alpha', True))
        self.assertTrue((self.run_dir/'ui-launch').exists())

    def test_a_menu_package_is_checked_like_a_ui(self):
        directory = self.root/'menu'
        self.package(directory, GOOD, role='menu', name='disc-menu')
        self.assertEqual(self.verify(directory, 'menu')[0], 0)
        self.package(directory, GOOD, role='menu', name='disc-menu', entry='bin/launch')
        self.assertIn("a menu package's entry must be named mq_ui", self.verify(directory, 'menu')[1]['error'])
        self.package(directory, GOOD, role='menu', name='disc-menu', edit=lambda m: m.update(player='bin/mq_ui'))
        self.assertIn('only a ui package brings a player launcher', self.verify(directory, 'menu')[1]['error'])

    # Stock's player and a ui package's player launcher

    def launch_player(self):
        """Stock's start of its player: the image's /sbin/mq_player wrapper, with the fixture's paths."""
        launcher = self.root/'opt/disc-boot/mq_player'
        if not launcher.exists():
            launcher.parent.mkdir(parents=True, exist_ok=True)
            launcher.symlink_to(BINARY)
        wrapper = self.root/'sbin/mq_player'
        text = self.fixture_wrapper(BUILDER.player_wrapper(), 'mq_player')
        wrapper.write_text(text)
        wrapper.chmod(0o755)
        return subprocess.Popen(['/bin/sh', str(wrapper)], env=self.env, start_new_session=True)

    def stock_player(self):
        stock = self.root/'usr/bin/mq_player'
        stock.write_text(f'#!/bin/sh\necho "stock ${{DISC_BOOT_ROLE:-none}} ${{PATH%%:*}}" >> "{self.root}/out/player"\n')
        stock.chmod(0o755)

    def players(self):
        path = self.root/'out/player'
        return path.read_text().splitlines() if path.exists() else []

    def player_status(self):
        return json.loads((self.run_dir/'ui/player.json').read_text())

    def install_with_player(self, player=True):
        extra = {'bin/player': (f'#!/bin/sh\necho "launcher $DISC_BOOT_ROLE" >> "{self.root}/out/player"\n'
                                f'exec "{self.root}/usr/bin/mq_player" "$@"\n', 0o755)}
        return self.install('ui', 'a', f'echo package >> "{self.root}/out/ui"\n: > "$DISC_BOOT_RUN/ready"\nsleep 2.5\n',
                            name='other-ui', extra=extra, edit=(lambda m: m.update(player='bin/player')) if player else None)

    def test_the_player_starts_through_the_ui_package_launcher_with_the_guard_first(self):
        guard = f'{self.root}/opt/disc-boot/guard'
        self.stock_ui(); self.stock_player()
        self.install_with_player()
        self.early()
        self.launch_player().wait(timeout=10)
        self.assertEqual(self.players(), ['launcher ui', f'stock ui {guard}'])
        self.assertEqual({k: self.player_status()[k] for k in ('launch', 'name')}, {'launch': 'package', 'name': 'other-ui'})
        # By the name stock's watch loop looks for: a link named mq_player to the package's launcher.
        self.assertEqual(os.readlink(self.run_dir/'ui/mq_player'), str(self.data/'ui/other-ui/a/bin/player'))
        status = json.loads(self.boot('status', check=True).stdout)
        self.assertEqual(status['player']['launch'], 'package')
        # After the UI's fallback to stock, stock's player starts from the wrapper alone.
        (self.run_dir/'ui').mkdir(exist_ok=True)
        (self.run_dir/'ui/fallback').write_text('\n')
        self.launch_player().wait(timeout=10)
        self.assertEqual(self.players()[2:], [f'stock none {guard}'])
        # Stock mode: the wrapper starts stock's player, the guard still first.
        (self.run_dir/'ui/fallback').unlink()
        self.early('volume-up')
        self.launch_player().wait(timeout=10)
        self.assertEqual(self.players()[3:], [f'stock none {guard}'])

    def test_stock_player_starts_when_the_ui_package_brings_no_launcher_or_fails_its_check(self):
        self.stock_ui(); self.stock_player()
        self.install_with_player(player=False)
        self.early()
        self.launch_player().wait(timeout=10)
        self.assertEqual(self.players(), [f'stock none {self.root}/opt/disc-boot/guard'])
        self.assertEqual(self.player_status()['note'], 'the ui package brings no player launcher')
        self.install_with_player()
        (self.data/'ui/other-ui/a/bin/player').write_text('#!/bin/sh\necho tampered\n')
        self.launch_player().wait(timeout=10)
        self.assertEqual(len(self.players()), 2)
        self.assertTrue(self.players()[1].startswith('stock none'))
        self.assertIn('bin/player', self.player_status()['note'])
        self.assertEqual(self.player_status()['launch'], 'stock')

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
