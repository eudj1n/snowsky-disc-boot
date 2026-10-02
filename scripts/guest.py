#!/usr/bin/env python3
"""A disposable stock-init guest of a boot-layer image on the pinned emulator.

snowsky-disc-qemu, at a revision this repository reviewed
(firmware/emulator-revisions.json), runs in Docker from its own checkout: the guest's rootfs
comes from a boot-layer image (scripts/deployment/build_candidate.py) built on
the selected stock firmware (FW_VERSION, else the active profile), /usr/data is
an 83 MiB file system, stock's rcS, fiio_init.sh and
its watch loop run, keys can be held at power-on and power can be cut
(snowsky-disc-qemu docs/development/emulator-depth-handoff.md). The guest has
the player's gauge layout, a synthetic serial number and no idle power-off.
This repository's guest acceptance (tests/integration/boot_guest.py) and the
server's package checks use it. Nothing here touches a player, a real card or
the emulator's checkout.

  up --reference DIR --image FILE --ota DIR [--publish PORT ...] [--mount NAME=DIR ...]
  run COMMAND...              in the container: /repo the emulator, /boot this repository
  stage --package FILE        a package (zip or folder) onto the card for Play, the guest off
  power on|reboot|off|cut [--unsynced]|status [--hold KEYS]
  status                      the boot layer's status files and the machine
  down                        removes only the recorded stack and its volume

--state chooses the record (default work/guest.json), so another repository
keeps its own stack.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from firmware_profile import load_profile  # noqa: E402

SERIAL = '00000000000000'  # synthetic, in the player's format
GUEST = '/work/rootfs'
DEFAULT_IMAGE = 'snowsky-disc-qemu-ci'


def compose(state, *args, capture=False, check=True, env_extra=None):
    reference = Path(state['reference'])
    env = {key: value for key, value in os.environ.items() if key not in ('COMPOSE_PROFILES', 'DEVICE_BOOT_SCRIPT')}
    env.update(OTA_DIR=state['ota'], FW_VERSION=state['firmwareVersion'], EMU_IMAGE=state['emulatorImage'],
               EMU_CONTAINER_NAME=state['id'] + '-emu', WORK_VOLUME=state['id'] + '-work', SD_DIR=state['sd'],
               BOOT_MODE='init', GUEST_TTL='0', USERDATA_MB='83', BATTERY_PROFILE='device', DEVICE_SN=SERIAL,
               SETTINGS_PROFILE='always-on', SETTINGS='POWER_SAVE=0', **(env_extra or {}))
    return subprocess.run(['docker', 'compose', '--project-directory', str(reference), '--env-file', '/dev/null',
                           '-p', state['id'], '-f', str(reference/'emulator/compose.yaml'), '-f', str(reference/'ci/compose.yml'),
                           '-f', state['overlay'], *args], env=env, check=check, text=True,
                          capture_output=capture)


def inside(state, *command, capture=False, check=True):
    return compose(state, 'exec', '-T', 'emulator', *command, capture=capture, check=check)


def guest_file(state, path):
    out = inside(state, 'sh', '-c', f'cat {GUEST}{path} 2>/dev/null || true', capture=True).stdout
    try:
        return json.loads(out) if out.strip() else None
    except json.JSONDecodeError:
        return None


def status(state):
    machine = inside(state, 'bash', '/repo/emulator/scripts/25_power.sh', 'status', capture=True, check=False).stdout
    out = {'machine': json.loads(machine) if machine.strip().startswith('{') else machine.strip()}
    for name in ('boot', 'service', 'ui'):
        out[name] = guest_file(state, f'/run/disc-boot/{name}.json')
    return out


# The card is written from the container only while the guest is off: no two writers on one file system.
STAGE = r'''
set -e
state=$(bash /repo/emulator/scripts/25_power.sh status | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])')
[ "$state" = off ] || { echo "power the guest off before staging ($state)" >&2; exit 1; }
node=/work/rootfs/dev/mmcblk0p1
kind=$(blkid -o value -s TYPE "$node" || true)
mkdir -p /mnt/guest-card
mount -t "${kind:-vfat}" -o iocharset=utf8 "$node" /mnt/guest-card
trap 'sync; umount /mnt/guest-card' EXIT
PYTHONPATH=/boot/scripts python3 -B /boot/scripts/package.py stage --package /work/guest-stage/"$1" --card /mnt/guest-card --confirm-card-write
'''


def stage(state, source):
    source = Path(source).resolve()
    inside(state, 'sh', '-c', 'rm -rf /work/guest-stage && mkdir -p /work/guest-stage')
    compose(state, 'cp', str(source), f'emulator:/work/guest-stage/{source.name}')
    inside(state, 'bash', '-c', STAGE, 'stage', source.name)


def up(args):
    state_path = Path(args.state)
    if state_path.exists():
        raise SystemExit(f'A guest is recorded in {state_path}; use status or down first')
    profile = load_profile(args.version)
    reference, image, ota = args.reference.resolve(), args.image.resolve(), args.ota.resolve()
    revision = subprocess.check_output(['git', '-C', str(reference), 'rev-parse', 'HEAD'], text=True).strip()
    reviewed = json.loads((ROOT/'firmware/emulator-revisions.json').read_text())['revisions']
    if profile['version'] not in reviewed.get(revision, {}).get('profiles', []):
        raise SystemExit(f'Emulator revision {revision[:12]} is not reviewed for firmware {profile["version"]} '
                         '(firmware/emulator-revisions.json)')
    if subprocess.run(['git', '-C', str(reference), 'status', '--porcelain'], capture_output=True, text=True).stdout.strip():
        raise SystemExit('The emulator checkout has changes; use a clean checkout of a reviewed revision')
    if not image.is_file() or not ota.is_dir():
        raise SystemExit('Image or OTA folder missing')
    # The emulator's own image, built from that revision under a tag of its own (other stacks keep theirs).
    emulator_image = args.emulator_image or f'{DEFAULT_IMAGE}:{revision[:7]}'
    if subprocess.run(['docker', 'image', 'inspect', emulator_image], capture_output=True).returncode:
        raise SystemExit(f'Build the emulator image first: docker build -t {emulator_image} {reference}/emulator/docker')
    mounts = {'boot': ROOT, 'images': image.parent}
    for item in args.mount or []:
        name, _, path = item.partition('=')
        if not re.fullmatch('[a-z][a-z0-9-]{0,30}', name) or name in mounts or name in ('repo', 'work', 'ota', 'sdcard'):
            raise SystemExit(f'Bad mount {item}')
        mounts[name] = Path(path).resolve()
    ident = f'{args.name}-{time.time_ns()}'
    folder = state_path.parent/ident
    sd = folder/'sdcard'
    sd.mkdir(parents=True)
    with image.open('rb') as source:
        image_sha = hashlib.file_digest(source, 'sha256').hexdigest()
    overlay = folder/'compose-overlay.json'
    overlay.write_text(json.dumps({'services': {'emulator': {
        'volumes': [f'{path}:/{name}:ro' for name, path in mounts.items()],
        'ports': [f'127.0.0.1:{port}:{port}' for port in args.publish or []]}}}, indent=2) + '\n')
    state = dict(id=ident, reference=str(reference), referenceRevision=revision, ota=str(ota), image=str(image),
                 imageSha256=image_sha, emulatorImage=emulator_image, sd=str(sd), overlay=str(overlay),
                 firmwareVersion=profile['version'], mounts={k: str(v) for k, v in mounts.items()}, ports=args.publish or [])
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2) + '\n')
    print('Disposable guest:', ident, flush=True)
    # The emulator's generated media (three short tracks) is the card's content.
    subprocess.run(['docker', 'run', '--rm', '--network', 'none', '-v', f'{reference}:/repo:ro', '-v', f'{sd}:/fixtures',
                    emulator_image, 'python3', '-B', '-m', 'tests.fixtures.fixture', '/fixtures'], check=True)
    compose(state, 'up', '-d', '--no-build', '--wait', '--wait-timeout', '60', 'emulator')
    inside(state, 'bash', '/repo/emulator/scripts/01_image_rootfs.sh', f'/images/{image.name}')
    inside(state, 'bash', '/repo/emulator/scripts/10_setup_env.sh')
    print(json.dumps(status(state), indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--state', default=str(ROOT/'work/guest.json'))
    sub = p.add_subparsers(dest='action', required=True)
    u = sub.add_parser('up')
    u.add_argument('--reference', type=Path, required=True, help='A clean checkout of snowsky-disc-qemu at a reviewed revision')
    u.add_argument('--image', type=Path, required=True, help='disc-boot-v<version>-review-only.bin')
    u.add_argument('--ota', type=Path, required=True, help="The firmware's main_os/ota_v<version> folder")
    u.add_argument('--version', help='Reviewed firmware profile (default: the active one)')
    u.add_argument('--name', default='disc-boot-guest')
    u.add_argument('--emulator-image', help=f'Default: {DEFAULT_IMAGE}:<the revision\'s first 7 characters>')
    u.add_argument('--publish', type=int, action='append', help='A guest port published on host loopback under the same number')
    u.add_argument('--mount', action='append', help='NAME=DIR, read-only at /NAME in the container')
    r = sub.add_parser('run')
    r.add_argument('command', nargs=argparse.REMAINDER)
    w = sub.add_parser('power')
    w.add_argument('event', choices=['on', 'reboot', 'off', 'cut', 'status'])
    w.add_argument('--unsynced', action='store_true')
    w.add_argument('--hold', default='', help='Keys held at power-on, e.g. play or volume_up')
    t = sub.add_parser('stage')
    t.add_argument('--package', type=Path, required=True)
    sub.add_parser('status')
    sub.add_parser('down')
    args = p.parse_args()
    if args.action == 'up':
        return up(args)
    state_path = Path(args.state)
    if not state_path.exists():
        raise SystemExit(f'No guest is recorded in {state_path}')
    state = json.loads(state_path.read_text())
    if args.action == 'run':
        command = args.command[1:] if args.command[:1] == ['--'] else args.command
        raise SystemExit(inside(state, *command, check=False).returncode)
    if args.action == 'power':
        extra = ['--unsynced'] if args.unsynced and args.event == 'cut' else []
        compose(state, 'exec', '-T', '-e', f'BOOT_KEYS={args.hold}', 'emulator', 'bash', '/repo/emulator/scripts/25_power.sh',
                args.event, *extra)
        return
    if args.action == 'status':
        print(json.dumps(status(state), indent=2))
        return
    if args.action == 'stage':
        stage(state, args.package)
        return
    # down: the guest's own cleanup is best effort when a setup never booted.
    inside(state, 'bash', '/repo/ci/cleanup.sh', check=False)
    compose(state, 'down', '--volumes', '--timeout', '5')
    state_path.unlink()
    print('Removed only the recorded guest stack and its volume; its folder keeps the card content.')


if __name__ == '__main__':
    main()
