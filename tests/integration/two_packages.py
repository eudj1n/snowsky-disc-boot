#!/usr/bin/env python3
"""Two independent packages through the boot layer, inside the emulator's container (plan, stage 3).

Our server (snowsky-disc-server's service package) and diskOS's UI as the ui
package (tests/integration/diskos_package.py, built locally), with the player
page installed through the server's application manager:

    python3 scripts/guest.py run -- python3 -B /boot/tests/integration/two_packages.py \\
        --server /boot/work/<run>/server.zip --ui /boot/work/<run>/diskos --app /boot/work/<run>/page.zip \\
        --output /work/two-packages.json

Both are staged on the card and installed with Play, then: both confirmed; diskOS's
UI under stock's watch loop, past its start-up (it holds the touch panel), and
stock's player through diskOS's own launcher (its card guard, its local-mode check,
its verdict); the server answering and serving the page beside it; a busy card kept through stock's card event; the pair killed
and brought back by stock's loop with the server untouched; stock mode with both
installed (Volume Up); and a broken diskOS update giving way to the confirmed one
without touching the server. Real timings: a version is confirmed after 180 s.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import boot_guest as bg  # noqa: E402

PORT, MANAGER_PORT = 7870, 7871
SERIAL = '00000000000000'  # the guest's synthetic serial number (scripts/guest.py)
UI = '/usr/data/disc-boot/ui'
counter = iter(range(10000))


def http(port, method, path, body=None, change=False, content_type='application/json', raw=False):
    headers = {'Host': f'127.0.0.1:{port}'}
    if change:
        headers.update({'X-Disc-Token': SERIAL, 'X-Disc-Request': f'two-{time.time_ns()}-{next(counter)}',
                        'Content-Type': content_type})
    request = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
            return response.status, data if raw else json.loads(data or b'null')
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode(errors='replace').strip()
    except OSError as error:
        return None, str(error)


def ui_status():
    return bg.guest_json('/run/disc-boot/ui.json')


def ui_confirmed(version, timeout=480):
    return bg.wait(ui_status, lambda u: u['state'] == 'confirmed' and u['version'] == version, f'ui {version} confirmed', timeout)


def cmdline(pid):
    return Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')


def environ(pid):
    pairs = (entry.split(b'=', 1) for entry in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0') if b'=' in entry)
    return {key.decode(): value.decode(errors='replace') for key, value in pairs}


def holds(pid, device):
    """Whether the process holds the guest's input device (its touch panel: event1)."""
    try:
        return any(fd.resolve() == bg.ROOTFS/'dev/input'/device for fd in Path(f'/proc/{pid}/fd').iterdir())
    except OSError:
        return False


def diskos_runs(slot):
    """diskOS's UI running (past its start-up: it holds the touch panel), stock's player started by its
    launcher with both guards first."""
    # By its binary: the launcher's watcher (until the confirmation) and diskOS's own helpers share the name.
    binary = f'{slot}/diskos/mq_ui'.encode()
    uis = sorted(p for p in bg.guest_pids('mq_ui') if binary in cmdline(p) and holds(p, 'event1'))
    players = bg.guest_pids('mq_player')
    if not uis or len(players) != 1:
        return None
    ui_args, player_env = cmdline(uis[0]), environ(players[0])
    verdict = bg.guest('cat /tmp/.diskos_launch_status 2>/dev/null').strip()
    if verdict != f'guard=1 local=1 pid={guest_pid(players[0])}':
        return None
    return dict(ui=uis[0], player=players[0], uiArgs=[a.decode() for a in ui_args if a][-2:], verdict=verdict,
                playerPath=player_env.get('PATH'), uiRole=environ(uis[0]).get('DISC_BOOT_ROLE'))


def guest_pid(pid):
    """The pid the guest sees (its own PID namespace), as diskOS records it."""
    for line in Path(f'/proc/{pid}/status').read_text().splitlines():
        if line.startswith('NSpid:'):
            return int(line.split()[-1])
    return pid


def service_pid():
    pids = [p for p in bg.guest_pids('disc-service')]
    return pids[0] if len(pids) == 1 else None


def busy_card_kept(mount):
    """Stock's card event while a card file is open: stock's player removes the mount point after a failed
    unmount; with diskOS's launcher its guard comes first, ours after it; either refusal keeps the card."""
    card_root = bg.ROOTFS/mount.lstrip('/')
    before = sorted(str(p.relative_to(card_root)) for p in card_root.rglob('*'))
    held = next(p for p in card_root.rglob('*') if p.is_file() and not p.name.startswith('.'))
    with held.open('rb'):
        bg.wait(lambda: bg.listener_ready() or None, bool, 'stock player listening for card events', 60)
        bg.Peripherals(bg.Device(bg.ROOTFS))._event('add')
        time.sleep(10)
    after = sorted(str(p.relative_to(card_root)) for p in card_root.rglob('*'))
    logs = bg.guest(f'grep -h "{mount}" /usr/data/diskos_rmguard.log /run/disc-boot/guard.log 2>/dev/null').splitlines()
    assert after == before and logs, (len(before), len(after), logs)
    return dict(files=len(before), refusals=logs[-2:])


def card_mounted(mount):
    """The card mounted, by the emulator: at the end of its boot and again after stock's
    player was restarted in mid-run (stock's player unmounts the card when it starts)."""
    card_root = bg.ROOTFS/mount.lstrip('/')
    return bg.wait(lambda: subprocess.run(['mountpoint', '-q', str(card_root)]).returncode == 0 or None, bool,
                   'card mounted', 90)


def screenshot(prefix):
    bg.shell(f'cd /repo && bash {bg.SCRIPTS}/capture.sh {prefix}', timeout=60, check=False)
    return sorted(str(p) for p in Path('/work/shots').glob(f'{prefix}*.png'))


def broken(ui, work):
    """The same diskOS package whose entry exits at once: a tentative version that never confirms."""
    folder = work/'diskos-broken'
    shutil.copytree(ui, folder)
    (folder/'package.json').unlink()
    (folder/'mq_ui').write_text('#!/bin/sh\necho broken >&2\nexit 1\n')
    manifest = json.loads((Path(ui)/'package.json').read_text())
    bg.package.describe(folder, manifest['name'], manifest['version'] + '-broken', 'ui', 'mq_ui', ready=30,
                        player=manifest['player'], profiles=manifest['profiles'], arch=manifest['arch'])
    return folder


def run(server, ui, app, output):
    bg.evidence.clear()
    bg.evidence.update(profile=bg.PROFILE, steps=[])
    work = Path(tempfile.mkdtemp())
    ui_version = json.loads((ui/'package.json').read_text())['version']
    bg.power('off')
    with bg.card() as root:
        for staged in (server, ui):
            bg.package.stage(staged, root, profile=bg.PROFILE)
    # 1. Play installs both; both confirm.
    bg.power('on', hold='play')
    service = bg.service(lambda s: s['state'] == 'confirmed', 'server confirmed', 480)
    ui_state = ui_confirmed(ui_version)
    boot = bg.guest_json('/run/disc-boot/boot.json')
    assert boot['reason'] == 'recovery', boot
    slot = f'{UI}/{ui_state["name"]}/{ui_state["slot"]}'
    runs = bg.wait(lambda: diskos_runs(slot), bool, "diskOS's UI with stock's player through its launcher", 120)
    # diskOS's guard first, then the boot layer's, in stock's player's PATH.
    assert runs['playerPath'].startswith('/usr/data/diskos/bin:/opt/disc-boot/guard:'), runs
    player = bg.guest_json('/run/disc-boot/ui/player.json')
    assert player['launch'] == 'package' and player['name'] == 'diskos', player
    assert bg.guest('cat /tmp/.diskos_boot_select').strip() == 'diskos'
    card_mounted(boot['card'])
    bg.step('both installed and confirmed', boot=boot, service=service, ui=ui_state, player=player, runs=runs,
            screens=screenshot('two-installed'),
            rmGuardLog=bg.guest('tail -n 4 /usr/data/diskos_rmguard.log 2>/dev/null').splitlines())
    # 2. The server beside diskOS: it answers, installs the page and serves it.
    status, health = http(PORT, 'GET', '/api/health')
    assert status == 200, (status, health)
    status, installed = http(MANAGER_PORT, 'POST', '/api/apps', Path(app).read_bytes(), change=True, content_type='application/zip')
    assert status in (200, 201), (status, installed)
    status, page = http(PORT, 'GET', '/', raw=True)
    assert status == 200 and b'<html' in page.lower(), (status, page[:200])
    bg.step('server beside diskOS', health=health, app=installed, pageBytes=len(page))
    # 3. A busy card through stock's card event stays whole.
    bg.step('busy card kept', **busy_card_kept(boot['card']))
    # 4. Stock's loop restarts the pair when the UI dies; the server's process is untouched.
    server_before = service_pid()
    bg.stop('mq_ui')
    again = bg.wait(lambda: (lambda r: r if r and r['player'] != runs['player'] else None)(diskos_runs(slot)), bool,
                    'the pair restarted through the launchers', 120)
    assert service_pid() == server_before and server_before, (server_before, service_pid())
    card_mounted(boot['card'])
    bg.step('the pair restarted by stock', runs=again, serverPid=server_before)
    # 5. Volume Up with both installed: stock mode, stock's UI and player, no package running.
    bg.power('off')
    bg.power('on', hold='volume_up')
    boot = bg.boot_status()
    assert (boot['mode'], boot['reason']) == ('stock', 'key'), boot
    stock = bg.wait(lambda: (bg.guest_pids('mq_ui') and bg.guest_pids('mq_player')
                             and b'/usr/bin/mq_ui' in cmdline(bg.guest_pids('mq_ui')[0])) or None, bool, "stock's UI", 120)
    assert not service_pid() and not bg.guest('cat /tmp/.diskos_launch_status 2>/dev/null').strip()
    bg.step('stock mode with both installed', boot=boot, stockUi=bool(stock), service=bg.guest_json('/run/disc-boot/service.json'))
    # 6. A broken diskOS update with Play gives way to the confirmed one; the server stays as it was.
    bg.power('off')
    with bg.card() as root:
        bg.package.stage(broken(ui, work), root, profile=bg.PROFILE)
    bg.power('on', hold='play')                   # diskOS's UI from the start of the boot
    back = bg.wait(ui_status, lambda u: u['version'] == ui_version and u['slot'] == ui_state['slot'],
                   'diskOS back after the broken update', 300)
    after = bg.service(lambda s: s['state'] == 'confirmed', 'server confirmed after the ui rollback', 480)
    assert (after['version'], after['slot']) == (service['version'], service['slot']), (service, after)
    runs = bg.wait(lambda: diskos_runs(slot), bool, "diskOS's UI again", 120)
    bg.power('off')
    with bg.card() as root:
        result = json.loads((root/'.disc/boot/result.json').read_text())
    staged = result['roles']['ui'][ui_state['name']]
    assert staged['installed'] and staged['note'].endswith('-broken'), result
    assert 'service' not in result['roles'], result
    bg.step('broken ui update rolled back', result=result, ui=back, service=after, runs=runs)
    bg.evidence['status'] = 'passed'
    Path(output).write_text(json.dumps(bg.evidence, indent=2) + '\n')
    print(json.dumps({'status': 'passed', 'steps': [s['step'] for s in bg.evidence['steps']]}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--server', type=Path, required=True, help="The server's service package (zip or folder)")
    parser.add_argument('--ui', type=Path, required=True, help="diskOS's ui package folder (diskos_package.py)")
    parser.add_argument('--app', type=Path, required=True, help="The player page's release zip")
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        run(args.server, args.ui, args.app, args.output)
    except BaseException as error:
        bg.evidence['status'] = f'failed: {error}'
        bg.evidence['ui'] = bg.guest_json('/run/disc-boot/ui.json')
        bg.evidence['service'] = bg.guest_json('/run/disc-boot/service.json')
        Path(args.output).write_text(json.dumps(bg.evidence, indent=2, default=str) + '\n')
        raise
