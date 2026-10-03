"""The packed image's boot layer on its own stock BusyBox, in private namespaces.

Runs the generated hooks, the /sbin/mq_ui and /sbin/mq_player wrappers and the card
guard inside the verified tree with the production disc-boot and its real timings
(confirmation after 180 s). Keys are unreadable here (no /dev/mem), so the default
mode applies. Stock's UI and player are replaced by marker scripts; the player's
marker repeats stock's unchecked "umount, then rm -rf" of the card's mount point
while the mount is busy. No kernel, driver, key, card or USB acceptance is claimed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from firmware_profile import load_profile  # noqa: E402

# The profile the image was built for (FW_VERSION, else the active one).
PROFILE = load_profile()['version']


def package(root, directory, role, entry, script, player=None):
    target = root/directory.lstrip('/')
    (target/'bin').mkdir(parents=True)
    files = {}
    for path, text in ((entry, script), *([player] if player else [])):
        data = ('#!/bin/sh\n' + text).encode()
        (target/path).write_bytes(data)
        (target/path).chmod(0o755)
        files[path] = dict(size=len(data), sha256=hashlib.sha256(data).hexdigest(), mode='0755')
    manifest = dict(schema=1, name='probe-' + role, version='1', role=role, bootApi=1, arch='mips32el-linux-static',
                    profiles=[PROFILE], entry=entry, ready=30, files=files)
    if player:
        manifest['player'] = player[0]
    (target/'package.json').write_text(json.dumps(manifest))


def wait(path, condition, limit):
    until = time.monotonic() + limit
    while time.monotonic() < until:
        try:
            value = json.loads(path.read_text())
            if condition(value):
                return value
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        time.sleep(1)
    raise AssertionError(f'{path} never matched: {path.read_text() if path.exists() else "missing"}')


def run(output):
    assert os.getpid() == 1
    root = output/'verified-tree'
    report = json.loads((output/'report.json').read_text())
    assert report['variant'] == 'boot' and report['packages'] == [] and report['fullRoundTrip']
    subprocess.run(['mount', '--make-rprivate', '/'], check=True)
    for name in ('run', 'sys', 'tmp', 'usr/data'):
        subprocess.run(['mount', '-t', 'tmpfs', 'tmpfs', str(root/name)], check=True)
    # This namespace's own proc: the UI watcher follows the UI's process by its name there.
    subprocess.run(['mount', '-t', 'proc', 'proc', str(root/'proc')], check=True)
    if not (root/'dev/null').exists():
        os.mknod(root/'dev/null', 0o20666, os.makedev(1, 3))
    # Stock's UI becomes a marker: a bind mount leaves the packed file unchanged.
    marker = root/'run/stock-ui'
    marker.write_text('#!/bin/sh\necho stock >> /run/ui-runs\n')
    marker.chmod(0o755)
    subprocess.run(['mount', '--bind', str(marker), str(root/'usr/bin/mq_ui')], check=True)
    # Stock's player becomes a marker that does what stock does to the card (mount_storage_dev.c):
    # unmount the mount point, then remove it with rm -rf whatever the unmount answered.
    player = root/'run/stock-player'
    player.write_text('#!/bin/sh\necho "player $(command -v rm)" >> /run/player-runs\n'
                      'umount /tmp/sdcard 2>/dev/null; rm -rf /tmp/sdcard\n')
    player.chmod(0o755)
    subprocess.run(['mount', '--bind', str(player), str(root/'usr/bin/mq_player')], check=True)

    def chroot(*command, timeout=30):
        return subprocess.run(['chroot', str(root), *command], check=True, timeout=timeout, capture_output=True, text=True)

    def boot_json():
        return json.loads((root/'run/disc-boot/boot.json').read_text())

    # Nothing installed: the default mode, nothing counted, and stock's UI through stock's PATH lookup.
    chroot('/bin/sh', '/etc/init.d/S22disc-boot', 'start')
    first = boot_json()
    assert (first['mode'], first['reason'], first['keys']['read'], first['profile']) == ('platform', 'default', False, PROFILE), first
    assert not (root/'usr/data/disc-boot/state.json').exists()
    path = '/sbin:/usr/sbin:/bin:/usr/bin'  # BusyBox init's root PATH, which fiio_init.sh inherits
    chroot('/usr/bin/env', '-i', 'PATH=' + path, '/bin/sh', '-c', 'mq_ui')
    assert (root/'run/ui-runs').read_text().split() == ['stock']
    assert not (root/'run/disc-boot/ui.json').exists(), 'no package: the boot program never ran for the UI'
    # Stock's player through stock's PATH lookup, the card guard first: a busy card stays whole.
    card = root/'tmp/sdcard'
    card.mkdir(parents=True, exist_ok=True)
    subprocess.run(['mount', '-t', 'tmpfs', 'card', str(card)], check=True)
    (card/'Music').mkdir()
    (card/'Music/track.flac').write_text('audio')
    holder = subprocess.Popen(['sleep', '120'], cwd=card/'Music')
    chroot('/usr/bin/env', '-i', 'PATH=' + path, '/bin/sh', '-c', 'mq_player')
    assert (root/'run/player-runs').read_text().split('\n')[0] == 'player /opt/disc-boot/guard/rm'
    assert (card/'Music/track.flac').read_text() == 'audio', 'the busy card was emptied'
    assert 'refused rm -rf /tmp/sdcard' in (root/'run/disc-boot/guard.log').read_text()
    assert not (root/'run/disc-boot/ui/player.json').exists(), 'no package: the boot program never ran for the player'
    holder.kill()
    holder.wait()

    # A service and a UI package, installed into their slots as recovery would leave them.
    package(root, '/usr/data/disc-boot/service/a', 'service', 'bin/run',
            'trap "exit 0" TERM\nenv > "$DISC_BOOT_DATA/env"\n: > "$DISC_BOOT_RUN/ready"\nwhile :; do sleep 1; done\n')
    package(root, '/usr/data/disc-boot/ui/probe-ui/a', 'ui', 'bin/mq_ui',
            'echo package >> /run/ui-runs\n: > "$DISC_BOOT_RUN/ready"\nsleep 200\n',
            player=('bin/player', 'echo "launcher $DISC_BOOT_ROLE" >> /run/player-runs\nexec /usr/bin/mq_player "$@"\n'))
    for domain in ('service', 'ui/probe-ui'):
        (root/f'usr/data/disc-boot/{domain}/state.json').write_text('{"schema":1,"current":"a","confirmed":false,"previous":null}')
    chroot('/bin/sh', '/etc/init.d/S22disc-boot', 'start')
    assert json.loads((root/'usr/data/disc-boot/state.json').read_text())['unconfirmed'] == 1
    assert (root/'run/disc-boot/ui-launch').exists()
    started = time.monotonic()
    chroot('/bin/sh', '/etc/init.d/S99disc-boot', 'start')
    ui = subprocess.Popen(['chroot', str(root), '/usr/bin/env', '-i', 'PATH=' + path, 'LD_LIBRARY_PATH=/usr/lib', '/bin/sh', '-c', 'mq_ui'])
    # The ui package's player launcher, then stock's player with the guard still first.
    time.sleep(2)
    chroot('/usr/bin/env', '-i', 'PATH=' + path, 'LD_LIBRARY_PATH=/usr/lib', '/bin/sh', '-c', 'mq_player')
    assert (root/'run/player-runs').read_text().splitlines()[1:] == ['launcher ui', 'player /opt/disc-boot/guard/rm']
    launched = json.loads((root/'run/disc-boot/ui/player.json').read_text())
    assert (launched['launch'], launched['name']) == ('package', 'probe-ui'), launched
    service = wait(root/'run/disc-boot/service.json', lambda s: s['state'] == 'confirmed', 260)
    ui_status = wait(root/'run/disc-boot/ui.json', lambda s: s['state'] == 'confirmed', 60)
    confirmed_after = round(time.monotonic() - started, 1)
    assert service['name'] == 'probe-service' and ui_status['name'] == 'probe-ui'
    assert (root/'run/ui-runs').read_text().split() == ['stock', 'package']
    assert json.loads((root/'usr/data/disc-boot/state.json').read_text())['unconfirmed'] == 0
    env = dict(line.split('=', 1) for line in (root/'usr/data/disc-boot/data/probe-service/env').read_text().splitlines() if '=' in line)
    assert env['DISC_BOOT_SLOT'] == '/usr/data/disc-boot/service/a' and env['DISC_BOOT_PROFILE'] == PROFILE, env
    assert env['LD_LIBRARY_PATH'].startswith('/usr/data/disc-boot/service/a/lib:/usr/lib:'), env
    status = json.loads(chroot('/opt/disc-boot/disc-boot', 'status').stdout)
    assert status['service']['state'] == 'confirmed' and status['boot']['mode'] == 'platform'
    # rcK's stop ends the supervisor and the package.
    chroot('/bin/sh', '/etc/init.d/S99disc-boot', 'stop', timeout=30)
    wait(root/'run/disc-boot/service.json', lambda s: s['state'] == 'stopped', 15)
    assert not (root/'run/disc-boot/supervisor.pid').exists()
    ui.terminate()
    result = dict(status='passed', stockBusyBox=True, productionBinary=True, stockPathLookup=path,
                  stockUiWithoutPackage=True, stockPlayerGuarded=True, busyCardKept=True,
                  packagePlayerLauncher=True, serviceConfirmed=True, uiConfirmed=True,
                  confirmedAfterSeconds=confirmed_after, bootLoopCountCleared=True, stopped=True,
                  scope='Packed tree in private namespaces; keys unreadable, no kernel/driver/card/USB acceptance')
    (output/'boot-layer-test.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert args.output.resolve().is_relative_to('/work')
    run(args.output)
