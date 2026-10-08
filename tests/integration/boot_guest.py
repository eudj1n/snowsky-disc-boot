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

Each boot that ends in stock's UI or a running service is watched for 45 s: the same UI and
player all along and no restart in stock's log. One boot runs without the boot program at all
(it cannot be executed), as on the owner's player on 2026-10-05: stock must run all the same.

Stock's card event is also sent while a file on the mounted card is open (as a
server streaming it holds one): stock's player then unmounts, fails and removes
the mount point with rm -rf. The emulator's own card controls refuse to send it
while the card is busy, so the test uses its uevent sender directly.
"""
import argparse
from contextlib import contextmanager
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, '/boot/scripts')
sys.path.insert(0, '/repo')
import package  # noqa: E402
from firmware_profile import load_profile  # noqa: E402
from emulator.runtime.keys import Device  # noqa: E402
from emulator.runtime.peripherals import Peripherals  # noqa: E402
try:  # the emulator's own rule (snowsky-disc-qemu f1d5e33 on, issue #57); older revisions lack it
    from emulator.runtime.boot_ready import watched as emulator_watched  # noqa: E402
except ImportError:
    emulator_watched = None

PROFILE = load_profile()['version']
ROOTFS = Path('/work/rootfs')
SCRIPTS = '/repo/emulator/scripts'
CARD = Path('/mnt/boot-guest-card')
NAME, UI_NAME, TWO, MENU_NAME = 'disc-probe', 'disc-probe-ui', 'disc-probe-two', 'disc-probe-menu'
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
    echo "$id" >> "$data/done"; sync
    read action < "$job"
    if [ "$action" = activate ]; then
      inactive="${DISC_BOOT_INACTIVE:?}"
      rm -rf "${inactive:?}"/*
      cp -R "$card/.disc/probe/$id/." "$inactive/"
      # A FAT card keeps no modes: the package lists them.
      while read path mode; do chmod "$mode" "$inactive/$path"; done < "$inactive/modes"
      # On the disk before the request, as a package's update does: a power cut keeps the slot whole.
      sync
      "$DISC_BOOT_PROGRAM" verify service "$inactive" > "$data/verify-$id.json"
    fi
    case "$action" in
      ui-*) set -- $action; printf '{"action":"%s","ui":"%s"}' "$1" "$2" > "$DISC_BOOT_REQUEST.new" ;;
      *) printf '{"action":"%s"}' "$action" > "$DISC_BOOT_REQUEST.new" ;;
    esac && mv "$DISC_BOOT_REQUEST.new" "$DISC_BOOT_REQUEST"
    log "request $action $id"
    exit 0
  done
done
'''
UI = r'''#!/bin/sh
# A probe UI: records its start, says it is ready, then becomes stock's own UI under the name
# stock's watch loop looks for (contract: started by its path it is not found).
read version < "$DISC_BOOT_SLOT/version"
echo "$(date +%s) $version ui" >> "$DISC_BOOT_DATA/probe.log"
: > "$DISC_BOOT_RUN/ready"
exec -a mq_ui /usr/bin/mq_ui "$@"
'''
PLAYER = r'''#!/bin/sh
# A probe player launcher: records its start, then becomes stock's player, as mq_player.
read version < "$DISC_BOOT_SLOT/version"
echo "$(date +%s) $version player" >> "$DISC_BOOT_DATA/probe.log"
exec -a mq_player /usr/bin/mq_player "$@"
'''
MENU = r'''#!/bin/sh
# A probe menu: records its turn and what it was offered, answers the second probe UI and hands over.
echo "$(date +%s) menu" >> "$DISC_BOOT_DATA/probe.log"
cp "$DISC_BOOT_STATUS/ui/choices.json" "$DISC_BOOT_DATA/choices.json"
printf '{"ui":"disc-probe-two"}' > "$DISC_BOOT_RUN/choice.new" && mv "$DISC_BOOT_RUN/choice.new" "$DISC_BOOT_RUN/choice"
exec "$DISC_BOOT_LAUNCHER"
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


def probe(folder, version, behavior='healthy', role='service', name=None):
    entry = 'bin/run' if role == 'service' else 'bin/mq_ui'
    (folder/'bin').mkdir(parents=True)
    files = {entry: ({'service': SERVICE, 'ui': UI, 'menu': MENU}[role], 0o755), 'version': (version + '\n', 0o644),
             'behavior': (behavior + '\n', 0o644)}
    if role == 'ui':
        files['bin/player'] = (PLAYER, 0o755)
    files['modes'] = (''.join(f'{path} {mode:04o}\n' for path, (_, mode) in files.items()) + 'modes 0644\n', 0o644)
    for path, (text, mode) in files.items():
        (folder/path).write_text(text)
        (folder/path).chmod(mode)
    name = name or {'service': NAME, 'ui': UI_NAME, 'menu': MENU_NAME}[role]
    package.describe(folder, name, version, role, entry, ready=30, profiles=[PROFILE],
                     player='bin/player' if role == 'ui' else None)
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


def guest_pids(name):
    """The guest's processes of that name, seen from the container."""
    pids = []
    for process in Path('/proc').iterdir():
        try:
            if process.name.isdecimal() and (process/'root').resolve() == ROOTFS and (process/'comm').read_text().strip() == name:
                pids.append(int(process.name))
        except OSError:
            continue
    return pids


def watched(name):
    """The guest's processes the player's pgrep -x NAME finds, the way stock's watch loop looks:
    BusyBox 1.31.1 matches argv[0] first and the process name only when NAME is nowhere in
    argv[0], so /usr/bin/mq_ui is not found (owner's player, 2026-10-04 and 05). Here argv[0] is
    cmdline's third field (qemu-user, then the file, then argv[0]); pgrep in the guest falls back
    to the name and cannot show it."""
    pids = []
    for process in Path('/proc').iterdir():
        try:
            if not process.name.isdecimal() or (process/'root').resolve() != ROOTFS:
                continue
            fields = [f.decode(errors='replace') for f in (process/'cmdline').read_bytes().split(b'\0')]
            argv0 = fields[2] if len(fields) > 2 and 'qemu' in Path(fields[0]).name else fields[0]
            if (argv0 if name in argv0 else (process/'comm').read_text().strip()) == name:
                pids.append(int(process.name))
        except OSError:
            continue
    return pids


def found_by_watch_loop():
    """Stock's watch loop on the player finds the running pair under the names it looks for.
    The two readings are taken apart, so a child of the pair caught on its way to exec (2026-10-06:
    mq_player 12230 beside 8589, gone at once) may show in one; they are read again until they
    agree, for at most two seconds; a lasting difference still fails."""
    for attempt in range(5):
        seen = {name: (sorted(guest_pids(name)), sorted(watched(name))) for name in ('mq_ui', 'mq_player')}
        if all(running and running == found for running, found in seen.values()):
            break
        time.sleep(0.5)
    assert all(running and running == found for running, found in seen.values()), seen
    if emulator_watched is not None:
        # The emulator's reading of the same rule agrees with this one, process by process.
        theirs = {name: sorted(pid for pid in guest_pids(name) if emulator_watched(name, pid)) for name in seen}
        assert theirs == {name: found for name, (_, found) in seen.items()}, (seen, theirs)
    return {name: found for name, (_, found) in seen.items()}


def stop(name):
    """SIGTERM to the guest's processes of that name, from the container (the guest has no pkill)."""
    for pid in guest_pids(name):
        try:
            os.kill(pid, 15)
        except OSError:
            continue


def guarded_player():
    """Stock's player runs with the card guard first in its PATH."""
    paths = []
    for pid in guest_pids('mq_player'):
        environ = Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
        paths += [entry.decode() for entry in environ if entry.startswith(b'PATH=')]
    assert paths and all(p.startswith('PATH=/opt/disc-boot/guard:') for p in paths), paths
    return paths[0]


def listener_ready():
    try:
        Peripherals(Device(ROOTFS))._listener()
        return True
    except (ValueError, OSError):
        return False


def busy_card_event(mount):
    """Stock's card event while a file on the mounted card is open: the card must stay whole."""
    card_root = ROOTFS/mount.lstrip('/')
    wait(lambda: subprocess.run(['mountpoint', '-q', str(card_root)]).returncode == 0 or None, bool, 'card mounted', 120)
    before = sorted(str(p.relative_to(card_root)) for p in card_root.rglob('*'))
    held = next(p for p in card_root.rglob('*') if p.is_file())
    with held.open('rb'):
        # The player's uevent socket comes up a little after the card's first mount.
        wait(lambda: listener_ready() or None, bool, 'stock player listening for card events', 60)
        Peripherals(Device(ROOTFS))._event('add')
        time.sleep(10)
    after = sorted(str(p.relative_to(card_root)) for p in card_root.rglob('*'))
    refusals = [line for line in guest('cat /run/disc-boot/guard.log 2>/dev/null').splitlines() if f'rm -rf {mount}' in line]
    assert after == before and refusals, (before, after, refusals)
    return dict(files=len(before), refusals=refusals)


def stock_ui_runs():
    return bool(guest('pgrep -x mq_ui').strip()) and bool(guest('pgrep -x mq_player').strip())


def count(path, pattern):
    """Lines of a guest file that match."""
    return int(guest(f'grep -c "{pattern}" {path} 2>/dev/null').strip() or 0)


def pair_restarts():
    """The restarts of the UI and the player stock's watch loop logged."""
    return count('/usr/data/fiio/log/process_failed.txt', 'Restarting')


def steady(seconds=45):
    """Stock's pair keeps running: the same UI and player all along and no restart in stock's
    log (owner's player, 2026-10-05: a player that never started made the watch loop restart
    the UI every few seconds, which no step looked for)."""
    before, ui, player = pair_restarts(), guest_pids('mq_ui'), guest_pids('mq_player')
    assert ui and player, f'stock pair not running: ui {ui}, player {player}'
    time.sleep(seconds)
    after = dict(restarts=pair_restarts() - before, ui=guest_pids('mq_ui'), player=guest_pids('mq_player'))
    assert (after['restarts'], after['ui'], after['player']) == (0, ui, player), dict(before=dict(ui=ui, player=player), **after)
    return dict(seconds=seconds, ui=ui, player=player, watched=found_by_watch_loop())


def boot_log():
    """The persistent boot log's last boot: its section opened by the early hook, the decision's exit
    status and the wrappers' starts of the pair (they survive a reset; /run does not)."""
    lines = guest('cat /usr/data/disc-boot/boot.log 2>/dev/null').splitlines()
    heads = [i for i, line in enumerate(lines) if re.match(r'boot [0-9a-f-]{36} at [0-9.]+$', line)]
    last = '\n'.join(lines[heads[-1]:]) if heads else ''
    assert 'early exit 0' in last and ' mq_ui' in last and ' mq_player' in last, lines[-12:]
    return last.splitlines()[:10]


def ui_runs(name):
    return wait(lambda: guest_json('/run/disc-boot/ui.json'), lambda u: u['state'] in ('ready', 'confirmed') and u['name'] == name,
                f'{name} runs', 300)


def run(output):
    work = Path(tempfile.mkdtemp())
    power('off')
    # 1. Nothing installed: the default mode, nothing started, stock's UI through the wrapper.
    power('on')
    boot = boot_status()
    assert (boot['mode'], boot['reason'], boot['keys']['read']) == ('platform', 'default', True), boot
    assert wait(lambda: stock_ui_runs() or None, bool, 'stock UI', 120)
    assert not guest('ls /run/disc-boot/ui-launch 2>/dev/null').strip()
    step('nothing installed', boot=boot, service=guest_json('/run/disc-boot/service.json'), steady=steady(), bootLog=boot_log())
    # Stock's player, started by stock's PATH lookup, has the card guard first: a busy card stays whole.
    path = guarded_player()
    step('busy card kept', path=path, **busy_card_event(boot['card']))
    power('off')
    # 1b. The boot program does not run at all (as on the owner's player, 2026-10-05): no run
    # folder, no decision; both wrappers start stock's programs and the pair stays up.
    program = ROOTFS/'opt/disc-boot/disc-boot'
    program.chmod(0o644)
    try:
        power('on')
        assert wait(lambda: stock_ui_runs() or None, bool, 'stock UI without the boot program', 120)
        assert not guest('ls -d /run/disc-boot 2>/dev/null').strip(), 'the boot program ran'
        step('the boot program fails, stock runs', steady=steady(), early=guest('cat /run/disc-boot-early.log 2>&1').strip()[-200:])
        power('off')
    finally:
        program.chmod(0o755)
    # 2. Volume Up held: stock mode.
    power('on', hold='volume_up')
    boot = boot_status()
    assert (boot['mode'], boot['reason'], boot['keys']['volumeUp']) == ('stock', 'key', True), boot
    step('volume up', boot=boot, steady=steady())
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
    step('installed with play', boot=boot, service=status, confirmedAfter=round(time.monotonic() - started), steady=steady())
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
    #    Play then runs the platform again and the version confirms, which clears the count. The new
    #    version runs at each start (the probe's slot is on the disk before its request): a start that
    #    falls back to the confirmed one is healthy and clears the count (stage 4c).
    power('off')
    job(work, 'activate', '6')
    power('on')
    service(lambda s: s['version'] == '6' and s['state'] == 'ready', 'version 6 tentative', 420)
    time.sleep(5)
    power('cut', unsynced=True)
    counts = []
    # The start that activated it ran the confirmed version first (the job is the running probe's),
    # whose readiness cleared the count: three starts with the tentative one follow.
    for _ in range(3):
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
    # The package's player launcher started stock's player each time, the guard still first.
    player = guest_json('/run/disc-boot/ui/player.json')
    launches = int(guest(f'grep -c " player" /usr/data/disc-boot/data/{UI_NAME}/probe.log').strip() or 0)
    assert player['launch'] == 'package' and launches >= 2, (player, launches)
    step('ui package', ui=ui, starts=int(starts.strip()), player=player, playerLaunches=launches, path=guarded_player(),
         watched=found_by_watch_loop())
    # 11. A second ui package and the boot menu with Play. Stock's player ran before the card came in
    #     this boot, so it starts beside the menu at once, and the menu's choice, which brings its own
    #     player launcher, gets the pair restarted by stock's loop.
    power('off')
    with card() as root:
        package.stage(probe(work/'two', '1', role='ui', name=TWO), root, profile=PROFILE)
        package.stage(probe(work/'menu', '1', role='menu'), root, profile=PROFILE)
    power('on', hold='play')
    ui = ui_runs(TWO)
    choice = guest_json('/run/disc-boot/ui/choice.json')
    assert (choice['ui'], choice['by'], choice['menu']) == (TWO, 'menu', False), choice
    menu = guest_json('/run/disc-boot/menu.json')
    assert menu['state'] == 'answered', menu
    offered = guest_json(f'/usr/data/disc-boot/data/{MENU_NAME}/choices.json')
    assert (offered['default'], [e['ui'] for e in offered['entries']]) == (UI_NAME, [TWO, UI_NAME, 'stock']), offered
    player = wait(lambda: guest_json('/run/disc-boot/ui/player.json'), lambda p: p['launch'] == 'package' and p['name'] == TWO,
                  'the chosen ui package launches the player', 120)
    step('a second ui and the menu with play', choice=choice, menu=menu, offered=offered, ui=ui, player=player)
    # 12. A plain power-on: the menu asks at the boot's first start of the pair. No player ran yet, so the
    #     player waits for the choice; the menu hands over in its own process; nothing is restarted.
    restarts = pair_restarts()
    menus = count(f'/usr/data/disc-boot/data/{MENU_NAME}/probe.log', 'menu')
    power('off')
    power('on')
    ui = ui_runs(TWO)
    choice = guest_json('/run/disc-boot/ui/choice.json')
    assert (choice['ui'], choice['by']) == (TWO, 'menu'), choice
    player = wait(lambda: guest_json('/run/disc-boot/ui/player.json'), lambda p: p['launch'] == 'package' and p['name'] == TWO,
                  'the player waited for the choice', 120)
    ui = wait(lambda: guest_json('/run/disc-boot/ui.json'), lambda u: u['state'] == 'confirmed' and u['name'] == TWO, f'{TWO} confirmed', 420)
    assert count(f'/usr/data/disc-boot/data/{MENU_NAME}/probe.log', 'menu') == menus + 1
    assert pair_restarts() == restarts, ('stock restarted the pair', restarts, pair_restarts())
    assert stock_ui_runs()
    step('the menu hands over without a restart', choice=choice, player=player, ui=ui, pairRestarts=pair_restarts() - restarts,
         path=guarded_player(), watched=found_by_watch_loop())
    # 13. A choice for the next boot only, asked by the service: that boot runs it without the menu.
    menus = count(f'/usr/data/disc-boot/data/{MENU_NAME}/probe.log', 'menu')
    power('off')
    job(work, f'ui-next {UI_NAME}')
    power('on')
    service(lambda s: s['lastRequest'] == f"next boot's ui {UI_NAME}", 'the next boot\'s ui asked for', 300)
    power('off')
    power('on')
    ui = ui_runs(UI_NAME)
    choice = guest_json('/run/disc-boot/ui/choice.json')
    assert (choice['ui'], choice['by'], choice['menu']) == (UI_NAME, 'next', False), choice
    assert count(f'/usr/data/disc-boot/data/{MENU_NAME}/probe.log', 'menu') == menus + 1, 'only the asking boot ran the menu'
    step('next skips the menu', choice=choice, ui=ui)
    # 14. Volume Up with the menu installed: stock mode, no menu, no package.
    power('off')
    power('on', hold='volume_up')
    boot = boot_status()
    assert (boot['mode'], boot['reason']) == ('stock', 'key'), boot
    assert wait(lambda: stock_ui_runs() or None, bool, 'stock UI', 120)
    assert not guest('ls /run/disc-boot/ui-launch /run/disc-boot/ui/choice.json 2>/dev/null').strip()
    assert count(f'/usr/data/disc-boot/data/{MENU_NAME}/probe.log', 'menu') == menus + 1
    step('volume up with the menu installed', boot=boot)
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
