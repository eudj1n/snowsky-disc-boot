#!/usr/bin/env python3
"""The boot layer's release files (docs/dev/development.md, "Releases").

A release is named after the FiiO firmware it is for and our number for it (owner,
2026-10-03): 2.57.1 is the first for FiiO's 2.57, tagged v2.57.1; the firmware must be a
reviewed profile. Its files come from build/mips, the MIPS build of sources without local
changes (scripts/build.sh mips):

  disc-menu-<version>.zip           the boot menu's package (role menu), for the card
  disc-boot-<version>-mips.tar.gz   disc-boot and disc-usb-console, which install.py puts into
                                    the image it builds from FiiO's update on the user's computer
  disc-usb-payloads-<version>.tar.gz  the programs install.py runs from the player's RAM in USB Boot
  disc-installer-<version>.tar.gz   the installer for users: install.py with what it reads, these
                                    files and the default server and apps (release.py installer)
  SHA256SUMS

The image is never a release file (it holds FiiO's firmware), and debug builds stay local.

  python3 scripts/release.py build --version 2.57.1 --output dist
  python3 scripts/release.py installer --version 2.57.1 --dist dist   # once the catalog names this menu
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
import subprocess
import sys
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import package  # noqa: E402
from firmware_profile import load_profile  # noqa: E402

REPOSITORY = 'https://github.com/eudj1n/snowsky-disc-boot'
RELEASES = ROOT/'releases'
# Present only in the installer's archive (release.py installer).
INSTALLER_MANIFEST = ROOT/'installer.json'
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
    return f'disc-menu-{version}.zip', f'disc-boot-{version}-mips.tar.gz', f'disc-usb-payloads-{version}.tar.gz'


# The programs the installer runs from the player's RAM in USB Boot (plan, stage 6: built once here, with the boot
# layer's toolchain, and taken from the release by their digest, so a user's computer compiles nothing).
PAYLOAD_MODES = ('metadata', 'rootfs', 'rootfs-digest', 'rootfs-probe', 'staging-check')
PAYLOAD_FILES = ('build.json', 'identity.elf', 'identity.bin', 'identity_layout.h', 'identity.ld')
EVIDENCE_SCOPES = ('uboot', 'ota')


def build_payloads(firmware, diskos, output):
    """Every payload into output/<mode>, and the boot evidence's into output/boot-evidence (Docker, the boot
    layer's toolchain image, scripts/deployment/build_identity.py and boot_evidence.py)."""
    import subprocess
    tools = ROOT/'scripts/deployment'
    for mode in PAYLOAD_MODES:
        subprocess.run([sys.executable, '-B', str(tools/'build_identity.py'), '--version', firmware, '--mode', mode,
                        '--diskos', str(diskos), '--output', str(Path(output)/mode)], check=True, capture_output=True)
    subprocess.run([sys.executable, '-B', str(tools/'boot_evidence.py'), 'build', '--version', firmware,
                    '--diskos', str(diskos), '--output', str(Path(output)/'boot-evidence')], check=True, capture_output=True)


def payload_members(built):
    """(name in the archive, source) of every file the installer's tools read from the payloads' builds."""
    built = Path(built)
    members = [(f'{mode}/{name}', built/mode/name) for mode in PAYLOAD_MODES for name in PAYLOAD_FILES]
    members.append(('boot-evidence/build.json', built/'boot-evidence/build.json'))
    members += [(f'boot-evidence/{scope}/{name}', built/'boot-evidence'/scope/name)
                for scope in EVIDENCE_SCOPES for name in PAYLOAD_FILES if name != 'build.json']
    return members


def kit(version, mips, output):
    """disc-boot and disc-usb-console with the build id and the licence, as a tar.gz whose bytes
    depend on nothing but theirs."""
    tarball(f'disc-boot-{version}', [('disc-boot', mips/'disc-boot', 0o755), ('disc-usb-console', mips/'disc-usb-console', 0o755),
                                    ('build-id', mips/'build-id', 0o644), ('LICENSE', ROOT/'LICENSE', 0o644)], output)


def tarball(top, members, output):
    """members (name, source, mode) under top, as a tar.gz whose bytes depend on nothing but theirs."""
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


def build(version, output, mips=ROOT/'build/mips', release=True, diskos=None, payloads=build_payloads):
    firmware = firmware_of(version, release)
    mips, output = Path(mips), Path(output)
    build_id = (mips/'build-id').read_text().strip() if (mips/'build-id').exists() else ''
    if not re.fullmatch('[0-9a-f]{12}', build_id):
        raise ReleaseError(f'the MIPS build is {build_id or "missing"}: build committed sources (scripts/build.sh mips)')
    if output.exists() and any(output.iterdir()):
        raise ReleaseError(f'{output} is not empty')
    output.mkdir(parents=True, exist_ok=True)
    menu_zip, kit_name, _ = names(version)
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
    payload_name = names(version)[2]
    if diskos is None:
        import sources
        diskos = sources.fetch(firmware)
    (ROOT/'work').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT/'work', prefix='release-payloads-') as temp:
        payloads(firmware, diskos, Path(temp)/'built')
        tarball(f'disc-usb-payloads-{version}', [(name, source, 0o644) for name, source in payload_members(Path(temp)/'built')],
                output/payload_name)
    files = {name: dict(bytes=(output/name).stat().st_size, sha256=digest(output/name)) for name in names(version)}
    (output/'SHA256SUMS').write_text(''.join(f'{f["sha256"]}  {name}\n' for name, f in sorted(files.items())))
    return dict(version=version, firmware=firmware, buildId=build_id, files=files)


def observed(version, dist):
    """The release files in dist (the installer's archive when it was built), checked against SHA256SUMS."""
    dist = Path(dist)
    present = {p.name for p in dist.iterdir()} if dist.is_dir() else set()
    listed = list(names(version)) + ([installer_name(version)] if installer_name(version) in present else [])
    expected = set(listed) | {'SHA256SUMS'}
    if present != expected:
        raise ReleaseError(f'{dist} holds {sorted(present)}, a release holds {sorted(expected)}')
    files = {name: dict(bytes=(dist/name).stat().st_size, sha256=digest(dist/name)) for name in listed}
    sums = ''.join(f'{f["sha256"]}  {name}\n' for name, f in sorted(files.items()))
    if (dist/'SHA256SUMS').read_text() != sums:
        raise ReleaseError('SHA256SUMS does not list the files')
    with tarfile.open(dist/names(version)[1]) as tar:
        build_id = tar.extractfile(f'disc-boot-{version}/build-id').read().decode().strip()
    return files, build_id


def installer_name(version):
    return f'disc-installer-{version}.tar.gz'


# What install.py reads when it runs (plan, stage 6: the installer for users, one archive, nothing to clone): the
# scripts and the reviewed profiles, the catalogs, the earlier releases' records (their images are known), the
# payloads' sources their builds are checked by, the files the image takes from here, and the users' guide (the
# README and docs/, never docs/dev).
INSTALLER_TREE = ('install.py', 'console.py', 'LICENSE', 'README.md', 'docs/install.md', 'docs/way-back.md', 'docs/assets',
                  'scripts', 'firmware', 'catalog', 'releases', 'device/usbboot', 'device/scripts')
INSTALLER_SKIP = ('scripts/build.sh', 'scripts/test.sh', 'scripts/menu_font.py', 'scripts/menu_screens.py')


def tracked(paths, root=ROOT, committed=True):
    """(path, mode) of the files git tracks under paths; local changes to them refuse a release's build
    (committed; the tests build from the working tree)."""
    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, check=True).stdout
    if committed and git('status', '--porcelain', '--untracked-files=no', '--', *paths).strip():
        raise ReleaseError('the installer is built from committed files: commit or drop the changes to them first')
    found = []
    for line in git('ls-files', '-s', '--', *paths).splitlines():
        meta, path = line.split('\t', 1)
        found.append((path, 0o755 if int(meta.split()[0], 8) & 0o111 else 0o644))
    return sorted(found)


def bundled(version, dist, catalog_path, places, downloads, allow_download):
    """The archives the installer offers by default, in it (packages/): this release's files, then the
    catalog's default packages and the default server's default apps, each found by its digest."""
    import catalog
    dist = Path(dist)
    # The folder the downloads go to, made here (on the tag v2.57.5 there was none: every build before had been
    # given the server and apps with --from).
    downloads = Path(downloads)
    downloads.mkdir(parents=True, exist_ok=True)
    found = {name: dist/name for name in names(version)}
    entries = catalog.load(catalog_path, kind='packages')['entries']
    menu = names(version)[0]
    if not any(e['source'].get('url') == url(version, menu) and e['source'].get('sha256') == digest(dist/menu) and e.get('default')
               for e in entries):
        raise ReleaseError(f'the catalog does not offer {menu} by default: update catalog/packages.json first')
    def file_name(entry):
        return Path(entry['source']['url']).name if entry['source'].get('url') else f'{entry["name"]}-{entry["version"]}.zip'
    with tempfile.TemporaryDirectory() as temp:
        for entry in entries:
            source = entry['source']
            if not entry.get('default') or source.get('url') == url(version, menu):
                continue
            path = catalog.obtain(source, places, downloads, allow_download)
            if path is None:
                raise ReleaseError(f'{entry["name"]} {entry["version"]}: no file with its digest (--from) and no download')
            found[file_name(entry)] = path
            if entry['role'] == 'service':
                folder = catalog.fetch(entry, Path(temp)/entry['name'], [path], False)
                apps = Path(folder)/'catalog/apps.json'
                for app in (catalog.load(apps, kind='apps')['entries'] if apps.exists() else []):
                    if not app.get('default'):
                        continue
                    archive = catalog.obtain(app['source'], places, downloads, allow_download)
                    if archive is None:
                        raise ReleaseError(f'{app["name"]} {app["version"]}: no file with its digest (--from) and no download')
                    found[file_name(app)] = archive
    return found


def installer(version, dist, places=(), allow_download=True, root=ROOT, catalog_path=None, downloads=None, committed=True):
    """disc-installer-<version>.tar.gz into dist, and SHA256SUMS again: install.py with what it reads from this
    repository at its committed state, this release's files and the default server and apps (packages/), and
    installer.json, this release as its record will name it. Its own record is not in it (the record names the
    archive's digest), so the archive is the same wherever it is built from the same commit and files."""
    firmware = firmware_of(version)
    dist = Path(dist)
    files, build_id = observed(version, dist)
    files.pop(installer_name(version), None)
    catalog_path = Path(catalog_path or Path(root)/'catalog/packages.json')
    own = f'releases/{version}.json'
    tree = [(path, mode) for path, mode in tracked(INSTALLER_TREE, root, committed) if path not in INSTALLER_SKIP and path != own]
    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        packages = bundled(version, dist, catalog_path, list(places) + [dist], Path(downloads or temp/'downloads'), allow_download)
        manifest = dict(schema=1, version=version, firmware=firmware, tag=f'v{version}', buildId=build_id, files=files,
                        urls={name: url(version, name) for name in files},
                        packages={name: dict(bytes=p.stat().st_size, sha256=digest(p)) for name, p in sorted(packages.items())})
        (temp/'installer.json').write_text(json.dumps(manifest, indent=2) + '\n')
        members = [(path, Path(root)/path, mode) for path, mode in tree]
        if catalog_path != Path(root)/'catalog/packages.json':
            members = [(n, catalog_path if n == 'catalog/packages.json' else s, m) for n, s, m in members]
        members += [(f'packages/{name}', path, 0o644) for name, path in sorted(packages.items())]
        members.append(('installer.json', temp/'installer.json', 0o644))
        tarball(f'disc-installer-{version}', sorted(members), dist/installer_name(version))
    listed = dict(files, **{installer_name(version): dict(bytes=(dist/installer_name(version)).stat().st_size,
                                                         sha256=digest(dist/installer_name(version)))})
    (dist/'SHA256SUMS').write_text(''.join(f'{f["sha256"]}  {name}\n' for name, f in sorted(listed.items())))
    return dict(version=version, installer=listed[installer_name(version)], packages=manifest['packages'])


def image_of(folder):
    """The image the guest accepted, from its build's folder (build_candidate.py's report.json and the image):
    its name, bytes, digest and its first blocks' digest, which a player's identity probe reads (stage 6)."""
    folder = Path(folder)
    report = json.loads((folder/'report.json').read_text())
    name = next((n for n in report.get('artifacts', {}) if n.startswith('disc-boot-')), None)
    if not name or not (folder/name).is_file():
        raise ReleaseError(f'{folder} holds no built image')
    data = (folder/name).read_bytes()
    if hashlib.sha256(data).hexdigest() != report['artifacts'][name]['sha256']:
        raise ReleaseError(f'{name} is not the image its report names')
    return dict(name=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), first_blocks_bytes=FIRST_BLOCKS,
                first_blocks_sha256=hashlib.sha256(data[:FIRST_BLOCKS]).hexdigest())


FIRST_BLOCKS = 262144


def record(version, dist, accepted, releases=RELEASES, image=None):
    firmware = firmware_of(version)
    files, build_id = observed(version, dist)
    path = Path(releases)/f'{version}.json'
    if path.exists():
        raise ReleaseError(f'{path} exists: a release is recorded once; the next one takes the next number')
    if not accepted.strip():
        raise ReleaseError('say what the guest accepted (--accepted)')
    data = dict(schema=1, version=version, firmware=firmware, tag=f'v{version}', buildId=build_id, files=files,
                accepted=accepted.strip(), urls={name: url(version, name) for name in files})
    if image is not None:
        # The image built from FiiO's update and these files, as the guest ran it: a player holding it is known.
        data['image'] = image_of(image)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + '\n')
    return data


def url(version, name):
    return f'{REPOSITORY}/releases/download/v{version}/{name}'


BOOT_PROGRAMS = ('disc-boot', 'disc-usb-console')


def boot_release(firmware, releases=RELEASES):
    """The newest release recorded here for the firmware with the boot layer's programs: its version and
    the archive by its record's address, digest and size (plan, stage 6: the installer builds the image
    from the release file, not from a local build)."""
    found = []
    records = [json.loads(path.read_text()) for path in Path(releases).glob('*.json')]
    # In the installer's archive its own release is installer.json (its record names the archive).
    if INSTALLER_MANIFEST.exists() and Path(releases) == RELEASES:
        records.append(json.loads(INSTALLER_MANIFEST.read_text()))
    for data in records:
        name = f'disc-boot-{data["version"]}-mips.tar.gz'
        if data.get('firmware') == firmware and name in data.get('files', {}):
            number = tuple(int(part) for part in data['version'].split('.'))
            found.append((number, data, name))
    if not found:
        raise ReleaseError(f'no release of the boot layer is recorded for firmware {firmware}')
    _, data, name = max(found, key=lambda item: item[0])
    archive = lambda file: dict(url=data.get('urls', {}).get(file) or url(data['version'], file),
                                sha256=data['files'][file]['sha256'], size=data['files'][file]['bytes'])
    payloads = f'disc-usb-payloads-{data["version"]}.tar.gz'
    # Releases before 2.57.5 carry no payloads: the installer builds them then (Docker, the boot layer's toolchain).
    return dict(version=data['version'], buildId=data['buildId'], name=name, archive=archive(name),
                payloads=dict(name=payloads, archive=archive(payloads)) if payloads in data['files'] else None)


def extract_payloads(archive, version, output):
    """The payloads' builds from the release's archive (already checked by its digest) into output/<mode> and
    output/boot-evidence, every file the tools read and nothing else."""
    output = Path(output)
    if output.exists():
        raise ReleaseError(f'{output} exists; choose a fresh folder')
    top = f'disc-usb-payloads-{version}'
    wanted = {name for name, _ in payload_members(Path('.'))}
    with tarfile.open(archive, 'r:gz') as tar:
        members = {m.name: m for m in tar.getmembers()}
        if set(members) != {f'{top}/{name}' for name in wanted}:
            raise ReleaseError('the payloads archive does not hold exactly the payloads')
        for name in sorted(wanted):
            member = members[f'{top}/{name}']
            if not member.isfile():
                raise ReleaseError(f'{name} is not a regular file in the payloads archive')
            target = output/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(tar.extractfile(member).read())
    return output


def extract_boot(archive, version, build_id, output):
    """disc-boot and disc-usb-console from the release's archive (already checked by its digest), and its
    build id, which must be the record's; the archive holds nothing else that runs."""
    output = Path(output)
    if output.exists():
        raise ReleaseError(f'{output} exists; choose a fresh folder')
    folder = f'disc-boot-{version}'
    with tarfile.open(archive, 'r:gz') as tar:
        def member(name):
            try:
                found = tar.getmember(f'{folder}/{name}')
            except KeyError:
                raise ReleaseError(f'{folder}/{name} is not in the release') from None
            if not found.isfile():
                raise ReleaseError(f'{folder}/{name} is not a regular file in the release')
            return tar.extractfile(found).read()
        if member('build-id').decode().strip() != build_id:
            raise ReleaseError(f'the release says another build than its record ({build_id})')
        output.mkdir(parents=True)
        for name in BOOT_PROGRAMS:
            (output/name).write_bytes(member(name))
            (output/name).chmod(0o755)
    return {name: output/name for name in BOOT_PROGRAMS}


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
             'The image is built on your computer from FiiO\'s update by install.py; it is never a release file.', '']
    name = installer_name(data['version'])
    if name in data['files']:
        lines += [f'To install: unpack `{name}` and run `python3 install.py` in its folder (Python 3.11 or later, libusb, '
                  'squashfs-tools 4.6 or later and openssl; the installer names any that is missing). It holds this '
                  'release\'s files and the default server and apps; it downloads only the pinned files of diskOS (the '
                  'writer and its SPL) and of the emulator (the reader of FiiO\'s update), each checked by its digest.', '']
    lines += ['| File | Bytes | SHA-256 |', '| --- | --- | --- |']
    lines += [f'| `{name}` | {f["bytes"]} | `{f["sha256"]}` |' for name, f in sorted(data['files'].items())]
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build', help='The release files from build/mips')
    b.add_argument('--version', required=True)
    b.add_argument('--output', type=Path, required=True)
    b.add_argument('--mips', type=Path, default=ROOT/'build/mips')
    b.add_argument('--diskos', type=Path, help="diskOS's pinned files for the payloads (default: fetched, scripts/sources.py)")
    b.add_argument('--not-a-release', action='store_true', help='A build that is not a release (CI artifacts): a suffixed version')
    r = sub.add_parser('record', help='Record accepted release files in releases/<version>.json')
    r.add_argument('--version', required=True)
    r.add_argument('--dist', type=Path, required=True)
    r.add_argument('--accepted', required=True, help='What the guest accepted, with which emulator and image')
    r.add_argument('--image', type=Path, help="The image's build folder (build_candidate.py's output) the guest ran")
    i = sub.add_parser('installer', help="The installer's archive beside the release files, once the catalog offers this menu")
    i.add_argument('--version', required=True)
    i.add_argument('--dist', type=Path, required=True)
    i.add_argument('--from', dest='places', type=Path, action='append', default=[],
                   help='Files or folders holding the default server and apps by their digest (else downloaded)')
    i.add_argument('--offline', dest='download', action='store_false')
    c = sub.add_parser('check', help='A fresh build against its record and the catalog; the notes')
    c.add_argument('--version', required=True)
    c.add_argument('--dist', type=Path, required=True)
    c.add_argument('--notes', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'build':
            result = build(args.version, args.output, args.mips, release=not args.not_a_release, diskos=args.diskos)
        elif args.command == 'installer':
            result = installer(args.version, args.dist, args.places, args.download)
        elif args.command == 'record':
            result = record(args.version, args.dist, args.accepted, image=args.image)
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
