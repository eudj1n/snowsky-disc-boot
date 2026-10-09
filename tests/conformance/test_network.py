"""disc-network (plan, stage 7) through its fixture build, in a temporary root, against a stand-in of
stock's wpa_cli and wpa_supplicant (tests/integration/wpa_cli_stand_in.sh, the same on the guest):
stock's connection keeps one network; the service keeps each it connected to and adds the others back,
never in the middle of stock's sequence, forgets them after a reset and shows no key."""
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
        return subprocess.run([str(self.root/'usr/sbin/wpa_cli'), '-i', 'wlan0', *args], capture_output=True, text=True,
                              timeout=10).stdout

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
        return self.report(lambda r: r['connected'] == name and name in self.names(r) and all(n['held'] for n in r['networks']))

    def test_it_keeps_each_network_stock_connected_to_and_adds_the_others_back(self):
        self.start()
        self.assertEqual(self.report()['wifi'], 'off')
        self.assertTrue((self.run_dir/'ready').exists())
        self.wpa('_up')
        report = self.connected('"Home"', '"home secret"', 'Home')
        self.assertEqual(report['networks'], [dict(name='Home', open=False, held=True)])
        self.assertEqual(stat.S_IMODE((self.data/'networks.json').stat().st_mode), 0o600, 'it holds the keys')
        self.assertEqual(self.store()[0]['psk'], '"home secret"')
        # Stock's next connection removes Home: the service keeps Office and adds Home back after it.
        self.connected('"Office"', '"office pass"', 'Office')
        until = time.monotonic() + 10
        while len(self.conf()) < 2 and time.monotonic() < until:
            time.sleep(0.05)
        office, home = self.conf()
        self.assertEqual((office['ssid'], home['ssid'], home['psk'], home['priority'], 'disabled' in home),
                         ('"Office"', '"Home"', '"home secret"', '-1', False), 'after stock\'s own, below it')
        self.assertEqual(sorted(self.names(self.report(lambda r: len(r['networks']) == 2))), ['Home', 'Office'])
        text = (self.run_dir/'status.json').read_text()
        self.assertNotIn('secret', text)
        self.assertNotIn('office pass', text)
        # Office out of range, Home in it: wpa_supplicant joins Home, which the service then counts as the most recent.
        self.wpa('_scan', '1')
        self.report(lambda r: r['connected'] == 'Home')
        until = time.monotonic() + 10
        while max(self.store(), key=lambda n: n['used'])['ssid'] != '"Home"' and time.monotonic() < until:
            time.sleep(0.05)
        self.assertEqual(max(self.store(), key=lambda n: n['used'])['ssid'], '"Home"')

    def test_a_network_stock_disabled_is_enabled_again(self):
        self.start()
        self.wpa('_up')
        self.connected('"Home"', '"home secret"', 'Home')
        self.connected('"Office"', '"office pass"', 'Office')
        self.report(lambda r: len(r['networks']) == 2)
        until = time.monotonic() + 10
        while len(self.conf()) < 2 and time.monotonic() < until:
            time.sleep(0.05)
        # Stock's reconnect selects its network, which disables every other one.
        self.wpa('select_network', '0')
        self.wpa('save_config')
        until = time.monotonic() + 10
        while 'disabled' in self.conf()[1] and time.monotonic() < until:
            time.sleep(0.05)
        self.assertNotIn('disabled', self.conf()[1])

    def test_never_in_the_middle_of_stocks_sequence(self):
        self.start()
        self.wpa('_up')
        self.connected('"Home"', '"home secret"', 'Home')
        self.wpa('remove_network', 'all')   # stock's first step; nothing saved yet
        asked = len(self.asked())
        time.sleep(1.5)
        self.assertNotIn('add_network ', self.asked()[asked:], 'wpa_supplicant differs from the file: it waits')
        # The sequence's end (added, saved): now Home comes back after it.
        self.connected('"Office"', '"office pass"', 'Office')
        until = time.monotonic() + 10
        while len(self.conf()) < 2 and time.monotonic() < until:
            time.sleep(0.05)
        self.assertEqual([b['ssid'] for b in self.conf()], ['"Office"', '"Home"'])

    def test_a_reset_forgets_and_an_emptied_configuration_gets_them_back(self):
        self.start()
        self.wpa('_up')
        self.connected('"Home"', '"home secret"', 'Home')
        self.connected('"Office"', '"office pass"', 'Office')
        self.report(lambda r: len(r['networks']) == 2)
        # Emptied while Wi-Fi is on (stock removed everything and saved): they come back.
        self.wpa('remove_network', 'all')
        self.wpa('save_config')
        until = time.monotonic() + 10
        while len(self.conf()) < 2 and time.monotonic() < until:
            time.sleep(0.05)
        self.assertEqual(sorted(b['ssid'] for b in self.conf()), ['"Home"', '"Office"'])
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
        report = self.report(lambda r: len(r['networks']) == 8 and all(n['held'] for n in r['networks']))
        self.assertEqual(sorted(self.names(report)), sorted(f'net{k}' for k in range(2, 10)))
        self.assertNotIn('"net1"', [n['ssid'] for n in self.store()])

    def test_open_and_utf8_networks_and_what_it_does_not_copy(self):
        self.start()
        self.wpa('_up')
        home = 'Дом'.encode().hex()
        report = self.connected(home, 'NONE', 'Дом')
        self.assertEqual(report['networks'], [dict(name='Дом', open=True, held=True)])
        self.assertEqual((self.store()[0]['ssid'], self.store()[0]['keyMgmt'], self.store()[0]['psk']), (home, 'NONE', None))
        self.connected('"Office"', '"office pass"', 'Office')
        until = time.monotonic() + 10
        while len(self.conf()) < 2 and time.monotonic() < until:
            time.sleep(0.05)
        self.assertEqual((self.conf()[1]['ssid'], self.conf()[1]['key_mgmt']), (home, 'NONE'))
        # A network with settings it does not copy (EAP) is not kept.
        self.wpa('_stock_connect', '"Work"', '"x"')
        self.wpa('set_network', '0', 'eap', 'PEAP')
        self.wpa('save_config')
        report = self.report(lambda r: r['connected'] == 'Work' and (r['lastChange'] or {}).get('what', '').startswith('not kept'))
        self.assertNotIn('Work', self.names(report))


if __name__ == '__main__':
    unittest.main()
