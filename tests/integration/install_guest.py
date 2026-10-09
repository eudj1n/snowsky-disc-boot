#!/usr/bin/env python3
"""install.py's card on the guest (plan, stage 4), inside the emulator's container.

    python3 scripts/guest.py run -- python3 -B /boot/tests/integration/install_guest.py \\
        --server /boot/work/<run>/disc-server.zip --menu /boot/work/<run>/disc-menu.zip \\
        --apps-from /boot/work/<run> --output /work/install-guest.json

The card is written with the installer's own code (scripts/installer/card.py): the server and
the boot menu for the recovery with Play, the apps the server's catalog offers by default (the
player page, taken by its digest), and the console's marker. Then Play installs them: the
server is confirmed after its 180 s, the menu, with stock's UI as the only entry, counts down to
it, and the server serves the page from the card at /.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, '/boot/scripts')
import boot_guest as bg  # noqa: E402
from installer import card as cards  # noqa: E402


def run(server, menu, apps_from, output):
    bg.evidence.clear()
    bg.evidence.update(profile=bg.PROFILE, steps=[])
    work = Path(tempfile.mkdtemp())
    folders = [bg.package.unpack(server, work/'server'), bg.package.unpack(menu, work/'menu')]
    offered = [a for a in cards.app_entries(folders[0]) if a['default']]
    assert [a['name'] for a in offered] == ['Disc Player'], offered
    bg.power('off')
    with bg.card() as root:
        staged = cards.stage_packages(folders, root, bg.PROFILE)
        apps = cards.stage_apps(offered, root, [Path(apps_from)], False, work)
        marker = cards.write_marker(root)
    bg.step('the installer\'s card', packages=staged, apps=apps, marker=str(marker.relative_to(bg.CARD)))
    bg.power('on', hold='play')
    menu_status = bg.wait(lambda: bg.guest_json('/run/disc-boot/menu.json'), lambda m: m['state'] == 'answered', 'the menu answered', 300)
    choice = bg.guest_json('/run/disc-boot/ui/choice.json')
    assert (choice['ui'], choice['by']) == ('stock', 'menu'), choice
    controller = bg.controller(lambda s: s['state'] == 'confirmed', 'server confirmed', 480)
    with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:7870/', headers={'Host': '127.0.0.1:7870'}), timeout=60) as response:
        page = response.read()
    assert response.status == 200 and b'<html' in page.lower(), page[:200]
    bg.step('play installs, the menu starts stock\'s UI, the server serves the page', menu=menu_status, choice=choice,
            controller=controller, pageBytes=len(page))
    bg.power('off')
    with bg.card() as root:
        result = json.loads((root/'.disc/boot/result.json').read_text())
        kept = (root/'.disc/dev/usb-console').read_text()
    assert result['roles']['controller']['installed'] and result['roles']['menu']['installed'], result
    assert kept == cards.MARKER_TEXT, 'the console marker stays'
    bg.step('after the installation', result=result, marker=kept.strip())
    bg.evidence['status'] = 'passed'
    Path(output).write_text(json.dumps(bg.evidence, indent=2) + '\n')
    print(json.dumps({'status': 'passed', 'steps': [s['step'] for s in bg.evidence['steps']]}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--server', type=Path, required=True)
    parser.add_argument('--menu', type=Path, required=True)
    parser.add_argument('--apps-from', type=Path, required=True, help='A folder holding the apps\' release zips')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        run(args.server, args.menu, args.apps_from, args.output)
    except BaseException as error:
        bg.evidence['status'] = f'failed: {error}'
        bg.evidence['controller'] = bg.guest_json('/run/disc-boot/controller.json')
        bg.evidence['menu'] = bg.guest_json('/run/disc-boot/menu.json')
        Path(args.output).write_text(json.dumps(bg.evidence, indent=2, default=str) + '\n')
        raise
