"""disc-network (plan, stage 7) through its fixture build, in a temporary root, against a stand-in of
stock's wpa_cli and wpa_supplicant (tests/integration/wpa_cli_stand_in.sh, the same on the guest):
stock's configuration keeps one network, as stock's UI needs (the owner's player, 2026-10-09); the
service keeps each network stock connected to and, out of reach, puts a kept one in range in its
place, never in the middle of stock's sequence; it forgets them after a reset and shows no key."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
NETWORK = Path(os.environ.get('DISC_NETWORK_FIXTURE_BINARY', ROOT/'build/host/disc-network-fixture'))
STAND_IN = ROOT/'tests/integration/wpa_cli_stand_in.sh'


class NetworkTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.data = self.root/'usr/data/disc-boot/data/disc-network'
        self.run_dir = self.root/'run/disc-boot/service/disc-network'
        for folder in (self.data, self.run_dir, self.root/'usr/sbin'):
            folder.mkdir(parents=True)
        shutil.copyfile(STAND_IN, self.root/'usr/sbin/wpa_cli')
        (self.root/'usr/sbin/wpa_cli').chmod(0o755)
        (self.root/'usr/data/wpa_supplicant.conf').write_text('ctrl_interface=/var/run/wpa_supplicant\nupdate_config=1\ncountry=GB\n')
        self.env = dict(os.environ, DISC_BOOT_FIXTURE_ROOT=str(self.root), DISC_BOOT_DATA=str(self.data),
                        DISC_BOOT_RUN=str(self.run_dir), DISC_NETWORK_INTERVAL='0.1')
        self.proc = None
        self.addCleanup(self.stop)

    def wpa(self, *args):
        return subprocess.run([str(self.root/'usr/sbin/wpa_cli'), '-i', 'wlan0', '--', *args], capture_output=True, text=True,
                              timeout=10).stdout

    def test_the_stand_in_takes_options_as_stocks_wpa_cli_does(self):
        """glibc's getopt in stock's wpa_cli takes an argument beginning with "-" for an option unless "--"
        ends them (the owner's player, 2026-10-09): the stand-in answers so, and the service passes "--"."""
        cli = [str(self.root/'usr/sbin/wpa_cli'), '-i', 'wlan0']
        self.wpa('_up')
        self.wpa('add_network')
        refused = subprocess.run([*cli, 'set_network', '0', 'priority', '-1'], capture_output=True, text=True, timeout=10)
        self.assertIn("invalid option -- '1'", refused.stdout)
        self.assertEqual(self.wpa('set_network', '0', 'priority', '-1').strip(), 'OK')

    def start(self):
        self.proc = subprocess.Popen([str(NETWORK)], env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()

    def report(self, condition=lambda r: True, timeout=10):
        until, report = time.monotonic() + timeout, None
        while time.monotonic() < until:
            try:
                report = json.loads((self.run_dir/'status.json').read_text())
            except (OSError, ValueError):
                report = None
            if report and condition(report):
                return report
            time.sleep(0.05)
        self.fail(f'no such report: {report}; wpa_cli was asked: {self.asked()[-12:]}')

    def asked(self):
        path = self.root/'run/wpa-stand-in/log'
        return path.read_text().splitlines() if path.exists() else []

    def conf(self):
        """The saved networks, in order: {key: value} of each block."""
        blocks, current = [], None
        for line in (self.root/'usr/data/wpa_supplicant.conf').read_text().splitlines():
            line = line.strip()
            if line == 'network={':
                current = {}
            elif line == '}':
                blocks.append(current)
                current = None
            elif current is not None and '=' in line:
                key, value = line.split('=', 1)
                current[key] = value
        return blocks

    def names(self, report):
        return [n['name'] for n in report['networks']]

    def store(self):
        return json.loads((self.data/'networks.json').read_text())['networks']

    def connected(self, ssid, psk, name):
        self.wpa('_stock_connect', ssid, psk)
        return self.report(lambda r: r['connected'] == name and name in self.names(r))

    def ssids(self):
        return [b['ssid'] for b in self.conf()]

    def until(self, condition, label, timeout=15):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if condition():
                return
            time.sleep(0.05)
        self.fail(f'{label}; wpa_cli was asked: {self.asked()[-12:]}')

    def test_it_keeps_each_network_stock_connected_to_and_stock_keeps_one(self):
        self.start()
        self.assertEqual(self.report()['wifi'], 'off')
        self.assertTrue((self.run_dir/'ready').exists())
        self.wpa('_up')
        report = self.connected('"Home"', '"home secret"', 'Home')
        self.assertEqual(report['networks'], [dict(name='Home', open=False, held=True, inRange=True)])
        self.assertEqual(stat.S_IMODE((self.data/'networks.json').stat().st_mode), 0o600, 'it holds the keys')
        self.assertEqual(self.store()[0]['psk'], '"home secret"')
        # Stock's next connection removes Home: the service keeps it in its store, never in stock's configuration.
        self.connected('"Office"', '"office pass"', 'Office')
        report = self.report(lambda r: len(r['networks']) == 2)
        time.sleep(1.0)
        self.assertEqual(self.ssids(), ['"Office"'], 'stock\'s configuration keeps one network')
        self.assertEqual({n['name']: n['held'] for n in report['networks']}, dict(Home=False, Office=True))
        self.assertNotIn('add_network ', self.asked(), 'nothing added while stock is connected')
        text = (self.run_dir/'status.json').read_text()
        self.assertNotIn('secret', text)
        self.assertNotIn('office pass', text)

    def test_out_of_reach_the_strongest_kept_network_in_range_takes_its_place(self):
        self.start()
        self.wpa('_up')
        for name, key in (('Home', 'home secret'), ('Cafe', 'cafe pass'), ('Office', 'office pass')):
            self.connected(f'"{name}"', f'"{key}"', name)
        self.report(lambda r: len(r['networks']) == 3)
        # Office out of reach; Cafe and Home in range, Cafe the stronger.
        self.wpa('_range', '"Cafe"', '"Home"')
        report = self.report(lambda r: r['connected'] == 'Cafe')
        self.assertEqual(self.conf(), [dict(ssid='"Cafe"', psk='"cafe pass"', scan_ssid='1')], 'it alone, as stock saves it')
        self.assertTrue(report['lastChange']['what'].startswith('switched to Cafe'), report['lastChange'])
        asked = self.asked()
        self.assertEqual(asked[asked.index('remove_network all'):][:6],
                         ['remove_network all', 'add_network ', 'set_network 0 ssid "Cafe"', 'set_network 0 psk "cafe pass"',
                          'set_network 0 scan_ssid 1', 'select_network 0'])
        self.assertEqual({n['name']: (n['held'], n['inRange']) for n in report['networks']},
                         dict(Home=(False, True), Cafe=(True, True), Office=(False, False)))

    def test_a_network_that_does_not_connect_waits_and_the_next_takes_its_place(self):
        self.start()
        self.wpa('_up')
        for name in ('Home', 'Cafe', 'Office'):
            self.connected(f'"{name}"', f'"{name.lower()} pass"', name)
        self.report(lambda r: len(r['networks']) == 3)
        self.wpa('_fail', '"Home"')                 # as with a key changed since
        self.wpa('_range', '"Home"', '"Cafe"')
        self.until(lambda: self.ssids() == ['"Home"'], 'Home, the strongest, first')
        self.report(lambda r: r['connected'] == 'Cafe', timeout=20)
        self.assertEqual(self.ssids(), ['"Cafe"'])

    def test_a_minute_after_stocks_own_connection_it_waits(self):
        """A network the owner has just chosen in stock's UI is given its time before another takes its place."""
        self.env['DISC_NETWORK_INTERVAL'] = '0.25'
        self.start()
        self.wpa('_up')
        self.connected('"Home"', '"home secret"', 'Home')
        self.wpa('_range', '"Home"')
        self.connected('"Office"', '"office pass"', 'Office')
        self.wpa('_range', '"Home"')                # Office chosen, then out of reach (or its key wrong)
        started = time.monotonic()
        self.report(lambda r: r['connected'] == 'Home', timeout=30)
        self.assertGreater(time.monotonic() - started, 8 * 0.25, 'the quiet after a change of the configuration, beyond 4 looks away')

    def test_never_in_the_middle_of_stocks_sequence(self):
        self.start()
        self.wpa('_up')
        self.connected('"Home"', '"home secret"', 'Home')
        self.connected('"Office"', '"office pass"', 'Office')
        self.wpa('_range', '"Home"')
        self.wpa('remove_network', 'all')   # stock's first step; nothing saved yet
        asked = len(self.asked())
        time.sleep(2.5)
        self.assertNotIn('add_network ', self.asked()[asked:], 'wpa_supplicant differs from the file: it waits')
        # The sequence's end (added, saved): stock's own network stays.
        self.connected('"Cafe"', '"cafe pass"', 'Cafe')
        time.sleep(1.0)
        self.assertEqual(self.ssids(), ['"Cafe"'])

    def test_networks_an_earlier_version_added_back_are_removed(self):
        (self.root/'usr/data/wpa_supplicant.conf').write_text(
            'ctrl_interface=/var/run/wpa_supplicant\nupdate_config=1\ncountry=GB\n\n'
            'network={\n\tssid="Office"\n\tpsk="office pass"\n}\n\nnetwork={\n\tssid="Home"\n\tpsk="home secret"\n\tpriority=-1\n}\n')
        (self.data/'networks.json').write_text(json.dumps(dict(schema=1, networks=[
            dict(ssid='"Home"', psk='"home secret"', keyMgmt=None, scanSsid=0, used=1),
            dict(ssid='"Office"', psk='"office pass"', keyMgmt=None, scanSsid=0, used=2)])))
        self.start()
        self.wpa('_up')
        self.wpa('_range', '"Office"', '"Home"')
        report = self.report(lambda r: (r['lastChange'] or {}).get('what', '').startswith('stock keeps one network'))
        self.assertEqual(self.ssids(), ['"Office"'])
        self.assertEqual(report['connected'], 'Office')

    def test_a_reset_forgets_and_an_emptied_configuration_gets_one_back(self):
        self.start()
        self.wpa('_up')
        self.connected('"Home"', '"home secret"', 'Home')
        self.connected('"Office"', '"office pass"', 'Office')
        self.report(lambda r: len(r['networks']) == 2)
        # Emptied while Wi-Fi is on (stock removed everything and saved): a kept one in range comes back alone.
        self.wpa('remove_network', 'all')
        self.wpa('save_config')
        self.until(lambda: len(self.conf()) == 1, 'one network back')
        self.assertIn(self.ssids()[0], ('"Home"', '"Office"'))
        # Stock's reset: a new configuration without networks when Wi-Fi comes on.
        self.wpa('_down')
        self.report(lambda r: r['wifi'] == 'off')
        (self.root/'usr/data/wpa_supplicant.conf').write_text('ctrl_interface=/var/run/wpa_supplicant\nupdate_config=1\ncountry=GB\n')
        self.wpa('_up')
        report = self.report(lambda r: r['networks'] == [])
        self.assertEqual(report['lastChange']['what'], 'forgot 2 networks: the player has none saved (reset)')
        self.assertEqual(self.store(), [])
        self.assertEqual(self.conf(), [])

    def test_at_most_eight_the_least_recent_dropped(self):
        self.start()
        self.wpa('_up')
        for k in range(1, 10):
            self.connected(f'"net{k}"', f'"pass{k}word"', f'net{k}')
        report = self.report(lambda r: len(r['networks']) == 8)
        self.assertEqual(sorted(self.names(report)), sorted(f'net{k}' for k in range(2, 10)))
        self.assertNotIn('"net1"', [n['ssid'] for n in self.store()])
        self.assertEqual(self.ssids(), ['"net9"'])

    def test_open_and_utf8_networks_and_what_it_does_not_copy(self):
        self.start()
        self.wpa('_up')
        home = 'Дом'.encode().hex()
        report = self.connected(home, 'NONE', 'Дом')
        self.assertEqual(report['networks'], [dict(name='Дом', open=True, held=True, inRange=True)])
        self.assertEqual((self.store()[0]['ssid'], self.store()[0]['keyMgmt'], self.store()[0]['psk']), (home, 'NONE', None))
        self.connected('"Office"', '"office pass"', 'Office')
        self.wpa('_range', home)
        self.report(lambda r: r['connected'] == 'Дом')
        self.assertEqual(self.conf(), [dict(ssid=home, key_mgmt='NONE', scan_ssid='1')])
        # A network with settings it does not copy (EAP) is not kept.
        self.wpa('_stock_connect', '"Work"', '"x"')
        self.wpa('set_network', '0', 'eap', 'PEAP')
        self.wpa('save_config')
        report = self.report(lambda r: r['connected'] == 'Work' and (r['lastChange'] or {}).get('what', '').startswith('not kept'))
        self.assertNotIn('Work', self.names(report))


if __name__ == '__main__':
    unittest.main()
