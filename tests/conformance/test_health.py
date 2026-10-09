"""disc-health (plan, stage 7) through its fixture build, in a temporary root: the player's sysfs
and /proc as files, the kernel's ring as a file (fixture/kmsg). It never reads the computer's own;
statvfs alone looks at real folders (the root's)."""
import hashlib
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

PROFILE = load_profile()['version']
HEALTH = Path(os.environ.get('DISC_HEALTH_FIXTURE_BINARY', ROOT/'build/host/disc-health-fixture'))
BOOT = Path(os.environ.get('DISC_BOOT_FIXTURE_BINARY', ROOT/'build/host/disc-boot-fixture'))
KMSG = ('<6>[    1.000000] Booting Linux\n'
        '<3>[    5.250000] mmc0: Timeout waiting for hardware interrupt.\n'
        '<3>[    6.500000] FAT-fs (mmcblk0p1): error, fat_get_cluster: invalid cluster chain\n'
        '<6>[    7.000000] mmc0: new high speed SDXC card at address aaaa\n'
        '<4>[    9.000000] mq_ui[123]: potentially unexpected fatal signal 11.\n')


class HealthTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.data, self.run, self.card = self.root/'usr/data/disc-boot/data/disc-health', self.root/'run/disc-boot/service/disc-health', self.root/'tmp/sdcard'
        for folder in (self.data, self.run, self.card, self.root/'fixture'):
            folder.mkdir(parents=True)
        self.env = dict(os.environ, DISC_BOOT_FIXTURE_ROOT=str(self.root), DISC_BOOT_DATA=str(self.data), DISC_BOOT_RUN=str(self.run),
                        DISC_BOOT_CARD=str(self.card), DISC_HEALTH_INTERVAL='0.3')
        self.proc = None
        self.addCleanup(self.stop)

    def player(self):
        """What a V2.57 player shows of itself (the gauge as snowsky-disc-qemu's device profile)."""
        files = {
            'sys/class/power_supply/cw221X-bat/capacity': '87\n', 'sys/class/power_supply/cw221X-bat/voltage_now': '4012000\n',
            'sys/class/power_supply/cw221X-bat/current_now': '-120000\n', 'sys/class/power_supply/cw221X-bat/temp': '251\n',
            'sys/class/power_supply/cw221X-bat/cycle_count': '3\n', 'sys/class/power_supply/cw221X-bat/type': 'Mains\n',
            'sys/class/power_supply/usb/online': '1\n',
            'sys/class/thermal/thermal_zone0/temp': '45250\n', 'sys/class/thermal/thermal_zone0/type': 'cpu-thermal\n',
            'proc/uptime': '3600.12 7000.50\n', 'proc/loadavg': '0.12 0.30 0.25 1/80 1234\n',
            'proc/meminfo': 'MemTotal:         120000 kB\nMemFree:           20000 kB\nMemAvailable:      60000 kB\n',
            'proc/mounts': '/dev/root / squashfs ro 0 0\n/dev/mmcblk0p1 /tmp/sdcard exfat rw 0 0\n',
            'proc/sys/kernel/random/boot_id': '1a2b3c4d-0000-4000-8000-000000000000\n',
            'usr/data/fiio/log/process_failed.txt': 'Restarting mq_ui\nsomething else\nRestarting mq_player\n',
            'fixture/kmsg': KMSG,
        }
        for path, text in files.items():
            (self.root/path).parent.mkdir(parents=True, exist_ok=True)
            (self.root/path).write_text(text)

    def start(self):
        self.proc = subprocess.Popen([str(HEALTH)], env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()

    def report(self, condition=lambda r: True, timeout=10):
        until, report = time.monotonic() + timeout, None
        while time.monotonic() < until:
            try:
                report = json.loads((self.run/'status.json').read_text())
            except (OSError, ValueError):
                report = None
            if report and condition(report):
                return report
            time.sleep(0.05)
        self.fail(f'no such report: {report}')

    def test_a_reading_goes_to_the_journal_and_the_report(self):
        self.player()
        self.start()
        report = self.report()
        self.assertTrue((self.run/'ready').exists(), 'ready after the first reading')
        latest = report['latest']
        self.assertEqual(latest['battery'], dict(name='cw221X-bat', percent=87, mV=4012, mA=-120, celsius=25.1, cycles=3))
        self.assertEqual(latest['thermal'], [dict(zone='cpu-thermal', celsius=45.2)])
        self.assertEqual((latest['boot'], latest['uptime'], latest['load']), ('1a2b3c4d', 3600, [0.12, 0.3, 0.25]))
        self.assertEqual(latest['memory'], dict(totalKB=120000, availableKB=60000))
        self.assertGreater(latest['space']['data']['totalKB'], 0)
        self.assertGreater(latest['space']['card']['totalKB'], 0, 'the card is mounted where boot says')
        self.assertEqual((latest['kernel'], latest['pairRestarts']), (dict(cardErrors=2, fatalSignals=1), 2))
        self.assertIsInstance(latest['t'], int)
        self.assertEqual((report['schema'], report['interval']), (1, 0))
        self.assertLessEqual(len((self.run/'status.json').read_bytes()), 4096)
        # Only what the kernel and stock's loop said since: counted once, summed since the start.
        with open(self.root/'fixture/kmsg', 'a') as kmsg:
            kmsg.write('<3>[   20.000000] blk_update_request: I/O error, dev mmcblk0, sector 2048\n')
        with open(self.root/'usr/data/fiio/log/process_failed.txt', 'a') as log:
            log.write('Restarting mq_ui\n')
        report = self.report(lambda r: r['sinceStart']['cardErrors'] == 3)
        self.assertEqual(report['sinceStart'], dict(cardErrors=3, fatalSignals=1, pairRestarts=1))
        report = self.report(lambda r: r['samples'] >= report['samples'] + 1)
        self.assertEqual(report['latest']['kernel'], dict(cardErrors=0, fatalSignals=0))
        lines = (self.data/'journal.jsonl').read_text().splitlines()
        self.assertGreaterEqual(len(lines), 3)
        self.assertTrue(all(json.loads(line)['battery']['percent'] == 87 for line in lines))
        self.assertEqual(report['journalBytes'], (self.data/'journal.jsonl').stat().st_size)

    def test_the_journal_stays_within_256_kib(self):
        self.player()
        (self.data/'journal.1.jsonl').write_text('the oldest\n')
        (self.data/'journal.jsonl').write_text('x' * (128 * 1024 - 10) + '\n')
        self.start()
        self.report()
        self.stop()
        self.assertEqual((self.data/'journal.1.jsonl').stat().st_size, 128 * 1024 - 9, 'the full one became the older')
        self.assertEqual(len((self.data/'journal.jsonl').read_text().splitlines()), 1)

    def test_what_a_player_does_not_show_is_null(self):
        self.start()
        latest = self.report()['latest']
        self.assertEqual((latest['battery'], latest['thermal'], latest['memory'], latest['space']['card']), (None, [], None, None))
        self.assertEqual(latest['kernel'], dict(cardErrors=0, fatalSignals=0))
        self.assertNotIn('pairRestarts', latest)

    def test_it_stops_on_sigterm_and_needs_the_boot_layer(self):
        self.player()
        self.start()
        self.report()
        self.proc.send_signal(signal.SIGTERM)
        self.assertEqual(self.proc.wait(timeout=5), 0)
        env = {k: v for k, v in self.env.items() if k != 'DISC_BOOT_RUN'}
        result = subprocess.run([str(HEALTH)], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn('runs as a service of the boot layer', result.stderr)

    def test_boot_runs_it_as_a_service(self):
        """The fixture binary as a service package of boot API 2, run by the boot program's fixture."""
        self.player()
        slot = self.root/'usr/data/disc-boot/service/disc-health/a'
        (slot/'bin').mkdir(parents=True)
        data = HEALTH.read_bytes()
        (slot/'bin/disc-health').write_bytes(data)
        (slot/'bin/disc-health').chmod(0o755)
        (slot/'package.json').write_text(json.dumps(dict(
            schema=1, name='disc-health', version='1', role='service', bootApi=2, arch='fixture', profiles=[PROFILE],
            entry='bin/disc-health', ready=30, files={'bin/disc-health': dict(size=len(data), sha256=hashlib.sha256(data).hexdigest(), mode='0755')})))
        (slot.parent/'state.json').write_text('{"schema":1,"current":"a","confirmed":false,"previous":null}')
        env = dict(os.environ, DISC_BOOT_FIXTURE_ROOT=str(self.root), DISC_BOOT_FIXTURE_TIMING='confirm=1,grace=1,card=2')
        boot = lambda *a: subprocess.run([str(BOOT), *a], env=env, capture_output=True, text=True, timeout=30, check=True)
        self.addCleanup(lambda: subprocess.run([str(BOOT), 'stop'], env=env, capture_output=True, timeout=30))
        boot('early', '--profile', PROFILE, '--card', '/tmp/sdcard', '--card-source', '/dev/mmcblk0p1')
        boot('start')
        until, status = time.monotonic() + 20, None
        while time.monotonic() < until:
            try:
                status = json.loads((self.root/'run/disc-boot/service/disc-health.json').read_text())
            except (OSError, ValueError):
                status = None
            if status and status['state'] == 'confirmed':
                break
            time.sleep(0.1)
        self.assertEqual((status or {}).get('state'), 'confirmed', status)
        report = self.report()
        self.assertEqual((report['interval'], report['latest']['battery']['percent']), (600, 87))


if __name__ == '__main__':
    unittest.main()
