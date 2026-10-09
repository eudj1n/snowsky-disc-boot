#!/usr/bin/env python3
"""Offline profile-selected boot layer image and stock restore image. No USB/network.

Ported from snowsky-disc-web's companion builder (at faaf502), reduced to the
boot layer: stock plus the boot layer's own objects, and no package inside
(docs/contract.md). Run inside the pinned Linux tooling container with the
reference on PYTHONPATH, or on the user's computer without root (plan, stage 6):
squashfs-tools 4.6 or later and openssl there, the reference's update reader
given as a file (--reader). Without root, stock's owners, mode bits and times
are not trusted to the computer's file system: they are packed from stock's own
listing. Decrypted firmware and images belong in ignored work storage.
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
import tempfile

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
BOOT_LOG = '/usr/data/disc-boot/boot.log'  # survives a reset, unlike /run; the early hook keeps it bounded
BOOT_LOG_BYTES = 262144                    # then it becomes boot.log.1
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


LISTING = re.compile(r'^(?P<mode>\S{10}) (?P<owner>\d+/\d+) +(?P<size>\d+|\d+, *\d+) '
                     r'\d{4}-\d\d-\d\d \d\d:\d\d (?P<path>.*)$')


def parse_listing(text):
    """unsquashfs -lln -d '' as {path: (kind, mode, owner, size, target)}: the type and every mode
    bit (setuid, setgid and sticky too), uid/gid, a file's size or a device's numbers, a link's
    target. Dates are left out; a folder's size changes with what is added to it; a link's own mode
    means nothing on Linux."""
    entries = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        match = LISTING.match(line)
        if not match:
            raise ValueError(f'Unexpected squashfs listing line: {line!r}')
        mode, owner, size, path = match['mode'], match['owner'], match['size'].replace(' ', ''), match['path']
        target = None
        if mode.startswith('l'):
            path, target = path.split(' -> ', 1)
            mode = 'l'
        kind = mode[0]
        entries[path or '/'] = (kind, mode, owner, None if kind == 'd' else size, target)
    return entries


def listing(image):
    """Every entry of a squashfs as the image itself holds it, not as a tree some file system has
    rewritten (owner's player, 2026-10-05: a macOS share under Docker dropped stock's setuid and
    group bits and its owners, and the check against that tree could not see it)."""
    return parse_listing(subprocess.run(['unsquashfs', '-lln', '-d', '', str(image)], capture_output=True, text=True,
                                        check=True).stdout)


def check_listing(stock, candidate, additions):
    """Every stock entry exactly as stock has it; nothing else but the additions."""
    changed = sorted(k for k in stock if k in candidate and candidate[k] != stock[k])
    missing = sorted(set(stock) - set(candidate))
    extra = sorted(set(candidate) - set(stock) - {'/' + a for a in additions})
    if changed or missing or extra:
        raise ValueError(f'The image differs from stock: changed={changed[:8]} ({len(changed)}), missing={missing[:8]}, '
                         f'unexpected={extra[:8]}')


def check_extraction(stock, tree, exact=True):
    """The unpacked tree holds stock's entries as the image does: a folder whose file system drops
    owners or mode bits is refused before anything is built from it. Without root (exact=False) an
    unpacked tree cannot keep owners or setuid bits: its entries' kinds, sizes and link targets are
    compared, and the owners and modes are packed from stock's listing instead (pseudo_lines)."""
    def kept(entry):
        kind, mode, owner, size, target = entry
        return entry if exact else (kind, None, None, size, target)
    stock, tree = ({k: kept(v) for k, v in entries.items() if k != '/'} for entries in (stock, tree))
    changed = sorted(k for k in stock if k in tree and tree[k] != stock[k])
    if changed or set(stock) != set(tree):
        raise ValueError(f'The build folder\'s file system did not keep stock\'s tree (owners, mode bits): '
                         f'changed={changed[:8]} ({len(changed)}), missing={sorted(set(stock) - set(tree))[:8]}, '
                         f'unexpected={sorted(set(tree) - set(stock))[:8]}')


def check_case(stock):
    """No two of stock's paths differ only by case: a computer's case-insensitive file system (macOS
    by default) would unpack them over each other."""
    seen = {}
    for path in stock:
        other = seen.setdefault(path.lower(), path)
        if other != path:
            raise ValueError(f'Stock holds {other} and {path}, which differ only by case: build on a case-sensitive file system')


MODE_BITS = {'r': 4, 'w': 2, 'x': 1}


def mode_bits(text):
    """The permission bits of a listing's mode string (rwsr-xr-x and the like), with setuid, setgid
    and sticky."""
    bits = 0
    for i in range(3):
        r, w, x = text[1 + 3*i:4 + 3*i]
        bits |= ((4 if r == 'r' else 0) | (2 if w == 'w' else 0) | (1 if x in 'xst' else 0)) << (3 * (2 - i))
    bits |= (0o4000 if text[3] in 'sS' else 0) | (0o2000 if text[6] in 'sS' else 0) | (0o1000 if text[9] in 'tT' else 0)
    return bits


def pseudo_lines(stock, tree, files, created, stamp):
    """mksquashfs's pseudo definitions ("M" time mode uid gid) that pack, without root, every stock entry
    with stock's mode bits and owner and the unpacked time (unsquashfs keeps times), every addition
    as root's with its mode and the stock time, and the folders made for them likewise."""
    lines = []
    def line(path, mtime, mode, owner):
        if any(c in path for c in '"\\\n'):
            raise ValueError(f'Unexpected character in {path!r}')
        uid, gid = owner.split('/')
        lines.append(f'"{path}" M {int(mtime)} {mode:o} {uid} {gid}')
    entries = {}
    for path, (kind, mode, owner, size, target) in stock.items():
        if path != '/':
            rel = path.lstrip('/')
            entries[rel] = ((tree/rel).lstat().st_mtime, 0o777 if kind == 'l' else mode_bits(mode), owner)
    for name, (data, mode) in files.items():
        entries[name] = (stamp, 0o777 if data == 'link' else mode, '0/0')
    for folder in created:
        entries[folder] = (stamp, 0o755, '0/0')
    # A folder's line before its entries': mksquashfs makes a folder of its own for a path it meets first.
    for path in sorted(entries, key=lambda p: p.split('/')):
        line(path, *entries[path])
    return lines


def check_additions(candidate, files, created):
    """Every addition in the packed image as root's with its own mode: a file, a link or a folder."""
    wrong = []
    for name, (data, mode) in files.items():
        kind, text, owner, size, target = candidate.get('/' + name, (None,) * 5)
        expected_kind = 'l' if data == 'link' else '-'
        if kind != expected_kind or owner != '0/0' or (kind == '-' and mode_bits(text) != mode):
            wrong.append(name)
    for folder in created:
        kind, text, owner, size, target = candidate.get('/' + folder, (None,) * 5)
        if kind != 'd' or owner != '0/0' or mode_bits(text) != 0o755:
            wrong.append(folder)
    if wrong:
        raise ValueError(f'Additions not packed as root\'s with their modes: {sorted(wrong)[:8]}')


def mksquashfs_version():
    out = subprocess.run(['mksquashfs', '-version'], capture_output=True, text=True).stdout
    match = re.search(r'version (\d+)\.(\d+)(?:\.(\d+))?', out)
    if not match:
        raise ValueError('mksquashfs did not say its version')
    return tuple(int(part or 0) for part in match.groups())


def tree_listing(root):
    """A tree's entries in parse_listing's form, to compare an extraction with its image."""
    top = Path(root).lstat()
    entries = {'/': ('d', stat.filemode(top.st_mode), f'{top.st_uid}/{top.st_gid}', None, None)}
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(parent)/name
            info = path.lstat()
            mode = stat.filemode(info.st_mode)
            rel = '/' + str(path.relative_to(root))
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                entries[rel] = ('l', 'l', f'{info.st_uid}/{info.st_gid}', str(len(os.fsencode(target))), target)
            elif stat.S_ISDIR(info.st_mode):
                entries[rel] = ('d', mode, f'{info.st_uid}/{info.st_gid}', None, None)
            elif stat.S_ISCHR(info.st_mode) or stat.S_ISBLK(info.st_mode):
                entries[rel] = (mode[0], mode, f'{info.st_uid}/{info.st_gid}',
                                f'{os.major(info.st_rdev)},{os.minor(info.st_rdev)}', None)
            else:
                entries[rel] = (mode[0], mode, f'{info.st_uid}/{info.st_gid}', str(info.st_size), None)
    return entries


def check_delta(before, after, additions):
    removed = set(before)-set(after)
    changed = {name for name in before.keys() & after.keys() if before[name]!=after[name]}
    added = set(after)-set(before)
    if removed or changed or added!=additions:
        raise ValueError(f'Unexpected rootfs delta: removed={sorted(removed)}, changed={sorted(changed)}, added={sorted(added)}')


def stock_time(path):
    """The stock squashfs's own time (its superblock's modification time, seconds since 1970)."""
    with Path(path).open('rb') as handle:
        head = handle.read(12)
    if len(head) < 12 or head[:4] != b'hsqs':raise ValueError('The stock rootfs is not a squashfs')
    return struct.unpack_from('<I', head, 8)[0]


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
    source = Path(__file__).resolve().parents[2]/'device/scripts/boot-report.sh'
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
# Each boot opens a section of the persistent boot log (/usr/data survives a reset, /run does
# not): the boot's id and uptime, then the decision's output, its exit status and boot.json.
case "${{1:-}}" in
  start)
    log={BOOT_LOG}
    mkdir -p {BOOT_LOG.rsplit('/', 1)[0]} 2>/dev/null
    [ -f $log ] && [ "$(wc -c <$log)" -ge {BOOT_LOG_BYTES} ] 2>/dev/null && mv -f $log $log.1 2>/dev/null
    up=; read -r up _ 2>/dev/null </proc/uptime
    echo "boot $(cat /proc/sys/kernel/random/boot_id 2>/dev/null) at $up" 2>/dev/null >>$log
    /{BOOT} early --profile {profile['version']} --card {usb['sd_mount']} --card-source {usb['sd_source']} >/run/disc-boot-early.log 2>&1
    # Its exit status beside its output (the owner's player, 2026-10-05: no run folder and no way to see why).
    echo "early exit $?" >>/run/disc-boot-early.log 2>/dev/null
    {{ head -c 2048 /run/disc-boot-early.log; head -c 1024 /run/disc-boot/boot.json; }} 2>/dev/null >>$log ;;
esac
exit 0
'''


def start_hook():
    return f'''#!/bin/sh
# Recovery from the card and the service package's supervisor; start returns at once.
case "${{1:-}}" in
  start)
    /{BOOT} start >/run/disc-boot-start.log 2>&1
    status=$?; up=; read -r up _ 2>/dev/null </proc/uptime
    echo "$up start exit $status" 2>/dev/null >>{BOOT_LOG} ;;
  stop) /{BOOT} stop ;;
esac
exit 0
'''


def card_guard(usb):
    """The card guard with the profile's mount point (docs/contract.md, "The card guard")."""
    if not re.fullmatch('(/[a-zA-Z0-9_-]+)+', usb['sd_mount']):
        raise ValueError('The card guard needs a plain absolute mount point')
    source = Path(__file__).resolve().parents[2]/'device/scripts/card-guard.sh'
    return source.read_text().replace('@SD@', usb['sd_mount'])


# Both wrappers fail open (owner's player, 2026-10-05): nothing before the exec of stock's binary
# may end the script. A POSIX shell exits on a failed redirection of a special built-in (":")
# and on a failed exec; the player's wrapper once wrote its marker with ":", and on a boot where
# the boot program had made no run folder the wrapper ended there, stock's watch loop restarted
# the UI for hours and the power key, which stock's player handles, did nothing. The boot
# layer's branch execs only on ui-launch, which the boot program wrote in this very boot, so
# that binary runs here; every other step is a plain command whose failure is ignored.


# Stock's watch loop (fiio_init.sh) finds the pair with pgrep -x, and BusyBox's pgrep matches
# argv[0] before the process name: a program a shell starts by its path has that path as argv[0],
# and the loop kills and restarts the pair every few seconds (the owner's player, 2026-10-04 and
# 05; under qemu-user cmdline starts with the interpreter, pgrep falls back to the name and the
# guest never saw it). So the wrappers start every program with exec -a and the bare name, from
# its own path (the kernel takes the process name from it, the emulator finds stock's player by
# it). BusyBox ash, the player's shell, has exec -a; a shell without it (dash) starts by path.
def wrapper_lines(name, launch, launcher, mark=''):
    guard = f"/{GUARD.rsplit('/', 1)[0]}"
    return (f'PATH={guard}:$PATH; export PATH\n'
            f'{log_line(name)}'
            f'named=; (exec -a true true) 2>/dev/null && named=1\n'
            f'if {launch} && [ -x /{launcher} ]; then\n'
            f'  [ -n "$named" ] && exec -a {name} /{launcher} "$@"\n'
            f'  exec /{launcher} "$@"\n'
            f'fi\n'
            f'{mark}'
            f'[ -n "$named" ] && exec -a {name} /usr/bin/{name} "$@"\n'
            f'exec /usr/bin/{name} "$@"\n')


def log_line(name):
    """Each start of the pair in the persistent boot log, with the uptime and whether the boot
    layer chose a package. Only plain commands, every failure ignored; nothing is exported."""
    return (f'log={BOOT_LOG}; up=; read -r up _ 2>/dev/null </proc/uptime\n'
            f'{{ [ ! -f $log ] || [ "$(wc -c <$log)" -lt {BOOT_LOG_BYTES} ]; }} 2>/dev/null && '
            f'echo "$up {name}$([ -f /run/disc-boot/ui-launch ] && echo \' ui-launch\')" 2>/dev/null >>$log\n')


def ui_wrapper():
    return '''#!/bin/sh
# Stock's UI unless the boot layer chose a ui package for this boot (docs/contract.md).
# Without that choice (stock mode, Volume Up, no package, a failure of the boot program)
# stock's own program starts and the boot program stays out of its way. Either way the
# card guard comes first in its PATH. Nothing here may end the script before stock's UI starts.
# Every start is by the bare name stock's watch loop looks for (pgrep -x matches argv[0]).
''' + wrapper_lines('mq_ui', '[ -f /run/disc-boot/ui-launch ]', LAUNCHER)


def player_wrapper():
    return '''#!/bin/sh
# Stock's player with the card guard first in its PATH (docs/contract.md, "The card guard"):
# stock removes the card's mount point with rm -rf after an unmount it never checks, which
# empties a card that is still mounted. While a ui package runs or the boot menu chooses
# (not after a fallback to stock's UI) the boot program starts the player: the launcher the
# chosen package brings, if any, else stock's. Started here, stock's player marks that a
# player ran in this boot: it runs the watchdog from then on, so no later start waits. The
# mark is a plain command's redirection: without a run folder it fails and the player starts.
# Every start is by the bare name stock's watch loop looks for (pgrep -x matches argv[0]).
''' + wrapper_lines('mq_player', '[ -f /run/disc-boot/ui-launch ] && [ ! -f /run/disc-boot/ui/fallback ]',
                   PLAYER_LAUNCHER, 'true 2>/dev/null >/run/disc-boot/player-ran\n')


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


def build(ota, console, boot, out, profile, writer, reader=None):
    if out.exists() or out.is_symlink():raise ValueError('Output exists; select a fresh directory')
    # Unpacked, changed and packed in the build's own file system, never in the output folder: a
    # macOS share under Docker dropped stock's setuid and group bits and its owners (owner's
    # player, 2026-10-05). DISC_IMAGE_SCRATCH names another place; it is checked like any other.
    scratch = Path(tempfile.mkdtemp(prefix='disc-image-', dir=os.environ.get('DISC_IMAGE_SCRATCH')))
    try:
        assemble(ota, console, boot, out, scratch, profile, writer, reader)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def update_reader(path=None):
    """The reference's reader of FiiO's update: the module on PYTHONPATH (the tooling container), or the
    file given (fetched at its pinned revision, plan stage 6)."""
    if path is None:
        from firmware.tools import firmware_inventory
        return firmware_inventory
    import importlib.util
    spec = importlib.util.spec_from_file_location('firmware_inventory', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assemble(ota, console, boot, out, scratch, profile, writer, reader=None):
    # External pinned tooling is an offline build input, never a production dependency.
    inventory_module = update_reader(reader)
    verified_chunks, plaintext_digest = inventory_module.verified_chunks, inventory_module.plaintext_digest
    # Without root the computer's file system cannot hold stock's owners and setuid bits: they are packed
    # from stock's listing (pseudo_lines), with mksquashfs 4.6 or later (-root-uid and its kind).
    rootless = os.geteuid() != 0
    if rootless and mksquashfs_version() < (4, 6):
        raise ValueError('Without root the image needs mksquashfs 4.6 or later (squashfs-tools)')
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
    stock_entries = listing(stock)
    check_case(stock_entries)
    tree = scratch/'candidate-tree'
    command('unsquashfs','-no-progress','-no-xattrs','-d',tree,stock)
    check_extraction(stock_entries, tree_listing(tree), exact=not rootless)
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
    # The same update and the same boot layer give the same image (plan, stage 6): what is added takes the
    # stock file system's own time, the stock folders it goes into keep theirs, and so does the superblock.
    stamp = stock_time(stock)
    kept = {}
    for name in files:
        for parent in (tree/name).parents:
            if parent == tree or not parent.is_relative_to(tree):break
            if parent.is_dir() and not parent.is_symlink():kept.setdefault(parent, parent.stat().st_mtime_ns)
    for name, (data, mode) in sorted(files.items()):
        destination = tree/name
        if destination.exists() or destination.is_symlink():raise ValueError(f'Payload collision: {name}')
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        if data == 'link':destination.symlink_to(mode)
        else:destination.write_bytes(data);destination.chmod(mode)
        os.utime(destination, (stamp, stamp), follow_symlinks=False)
    for name in files:
        for parent in (tree/name).parents:
            if parent == tree or not parent.is_relative_to(tree):break
            if parent not in kept:os.utime(parent, (stamp, stamp))
    for parent, mtime in kept.items():os.utime(parent, ns=(mtime, mtime))
    created = sorted({str(parent.relative_to(tree)) for name in files for parent in (tree/name).parents
                      if parent != tree and parent.is_relative_to(tree) and parent not in kept})
    expected = inventory(tree);check_delta(before,expected,additions)
    packed = out/'candidate.squashfs'
    owners = []
    if rootless:
        pseudo = out/'owners.pseudo'
        pseudo.write_text('\n'.join(pseudo_lines(stock_entries, tree, files, created, stamp)) + '\n')
        owner = stock_entries['/'][2].split('/')
        owners = ['-pf', pseudo, '-root-uid', owner[0], '-root-gid', owner[1],
                  '-root-mode', f'{mode_bits(stock_entries["/"][1]):o}', '-root-time', str(int(tree.lstat().st_mtime))]
    command('mksquashfs',tree,packed,'-comp','lzo','-b','131072','-no-xattrs','-noappend','-no-progress','-processors','2',
            '-mkfs-time',str(stamp),*owners)
    if packed.stat().st_size>capacity:raise ValueError('Candidate too large')
    candidate_entries = listing(packed)
    check_listing(stock_entries, candidate_entries, additions)
    check_additions(candidate_entries, files, created)
    verified = scratch/'verified-tree'
    command('unsquashfs','-no-progress','-no-xattrs','-d',verified,packed)
    actual = inventory(verified)
    if actual!=expected:raise ValueError('Packed rootfs failed full content/metadata round trip')
    check_delta(before,actual,additions)
    for name, image in (('stock-listing.txt', stock), ('candidate-listing.txt', packed)):
        (out/name).write_text(subprocess.run(['unsquashfs', '-lln', '-d', '', str(image)], capture_output=True, text=True,
                                             check=True).stdout)
    # The integration tests' tree (tests/integration/boot_*.py run it in the guest's container).
    command('unsquashfs','-no-progress','-no-xattrs','-d',out/'verified-tree',packed)
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
                  stockEntriesPreserved=len(before),stockEntriesExact=len(stock_entries),fullRoundTrip=True,packages=[],
                  builder=dict(rootless=rootless,mksquashfs='.'.join(map(str,mksquashfs_version()))),
                  listingsSha256={name:digest(out/name) for name in ('stock-listing.txt','candidate-listing.txt')},
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
    parser.add_argument('--reader',type=Path,help="The reference's update reader (firmware/tools/firmware_inventory.py at its "
                                                  "pinned revision) when it is not on PYTHONPATH")
    args = parser.parse_args()
    profile = load_profile(args.version)
    build(args.ota,args.console,args.boot,args.output,profile,load_writer(profile['writer']),args.reader)
