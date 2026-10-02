"""Explicit reviewed firmware selection, independent of external repos or firmware."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re

PROFILES = Path(__file__).resolve().parents[1]/'firmware'
SCENARIOS = {'smoke', 'coexistence', 'handover', 'lifecycle', 'transitions', 'offline', 'idle', 'soak'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def positive(value):
    return type(value) is int and value > 0


def sha(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) is not None


def pins(value):
    require(isinstance(value, dict) and value, 'Missing file fingerprints')
    for name, digest in value.items():
        path = PurePosixPath(name)
        require(not path.is_absolute() and '..' not in path.parts and name == path.as_posix()
                and name != '.' and sha(digest), 'Invalid pinned path/hash')


def load_profile(version=None, directory=PROFILES):
    version = version or os.environ.get('FW_VERSION') or (directory/'active-version').read_text().strip()
    require(isinstance(version, str) and re.fullmatch(r'\d+\.\d{2}', version), 'Invalid firmware selector')
    path = directory/f'v{version}.json'
    require(path.is_file(), 'Unknown firmware; add a reviewed profile first')
    p = json.loads(path.read_text())
    require(p.get('schema_version') == 1 and p.get('version') == version, 'Invalid profile identity/schema')
    for key in ('main_os_version', 'recovery_os_version', 'rootfs_chunks', 'rootfs_size'):
        require(positive(p.get(key)), f'Invalid profile {key}')
    require(p['main_os_version'] == int(version.replace('.', '')), 'Version/main OS mismatch')
    require(isinstance(p.get('product'), str) and p['product'], 'Missing product')
    require(sha(p.get('rootfs_sha256')), 'Invalid rootfs fingerprint')
    pins(p.get('stock_files'))
    require(isinstance(p.get('writer'), str) and re.fullmatch('[a-z0-9-]+', p['writer']), 'Invalid writer selector')
    require(isinstance(p.get('protocol_identity'), str) and re.fullmatch('[0-9a-f]{4}', p['protocol_identity']), 'Invalid protocol identity')
    require(isinstance(p.get('reference_revisions'), list) and p['reference_revisions']
            and all(isinstance(x, str) and re.fullmatch('[0-9a-f]{40}', x) for x in p['reference_revisions']), 'Invalid reference revisions')
    require(isinstance(p.get('acceptance'), list) and all(isinstance(x, str) and x in SCENARIOS for x in p['acceptance']), 'Invalid acceptance scenarios')
    require(type(p.get('hardware_qualified')) is bool, 'Missing physical qualification status')
    return p


def fingerprint(profile):
    return hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def state_profile(state, directory=PROFILES):
    require('firmwareVersion' in state and 'firmwareProfileSha256' in state,
            'Legacy stack lacks an explicit profile pin; validate/migrate its state or recreate it')
    p = load_profile(state['firmwareVersion'], directory)
    require(fingerprint(p) == state['firmwareProfileSha256'], 'Recorded stack profile changed; recreate the stack')
    return p


def load_writer(name, directory=PROFILES):
    require(isinstance(name, str) and re.fullmatch('[a-z0-9-]+', name), 'Invalid writer selector')
    p = json.loads((directory/'writers'/f'{name}.json').read_text())
    require(p.get('schema_version') == 1 and p.get('id') == name
            and p.get('format') == 'diskos-my-write5-v1', 'Unsupported writer format')
    for key in ('block_bytes', 'logical_blocks', 'bad_block_reserve', 'max_tries', 'capacity_instruction_offset',
                'capacity_instruction_mask', 'capacity_instruction_value'):
        require(positive(p.get(key)), f'Invalid writer {key}')
    require(type(p.get('start_block')) is int and p['start_block'] >= 0, 'Invalid writer start')
    require(p['bad_block_reserve'] <= 64 and p['logical_blocks'] <= 65535,
            'Writer fields exceed supported record/instruction format')
    require(p['block_bytes'] == 64*2048, 'Writer format requires 64 pages of 2048 bytes')
    pins(p.get('source_pins'))
    require(p.get('writer_file') in p['source_pins'], 'Writer binary must be pinned')
    return p


def require_scenario(profile, name):
    require(name in profile['acceptance'], f'Unreviewed scenario {name!r} for {profile["version"]}')


def artifact_names(profile, variant='companion'):
    """Image names per variant: the loopback companion, the USB engineering image,
    and (combined-008) the product image without engineering parts."""
    require(variant in ('companion', 'usb-engineering', 'product'), 'Unknown image variant')
    suffix = profile['version'].replace('.', '')
    mode = {'usb-engineering': '-usb-engineering', 'product': '-product'}.get(variant, '')
    return f'disc-web-v{suffix}{mode}-review-only.bin', f'stock-v{suffix}-restore-review-only.bin'


def load_usb_profile(profile, directory=PROFILES):
    p = json.loads((directory/'usb'/f'v{profile["version"]}.json').read_text())
    require(p.get('schema_version') == 1 and p.get('version') == profile['version']
            and p.get('rootfs_sha256') == profile['rootfs_sha256'], 'USB profile does not match firmware')
    for key in ('sd_mount', 'sd_source'):
        value = p.get(key)
        require(isinstance(value, str) and re.fullmatch('/[a-zA-Z0-9_/-]+', value)
                and '..' not in value and len(value) < 200, f'Invalid USB {key}')
    require(isinstance(p.get('udc'), str) and re.fullmatch('[a-zA-Z0-9_.-]+', p['udc'])
            and len(p['udc']) < 200, 'Invalid UDC')
    for key, maximum in (('startup_seconds', 120), ('session_seconds', 900)):
        require(positive(p.get(key)) and p[key] <= maximum, f'Invalid USB {key}')
    report = p.get('boot_report')
    require(isinstance(report, dict) and set(report) == {'delay_seconds', 'wait_seconds', 'max_bytes'},
            'Missing boot report policy')
    require(positive(report['delay_seconds']) and positive(report['wait_seconds'])
            and p['startup_seconds'] < report['delay_seconds'] < report['wait_seconds'] <= 180,
            'Invalid boot report timing')
    require(positive(report['max_bytes']) and 8192 <= report['max_bytes'] <= 16384,
            'Invalid boot report size')
    pins(p.get('stock_files'))
    require(p.get('physical_qualified') is False, 'Only unqualified engineering USB profiles are supported')
    return p


def load_os_profile(profile, directory=PROFILES):
    """OS-level sources observed on the player for this firmware (sysfs, procfs)."""
    p = json.loads((directory/'os'/f'v{profile["version"]}.json').read_text())
    require(p.get('schema_version') == 1 and p.get('version') == profile['version']
            and p.get('rootfs_sha256') == profile['rootfs_sha256'], 'OS profile does not match firmware')
    battery = p.get('battery')
    require(isinstance(battery, dict) and set(battery) == {'path', 'type', 'attributes', 'absent'},
            'Invalid battery source')
    require(isinstance(battery['path'], str)
            and re.fullmatch('/sys/class/power_supply/[A-Za-z0-9_-]{1,32}', battery['path']),
            'Invalid battery path')
    require(isinstance(battery['type'], str) and re.fullmatch('[A-Za-z]{1,16}', battery['type']),
            'Invalid battery type')
    names = []
    for key in ('attributes', 'absent'):
        value = battery[key]
        require(isinstance(value, list) and len(value) <= 32
                and all(isinstance(x, str) and re.fullmatch('[a-z][a-z_]{0,31}', x) for x in value)
                and len(set(value)) == len(value), f'Invalid battery {key}')
        names += value
    require(len(set(names)) == len(names), 'Battery attribute both present and absent')
    require({'capacity', 'type'} <= set(battery['attributes']), 'Battery lacks capacity/type')
    asound = p.get('asound')
    require(isinstance(asound, dict) and set(asound) == {'card'} and isinstance(asound['card'], str)
            and re.fullmatch('/proc/asound/card[0-9]{1,2}', asound['card']), 'Invalid ALSA card source')
    player = p.get('player')
    require(isinstance(player, dict) and set(player) == {'process'} and isinstance(player['process'], str)
            and re.fullmatch('[a-z_][a-z0-9_.-]{0,14}', player['process']), 'Invalid player process')
    mdns = p.get('mdns', {'name': 'x'})
    require(isinstance(mdns, dict) and set(mdns) == {'name'} and isinstance(mdns['name'], str)
            and re.fullmatch('[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', mdns['name']), 'Invalid mDNS name')
    return p


def os_service_args(os_profile):
    """Arguments that point the companion at the profile's read-only device sources."""
    args = ['--battery-dir', os_profile['battery']['path'], '--asound-dir', os_profile['asound']['card'],
            '--player-process', os_profile['player']['process']]
    # The player's own mDNS name, the only .local name the service admits (combined-008).
    if 'mdns' in os_profile:
        args += ['--mdns-name', os_profile['mdns']['name']]
    return args


# Everything the service keeps on the music card lives in one hidden folder
# (combined-008): stock's folder browsers do not list it, and the service never
# uploads or creates anything there for a client.
DATA_DIR = '.disc'


def data_dir(sd_mount):
    """The service's folder on the music card."""
    return f'{sd_mount}/{DATA_DIR}'


def webroot(sd_mount):
    """Combined-008's page releases on the card (active.json and releases/); combined-009 moves the page to Apps."""
    return f'{data_dir(sd_mount)}/www'


def apps(sd_mount):
    """The apps on the card (combined-009): Apps/<App>/, Disc Player served at /."""
    return f'{sd_mount}/Apps'


def card_catalog(sd_mount):
    """The card's override of the reviewed catalogs (combined-009)."""
    return f'{data_dir(sd_mount)}/catalog'


def raw_switch(sd_mount):
    """The engineering raw-records switch (exact content), absent by default."""
    return f'{data_dir(sd_mount)}/dev/raw-records'


def database(sd_mount):
    """The service's own database on the music card (play history, collections)."""
    return f'{data_dir(sd_mount)}/disc.db'


def disable_switch(sd_mount):
    """A file or folder here stops the service while the card is in the player."""
    return f'{data_dir(sd_mount)}/disabled'


def trash(sd_mount):
    """Where files and folders moved to the trash wait on the music card."""
    return f'{data_dir(sd_mount)}/trash'


def internal_lists(sd_mount):
    """The service's own M3U lists, hidden on the music card (combined-009); stock plays them by path."""
    return f'{data_dir(sd_mount)}/playlists'


def external_lists(sd_mount):
    """M3U lists the player's own file browser shows and plays (combined-009, the owner's choice)."""
    return f'{sd_mount}/Playlists'


def load_probe_profile(profile, directory=PROFILES):
    p = json.loads((directory/'probes'/f'v{profile["version"]}.json').read_text())
    require(p.get('schema_version') == 1 and p.get('version') == profile['version']
            and p.get('rootfs_sha256') == profile['rootfs_sha256'], 'Probe profile does not match firmware')
    require(p.get('protocol') == 'ingenic-rom-cpu-info-v1', 'Unsupported probe protocol')
    for key in ('vid', 'pid'):
        require(positive(p.get(key)) and p[key] <= 65535, f'Invalid probe {key}')
    require(positive(p.get('timeout_ms')) and p['timeout_ms'] <= 5000, 'Invalid probe timeout')
    replies = p.get('accepted_reply_hex')
    require(isinstance(replies, list) and 1 <= len(replies) <= 8
            and all(isinstance(x, str) and re.fullmatch(r'(?:[0-9a-f]{2}){1,8}', x) for x in replies)
            and len(set(replies)) == len(replies), 'Invalid exact CPU reply signatures')
    require(sha(p.get('reference_source_sha256')), 'Missing probe protocol provenance')
    return p


def load_reader_profile(profile, directory=PROFILES):
    p = json.loads((directory/'readers'/f'v{profile["version"]}.json').read_text())
    require(p.get('schema_version') == 1 and p.get('version') == profile['version']
            and p.get('rootfs_sha256') == profile['rootfs_sha256'], 'Reader profile does not match firmware')
    require(p.get('protocol') == 'disc-x2000-identity-v1'
            and p.get('physical_qualified') is False, 'Unsupported reader protocol/qualification')
    for key in ('load_address', 'stack_bottom', 'stack_top', 'request_address', 'result_address'):
        require(type(p.get(key)) is int and 0xa0800000 <= p[key] < 0xa2000000
                and p[key] % 16 == 0, f'Invalid uncached RAM address: {key}')
    require(positive(p.get('code_bytes')) and p['code_bytes'] <= 32768
            and p['code_bytes'] % 16 == 0, 'Invalid code budget')
    require(32768 <= p['stack_top'] - p['stack_bottom'] <= 65536, 'Invalid stack budget')
    ranges = sorted([(p['load_address'], p['load_address'] + p['code_bytes']),
                     (p['stack_bottom'], p['stack_top']),
                     (p['request_address'], p['request_address'] + 48),
                     (p['result_address'], p['result_address'] + 4428)])
    require(all(end <= 0xa2000000 for _, end in ranges)
            and all(a[1] <= b[0] for a, b in zip(ranges, ranges[1:])), 'Overlapping/out-of-range RAM regions')
    for key, maximum in [('sfc_polls', 800000), ('nand_polls', 10000),
                         ('extal_mhz', 50), ('target_mhz', 50)]:
        require(positive(p.get(key)) and p[key] <= maximum, f'Invalid reader {key}')
    require(type(p.get('id_address_bytes')) is int and p['id_address_bytes'] in (0, 1),
            'Invalid ID address phase')
    pins(p.get('source_pins'))
    require(set(p['source_pins']) == {'flash/my_write5.c', 'flash/disc_spl_lpddr3.bin',
                                    'spl-src/uboot-xburst-lpddr3-src.tar.gz'}, 'Missing reader provenance')
    return p
