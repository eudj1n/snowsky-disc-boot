"""What the installer puts on the card (contract, "Installation for users"): the chosen packages
for the recovery with Play, the default apps of the chosen server in Apps/, and the console's
marker (owner, 2026-10-02/03: written and left in place)."""
import json
import os
from pathlib import Path
import shutil
import stat
import zipfile

import catalog
import package

MARKER = Path('.disc/dev/usb-console')
MARKER_TEXT = 'DISC_WEB_LOCAL_ROOT_CONSOLE\n'
APP_BYTES = 40 * 1024 * 1024


class CardError(ValueError):
    pass


def card_ok(path):
    """A mounted card: a folder of its own, never the computer's root or home."""
    path = Path(path)
    if not path.is_dir() or path.is_symlink():
        raise CardError(f'{path} is not a mounted card')
    resolved = path.resolve()
    if resolved in (Path('/'), Path.home().resolve()) or resolved in Path.home().resolve().parents:
        raise CardError(f'{path} is not a card')
    if os.environ.get('DISC_INSTALL_ANY_CARD') != '1' and Path('/Volumes') in resolved.parents and resolved.parent != Path('/Volumes'):
        raise CardError(f'{path} is inside a volume, not the card itself')
    return resolved


def stage_packages(folders, card, profile):
    """Each chosen package where the recovery with Play finds it."""
    return [package.stage(folder, card, profile=profile) for folder in folders]


def app_entries(server_folder):
    """The apps the chosen server offers (its package's catalog/apps.json)."""
    path = Path(server_folder)/'catalog/apps.json'
    return catalog.load(path, kind='apps')['entries'] if path.exists() else []


def unpack_app(archive, entry, apps):
    """An app's release zip into Apps/<its folder>: one top folder named as the app, regular files
    at safe paths, app.json naming the catalog's app and version; written beside, then swapped in."""
    with zipfile.ZipFile(archive) as z:
        names = [i for i in z.infolist() if not i.is_dir()]
        tops = {i.filename.split('/', 1)[0] for i in names}
        if tops != {entry['name']}:
            raise CardError(f'the app zip holds {sorted(tops)}, not one folder named {entry["name"]!r}')
        if sum(i.file_size for i in names) > APP_BYTES:
            raise CardError('the app is larger than an app may be')
        for info in names:
            rest = info.filename.split('/', 1)[1] if '/' in info.filename else ''
            kind = stat.S_IFMT(info.external_attr >> 16)
            if not rest or kind not in (0, stat.S_IFREG) or not package.path_ok(rest.replace(' ', '_')):
                raise CardError(f'{info.filename} is not a safe file of the app')
        app = json.loads(z.read(f'{entry["name"]}/app.json'))
        if (app.get('name'), app.get('version')) != (entry['name'], entry['version']):
            raise CardError(f'the zip is {app.get("name")} {app.get("version")}, the catalog names {entry["name"]} {entry["version"]}')
        apps.mkdir(parents=True, exist_ok=True)
        fresh, target = apps/f'.{entry["name"]}.staging', apps/entry['name']
        shutil.rmtree(fresh, ignore_errors=True)
        for info in names:
            out = fresh/info.filename.split('/', 1)[1]
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(z.read(info))
        if target.exists():
            shutil.rmtree(target)
        os.replace(fresh, target)
    return target


def stage_apps(entries, card, places, allow_download, workdir):
    staged = []
    for entry in entries:
        archive = catalog.obtain(entry['source'], places, workdir, allow_download)
        staged.append(dict(name=entry['name'], version=entry['version'], path=str(unpack_app(archive, entry, Path(card)/'Apps'))))
    return staged


def write_marker(card):
    path = Path(card)/MARKER
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(MARKER_TEXT)
    return path
