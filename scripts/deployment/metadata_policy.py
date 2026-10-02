"""Reviewed single-page experiment policy; independent of physical qualification."""
import json
from firmware_profile import PROFILES, fingerprint, load_writer, require
from deployment.chip_review import load_chip
from deployment.kernel_review import load_kernel_profile
from deployment.review import regular


def load_metadata_policy(base, reader, directory=PROFILES):
    path = directory/'pages'/f'v{base["version"]}.json'
    regular(path, 8192)
    p = json.loads(path.read_text())
    writer = load_writer(base['writer'], directory)
    kernel = load_kernel_profile(base, writer, directory)
    chip = load_chip(kernel['chip'], directory)
    require(p.get('schema_version') == 1 and p.get('version') == base['version'] and
            p.get('scope') == 'single-metadata-page' and p.get('physical_qualified') is False,
            'Invalid metadata experiment scope')
    for key, selected in [('reader', reader), ('kernel', kernel), ('chip', chip)]:
        require(p.get(key+'_profile_sha256') == fingerprint(selected), f'Metadata {key} audit changed')
    require(chip['id_prefix_hex'] and len(chip['id_prefix_hex']) == 4 and reader['id_address_bytes'] == 1,
            'Unsupported identity framing')
    require(type(p.get('page')) is int and p['page'] == kernel['metadata']['offset']//chip['page_bytes']
            and 0 <= p['page'] < chip['blocks']*chip['pages_per_block'], 'Metadata page mismatch')
    require(p.get('feature_mask') == 0x50 and p.get('feature_value') == 0x10,
            'Require ECC enabled and OTP access disabled')
    ecc = chip['ecc']
    require(ecc == dict(always_on=True, status_mask=240, status_shift=4, max_corrected=8, uncorrectable=15),
            'Unreviewed ECC semantics; add a reviewed experiment policy')
    require(chip['page_bytes'] == 2048 and chip['oob_bytes'] == 128 and
            chip['blocks']*chip['pages_per_block'] <= 0x1000000, 'Unsupported metadata geometry')
    return dict(profile=p, kernel=kernel, chip=chip, writer=writer,
                page=p['page'], expected_id=int.from_bytes(bytes.fromhex(chip['id_prefix_hex']), 'little'),
                id_mask=0xffff, main_bytes=chip['page_bytes'], oob_bytes=chip['oob_bytes'],
                total_pages=chip['blocks']*chip['pages_per_block'], feature_mask=p['feature_mask'],
                feature_value=p['feature_value'], ecc_mask=ecc['status_mask'],
                ecc_shift=ecc['status_shift'], ecc_admitted=(1 << (ecc['max_corrected']+1))-1)
