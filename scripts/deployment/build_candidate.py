#!/usr/bin/env python3
"""Offline profile-selected boot layer image and stock restore image. No USB/network.

Ported from snowsky-disc-web's companion builder (at faaf502), reduced to the
boot layer: stock plus the boot layer's own objects, and no package inside
(docs/contract.md). Run inside the pinned Linux tooling container with the
reference on PYTHONPATH. Decrypted firmware and images belong in ignored work
storage.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from firmware_profile import load_profile, load_writer, load_usb_profile, fingerprint, artifact_names

VARIANT = 'boot'
# The boot layer's objects (docs/contract.md). Stage 1 renamed the console and the
# report from /opt/disc-web to /opt/disc-boot; the console's hook and marker stay.
BOOT = 'opt/disc-boot/disc-boot'
LAUNCHER = 'opt/disc-boot/mq_ui'  # a link to disc-boot, which acts as the launcher by this name
UI_WRAPPER = 'sbin/mq_ui'          # ahead of /usr/bin in the PATH stock's fiio_init.sh runs with
PLAYER_LAUNCHER = 'opt/disc-boot/mq_player'  # the same program as a ui package's player launcher
PLAYER_WRAPPER = 'sbin/mq_player'  # stock starts its player by name too
GUARD = 'opt/disc-boot/guard/rm'   # first in the PATH of stock's player and UI
EARLY_HOOK = 'etc/init.d/S22disc-boot'
START_HOOK = 'etc/init.d/S99disc-boot'
CONSOLE = 'opt/disc-boot/disc-usb-console'
CONSOLE_HOOK = 'etc/init.d/S99disc-usb'
BOOT_REPORT = 'opt/disc-boot/boot-report.sh'
PT_MIPS_ABIFLAGS = 0x70000003
FP_ABI_SOFT = 3  # Val_GNU_MIPS_ABI_FP_SOFT in .MIPS.abiflags


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source,'sha256').hexdigest()


def check_stock(path, profile):
    if path.stat().st_size != profile['rootfs_size'] or digest(path) != profile['rootfs_sha256']:
        raise ValueError('Expected exact profile-pinned stock squashfs')


def check_elf(path):
    data = path.read_bytes()
    if len(data)<52 or data[:7] != b'\x7fELF\x01\x01\x01':
        raise ValueError('Expected ELF32 little-endian version 1')
    kind, machine, version, _, phoff, _, flags, ehsize, phsize, count = struct.unpack_from('<HHIIIIIHHH',data,16)
    if kind not in (2,3) or machine!=8 or version!=1 or ehsize!=52 or phsize!=32 or not count or phoff<52 or phoff+count*32>len(data):
        raise ValueError('Invalid MIPS executable/program headers')
    loads = 0
    fp_abi = None
    for i in range(count):
        ptype, offset, _, _, size, _, _, _ = struct.unpack_from('<8I',data,phoff+i*32)
        if offset+size>len(data):raise ValueError('Truncated program segment')
        if ptype==1:loads += 1
        if ptype==PT_MIPS_ABIFLAGS:
            if size<24 or fp_abi is not None:raise ValueError('Invalid MIPS ABI flags')
            fp_abi = data[offset+7]
        if ptype==3:raise ValueError('Dynamic interpreter is not supported')
        if ptype==2:
            if size%8:raise ValueError('Invalid dynamic table')
            for pos in range(offset,offset+size,8):
                tag,_ = struct.unpack_from('<II',data,pos)
                if tag==0:break
                if tag==1:raise ValueError('DT_NEEDED is not supported')
    if not loads:raise ValueError('No loadable segment')
    # The player's Linux 4.4.94 emulates FPU branch delay slots through a
    # trampoline on the user stack; a hard-float executable died there
    # (snowsky-disc-web docs/combined-browser-observation.md). Only soft-float
    # executables, which never trap into that emulator, may enter an image.
    if fp_abi != FP_ABI_SOFT:
        raise ValueError('Native executable must be soft-float (MIPS ABI flags FP ABI 3)')
    return dict(bytes=len(data),sha256=digest(path),elfFlags=f'0x{flags:08x}',fpAbi='soft',
                compatibility='ELF shape and soft-float ABI only; physical ISA/kernel acceptance remains open')


def inventory(root):
    result = {}
    # Never follow firmware symlinks into the host filesystem.
    for parent, dirs, files in os.walk(root,followlinks=False):
        for name in dirs+files:
            path = Path(parent)/name; info = path.lstat()
            row = dict(mode=info.st_mode,uid=info.st_uid,gid=info.st_gid)
            if stat.S_ISLNK(info.st_mode):row['target'] = os.readlink(path)
            elif stat.S_ISREG(info.st_mode):row.update(bytes=info.st_size,sha256=digest(path))
            elif stat.S_ISCHR(info.st_mode) or stat.S_ISBLK(info.st_mode):row['rdev'] = info.st_rdev
            result[str(path.relative_to(root))] = row
    return result


def check_usb_binary(path):
    info = check_elf(path)
    if b'--fixture-root' in path.read_bytes():
        raise ValueError('Fixture-only console must never enter an image')
    return info


def check_boot_binary(path):
    info = check_elf(path)
    if b'DISC_BOOT_FIXTURE' in path.read_bytes():
        raise ValueError('The fixture build of disc-boot must never enter an image')
    return info


def additions_of(payload, before):
    """Every added path: the payload's files and each folder on the way that stock lacks."""
    added = set(payload)
    for name in payload:
        parts = name.split('/')
        added |= {'/'.join(parts[:i]) for i in range(1, len(parts)) if '/'.join(parts[:i]) not in before}
    return added


def check_delta(before, after, additions):
    removed = set(before)-set(after)
    changed = {name for name in before.keys() & after.keys() if before[name]!=after[name]}
    added = set(after)-set(before)
    if removed or changed or added!=additions:
        raise ValueError(f'Unexpected rootfs delta: removed={sorted(removed)}, changed={sorted(changed)}, added={sorted(added)}')


def pad(source, destination, capacity):
    size = source.stat().st_size
    if size>capacity:raise ValueError('Image exceeds writer capacity; refusing truncation')
    # Exclusive creation: never replace a recovery or candidate image.
    with source.open('rb') as src, destination.open('xb') as dst:
        shutil.copyfileobj(src,dst)
        dst.truncate(capacity)
    if destination.stat().st_size != capacity:raise ValueError('Bad padded size')


def command(*args):
    subprocess.run(list(map(str,args)),check=True)


def usb_hook(profile):
    # All fields are validated by load_usb_profile; no card contents become code.
    return f'''#!/bin/sh
# The console starts only with its exact card marker, checked by the native helper.
case "${{1:-}}" in
  start)
    /bin/sh /{BOOT_REPORT} >/run/disc-boot-report.log 2>&1 &
    (
      printf 'usb hook entered\\n'
      /{CONSOLE} --sd-mount {profile['sd_mount']} --sd-source {profile['sd_source']} --udc {profile['udc']} --startup-seconds {profile['startup_seconds']} --session-seconds {profile['session_seconds']}
      rc=$?
      printf 'usb helper exit=%s\\n' "$rc"
    ) >/run/disc-usb.log 2>&1 &
    ;;
  stop) : > /run/disc-usb.stop ;;
esac
exit 0
'''


def boot_report_script(profile):
    source = Path(__file__).resolve().parents[2]/'device/deployment/boot-report.sh'
    text = source.read_text()
    report = profile['boot_report']
    for token, value in {'SD':profile['sd_mount'], 'SOURCE':profile['sd_source'],
                         'UDC':profile['udc'], 'DELAY':report['delay_seconds'],
                         'LIMIT':report['wait_seconds'], 'BYTES':report['max_bytes'],
                         'PROFILE':fingerprint(profile)}.items():
        text = text.replace('@'+token+'@', str(value))
    return text


def early_hook(profile, usb):
    # Fields come from the reviewed profiles; disc-boot validates them again.
    return f'''#!/bin/sh
# The boot layer's decision for this boot: keys, mode and the boot-loop count (docs/contract.md).
case "${{1:-}}" in
  start) /{BOOT} early --profile {profile['version']} --card {usb['sd_mount']} --card-source {usb['sd_source']} >/run/disc-boot-early.log 2>&1 ;;
esac
exit 0
'''


def start_hook():
    return f'''#!/bin/sh
# Recovery from the card and the service package's supervisor; start returns at once.
case "${{1:-}}" in
  start) /{BOOT} start >/run/disc-boot-start.log 2>&1 ;;
  stop) /{BOOT} stop ;;
esac
exit 0
'''


def card_guard(usb):
    """The card guard with the profile's mount point (docs/contract.md, "The card guard")."""
    if not re.fullmatch('(/[a-zA-Z0-9_-]+)+', usb['sd_mount']):
        raise ValueError('The card guard needs a plain absolute mount point')
    source = Path(__file__).resolve().parents[2]/'device/deployment/card-guard.sh'
    return source.read_text().replace('@SD@', usb['sd_mount'])


def ui_wrapper():
    return f'''#!/bin/sh
# Stock's UI unless the boot layer chose a ui package for this boot (docs/contract.md).
# Without that choice (stock mode, Volume Up, no package, a failure of the boot program)
# stock's own program starts and the boot program stays out of its way. Either way the
# card guard comes first in its PATH.
PATH=/{GUARD.rsplit('/', 1)[0]}:$PATH; export PATH
[ -f /run/disc-boot/ui-launch ] && [ -x /{LAUNCHER} ] && exec /{LAUNCHER} "$@"
exec /usr/bin/mq_ui "$@"
'''


def player_wrapper():
    return f'''#!/bin/sh
# Stock's player with the card guard first in its PATH (docs/contract.md, "The card guard"):
# stock removes the card's mount point with rm -rf after an unmount it never checks, which
# empties a card that is still mounted. While a ui package runs (not after its fallback to
# stock's UI) the boot program starts the player launcher that package brings, if any;
# that launcher ends in stock's player, and stock's player starts at once otherwise.
PATH=/{GUARD.rsplit('/', 1)[0]}:$PATH; export PATH
[ -f /run/disc-boot/ui-launch ] && [ ! -f /run/disc-boot/ui/fallback ] && [ -x /{PLAYER_LAUNCHER} ] && exec /{PLAYER_LAUNCHER} "$@"
exec /usr/bin/mq_player "$@"
'''


def payload(profile, usb, console, boot):
    """What the boot image adds to stock: path -> (bytes, mode), or ('link', target)."""
    return {
        BOOT: (boot.read_bytes(), 0o755),
        LAUNCHER: ('link', 'disc-boot'),
        UI_WRAPPER: (ui_wrapper().encode(), 0o755),
        PLAYER_LAUNCHER: ('link', 'disc-boot'),
        PLAYER_WRAPPER: (player_wrapper().encode(), 0o755),
        GUARD: (card_guard(usb).encode(), 0o755),
        EARLY_HOOK: (early_hook(profile, usb).encode(), 0o755),
        START_HOOK: (start_hook().encode(), 0o755),
        CONSOLE: (console.read_bytes(), 0o755),
        CONSOLE_HOOK: (usb_hook(usb).encode(), 0o755),
        BOOT_REPORT: (boot_report_script(usb).encode(), 0o755),
    }


def build(ota, console, boot, out, profile, writer):
    # External pinned tooling is an offline build input, never a production dependency.
    from firmware.tools.firmware_inventory import verified_chunks, plaintext_digest
    if out.exists() or out.is_symlink():raise ValueError('Output exists; select a fresh directory')
    capacity = writer['block_bytes'] * writer['logical_blocks']
    usb = load_usb_profile(profile)
    console_native = check_usb_binary(console)
    boot_native = check_boot_binary(boot)
    files = payload(profile, usb, console, boot)
    out.mkdir(parents=True)
    stock = out/'stock.squashfs'
    chunks,_ = verified_chunks(ota)
    if len(chunks)!=profile['rootfs_chunks']:raise ValueError('Chunk count differs from selected profile')
    with stock.open('xb') as output:plaintext_digest(chunks,output=output)
    check_stock(stock, profile)
    tree = out/'candidate-tree'
    command('unsquashfs','-no-progress','-no-xattrs','-d',tree,stock)
    metadata = dict(line.split('=', 1) for line in
                    (tree/'etc/product_version/version.in').read_text().splitlines() if '=' in line)
    for key, expected in (('PRODUCT', profile['product']), ('MAIN_OS_VER', profile['main_os_version']),
                          ('RECOVERY_OS_VER', profile['recovery_os_version'])):
        if metadata.get(key) != str(expected):raise ValueError('Stock product/version metadata mismatch')
    for name, expected in {**profile['stock_files'], **usb['stock_files']}.items():
        if digest(tree/name) != expected:raise ValueError(f'Unexpected stock file: {name}')
    if (tree/'opt').is_symlink() or not (tree/'opt').is_dir():raise ValueError('Unexpected /opt layout')
    before = inventory(tree)
    additions = additions_of(files, before)
    for name, (data, mode) in sorted(files.items()):
        destination = tree/name
        if destination.exists() or destination.is_symlink():raise ValueError(f'Payload collision: {name}')
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        if data == 'link':destination.symlink_to(mode);continue
        destination.write_bytes(data);destination.chmod(mode)
    expected = inventory(tree);check_delta(before,expected,additions)
    packed = out/'candidate.squashfs'
    command('mksquashfs',tree,packed,'-comp','lzo','-b','131072','-no-xattrs','-noappend','-no-progress','-processors','2')
    if packed.stat().st_size>capacity:raise ValueError('Candidate too large')
    verified = out/'verified-tree'
    command('unsquashfs','-no-progress','-no-xattrs','-d',verified,packed)
    actual = inventory(verified)
    if actual!=expected:raise ValueError('Packed rootfs failed full content/metadata round trip')
    check_delta(before,actual,additions)
    # Publish artifacts only after full verification. These are NOT installer inputs yet.
    candidate_name, restore_name = artifact_names(profile,VARIANT)
    candidate = out/candidate_name
    recovery = out/restore_name
    pad(packed,candidate,capacity);pad(stock,recovery,capacity)
    report = dict(status='offline-verified',hardwareQualified=False,flashAuthorized=False,
                  product=profile['product'],firmware=profile['main_os_version'],
                  firmwareVersion=profile['version'],profileSha256=fingerprint(profile),
                  writerProfile=writer['id'],writerProfileSha256=fingerprint(writer),stockSha256=profile['rootfs_sha256'],
                  stockBytes=profile['rootfs_size'],packedBytes=packed.stat().st_size,
                  writerFormatBytes=capacity,added=sorted(additions),variant=VARIANT,
                  stockEntriesPreserved=len(before),fullRoundTrip=True,packages=[],
                  boot=dict(native=boot_native,api=1,launcher=f'/{LAUNCHER}',wrapper=f'/{UI_WRAPPER}',
                            playerLauncher=f'/{PLAYER_LAUNCHER}',playerWrapper=f'/{PLAYER_WRAPPER}',guard=f'/{GUARD}',
                            hooksSha256={name:digest(tree/name) for name in (EARLY_HOOK,START_HOOK,UI_WRAPPER,
                                                                              PLAYER_WRAPPER,GUARD)}),
                  artifacts={p.name:dict(bytes=p.stat().st_size,sha256=digest(p)) for p in (candidate,recovery)},
                  usbDiagnostic=dict(profileSha256=fingerprint(usb),native=console_native,
                                     marker='.disc/dev/usb-console',optInRequired=True,
                                     physicalQualified=False,sessionSeconds=usb['session_seconds'],
                                     bootReport=dict(usb['boot_report'],marker='.disc/dev/boot-report',
                                                     output='.disc/dev/boot-report.txt',
                                                     scriptSha256=digest(tree/BOOT_REPORT))),
                  blockers=['The boot program is unqualified on a device: keys, stock init and the ui launcher are host- and guest-checked only',
                            'Engineering USB console exists but physical enumeration/coexistence is unqualified',
                            'No physical writer/geometry/recovery validation for selected firmware',
                            'No native-kernel ISA/FPU/syscall/resource acceptance'])
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', help='Reviewed profile; defaults to FW_VERSION or firmware/active-version')
    parser.add_argument('--ota',type=Path,required=True)
    parser.add_argument('--console',type=Path,required=True,help='The USB console (build/mips/disc-usb-console)')
    parser.add_argument('--boot',type=Path,required=True,help='The boot program (build/mips/disc-boot)')
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    profile = load_profile(args.version)
    build(args.ota,args.console,args.boot,args.output,profile,load_writer(profile['writer']))
