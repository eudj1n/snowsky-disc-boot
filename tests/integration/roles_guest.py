#!/usr/bin/env python3
"""The roles of boot API 2 on the guest (plan, stage 7): the controller and the services.

Run through scripts/guest.py on a guest of a boot-layer image, after boot_guest.py's own checks:

    python3 scripts/guest.py run -- python3 -B /boot/tests/integration/roles_guest.py --output /work/roles-guest.json

The probes are boot_guest.py's (shell scripts that behave as their package says, and take the jobs
left on the card for them). In order:

1. The layout of boot API 1 as the server left it (service/, role service, bootApi 1, confirmed):
   the first boot moves it to controller/ once and says so in the boot log; it runs as the
   controller, told the role it names, its status also where it read it (service.json).
2. That server updates itself to a controller of boot API 2: it checks the update as "service",
   asks for its activation, and the new version reads controller.json (service.json goes).
3. Two services staged on the card and installed with Play: one healthy, one that exits before
   it is ready. The healthy one starts after the controller, at nice +10, and is confirmed after
   180 s; the failing one stops and says why, and the boot-loop count clears all the same.
4. A service whose autostart is off does not start.
5. Volume Up: stock mode, nothing of ours runs.

qemu-user ignores a guest's RLIMIT_AS (linux-user's setrlimit), so the memory bound is only
recorded here (the process's limits as the container sees them); it takes effect on the player.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import boot_guest as bg  # noqa: E402

HEALTH, FAILING = 'probe-health', 'probe-failing'
DATA = '/usr/data/disc-boot'


@contextmanager
def userdata():
    """/usr/data (the guest's own ext4 image, as the player's partition), written here while the
    guest is off: where it is mounted already, else mounted from its node for the change."""
    if bg.machine().get('state') not in ('off', None):
        raise AssertionError(f'/usr/data is written only with the guest off: {bg.machine()}')
    target = bg.ROOTFS/'usr/data'
    mounted = subprocess.run(['mountpoint', '-q', str(target)]).returncode == 0
    if not mounted:
        target = Path('/mnt/roles-guest-userdata')
        target.mkdir(parents=True, exist_ok=True)
        subprocess.run(['mount', '-t', 'ext4', str(bg.ROOTFS/'dev/ubi1_0'), str(target)], check=True)
    try:
        yield target
    finally:
        os.sync()
        if not mounted:
            subprocess.run(['umount', str(target)], check=True)


def service(name, predicate, label, timeout=300):
    return bg.wait(lambda: bg.guest_json(f'/run/disc-boot/service/{name}.json'), predicate, label, timeout)


def bounds(domain):
    """The running probe's niceness and address-space limit, read from the container: its process
    runs the slot's entry (qemu-user answers a guest's own /proc/self/stat itself)."""
    entry = f'{DATA}/{domain}/'.encode()
    for process in Path('/proc').iterdir():
        try:
            if not process.name.isdecimal() or (process/'root').resolve() != bg.ROOTFS or entry not in (process/'cmdline').read_bytes():
                continue
            nice = int((process/'stat').read_text().rsplit(')', 1)[1].split()[16])
            limit = next(line for line in (process/'limits').read_text().splitlines() if line.startswith('Max address space'))
            return dict(nice=nice, addressSpace=' '.join(limit.split()[3:5]))
        except (OSError, ValueError, IndexError, StopIteration):
            continue
    raise AssertionError(f'no running process of {domain}')


def run(output):
    work = Path(tempfile.mkdtemp())
    bg.power('off')
    # A guest after other runs: their probes' jobs and staged packages leave the card.
    with bg.card() as root:
        for left in ('.disc/probe', '.disc/boot/install'):
            shutil.rmtree(root/left, ignore_errors=True)
    # 1. The server as boot API 1 left it: service/ with its slot a, confirmed.
    legacy = bg.probe(work/'v1', '1', role='service', boot_api=1, name=bg.NAME)
    with userdata() as root:
        data = root/'disc-boot'
        shutil.rmtree(data, ignore_errors=True)
        shutil.copytree(legacy, data/'service/a')
        (data/'service/state.json').write_text(json.dumps(dict(schema=1, current='a', confirmed=True, previous=None, previousManifest=None)))
    bg.power('on')
    boot = bg.boot_status()
    status = bg.controller(lambda s: s['state'] in ('ready', 'confirmed') and s['version'] == '1', 'the server of boot API 1 runs')
    alias = bg.guest_json('/run/disc-boot/service.json')
    assert (alias or {}).get('name') == bg.NAME, alias
    assert not bg.guest(f'ls -d {DATA}/service 2>/dev/null').strip(), 'service/ is gone'
    log = bg.guest(f'cat {DATA}/boot.log')
    assert log.count('layout: service/ of boot API 1 became controller/') == 1, log[-2000:]
    nice = bounds('controller')
    assert nice['nice'] == 5, nice
    bg.step('the layout of boot API 1 moved', boot=boot, controller=status, alias=alias, bounds=nice)
    # 2. It updates itself to a controller of boot API 2.
    bg.power('off')
    ident = bg.job(work, 'activate', '2', role='controller')
    bg.power('on')
    status = bg.confirmed('2', 420)
    assert (status['slot'], status['lastRequest'], status['previous']['version']) == ('b', f'activated {bg.NAME} 2', '1'), status
    verify = bg.guest_json(f'{DATA}/data/{bg.NAME}/verify-{ident}.json')
    assert verify and verify['ok'], verify
    assert not bg.guest('ls /run/disc-boot/service.json 2>/dev/null').strip(), 'a controller of boot API 2 reads controller.json'
    assert bg.guest(f'cat {DATA}/boot.log').count('became controller/') == 1, 'moved once'
    bg.step('the server of boot API 1 updated to a controller of boot API 2', controller=status, verify=verify)
    # 3. Two services with Play: a healthy one and one that exits before it is ready.
    bg.power('off')
    with bg.card() as root:
        bg.package.stage(bg.probe(work/'health', '1', role='service', name=HEALTH), root, profile=bg.PROFILE)
        bg.package.stage(bg.probe(work/'failing', '1', 'exit-before-ready', role='service', name=FAILING), root, profile=bg.PROFILE)
    bg.power('on', hold='play')
    failing = service(FAILING, lambda s: s['state'] == 'failed', 'the failing service stops')
    assert failing['note'] == 'exited before its confirmation', failing
    health = service(HEALTH, lambda s: s['state'] == 'confirmed', 'the healthy service confirmed', 420)
    controller = bg.controller(lambda s: s['state'] == 'confirmed', 'the controller confirmed', 420)
    assert controller['failures'] == 0 and health['autostart'] is True, (controller, health)
    started = {name: int(bg.guest(f'head -n 1 {DATA}/data/{name}/probe.log').split()[0]) for name in (bg.NAME, HEALTH)}
    # The controller's run in this boot (its log's last start) came before the service's first.
    last = int(bg.guest(f'grep " start " {DATA}/data/{bg.NAME}/probe.log | tail -n 1').split()[0])
    assert started[HEALTH] >= last, (started, last)
    nice = bounds(f'service/{HEALTH}')
    assert nice['nice'] == 10, nice
    state = json.loads(bg.guest(f'cat {DATA}/state.json'))
    assert state['unconfirmed'] == 0, ('a failing service never keeps the count', state)
    status = json.loads(bg.guest('/opt/disc-boot/disc-boot status'))
    assert sorted(status['services']) == [FAILING, HEALTH], status['services']
    runs = bg.count(f'{DATA}/data/{HEALTH}/probe.log', 'start')
    bg.power('off')
    with bg.card() as root:
        result = json.loads((root/'.disc/boot/result.json').read_text())
    assert result['roles']['service'][HEALTH]['installed'] and result['roles']['service'][FAILING]['installed'], result
    bg.step('services with play', result=result, health=health, failing=failing, controller=controller, bounds=nice,
            memory='recorded only: qemu-user ignores RLIMIT_AS', state=state)
    # 4. Autostart off: the service does not start.
    with userdata() as root:
        path = root/f'disc-boot/service/{HEALTH}/state.json'
        path.write_text(json.dumps(dict(json.loads(path.read_text()), autostart=False)))
    bg.power('on')
    off = service(HEALTH, lambda s: s['state'] == 'disabled', 'autostart off')
    bg.controller(lambda s: s['state'] in ('ready', 'confirmed'), 'the controller runs')
    assert bg.count(f'{DATA}/data/{HEALTH}/probe.log', 'start') == runs, 'not started'
    bg.step('autostart off', health=off)
    # 5. Volume Up: stock mode, nothing of ours runs.
    bg.power('off')
    bg.power('on', hold='volume_up')
    boot = bg.boot_status()
    assert (boot['mode'], boot['reason']) == ('stock', 'key'), boot
    stock = service(FAILING, lambda s: s['state'] == 'stock-mode', 'stock mode')
    assert bg.guest_json('/run/disc-boot/controller.json')['state'] == 'stock-mode'
    assert bg.wait(lambda: bg.stock_ui_runs() or None, bool, 'stock UI', 120)
    bg.step('volume up', boot=boot, service=stock, steady=bg.steady())
    bg.power('off')
    bg.evidence['status'] = 'passed'
    Path(output).write_text(json.dumps(bg.evidence, indent=2) + '\n')
    print(json.dumps({'status': 'passed', 'steps': [s['step'] for s in bg.evidence['steps']]}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        run(args.output)
    except BaseException as error:
        bg.evidence['status'] = f'failed: {error}'
        bg.evidence['controller'] = bg.guest_json('/run/disc-boot/controller.json')
        bg.evidence['services'] = {name: bg.guest_json(f'/run/disc-boot/service/{name}.json') for name in (HEALTH, FAILING)}
        bg.evidence['bootLog'] = bg.guest(f'tail -n 40 {DATA}/boot.log')
        Path(args.output).write_text(json.dumps(bg.evidence, indent=2, default=str) + '\n')
        raise
