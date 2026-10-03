#!/usr/bin/env python3
"""Packages for the boot layer (docs/contract.md, "Packages").

Describes a folder as a package (package.json), checks a folder or a zip the
way disc-boot does (same rules, same messages), packs a checked folder as a
deterministic zip, and stages a package on a card for the recovery with Play
(.disc/boot/install/<role>/). Only `stage` writes to a card, and only with
--confirm-card-write.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from firmware_profile import load_profile  # noqa: E402

# disc-boot's bounds (device/src/manifest.h).
BOOT_API = 1
ARCH = 'mips32el-linux-static'
MAX_FILES = 256
MAX_ARGS = 32
MAX_PROFILES = 8
MANIFEST_BYTES = 65536
PACKAGE_BYTES = 32 * 1024 * 1024
MAX_READY = 120
ROLES = ('service', 'ui')
STAGING = Path('.disc/boot/install')
RESULT = Path('.disc/boot/result.json')
ZIP_TIME = (2026, 1, 1, 0, 0, 0)


class PackageError(ValueError):
    pass


def fail(message):
    raise PackageError(message)


def printable(value, longest, empty=False):
    return (isinstance(value, str) and (empty or value) and len(value) <= longest
            and all(0x20 <= ord(c) <= 0x7e for c in value))


def name_ok(name):
    return isinstance(name, str) and 1 <= len(name) <= 32 and all(c in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in name)


def path_ok(path):
    """Relative, no "." or ".." components, at most 8 deep, conservative characters."""
    if not isinstance(path, str) or not 1 <= len(path) <= 200 or path.startswith('/') or path.endswith('/'):
        return False
    parts = path.split('/')
    allowed = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+@-')
    if len(parts) > 8 or path == 'package.json':
        return False
    return all(part and len(part) <= 64 and part not in ('.', '..') and set(part) <= allowed for part in parts)


def integer(value, low=0, high=10**15 - 1):
    return type(value) is int and low <= value <= high


class Obj:
    """A JSON object that remembers repeated keys (disc-boot refuses them)."""

    def __init__(self, pairs):
        self.pairs = pairs
        self.keys = [key for key, _ in pairs]

    def get(self, key, missing=None):
        found = [value for name, value in self.pairs if name == key]
        if len(found) > 1:
            return Repeated
        return found[0] if found else missing


Repeated = object()
Missing = object()


def parse(text):
    if '\\u' in text:
        fail('package.json is not a JSON object')

    def refuse(_):
        fail('package.json is not a JSON object')
    try:
        root = json.loads(text, object_pairs_hook=Obj, parse_constant=refuse)
    except (json.JSONDecodeError, RecursionError):
        fail('package.json is not a JSON object')
    if not isinstance(root, Obj):
        fail('package.json is not a JSON object')
    return root


def load(folder):
    """dir/package.json, validated field by field as disc-boot's manifest_load."""
    path = Path(folder)/'package.json'
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size >= MANIFEST_BYTES:
            raise OSError
        text = path.read_bytes().decode('latin-1')
    except OSError:
        fail('package.json missing or over 64 KiB')
    root = parse(text)
    m = {}
    if root.get('schema') != 1 or not integer(root.get('schema')):
        fail('schema must be 1')
    m['name'] = root.get('name')
    if not printable(m['name'], 32) or not name_ok(m['name']):
        fail('name must match [a-z0-9-]{1,32}')
    m['version'] = root.get('version')
    if not printable(m['version'], 64):
        fail('version must be 1-64 printable ASCII')
    m['role'] = root.get('role')
    if m['role'] not in ROLES:
        fail('role must be service or ui')
    m['bootApi'] = root.get('bootApi')
    if not integer(m['bootApi'], 1, 1000):
        fail('bootApi must be a positive integer')
    m['arch'] = root.get('arch')
    if not printable(m['arch'], 47):
        fail('arch is required')
    m['entry'] = root.get('entry')
    if not path_ok(m['entry']):
        fail('entry must be a listed relative path')
    player = root.get('player', Missing)
    if player is not Missing and not path_ok(player):
        fail('player must be a listed relative path')
    m['player'] = None if player is Missing else player
    ready = root.get('ready', Missing)
    if ready is not Missing and not integer(ready, 1, MAX_READY):
        fail('ready must be 1-120 seconds')
    m['ready'] = 30 if ready is Missing else ready
    profiles = root.get('profiles')
    if not isinstance(profiles, list) or not 1 <= len(profiles) <= MAX_PROFILES:
        fail('profiles must list 1-8 firmware profiles')
    if not all(printable(p, 16) for p in profiles):
        fail('a profile is not a short string')
    m['profiles'] = profiles
    args = root.get('args', Missing)
    if args is not Missing and (not isinstance(args, list) or len(args) > MAX_ARGS):
        fail('args must list at most 32 strings')
    m['args'] = [] if args is Missing else args
    if not all(printable(a, 256, empty=True) for a in m['args']):
        fail('an argument is not a string of up to 256 printable ASCII')
    files = root.get('files')
    if not isinstance(files, Obj) or not 1 <= len(files.pairs) <= MAX_FILES:
        fail('files must list 1-256 files')
    m['files'], total = {}, 0
    for index, (path, value) in enumerate(files.pairs, 1):
        if not printable(path, 200) or not path_ok(path):
            fail(f'file {index} has an unsafe path')
        if path in m['files']:
            fail(f'{path} is listed twice')
        if not isinstance(value, Obj):
            fail(f'{path} needs size, sha256 and mode')
        size, digest, mode = value.get('size'), value.get('sha256'), value.get('mode')
        if (not integer(size) or not isinstance(digest, str) or len(digest) != 64 or set(digest) - set('0123456789abcdef')
                or mode not in ('0755', '0644')):
            fail(f'{path} needs an integer size, a lower-case sha256 and mode 0755 or 0644')
        m['files'][path] = dict(size=size, sha256=digest, mode=mode)
        total += size
        if total > PACKAGE_BYTES:
            fail('the package exceeds 32 MiB')
    if m['files'].get(m['entry'], {}).get('mode') != '0755':
        fail('entry must be a listed file with mode 0755')
    if m['role'] == 'ui' and m['entry'].rsplit('/', 1)[-1] != 'mq_ui':
        fail("a ui package's entry must be named mq_ui")
    if m['player'] is not None and m['role'] != 'ui':
        fail('only a ui package brings a player launcher')
    if m['player'] is not None and m['files'].get(m['player'], {}).get('mode') != '0755':
        fail('player must be a listed file with mode 0755')
    m['bytes'] = total
    return m


def fits(m, role, profile, arch=ARCH, boot_api=BOOT_API):
    """The package fits a boot layer: role, API, architecture and firmware profile."""
    if m['role'] != role:
        fail(f"the package's role is {m['role']}, not {role}")
    if m['bootApi'] > boot_api:
        fail(f"the package needs boot API {m['bootApi']} (this boot layer has {boot_api})")
    if m['arch'] != arch:
        fail(f"the package is built for {m['arch']}, not {arch}")
    if profile not in m['profiles']:
        fail(f'the package does not support firmware profile {profile}')


def sha256(path):
    with open(path, 'rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def verify(folder, m, check_modes=True):
    """Every listed file is a regular file of its size and digest (and mode, when asked);
    nothing else but package.json and folders lies in the folder."""
    folder = Path(folder)
    for path, entry in m['files'].items():
        target = folder/path
        try:
            info = target.lstat()
        except OSError:
            info = None
        if info is None or not stat.S_ISREG(info.st_mode):
            fail(f'{path} is missing or not a regular file')
        if info.st_size != entry['size']:
            fail(f"{path} has {info.st_size} bytes, not {entry['size']}")
        if sha256(target) != entry['sha256']:
            fail(f'{path} does not match its sha256')
        if check_modes and stat.S_IMODE(info.st_mode) != int(entry['mode'], 8):
            fail(f"{path} has mode {stat.S_IMODE(info.st_mode):o}, not {int(entry['mode'], 8):o}")

    def walk(relative, depth):
        here = folder/relative if relative else folder
        try:
            items = list(os.scandir(here))
        except OSError:
            fail(f"cannot list {relative or 'the package'}")
        for item in items:
            child = f'{relative}/{item.name}' if relative else item.name
            info = os.lstat(item.path)
            if stat.S_ISDIR(info.st_mode):
                if depth >= 8:
                    fail(f'{child} is nested too deep')
                walk(child, depth + 1)
            elif not stat.S_ISREG(info.st_mode):
                fail(f'{child} is not a regular file')
            elif child not in m['files'] and child != 'package.json':
                fail(f'{child} is not listed')
    walk('', 0)


def check(folder, role=None, profile=None, arch=ARCH, check_modes=True):
    m = load(folder)
    fits(m, role or m['role'], profile or load_profile()['version'], arch)
    verify(folder, m, check_modes)
    return m


def describe(folder, name, version, role, entry, args=(), ready=None, profiles=None, arch=ARCH, boot_api=BOOT_API,
             player=None):
    """package.json for a folder: every file with its size, digest and mode (0755 when executable)."""
    folder = Path(folder)
    files = {}
    for path in sorted(folder.rglob('*')):
        relative = path.relative_to(folder).as_posix()
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            fail(f'{relative} is not a regular file')
        if relative == 'package.json':
            continue
        if not path_ok(relative):
            fail(f'{relative} is not a safe package path (letters, digits and ._+@-, at most 8 folders deep)')
        files[relative] = dict(size=info.st_size, sha256=sha256(path), mode='0755' if info.st_mode & 0o111 else '0644')
    if entry not in files:
        fail(f'the entry {entry} is not in the folder')
    if files[entry]['mode'] != '0755':
        fail(f'the entry {entry} is not executable')
    if player is not None and files.get(player, {}).get('mode') != '0755':
        fail(f'the player launcher {player} is not an executable file of the folder')
    manifest = dict(schema=1, name=name, version=version, role=role, bootApi=boot_api, arch=arch,
                    profiles=list(profiles or [load_profile()['version']]), entry=entry, args=list(args),
                    ready=30 if ready is None else ready, files=files)
    if player is not None:
        manifest['player'] = player
    text = json.dumps(manifest, indent=2) + '\n'
    if len(text.encode()) >= MANIFEST_BYTES:
        fail('package.json would exceed 64 KiB')
    (folder/'package.json').write_text(text)
    load(folder)
    return manifest


def zip_package(folder, output):
    """A deterministic zip holding package.json and the files at its root, with their modes."""
    folder, output = Path(folder), Path(output)
    m = load(folder)
    verify(folder, m, True)
    if output.exists():
        fail(f'{output} exists; choose a fresh name')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, mode in [('package.json', 0o644)] + sorted((p, int(e['mode'], 8)) for p, e in m['files'].items()):
            info = zipfile.ZipInfo(path, date_time=ZIP_TIME)
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (folder/path).read_bytes())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(buffer.getvalue())
    return dict(zip=str(output), name=m['name'], version=m['version'], role=m['role'], files=len(m['files']),
                bytes=output.stat().st_size, sha256=hashlib.sha256(buffer.getvalue()).hexdigest())


def unpack(archive, folder):
    """A package zip into a fresh folder: regular files at safe paths, within the package bounds."""
    folder = Path(folder)
    total = 0
    with zipfile.ZipFile(archive) as z:
        seen = set()
        for info in z.infolist():
            name = info.filename
            if info.is_dir():
                continue
            kind = stat.S_IFMT(info.external_attr >> 16)
            if kind not in (0, stat.S_IFREG):
                fail(f'{name} in the zip is not a regular file')
            if name != 'package.json' and not path_ok(name):
                fail(f'{name} in the zip has an unsafe path')
            if name in seen:
                fail(f'{name} is twice in the zip')
            seen.add(name)
            total += info.file_size
            if total > PACKAGE_BYTES + MANIFEST_BYTES:
                fail('the zip holds more than a package may')
            target = folder/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(info))
            target.chmod(stat.S_IMODE(info.external_attr >> 16) or 0o644)
    return folder


def source_folder(source, temp):
    source = Path(source)
    if source.is_file():
        return unpack(source, Path(temp)/'package')
    if not source.is_dir():
        fail(f'{source} is neither a package folder nor a zip')
    return source


def write_new(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, 'wb') as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    if Path(path).read_bytes() != data:
        fail(f'Readback mismatch: {path}')


def stage(source, card, profile=None, arch=ARCH):
    """Copies a package (zip or folder) into <card>/.disc/boot/install/<role>/ for the recovery
    with Play, replacing what was staged for that role. Written beside, checked, then swapped in."""
    card = Path(card)
    if not card.is_dir() or card.is_symlink():
        fail('Card mount is not a directory')
    with tempfile.TemporaryDirectory() as temp:
        folder = source_folder(source, temp)
        m = check(folder, profile=profile, arch=arch)
        files = {path: (folder/path).read_bytes() for path in ['package.json', *m['files']]}
    staging = card/STAGING
    target, fresh = staging/m['role'], staging/f".{m['role']}.staging"
    if fresh.exists():
        shutil.rmtree(fresh)
    space = os.statvfs(card)
    if sum(map(len, files.values())) + 1024 * 1024 > space.f_bavail * space.f_frsize:
        fail('Not enough free space on the card for the package. Nothing was written.')
    staging.mkdir(parents=True, exist_ok=True)
    fresh.mkdir()
    try:
        for path, data in sorted(files.items()):
            (fresh/path).parent.mkdir(parents=True, exist_ok=True)
            write_new(fresh/path, data)
        os.sync()
        verify(fresh, m, check_modes=False)  # a card's file system keeps no modes
        if target.exists():
            shutil.rmtree(target)
        os.replace(fresh, target)
        os.sync()
    except (OSError, PackageError) as failure:
        shutil.rmtree(fresh, ignore_errors=True)
        fail(f'Staging failed ({failure}); nothing new is staged for {m["role"]}')
    return dict(role=m['role'], name=m['name'], version=m['version'], path=str(target),
                files=len(m['files']), physical_device_accessed=False)


def result(card):
    path = Path(card)/RESULT
    if not path.is_file():
        return dict(result=None, note='No recovery result on this card yet')
    return json.loads(path.read_text())


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)
    d = sub.add_parser('describe', help='Write package.json for a folder')
    d.add_argument('--source', type=Path, required=True)
    d.add_argument('--name', required=True)
    d.add_argument('--version', required=True)
    d.add_argument('--role', choices=ROLES, required=True)
    d.add_argument('--entry', required=True)
    d.add_argument('--player', help="A ui package's own launcher of stock's player (it ends in /usr/bin/mq_player)")
    d.add_argument('--arg', action='append', default=[], help='An argument for the entry (repeat for more)')
    d.add_argument('--ready', type=int, help='Seconds to become ready (1-120, default 30)')
    d.add_argument('--profile', action='append', help='A supported firmware profile (default: the active one)')
    d.add_argument('--arch', default=ARCH)
    c = sub.add_parser('check', help='Check a package folder or zip as disc-boot does')
    c.add_argument('--source', type=Path, required=True)
    c.add_argument('--role', choices=ROLES)
    c.add_argument('--profile')
    c.add_argument('--arch', default=ARCH)
    z = sub.add_parser('zip', help='Pack a checked package folder as a zip')
    z.add_argument('--source', type=Path, required=True)
    z.add_argument('--output', type=Path, required=True)
    s = sub.add_parser('stage', help='Stage a package on a card for the recovery with Play (explicit operator step)')
    s.add_argument('--package', type=Path, required=True)
    s.add_argument('--card', type=Path, required=True)
    s.add_argument('--profile')
    s.add_argument('--arch', default=ARCH)
    s.add_argument('--confirm-card-write', action='store_true')
    r = sub.add_parser('result', help="Show the card's last recovery result")
    r.add_argument('--card', type=Path, required=True)
    args = p.parse_args()
    try:
        if args.command == 'describe':
            m = describe(args.source, args.name, args.version, args.role, args.entry, args.arg, args.ready, args.profile, args.arch,
                         player=args.player)
            out = dict(name=m['name'], version=m['version'], role=m['role'], files=len(m['files']),
                       bytes=sum(f['size'] for f in m['files'].values()))
        elif args.command == 'check':
            with tempfile.TemporaryDirectory() as temp:
                m = check(source_folder(args.source, temp), args.role, args.profile, args.arch)
            out = dict(ok=True, name=m['name'], version=m['version'], role=m['role'], bytes=m['bytes'])
        elif args.command == 'zip':
            out = zip_package(args.source, args.output)
        elif args.command == 'stage':
            if not args.confirm_card_write:
                p.error('stage writes to the card; pass --confirm-card-write after verifying the mount')
            out = stage(args.package, args.card, args.profile, args.arch)
        else:
            out = result(args.card)
    except PackageError as error:
        print(json.dumps(dict(ok=False, error=str(error))))
        return 1
    print(json.dumps(out, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
