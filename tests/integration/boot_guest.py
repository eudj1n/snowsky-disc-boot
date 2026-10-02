#!/usr/bin/env python3
"""The boot layer's guest acceptance (plan, stage 2), inside the emulator's container.

Run through scripts/guest.py on a guest of a boot-layer image:

    python3 scripts/guest.py run -- python3 -B /boot/tests/integration/boot_guest.py --output /work/boot-guest.json

The guest is driven only as a player is: power events, keys held at power-on,
and the card, written while the guest is off. Probe packages are shell scripts
that behave as their package says (healthy, exit before ready, exit after
ready); a job left on the card makes the running probe stage the next version
into its inactive slot and ask boot to activate it, or ask for a rollback.
Real timings: a version is confirmed after 180 s of running.
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
import time

sys.path.insert(0, '/boot/scripts')
import package  # noqa: E402
from firmware_profile import load_profile  # noqa: E402

PROFILE = load_profile()['version']
ROOTFS = Path('/work/rootfs')
SCRIPTS = '/repo/emulator/scripts'
CARD = Path('/mnt/boot-guest-card')
NAME, UI_NAME = 'disc-probe', 'disc-probe-ui'
DATA = f'/usr/data/disc-boot/data/{NAME}'
SERVICE = r'''#!/bin/sh
# A probe service for the boot layer's guest acceptance: it behaves as its package says.
data="$DISC_BOOT_DATA"; card="$DISC_BOOT_CARD"; slot="$DISC_BOOT_SLOT"
read version < "$slot/version"; read behavior < "$slot/behavior"
log() { echo "$(date +%s) $version $*" >> "$data/probe.log"; }
log "start $behavior"
[ "$behavior" = exit-before-ready ] && { log exit; exit 3; }
: > "$DISC_BOOT_RUN/ready"
trap 'log stop; exit 0' TERM
n=0
while :; do
  sleep 1; n=$((n + 1))
  if [ "$behavior" = exit-after-ready ] && [ $n -ge 15 ]; then log exit; exit 4; fi
  for job in "$card"/.disc/probe/*.job; do
    [ -f "$job" ] || continue
    id=${job##*/}; id=${id%.job}
    grep -qx "$id" "$data/done" 2>/dev/null && continue
    echo "$id" >> "$data/done"
    read action < "$job"
    if [ "$action" = activate ]; then
      inactive="${DISC_BOOT_INACTIVE:?}"
      rm -rf "${inactive:?}"/*
      cp -R "$card/.disc/probe/$id/." "$inactive/"
      # A FAT card keeps no modes: the package lists them.
      while read path mode; do chmod "$mode" "$inactive/$path"; done < "$inactive/modes"
      "$DISC_BOOT_PROGRAM" verify service "$inactive" > "$data/verify-$id.json"
    fi
    printf '{"action":"%s"}' "$action" > "$DISC_BOOT_REQUEST.new" && mv "$DISC_BOOT_REQUEST.new" "$DISC_BOOT_REQUEST"
    log "request $action $id"
    exit 0
  done
done
'''
UI = r'''#!/bin/sh
# A probe UI: records its start, says it is ready, then becomes stock's own UI.
read version < "$DISC_BOOT_SLOT/version"
echo "$(date +%s) $version ui" >> "$DISC_BOOT_DATA/probe.log"
: > "$DISC_BOOT_RUN/ready"
exec /usr/bin/mq_ui "$@"
'''
evidence = {'profile': PROFILE, 'steps': []}


def shell(script, timeout=60, check=True, **environment):
    return subprocess.run(['bash', '-c', f'source {SCRIPTS}/lib.sh; {script}'], check=check, text=True, capture_output=True,
                          timeout=timeout, env={**os.environ, **environment}).stdout


def guest(command, check=False):
    return shell('guest_run 20 /bin/sh -c "$COMMAND"', check=check, COMMAND=command)


def guest_json(path):
    text = guest(f'cat {path} 2>/dev/null')
    try:
        return json.loads(text) if text.strip() else None
    except json.JSONDecodeError:
        return None


def machine():
    return json.loads(shell(f'bash {SCRIPTS}/25_power.sh status'))


def power(event, hold='', unsynced=False):
    args = [event] + (['--unsynced'] if unsynced else [])
    subprocess.run(['bash', f'{SCRIPTS}/25_power.sh', *args], check=True, timeout=600,
                   env={**os.environ, 'BOOT_MODE': 'init', 'BOOT_KEYS': hold})


def step(name, **facts):
    evidence['steps'].append({'step': name, **facts})
    print(f'[boot-guest] {name}: {json.dumps(facts)}', flush=True)


def wait(read, predicate, label, timeout):
    until, value = time.monotonic() + timeout, None
    while time.monotonic() < until:
        value = read()
        if value is not None and predicate(value):
            return value
        time.sleep(3)
    raise AssertionError(f'{label}: {value}')


def boot_status():
    return wait(lambda: guest_json('/run/disc-boot/boot.json'), lambda b: True, 'boot.json', 120)


def service(predicate, label, timeout=300):
    return wait(lambda: guest_json('/run/disc-boot/service.json'), predicate, label, timeout)


def confirmed(version, timeout=300):
    return service(lambda s: s['state'] == 'confirmed' and s['version'] == version, f'version {version} confirmed', timeout)


def back_to(version, request, label):
    return service(lambda s: s['version'] == version and s['lastRequest'] == request and s['state'] in ('ready', 'confirmed'),
                   label, 420)


@contextmanager
def card():
    """The card, mounted here while the guest is off: no two writers on one FAT."""
    if machine().get('state') not in ('off', None):
        raise AssertionError(f'the card is written only with the guest off: {machine()}')
    node = ROOTFS/'dev/mmcblk0p1'
    kind = subprocess.run(['blkid', '-o', 'value', '-s', 'TYPE', str(node)], capture_output=True, text=True).stdout.strip() or 'vfat'
    CARD.mkdir(parents=True, exist_ok=True)
    subprocess.run(['mount', '-t', kind, '-o', 'iocharset=utf8', str(node), str(CARD)], check=True)
    try:
        yield CARD
    finally:
        os.sync()
        subprocess.run(['umount', str(CARD)], check=True)


def probe(folder, version, behavior='healthy', role='service'):
    entry = 'bin/run' if role == 'service' else 'bin/mq_ui'
    (folder/'bin').mkdir(parents=True)
    files = {entry: (SERVICE if role == 'service' else UI, 0o755), 'version': (version + '\n', 0o644),
             'behavior': (behavior + '\n', 0o644)}
    files['modes'] = (''.join(f'{path} {mode:04o}\n' for path, (_, mode) in files.items()) + 'modes 0644\n', 0o644)
    for path, (text, mode) in files.items():
        (folder/path).write_text(text)
        (folder/path).chmod(mode)
    package.describe(folder, NAME if role == 'service' else UI_NAME, version, role, entry, ready=30, profiles=[PROFILE])
    return folder


def job(work, action, version=None, behavior='healthy'):
    """A job for the running probe, left on the card with the guest off."""
    ident = f'job-{time.time_ns()}'
    with card() as root:
        jobs = root/'.disc/probe'
        jobs.mkdir(parents=True, exist_ok=True)
        if action == 'activate':
            shutil.copytree(probe(work/ident, version, behavior), jobs/ident)
        (jobs/f'{ident}.job').write_text(action + '\n')
    return ident


def stop(name):
    """SIGTERM to the guest's processes of that name, from the container (the guest has no pkill)."""
    for process in Path('/proc').iterdir():
        try:
            if process.name.isdecimal() and (process/'root').resolve() == ROOTFS and (process/'comm').read_text().strip() == name:
                os.kill(int(process.name), 15)
        except OSError:
            continue


def stock_ui_runs():
    return bool(guest('pgrep -x mq_ui').strip()) and bool(guest('pgrep -x mq_player').strip())


def run(output):
    work = Path(tempfile.mkdtemp())
    power('off')
    # 1. Nothing installed: the default mode, nothing started, stock's UI through the wrapper.
    power('on')
    boot = boot_status()
    assert (boot['mode'], boot['reason'], boot['keys']['read']) == ('platform', 'default', True), boot
    assert wait(lambda: stock_ui_runs() or None, bool, 'stock UI', 120)
    assert not guest('ls /run/disc-boot/ui-launch 2>/dev/null').strip()
    step('nothing installed', boot=boot, service=guest_json('/run/disc-boot/service.json'))
    power('off')
    # 2. Volume Up held: stock mode.
    power('on', hold='volume_up')
    boot = boot_status()
    assert (boot['mode'], boot['reason'], boot['keys']['volumeUp']) == ('stock', 'key', True), boot
    step('volume up', boot=boot)
    power('off')
    # 3. A damaged staged package and Play: refused, nothing installed, the package stays on the card.
    with card() as root:
        package.stage(probe(work/'v1', '1'), root, profile=PROFILE)
        run_file = root/'.disc/boot/install/service/bin/run'
        run_file.write_text(run_file.read_text().replace('A probe', 'X probe', 1))  # same size, another hash
    power('on', hold='play')
    boot = boot_status()
    time.sleep(20)
    power('off')
    with card() as root:
        result = json.loads((root/'.disc/boot/result.json').read_text())
        assert result['roles']['service']['installed'] is False and 'sha256' in result['roles']['service']['note'], result
        assert (root/'.disc/boot/install/service').is_dir()
        # 4. The good package and Play: installed, ready, confirmed after 180 s.
        package.stage(work/'v1', root, profile=PROFILE)
    step('damaged package refused', boot=boot, result=result)
    power('on', hold='play')
    service(lambda s: s['state'] in ('ready', 'confirmed') and s['version'] == '1', 'version 1 ready')
    started = time.monotonic()
    status = confirmed('1')
    boot = guest_json('/run/disc-boot/boot.json')
    assert (boot['reason'], status['slot'], status['previous']) == ('recovery', 'a', None), (boot, status)
    step('installed with play', boot=boot, service=status, confirmedAfter=round(time.monotonic() - started))
    power('off')
    with card() as root:
        assert json.loads((root/'.disc/boot/result.json').read_text())['roles']['service']['installed'] is True
    # 5. An update staged by the running version and activated: version 2, confirmed; 1 kept for a rollback.
    job(work, 'activate', '2')
    power('on')
    status = confirmed('2', 420)
    assert (status['slot'], status['lastRequest'], status['previous']['version']) == ('b', f'activated {NAME} 2', '1'), status
    step('activated', service=status)
    # 6. A version that exits before ready: back to version 2.
    power('off')
    job(work, 'activate', '3', 'exit-before-ready')
    power('on')
    status = back_to('2', f'activated {NAME} 3', 'version 3 gave way to 2')
    step('exit before ready rolled back', service=status, log=guest(f'tail -n 8 {DATA}/probe.log'))
    # 7. A version that exits after ready, before its confirmation: back to version 2.
    power('off')
    job(work, 'activate', '4', 'exit-after-ready')
    power('on')
    status = back_to('2', f'activated {NAME} 4', 'version 4 gave way to 2')
    step('exit after ready rolled back', service=status, log=guest(f'tail -n 8 {DATA}/probe.log'))
    # 8. A healthy version 5, confirmed, then a rollback asked for: version 2 again.
    power('off')
    job(work, 'activate', '5')
    power('on')
    status = confirmed('5', 420)
    assert status['previous']['version'] == '2', status
    power('off')
    job(work, 'rollback')
    power('on')
    status = back_to('2', f'rolled back to {NAME} 2', 'rolled back to 2')
    step('rollback asked for', service=status)
    # The boot-loop count clears only once the restored version has run its 180 s again.
    confirmed('2')
    # 9. Power lost while a new version is tentative, three times: the fourth boot is stock (boot-loop);
    #    Play then runs the platform again and the version confirms, which clears the count.
    power('off')
    job(work, 'activate', '6')
    power('on')
    service(lambda s: s['version'] == '6' and s['state'] == 'ready', 'version 6 tentative', 420)
    time.sleep(5)
    power('cut', unsynced=True)
    counts = []
    for _ in range(2):
        power('on')
        counts.append(boot_status().get('unconfirmedBoots'))
        service(lambda s: s['version'] == '6' and s['state'] == 'ready', 'version 6 tentative again', 300)
        power('cut', unsynced=True)
    power('on')
    loop = boot_status()
    assert (loop['mode'], loop['reason']) == ('stock', 'boot-loop'), loop
    assert wait(lambda: stock_ui_runs() or None, bool, 'stock UI in the boot-loop fallback', 120)
    power('off')
    power('on', hold='play')
    status = confirmed('6', 420)
    step('power loss and the boot-loop guard', counts=counts, loop=loop, after=guest_json('/run/disc-boot/boot.json'), service=status)
    # 10. A ui package with Play: stock's watch loop starts it through the launcher; confirmed;
    #     killed, it comes back with the player as stock's loop restarts both.
    power('off')
    with card() as root:
        package.stage(probe(work/'ui1', '1', role='ui'), root, profile=PROFILE)
    power('on', hold='play')
    wait(lambda: guest_json('/run/disc-boot/ui.json'), lambda u: u['state'] in ('ready', 'confirmed'), 'ui ready', 300)
    assert guest('ls /run/disc-boot/ui-launch').strip()
    stop('mq_ui')
    starts = wait(lambda: guest(f'grep -c " ui" /usr/data/disc-boot/data/{UI_NAME}/probe.log') or None,
                  lambda count: int(count.strip() or 0) >= 2, 'ui package started again', 120)
    ui = wait(lambda: guest_json('/run/disc-boot/ui.json'), lambda u: u['state'] == 'confirmed', 'ui confirmed', 420)
    assert stock_ui_runs()
    step('ui package', ui=ui, starts=int(starts.strip()))
    power('off')
    evidence['status'] = 'passed'
    Path(output).write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps({'status': 'passed', 'steps': [s['step'] for s in evidence['steps']]}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        run(args.output)
    except BaseException as error:
        evidence['status'] = f'failed: {error}'
        evidence['service'] = guest_json('/run/disc-boot/service.json')
        evidence['boot'] = guest_json('/run/disc-boot/boot.json')
        Path(args.output).write_text(json.dumps(evidence, indent=2, default=str) + '\n')
        raise
