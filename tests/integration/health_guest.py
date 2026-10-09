#!/usr/bin/env python3
"""disc-health on the guest (plan, stage 7), inside the emulator's container.

    python3 scripts/guest.py run -- python3 -B /boot/tests/integration/health_guest.py \\
        --package /boot/work/<run>/disc-health-<version>.zip --output /work/health-guest.json

The release's package staged on the card as the installer stages it (install/service/disc-health/)
and installed with Play: boot runs it as a service, at nice +10, and confirms it after 180 s; its
report ($DISC_BOOT_RUN/status.json) names the emulator's fuel gauge (snowsky-disc-qemu's device
profile of the V2.57 player: cw221X-bat) and the free space of /usr/data and the card, and its
journal holds the reading; the card, which stock mounts after the service's start, is read again once
it is there. A changed gauge shows in the next start's reading. Under qemu-user the kernel's ring,
/proc/meminfo and the load are the container's, so they are recorded, not checked.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import boot_guest as bg  # noqa: E402

RUN = '/run/disc-boot/service/disc-health'
DATA = '/usr/data/disc-boot/data/disc-health'


def status():
    return bg.guest_json('/run/disc-boot/service/disc-health.json')


def run(archive, output):
    bg.evidence.clear()
    bg.evidence.update(profile=bg.PROFILE, steps=[])
    folder = bg.package.unpack(archive, Path(tempfile.mkdtemp())/'disc-health')
    manifest = bg.package.check(folder, 'service', bg.PROFILE)
    assert (manifest['bootApi'], manifest['memory']) == (2, 16), manifest
    bg.power('off')
    with bg.card() as root:
        staged = bg.package.stage(folder, root, profile=bg.PROFILE)
    assert staged['place'] == 'service/disc-health', staged
    bg.power('on', hold='play')
    ready = bg.wait(status, lambda s: s['state'] in ('ready', 'confirmed'), 'disc-health ready', 300)
    first = bg.wait(lambda: bg.guest_json(f'{RUN}/status.json'), lambda r: r['samples'] >= 1, 'its report', 120)
    # Stock mounts the card after the service's start: the reading is taken again once it is there.
    report = bg.wait(lambda: bg.guest_json(f'{RUN}/status.json'), lambda r: r['latest']['space']['card'], 'a reading with the card', 120)
    latest = report['latest']
    assert latest['battery']['name'] == 'cw221X-bat' and isinstance(latest['battery']['percent'], int), latest
    assert latest['space']['data']['totalKB'] > 0 and latest['space']['card']['totalKB'] > 0, latest['space']
    assert latest['uptime'] > 0 and len(latest['boot']) == 8 and len(latest['load']) == 3, latest
    assert report['interval'] == 600 and len(json.dumps(report)) <= 4096, report
    lines = [json.loads(line) for line in bg.guest(f'cat {DATA}/journal.jsonl').splitlines()]
    assert lines and lines[-1]['battery'] == latest['battery'], lines[-1:]
    bg.step('installed with play, read and reported', staged=staged, status=ready, first=first, report=report, journalLines=len(lines))
    confirmed = bg.wait(status, lambda s: s['state'] == 'confirmed', 'disc-health confirmed', 420)
    assert bg.guest_json('/run/disc-boot/boot.json')['reason'] == 'recovery'
    bg.step('confirmed', status=confirmed)
    # The emulator's gauge changed while the guest is off: the next start reads it.
    bg.power('off')
    from emulator.runtime import battery
    battery.update(bg.ROOTFS, capacity=42, temp=318)
    bg.power('on')
    report = bg.wait(lambda: bg.guest_json(f'{RUN}/status.json'), lambda r: r['latest']['battery'].get('percent') == 42,
                     'the changed gauge', 300)
    assert report['latest']['battery']['celsius'] == 31.8, report['latest']['battery']
    bg.step('the next start reads the gauge again', report=report)
    battery.update(bg.ROOTFS, capacity=100, temp=250)
    bg.power('off')
    bg.evidence['status'] = 'passed'
    Path(output).write_text(json.dumps(bg.evidence, indent=2) + '\n')
    print(json.dumps({'status': 'passed', 'steps': [s['step'] for s in bg.evidence['steps']]}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        run(args.package, args.output)
    except BaseException as error:
        bg.evidence['status'] = f'failed: {error}'
        bg.evidence['health'] = status()
        bg.evidence['report'] = bg.guest_json(f'{RUN}/status.json')
        Path(args.output).write_text(json.dumps(bg.evidence, indent=2, default=str) + '\n')
        raise
