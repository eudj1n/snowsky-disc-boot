"""The rootfs images a player may hold, known by their digests (plan, stage 6: the user's installation
without a history). firmware/images/v<version>.json names stock and the images before reproducible
builds; every boot release from 2.57.5 records its own (releases/<v>.json, "image"), built from FiiO's
update and the release. The identity probe's first blocks are matched by their SHA-256."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IMAGES = ROOT/'firmware/images'
RELEASES = ROOT/'releases'
HEX = set('0123456789abcdef')


class KnownImagesError(ValueError):
    pass


def digest_ok(value):
    return isinstance(value, str) and len(value) == 64 and not set(value) - HEX


def entry_ok(entry, where):
    if not isinstance(entry, dict) or not all(digest_ok(entry.get(k)) for k in ('sha256', 'first_blocks_sha256')) or \
            not isinstance(entry.get('bytes'), int) or entry['bytes'] <= 0:
        raise KnownImagesError(f'{where}: an image names its bytes, sha256 and first_blocks_sha256')


# The write's ABI a player ran (plan, stage 6): what a write without a history may use unchanged.
EXERCISED_DIGESTS = ('writer_sha256', 'spl_sha256', 'metadata_payload_sha256', 'staging_payload_sha256',
                     'firmware_profile_sha256', 'cpu_profile_sha256', 'reader_profile_sha256',
                     'transport_profile_sha256', 'page_policy_sha256', 'completion_profile_sha256',
                     'installer_contract_sha256')


def exercised_ok(entry):
    regions = entry.get('ram_regions') if isinstance(entry, dict) else None
    if not isinstance(entry, dict) or not all(digest_ok(entry.get(k)) for k in EXERCISED_DIGESTS) or \
            not all(type(entry.get(k)) is int and entry[k] > 0 for k in ('image_bytes', 'writer_entry')) or \
            not isinstance(regions, list) or not regions or not all(
                isinstance(r, dict) and set(r) == {'name', 'address', 'bytes'} and isinstance(r['name'], str)
                and type(r['address']) is int and type(r['bytes']) is int and r['bytes'] > 0 for r in regions):
        raise KnownImagesError('exercised: the write\'s ABI names its digests, image size, writer entry and RAM regions')


def load(version, images=IMAGES, releases=RELEASES):
    """{"first_blocks_bytes", "stock", "images": [{"release", "name", "bytes", "sha256", "first_blocks_sha256"}],
    "exercised"}: stock, the images listed before reproducible builds, then each recorded boot release's image,
    and the write's ABI a player ran."""
    path = Path(images)/f'v{version}.json'
    data = json.loads(path.read_text())
    if data.get('schema_version') != 1 or data.get('version') != version or data.get('first_blocks_bytes') != 262144:
        raise KnownImagesError(f'{path}: not a known-images list for {version}')
    entry_ok(data.get('stock'), 'stock')
    exercised_ok(data.get('exercised'))
    known = []
    for n, entry in enumerate(data.get('earlier', [])):
        entry_ok(entry, f'earlier[{n}]')
        known.append(dict(entry))
    for record in sorted(Path(releases).glob('*.json')):
        release = json.loads(record.read_text())
        image = release.get('image')
        if release.get('firmware') != version or not image:
            continue
        entry_ok(image, record.name)
        if image.get('first_blocks_bytes') != data['first_blocks_bytes']:
            raise KnownImagesError(f'{record.name}: its image counts other first blocks')
        known.append(dict(release=release['version'], name=image['name'], bytes=image['bytes'], sha256=image['sha256'],
                          first_blocks_sha256=image['first_blocks_sha256']))
    # One image recorded twice (a release whose programs did not change builds the same bytes) is one image;
    # two different images with the same first blocks could not be told apart by the probe.
    unique, seen = [], {}
    for entry in known:
        same = seen.get(entry['first_blocks_sha256'])
        if same is not None and same['sha256'] == entry['sha256'] and same['bytes'] == entry['bytes']:
            continue
        seen.setdefault(entry['first_blocks_sha256'], entry)
        unique.append(entry)
    known = unique
    digests = [data['stock']['first_blocks_sha256'], *(k['first_blocks_sha256'] for k in known)]
    if len(set(digests)) != len(digests):
        raise KnownImagesError('two known images share their first blocks: the probe could not tell them apart')
    return dict(first_blocks_bytes=data['first_blocks_bytes'], stock=dict(data['stock']), images=known,
                exercised={k: v for k, v in data['exercised'].items() if k != 'note'})


def first_blocks(path, size=262144):
    with open(path, 'rb') as source:
        return hashlib.sha256(source.read(size)).hexdigest()


def match(known, blocks):
    """What the probe's first blocks are: ('stock', stock entry), ('release', image entry) or (None, None)."""
    if len(blocks) != known['first_blocks_bytes']:
        raise KnownImagesError(f'the probe read {len(blocks)} bytes, the list counts {known["first_blocks_bytes"]}')
    return match_digest(known, hashlib.sha256(blocks).hexdigest())


def match_digest(known, found):
    """match() by the first blocks' SHA-256, as a review recomputes it from the probe's records."""
    if found == known['stock']['first_blocks_sha256']:
        return 'stock', known['stock']
    return next((('release', image) for image in known['images'] if image['first_blocks_sha256'] == found), (None, None))
