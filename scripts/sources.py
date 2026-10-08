"""The outside files the installer reads (plan, stage 6), each at the revision its
firmware/sources/<name>.json names: diskOS's writer, its SPL and the sources they are checked
against (their digests the reviewed profiles' own pins: reader, writer, probe), and the emulator's
reader of FiiO's update (its digest in its source file). A copy is taken from anywhere it is found
with those digests (a checkout, the installer's cache) and is otherwise fetched file by file from
that revision and checked; nothing of it is kept in this repository (owner, 2026-10-08)."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import urllib.parse
import urllib.request

import catalog
from firmware_profile import load_probe_profile, load_profile, load_reader_profile, load_writer

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT/'firmware/sources'
RAW = 'https://raw.githubusercontent.com/{owner}/{repo}/{revision}/{path}'
MAX_FILE = 64 << 20


class SourceError(Exception):
    pass


def load(name='diskos', directory=SOURCES):
    data = json.loads((Path(directory)/f'{name}.json').read_text())
    if data.get('schema') != 1 or not data.get('repository', '').startswith('https://github.com/') or \
            len(data.get('revision', '')) != 40:
        raise SourceError(f'{name}: a source names its GitHub repository and a full revision')
    return data


def diskos_files(version):
    """{path: sha256} of every diskOS file the reviewed profiles of the firmware pin."""
    base = load_profile(version)
    reader, probe, writer = load_reader_profile(base), load_probe_profile(base), load_writer(base['writer'])
    files = {}
    for path, digest in [*reader['source_pins'].items(), *writer['source_pins'].items(),
                         ('src/usbboot/usbboot.c', probe['reference_source_sha256'])]:
        if files.setdefault(path, digest) != digest:
            raise SourceError(f'the profiles pin {path} twice, differently')
    return files


def files_of(name, version):
    """{path: sha256} of a source's files: diskOS's by the profiles' pins, the others by their source file."""
    if name == 'diskos':
        return diskos_files(version)
    files = load(name).get('files')
    if not isinstance(files, dict) or not files:
        raise SourceError(f'{name}: its source file names no files')
    return dict(files)


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def differs(folder, files):
    """The files of folder that are missing or have another digest."""
    folder = Path(folder)
    return [p for p, d in sorted(files.items()) if not (folder/p).is_file() or (folder/p).is_symlink() or digest(folder/p) != d]


def fetch(version, places=(), cache=ROOT/'work/downloads', allow_download=True, opener=None, progress=None, name='diskos'):
    """A folder holding diskOS's pinned files: the first of places (and the cache) that holds them all,
    else a fresh copy fetched from the source's revision into the cache, every file checked."""
    source, files = load(name), files_of(name, version)
    cached = Path(cache)/f'{name}-{source["revision"][:7]}'
    for place in [*map(Path, places), cached]:
        if place.is_dir() and not differs(place, files):
            return place
    if not allow_download:
        raise SourceError(f'no copy of {name} {source["revision"][:7]} with the pinned files '
                          f'({", ".join(differs(cached, files)) or "none"}): give a copy'
                          + (' with --diskos' if name == 'diskos' else '') + ', or allow downloads')
    owner, repo = urllib.parse.urlsplit(source['repository']).path.strip('/').split('/')[:2]
    opener = opener or (lambda url: urllib.request.urlopen(url, timeout=60, context=catalog.tls_context()))
    Path(cache).mkdir(parents=True, exist_ok=True)
    fresh = Path(tempfile.mkdtemp(prefix=f'{name}-', dir=cache))
    try:
        for n, (path, expected) in enumerate(sorted(files.items())):
            url = RAW.format(owner=owner, repo=repo, revision=source['revision'], path=path)
            target = fresh/path
            target.parent.mkdir(parents=True, exist_ok=True)
            size = 0
            try:
                with opener(url) as response, open(target, 'wb') as out:
                    while chunk := response.read(1 << 20):
                        size += len(chunk)
                        if size > MAX_FILE:
                            raise SourceError(f'{path} is larger than any pinned file may be')
                        out.write(chunk)
            except OSError as error:
                raise SourceError(f'{path} could not be fetched from {urllib.parse.urlsplit(url).netloc} ({error}): '
                                  f'give a copy of {name} at {source["revision"][:7]}') from None
            if digest(target) != expected:
                raise SourceError(f'{path} at {source["revision"][:7]} does not match its pin')
            if progress:
                progress(path, (n + 1) / len(files))
        if cached.exists():
            shutil.rmtree(cached)
        fresh.rename(cached)
        return cached
    finally:
        if fresh.exists():
            shutil.rmtree(fresh)
