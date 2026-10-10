#!/usr/bin/env python3
"""The catalogs the installer offers from (docs/dev/boot/contract.md, "Catalogs").

Two levels in one format: this repository's catalog/packages.json names the boot
layer's packages (the server, the boot menu, a ui package such as diskOS), and a
server package carries catalog/apps.json, the apps it offers for the card's Apps/.
An entry names its archive by SHA-256 and size; the archive comes from its url
once published, or from a local file with that digest. A recipe entry is built on
the user's computer from someone else's published release (diskOS), so nothing of
it is redistributed from here.

    python3 scripts/catalog.py check [--catalog FILE]
    python3 scripts/catalog.py list
    python3 scripts/catalog.py fetch --name diskos --output DIR [--from DIR ...] [--download]
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import ssl
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import package  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT/'catalog/packages.json'
KINDS = ('packages', 'apps')
RECIPES = ('diskos-release',)
VERIFIED = ('date', 'acceptance')
# diskOS's UI as a ui package (contract, "Acceptance: two independent packages"): diskOS 1.2.0
# runs its UI only after its own boot hook recorded the choice; under the boot layer boot chose.
DISKOS_ENTRY = '''#!/bin/sh
# diskOS's UI as the boot layer's ui package, assembled on this computer from diskOS's release.
# diskOS's own image records its S96 hook's choice of UI; boot made that choice here.
printf 'diskos\\n' > /tmp/.diskos_boot_select
: > "$DISC_BOOT_RUN/ready"
exec -a mq_ui "$DISC_BOOT_SLOT/diskos/mq_ui" "$@"
'''


class CatalogError(ValueError):
    pass


class NotLocal(CatalogError):
    """No local file is the archive and it may not be downloaded: a place to look in would do."""


def fail(message):
    raise CatalogError(message)


def hex64(value):
    return isinstance(value, str) and len(value) == 64 and not set(value) - set('0123456789abcdef')


def archive_ok(source, where):
    url = source.get('url')
    if url is not None and not (isinstance(url, str) and url.startswith('https://')):
        fail(f'{where}: url must be an https address or null')
    if not hex64(source.get('sha256')):
        fail(f'{where}: sha256 must be 64 lower-case hex digits')
    if not package.integer(source.get('size'), 1):
        fail(f'{where}: size must be a positive integer')


def entry_ok(entry, kind):
    if not isinstance(entry, dict):
        fail('an entry is not an object')
    name = entry.get('name')
    where = f'{kind} entry {name!r}'
    if kind == 'packages' and not package.name_ok(name):
        fail(f'{where}: name must match [a-z0-9-]{{1,32}}')
    if kind == 'apps' and not package.printable(name, 64):
        fail(f'{where}: name must be 1-64 printable ASCII')
    if not package.printable(entry.get('version'), 64):
        fail(f'{where}: version must be 1-64 printable ASCII')
    if not package.printable(entry.get('license'), 200):
        fail(f'{where}: license is required')
    if not isinstance(entry.get('default'), bool):
        fail(f'{where}: default must be true or false')
    if 'homepage' in entry and not package.homepage_ok(entry['homepage']):
        fail(f'{where}: homepage must be an https address of at most 200 characters, without a query or a fragment')
    verified = entry.get('verified')
    if not isinstance(verified, dict) or not all(package.printable(verified.get(key), 200) for key in VERIFIED):
        fail(f'{where}: verified must give at least its date and acceptance')
    source = entry.get('source')
    if not isinstance(source, dict):
        fail(f'{where}: source is required')
    if 'recipe' in source:
        if kind != 'packages' or source['recipe'] not in RECIPES:
            fail(f'{where}: unknown recipe {source["recipe"]!r}')
        archives = source.get('archives')
        if not isinstance(archives, list) or not archives:
            fail(f'{where}: a recipe lists the archives it can start from')
        for archive in archives:
            archive_ok(archive, where)
            if archive.get('url') is None:
                fail(f'{where}: a recipe starts from published archives')
        if not package.path_ok(source.get('member')) or not hex64(source.get('memberSha256')):
            fail(f'{where}: a recipe names its member and the member\'s sha256')
    else:
        archive_ok(source, where)
    if kind == 'packages':
        if entry.get('role') not in package.ROLES:
            fail(f'{where}: role must be controller, service, ui or menu')
        profiles = entry.get('profiles')
        if not isinstance(profiles, list) or not profiles or not all(package.printable(p, 16) for p in profiles):
            fail(f'{where}: profiles must list firmware profiles')
        if not package.integer(entry.get('bootApi'), 1, 1000):
            fail(f'{where}: bootApi must be a positive integer')
        if 'title' in entry and not package.printable(entry['title'], 32):
            fail(f'{where}: title must be 1-32 printable ASCII')
    else:
        if not package.integer(entry.get('api'), 1, 1000):
            fail(f'{where}: api must name the server API the app needs')


def load(path=CATALOG, kind=None):
    """A catalog, checked; kind is "packages" or "apps" (taken from the file when not given)."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        fail(f'{path} is not a readable JSON catalog')
    if not isinstance(data, dict) or data.get('schema') != 1 or data.get('kind') not in KINDS:
        fail(f'{path}: schema must be 1 and kind packages or apps')
    if kind and data['kind'] != kind:
        fail(f'{path}: a catalog of {data["kind"]}, not {kind}')
    entries = data.get('entries')
    if not isinstance(entries, list) or not entries:
        fail(f'{path}: entries must list at least one entry')
    seen = set()
    for entry in entries:
        entry_ok(entry, data['kind'])
        if entry['name'] in seen:
            fail(f'{path}: {entry["name"]} is listed twice')
        seen.add(entry['name'])
    return data


def sha256(path):
    with open(path, 'rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def find_local(archive, places):
    """A local file that is this archive: its size and digest, in any of the places."""
    for place in places:
        place = Path(place)
        candidates = [place] if place.is_file() else sorted(p for p in place.rglob('*') if p.is_file()) if place.is_dir() else []
        for candidate in candidates:
            if candidate.stat().st_size == archive['size'] and sha256(candidate) == archive['sha256']:
                return candidate
    return None


def tls_context():
    """Certificates checked always: Python's own roots, or, where a Python build has none (the
    python.org builds for macOS before "Install Certificates"), the system's bundle."""
    paths = ssl.get_default_verify_paths()
    own = any(p and os.path.exists(p) for p in (paths.cafile, paths.capath, paths.openssl_cafile, os.environ.get('SSL_CERT_FILE')))
    for bundle in ('/etc/ssl/cert.pem', '/etc/ssl/certs/ca-certificates.crt'):
        if not own and os.path.exists(bundle):
            return ssl.create_default_context(cafile=bundle)
    return ssl.create_default_context()


def download(archive, folder, progress=None):
    """The archive from its url into folder, kept only when its size and digest match.
    progress(name, fraction) follows it."""
    if not archive.get('url'):
        fail('the archive is not published yet: give a local file with its digest')
    target = Path(folder)/Path(archive['url']).name
    digest, size = hashlib.sha256(), 0
    try:
        context = tls_context() if archive['url'].startswith('https:') else None
        with urllib.request.urlopen(archive['url'], timeout=60, context=context) as response, open(target, 'wb') as out:
            while chunk := response.read(1 << 20):
                size += len(chunk)
                if size > archive['size']:
                    break
                digest.update(chunk)
                out.write(chunk)
                if progress:
                    progress(target.name, size / archive['size'])
    except OSError as error:
        target.unlink(missing_ok=True)
        raise NotLocal(f'the download from {urllib.parse.urlsplit(archive["url"]).netloc} failed ({error}); '
                       'give a local file with its digest')
    if size != archive['size'] or digest.hexdigest() != archive['sha256']:
        target.unlink(missing_ok=True)
        fail(f'{archive["url"]} does not match its catalog entry')
    return target


def obtain(archive, places, folder, allow_download, progress=None):
    """A local file with the archive's digest, else its download when it is published and allowed."""
    found = find_local(archive, places)
    if found:
        return found
    if allow_download and archive.get('url'):
        return download(archive, folder, progress)
    why = 'not published yet' if not archive.get('url') else 'downloads not allowed'
    raise NotLocal(f'no local file with sha256 {archive["sha256"][:12]}… ({why}: give its file or folder)')


def diskos_release(entry, archive_path, output):
    """diskOS's ui package from its own release: its UI binary, checked by digest, and our entry."""
    source = entry['source']
    output = Path(output)
    if output.exists():
        fail(f'{output} exists; choose a fresh folder')
    with tarfile.open(archive_path, 'r:gz') as tar:
        try:
            member = tar.getmember(source['member'])
        except KeyError:
            fail(f'{source["member"]} is not in the release')
        if not member.isfile():
            fail(f'{source["member"]} is not a regular file in the release')
        data = tar.extractfile(member).read()
    if hashlib.sha256(data).hexdigest() != source['memberSha256']:
        fail(f'{source["member"]} does not match its catalog entry')
    (output/'diskos').mkdir(parents=True)
    (output/'diskos/mq_ui').write_bytes(data)
    (output/'diskos/mq_ui').chmod(0o755)
    (output/'mq_ui').write_text(DISKOS_ENTRY)
    (output/'mq_ui').chmod(0o755)
    package.describe(output, entry['name'], entry['version'], 'ui', 'mq_ui', ready=30, profiles=entry['profiles'],
                     player='diskos/mq_ui', title=entry.get('title'), homepage=entry.get('homepage'))
    return output


def fetch(entry, output, places=(), allow_download=False, progress=None, downloads=None):
    """The entry's package as a folder at output, checked as disc-boot will check it. Downloads
    are kept in downloads when given (found by their digest next time), else dropped."""
    source = entry['source']
    with tempfile.TemporaryDirectory() as temp:
        if downloads:
            Path(downloads).mkdir(parents=True, exist_ok=True)
            temp = str(downloads)
        if 'recipe' in source:
            archive = next((a for a in source['archives'] if find_local(a, places)), source['archives'][0])
            path = obtain(archive, places, temp, allow_download, progress)
            folder = diskos_release(entry, path, output)
        else:
            path = obtain(source, places, temp, allow_download, progress)
            folder = package.unpack(path, Path(output))
    m = package.check(folder, package.taken(entry))
    if package.taken(m) != package.taken(entry):
        fail(f'the package takes the role {package.taken(m)}, the catalog names {package.taken(entry)}')
    if (m['name'], m['version']) != (entry['name'], entry['version']):
        fail(f'the package is {m["name"]} {m["version"]}, the catalog names {entry["name"]} {entry["version"]}')
    return folder


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    c = sub.add_parser('check', help='Check a catalog')
    c.add_argument('--catalog', type=Path, default=CATALOG)
    sub.add_parser('list', help="This repository's packages")
    f = sub.add_parser('fetch', help='A package from the catalog as a checked folder')
    f.add_argument('--name', required=True)
    f.add_argument('--output', type=Path, required=True)
    f.add_argument('--from', dest='places', type=Path, action='append', default=[], help='A file or folder of local archives')
    f.add_argument('--download', action='store_true', help='Download a published archive when no local file matches')
    args = parser.parse_args()
    try:
        if args.command == 'check':
            data = load(args.catalog)
            out = dict(ok=True, kind=data['kind'], entries=[e['name'] for e in data['entries']])
        elif args.command == 'list':
            out = [dict(name=e['name'], role=e['role'], version=e['version'], default=e['default'],
                        published=bool(e['source'].get('url') or e['source'].get('archives')))
                   for e in load(kind='packages')['entries']]
        else:
            entries = {e['name']: e for e in load(kind='packages')['entries']}
            if args.name not in entries:
                fail(f'{args.name} is not in the catalog')
            folder = fetch(entries[args.name], args.output, args.places, args.download)
            out = dict(ok=True, name=args.name, folder=str(folder))
    except (CatalogError, package.PackageError) as error:
        print(json.dumps(dict(ok=False, error=str(error))))
        return 1
    print(json.dumps(out, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
