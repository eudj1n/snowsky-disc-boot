#!/usr/bin/env python3
"""disc-network on the guest (plan, stage 7), inside the emulator's container.

    python3 scripts/guest.py run -- python3 -B /boot/tests/integration/network_guest.py \\
        --package /boot/work/<run>/disc-network-<version>.zip --output /work/network-guest.json

The emulator has no Wi-Fi: stock's wpa_cli is replaced in the guest's tree by the stand-in
(tests/integration/wpa_cli_stand_in.sh, BusyBox's shell runs it) for the run and put back after. The
release's package is staged as the installer stages it and installed with Play; the MIPS build then
runs the stand-in as it would run stock's wpa_cli: stock's connection to Home, then to Office (Home
added back after it, below it), a new start with Office out of range joining Home, and its
confirmation after 180 s. The report names the networks and never a key.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import boot_guest as bg  # noqa: E402

RUN = '/run/disc-boot/service/disc-network'
CLI = bg.ROOTFS/'usr/sbin/wpa_cli'
STAND_IN = Path(__file__).resolve().parent/'wpa_cli_stand_in.sh'


def status():
    return bg.guest_json('/run/disc-boot/service/disc-network.json')


def report(predicate, label, timeout=120):
    return bg.wait(lambda: bg.guest_json(f'{RUN}/status.json'), predicate, label, timeout)


def wpa(*args):
    quoted = ' '.join("'" + a.replace("'", "'\\''") + "'" for a in args)
    return bg.guest(f'/usr/sbin/wpa_cli -i wlan0 {quoted}')


def saved():
    """The guest's /usr/data/wpa_supplicant.conf: each network's ssid and priority, in order."""
    blocks, current = [], None
    for line in bg.guest('cat /usr/data/wpa_supplicant.conf 2>/dev/null').splitlines():
        line = line.strip()
        if line == 'network={':
            current = {}
        elif line == '}' and current is not None:
            blocks.append(current)
            current = None
        elif current is not None and '=' in line:
            key, value = line.split('=', 1)
            current[key] = value
    return blocks


def run(archive, output, stock):
    bg.evidence.clear()
    bg.evidence.update(profile=bg.PROFILE, steps=[])
    folder = bg.package.unpack(archive, Path(tempfile.mkdtemp())/'disc-network')
    manifest = bg.package.check(folder, 'service', bg.PROFILE)
    assert (manifest['bootApi'], manifest['memory']) == (2, 32), manifest
    bg.power('off')
    shutil.copy2(CLI, stock)
    shutil.copyfile(STAND_IN, CLI)
    CLI.chmod(0o755)
    with bg.card() as root:
        staged = bg.package.stage(folder, root, profile=bg.PROFILE)
    bg.power('on', hold='play')
    ready = bg.wait(status, lambda s: s['state'] in ('ready', 'confirmed'), 'disc-network ready', 300)
    off = report(lambda r: r['wifi'] == 'off', 'Wi-Fi off')
    bg.step('installed with play, Wi-Fi off', staged=staged, status=ready, report=off)
    # Stock connects to Home, then to Office: Home comes back after Office, below it.
    wpa('_up')
    wpa('_stock_connect', '"Home"', '"home secret"')
    home = report(lambda r: r['connected'] == 'Home' and [n['name'] for n in r['networks']] == ['Home'], 'Home kept')
    wpa('_stock_connect', '"Office"', '"office pass"')
    both = report(lambda r: r['connected'] == 'Office' and len(r['networks']) == 2 and all(n['held'] for n in r['networks']), 'Home back')
    blocks = saved()
    assert [(b['ssid'], b.get('priority')) for b in blocks] == [('"Office"', None), ('"Home"', '-1')], blocks
    text = bg.guest(f'cat {RUN}/status.json')
    assert 'secret' not in text and 'office pass' not in text, text
    mode = bg.guest('ls -l /usr/data/disc-boot/data/disc-network/networks.json').split()[0]
    assert mode == '-rw-------', mode
    bg.step('stock\'s connections kept, the earlier one added back', home=home, both=both, saved=blocks, storeMode=mode)
    confirmed = bg.wait(status, lambda s: s['state'] == 'confirmed', 'disc-network confirmed', 420)
    bg.step('confirmed', status=confirmed)
    # A new start: wpa_supplicant loads both from the file; Office out of range, Home in it.
    bg.power('off')
    bg.power('on')
    wpa('_up')
    wpa('_scan', '1')
    joined = report(lambda r: r['connected'] == 'Home' and len(r['networks']) == 2, 'Home joined at the next start')
    bg.step('the next start joins the network in range', report=joined, asked=bg.guest('tail -n 5 /run/wpa-stand-in/log').splitlines())
    bg.power('off')
    bg.evidence['status'] = 'passed'
    Path(output).write_text(json.dumps(bg.evidence, indent=2) + '\n')
    print(json.dumps({'status': 'passed', 'steps': [s['step'] for s in bg.evidence['steps']]}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    stock = Path(tempfile.mkdtemp())/'wpa_cli'
    try:
        run(args.package, args.output, stock)
    except BaseException as error:
        bg.evidence['status'] = f'failed: {error}'
        bg.evidence['network'] = status()
        bg.evidence['report'] = bg.guest_json(f'{RUN}/status.json')
        bg.evidence['asked'] = bg.guest('tail -n 30 /run/wpa-stand-in/log 2>/dev/null')
        Path(args.output).write_text(json.dumps(bg.evidence, indent=2, default=str) + '\n')
        raise
    finally:
        # Stock's wpa_cli back in the guest's tree, with the guest off.
        if stock.exists():
            if bg.machine().get('state') not in ('off', None):
                bg.power('off')
            shutil.copy2(stock, CLI)
