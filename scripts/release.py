#!/usr/bin/env python3
"""The boot layer's release files (docs/development.md, "Releases").

A release is named after the FiiO firmware it is for and our number for it (owner,
2026-10-03): 2.57.1 is the first for FiiO's 2.57, tagged v2.57.1; the firmware must be a
reviewed profile. Its files come from build/mips, the MIPS build of sources without local
changes (scripts/build.sh mips):

  disc-menu-<version>.zip           the boot menu's package (role menu), for the card
  disc-boot-<version>-mips.tar.gz   disc-boot and disc-usb-console, which install.py puts into
                                    the image it builds from FiiO's update on the user's computer
  SHA256SUMS

The image is never a release file (it holds FiiO's firmware), and debug builds stay local.

  python3 scripts/release.py build --version 2.57.1 --output dist
  python3 scripts/release.py record --version 2.57.1 --dist dist --accepted "<what the guest ran>"
  python3 scripts/release.py check --version 2.57.1 --dist dist --notes notes.md

record keeps, after the guest accepted these very files, each file's size and SHA-256 and the
build id in releases/<version>.json (a record is never rewritten). check, in the release
workflow, compares a fresh build with the record and the catalog entries that point at the
release, and writes the release's notes; a release is published only as a draft, by hand.
"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import package  # noqa: E402
from firmware_profile import load_profile  # noqa: E402

REPOSITORY = 'https://github.com/eudj1n/snowsky-disc-boot'
RELEASES = ROOT/'releases'
# Builds that are not releases (CI's artifacts) carry a suffix: 2.57.0-ci.<commit>.
VERSION = re.compile(r'(\d+\.\d+)\.(\d+)(-[a-z0-9.]+)?')
KIT_TIME = 1767225600  # 2026-01-01T00:00:00Z, as the packages' zip entries


class ReleaseError(Exception):
    pass


def firmware_of(version, release=True):
    match = VERSION.fullmatch(version or '')
    if not match or (release and match.group(3)):
        raise ReleaseError(f'{version!r} is not a release version: <firmware>.<number>, as 2.57.1')
    try:
        load_profile(match.group(1))
    except Exception as error:  # noqa: BLE001 - any refusal of the profile loader
        raise ReleaseError(f'{match.group(1)} is not a reviewed firmware profile: {error}')
    return match.group(1)


def digest(path):
    with open(path, 'rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def names(version):
    return f'disc-menu-{version}.zip', f'disc-boot-{version}-mips.tar.gz'


def kit(version, mips, output):
    """disc-boot and disc-usb-console with the build id and the licence, as a tar.gz whose bytes
    depend on nothing but theirs."""
    top = f'disc-boot-{version}'
    members = [('disc-boot', mips/'disc-boot', 0o755), ('disc-usb-console', mips/'disc-usb-console', 0o755),
               ('build-id', mips/'build-id', 0o644), ('LICENSE', ROOT/'LICENSE', 0o644)]
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w', format=tarfile.USTAR_FORMAT) as tar:
        for name, source, mode in members:
            data = source.read_bytes()
            info = tarfile.TarInfo(f'{top}/{name}')
            info.size, info.mode, info.mtime, info.uid, info.gid, info.uname, info.gname = len(data), mode, KIT_TIME, 0, 0, '', ''
            tar.addfile(info, io.BytesIO(data))
    packed = io.BytesIO()
    with gzip.GzipFile(filename='', mode='wb', fileobj=packed, mtime=0, compresslevel=9) as out:
        out.write(raw.getvalue())
    output.write_bytes(packed.getvalue())


def build(version, output, mips=ROOT/'build/mips', release=True):
    firmware = firmware_of(version, release)
    mips, output = Path(mips), Path(output)
    build_id = (mips/'build-id').read_text().strip() if (mips/'build-id').exists() else ''
    if not re.fullmatch('[0-9a-f]{12}', build_id):
        raise ReleaseError(f'the MIPS build is {build_id or "missing"}: build committed sources (scripts/build.sh mips)')
    if output.exists() and any(output.iterdir()):
        raise ReleaseError(f'{output} is not empty')
    output.mkdir(parents=True, exist_ok=True)
    menu_zip, kit_name = names(version)
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)/'disc-menu'
        (folder/'bin').mkdir(parents=True)
        (folder/'licenses').mkdir()
        shutil.copyfile(mips/'disc-menu', folder/'bin/mq_ui')
        (folder/'bin/mq_ui').chmod(0o755)
        shutil.copyfile(ROOT/'LICENSE', folder/'LICENSE')
        shutil.copyfile(ROOT/'device/licenses/Inter.OFL', folder/'licenses/Inter.OFL')
        package.describe(folder, 'disc-menu', version, 'menu', 'bin/mq_ui', profiles=[firmware], homepage=REPOSITORY)
        package.zip_package(folder, output/menu_zip)
    kit(version, mips, output/kit_name)
    files = {name: dict(bytes=(output/name).stat().st_size, sha256=digest(output/name)) for name in (menu_zip, kit_name)}
    (output/'SHA256SUMS').write_text(''.join(f'{f["sha256"]}  {name}\n' for name, f in sorted(files.items())))
    return dict(version=version, firmware=firmware, buildId=build_id, files=files)


def observed(version, dist):
    dist = Path(dist)
    expected = set(names(version)) | {'SHA256SUMS'}
    present = {p.name for p in dist.iterdir()} if dist.is_dir() else set()
    if present != expected:
        raise ReleaseError(f'{dist} holds {sorted(present)}, a release holds {sorted(expected)}')
    files = {name: dict(bytes=(dist/name).stat().st_size, sha256=digest(dist/name)) for name in names(version)}
    sums = ''.join(f'{f["sha256"]}  {name}\n' for name, f in sorted(files.items()))
    if (dist/'SHA256SUMS').read_text() != sums:
        raise ReleaseError('SHA256SUMS does not list the files')
    with tarfile.open(dist/names(version)[1]) as tar:
        build_id = tar.extractfile(f'disc-boot-{version}/build-id').read().decode().strip()
    return files, build_id


def record(version, dist, accepted, releases=RELEASES):
    firmware = firmware_of(version)
    files, build_id = observed(version, dist)
    path = Path(releases)/f'{version}.json'
    if path.exists():
        raise ReleaseError(f'{path} exists: a release is recorded once; the next one takes the next number')
    if not accepted.strip():
        raise ReleaseError('say what the guest accepted (--accepted)')
    data = dict(schema=1, version=version, firmware=firmware, tag=f'v{version}', buildId=build_id, files=files,
                accepted=accepted.strip(), urls={name: url(version, name) for name in files})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + '\n')
    return data


def url(version, name):
    return f'{REPOSITORY}/releases/download/v{version}/{name}'


def check(version, dist, releases=RELEASES, catalog_path=ROOT/'catalog/packages.json'):
    firmware_of(version)
    path = Path(releases)/f'{version}.json'
    if not path.exists():
        raise ReleaseError(f'no record {path}: a release is built only from a recorded, accepted build')
    data = json.loads(path.read_text())
    files, build_id = observed(version, dist)
    if files != data['files'] or build_id != data['buildId']:
        differs = [n for n in files if files[n] != data['files'].get(n)] + (['build id'] if build_id != data['buildId'] else [])
        raise ReleaseError(f'this build is not the accepted one: {", ".join(differs)} differ from {path.name}')
    pointing = 0
    for entry in json.loads(Path(catalog_path).read_text())['entries']:
        source = entry.get('source', {})
        for name, wanted in files.items():
            if source.get('url') == url(version, name):
                pointing += 1
                if (source.get('sha256'), source.get('size')) != (wanted['sha256'], wanted['bytes']):
                    raise ReleaseError(f'the catalog\'s {entry["name"]} names {name} with another digest or size')
    return dict(data, catalogEntries=pointing)


def notes(data):
    lines = [f'The boot layer {data["version"]} for FiiO\'s firmware {data["firmware"]} (build {data["buildId"]}).', '',
             f'Accepted on the emulator\'s guest: {data["accepted"]}.', '',
             'The image is built on your computer from FiiO\'s update by install.py; it is never a release file.', '',
             '| File | Bytes | SHA-256 |', '| --- | --- | --- |']
    lines += [f'| `{name}` | {f["bytes"]} | `{f["sha256"]}` |' for name, f in sorted(data['files'].items())]
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build', help='The release files from build/mips')
    b.add_argument('--version', required=True)
    b.add_argument('--output', type=Path, required=True)
    b.add_argument('--mips', type=Path, default=ROOT/'build/mips')
    b.add_argument('--not-a-release', action='store_true', help='A build that is not a release (CI artifacts): a suffixed version')
    r = sub.add_parser('record', help='Record accepted release files in releases/<version>.json')
    r.add_argument('--version', required=True)
    r.add_argument('--dist', type=Path, required=True)
    r.add_argument('--accepted', required=True, help='What the guest accepted, with which emulator and image')
    c = sub.add_parser('check', help='A fresh build against its record and the catalog; the notes')
    c.add_argument('--version', required=True)
    c.add_argument('--dist', type=Path, required=True)
    c.add_argument('--notes', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'build':
            result = build(args.version, args.output, args.mips, release=not args.not_a_release)
        elif args.command == 'record':
            result = record(args.version, args.dist, args.accepted)
        else:
            result = check(args.version, args.dist)
            if args.notes:
                args.notes.write_text(notes(result))
    except (ReleaseError, package.PackageError, OSError) as error:
        print(f'release: {error}', file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
