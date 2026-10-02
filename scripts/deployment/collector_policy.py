"""Reviewed RAM buffers and rootfs-only acquisition limits; never opens USB."""
import json
from firmware_profile import PROFILES, fingerprint, require
from deployment.metadata_policy import load_metadata_policy
from deployment.review import regular

REQUEST_BYTES, RESULT_BYTES, MAX_PAGES = 3088, 283408, 64


def load_collector_policy(base, reader, mode, directory=PROFILES):
    require(mode in ('rootfs-probe', 'rootfs'), 'Unknown collector scope')
    page = load_metadata_policy(base, reader, directory)
    path = directory/'collectors'/f'v{base["version"]}.json'
    regular(path, 8192)
    p = json.loads(path.read_text())
    require(p.get('schema_version') == 1 and p.get('version') == base['version']
            and p.get('physical_qualified') is False and p.get('policy_sha256') == fingerprint(page),
            'Collector audit mismatch')
    require(type(p.get('probe_blocks')) is int and 1 <= p['probe_blocks'] <= 2, 'Invalid probe size')
    regions = [(reader['load_address'], reader['code_bytes']),
               (reader['stack_bottom'], reader['stack_top']-reader['stack_bottom']),
               (reader['request_address'], 48), (reader['result_address'], 4428)]
    for key, size in [('request_address', REQUEST_BYTES), ('result_address', RESULT_BYTES)]:
        address = p.get(key)
        require(type(address) is int and address % 16 == 0 and
                0xa0000000 <= address < address+size <= 0xa2000000, 'Invalid uncached batch RAM')
        require(all(address+size <= a or a+n <= address for a, n in regions), 'Overlapping batch RAM')
        regions.append((address, size))
    for key, low, high in [('probe_budget_ms', 10000, 120000), ('full_budget_ms', 120000, 3600000)]:
        require(type(p.get(key)) is int and low <= p[key] <= high, 'Invalid collector time budget')
    writer, ppb = page['writer'], page['chip']['pages_per_block']
    logical = p['probe_blocks'] if mode == 'rootfs-probe' else writer['logical_blocks']
    scan = logical if mode == 'rootfs-probe' else logical+writer['bad_block_reserve']
    require(logical <= writer['logical_blocks'] and writer['start_block']+scan <= page['chip']['blocks'],
            'Collector exceeds writer/chip range')
    return dict(profile=p, mode=mode, first_page=writer['start_block']*ppb,
                end_page=(writer['start_block']+scan)*ppb, logical_blocks=logical,
                session_budget_ms=p['probe_budget_ms'] if mode == 'rootfs-probe' else p['full_budget_ms'])
