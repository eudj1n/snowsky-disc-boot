"""Reviewed single-page experiment policy; independent of physical qualification.

A firmware version names the chips its kernel supports (firmware/kernels/<v>.json, chips); a
player has one of them, told by its Read ID in an identity session (ram_transport.py --mode
identity). The policy is built for one chip: the payload's constants, the ECC semantics, the
geometry. Where several chips are reviewed, the caller selects one (--chip, or the build's).
"""
import json
from firmware_profile import PROFILES, fingerprint, load_writer, require
from deployment.chip_review import load_chip
from deployment.kernel_review import load_kernel_profile
from deployment.review import regular

# The ECC status semantics reviewed, exactly as the chip profiles document them (the policy
# admits status values 0..max_corrected; the C0h field is (status & status_mask) >> status_shift).
# XTX XT26G02C: the high nibble, 0 clean, 1-8 corrected, F uncorrectable (datasheet Rev 1.8, Table 8).
XTX_ECC = dict(always_on=True, status_mask=240, status_shift=4, max_corrected=8, uncorrectable=15)
# HeYangTek HYF2GQ4U: two bits ECCS1/ECCS0, 00 clean, 01 corrected, 10 not corrected, 11 corrected
# at the ECC maximum, which the stock driver refuses as it refuses 10 (nand-chip-review.md).
HYF_ECC = dict(always_on=False, status_mask=48, status_shift=4, max_corrected=1, uncorrectable=2,
               values={'0': 'no bit errors detected', '1': 'bit errors detected and corrected',
                       '2': 'bit errors detected and not corrected',
                       '3': 'corrected, at the ECC maximum: the stock driver refuses the page (-EBADMSG), '
                            'and so does this reader'})
REVIEWED_ECC = (XTX_ECC, HYF_ECC)


def kernel_chips(kernel):
    """The chips the kernel profile names, in its order."""
    return list(kernel['chips'])


def select_chip(kernel, chip=None):
    """The chip a policy is built for: the one selected, which the kernel must name, else the only one."""
    names = kernel_chips(kernel)
    if chip is None:
        require(len(names) == 1, 'Several reviewed chips for this firmware (' + ', '.join(names) +
                '): select the player\'s with --chip, as its identity session names it')
        return names[0]
    require(chip in names, f'Chip {chip!r} is not reviewed for this firmware (' + ', '.join(names) + ')')
    return chip


def load_chips(base, directory=PROFILES):
    """Every chip profile the firmware's kernel names, by id (the identity session's match)."""
    writer = load_writer(base['writer'], directory)
    kernel = load_kernel_profile(base, writer, directory)
    return {name: load_chip(name, directory) for name in kernel_chips(kernel)}


def match_chip(observed_id, chips):
    """The chip whose documented two ID bytes an identity observation shows, else None (chips: by id)."""
    require(type(observed_id) is int and 0 <= observed_id <= 0xffffff, 'Identity exceeds three wire bytes')
    wire = observed_id.to_bytes(3, 'little')[:2].hex()
    found = [name for name, chip in chips.items() if chip['id_prefix_hex'] == wire]
    require(len(found) <= 1, 'Two reviewed chips share an ID')
    return found[0] if found else None


def pinned_policy(pins, policy):
    """Whether a profile's pin of the page policy, one fingerprint a chip, names every chip the
    kernel does and this policy's chip by this policy."""
    return (isinstance(pins, dict) and set(pins) == set(kernel_chips(policy['kernel']))
            and pins.get(policy['chip']['id']) == fingerprint(policy))


def load_metadata_policy(base, reader, directory=PROFILES, chip=None):
    path = directory/'pages'/f'v{base["version"]}.json'
    regular(path, 8192)
    p = json.loads(path.read_text())
    writer = load_writer(base['writer'], directory)
    kernel = load_kernel_profile(base, writer, directory)
    name = select_chip(kernel, chip)
    chip = load_chip(name, directory)
    require(p.get('schema_version') == 1 and p.get('version') == base['version'] and
            p.get('scope') == 'single-metadata-page' and p.get('physical_qualified') is False,
            'Invalid metadata experiment scope')
    for key, selected in [('reader', reader), ('kernel', kernel)]:
        require(p.get(key+'_profile_sha256') == fingerprint(selected), f'Metadata {key} audit changed')
    pins = p.get('chip_profiles_sha256')
    require(isinstance(pins, dict) and set(pins) == set(kernel_chips(kernel)), 'Metadata chip set differs from the kernel\'s')
    require(pins[name] == fingerprint(chip), 'Metadata chip audit changed')
    require(chip['id_prefix_hex'] and len(chip['id_prefix_hex']) == 4 and reader['id_address_bytes'] == 1,
            'Unsupported identity framing')
    require(type(p.get('page')) is int and p['page'] == kernel['metadata']['offset']//chip['page_bytes']
            and 0 <= p['page'] < chip['blocks']*chip['pages_per_block'], 'Metadata page mismatch')
    require(p.get('feature_mask') == 0x50 and p.get('feature_value') == 0x10,
            'Require ECC enabled and OTP access disabled')
    ecc = chip['ecc']
    require(ecc in REVIEWED_ECC, 'Unreviewed ECC semantics; add a reviewed experiment policy')
    require(chip['page_bytes'] == 2048 and chip['oob_bytes'] == 128 and
            chip['blocks']*chip['pages_per_block'] <= 0x1000000, 'Unsupported metadata geometry')
    return dict(profile=p, kernel=kernel, chip=chip, writer=writer,
                page=p['page'], expected_id=int.from_bytes(bytes.fromhex(chip['id_prefix_hex']), 'little'),
                id_mask=0xffff, main_bytes=chip['page_bytes'], oob_bytes=chip['oob_bytes'],
                total_pages=chip['blocks']*chip['pages_per_block'], feature_mask=p['feature_mask'],
                feature_value=p['feature_value'], ecc_mask=ecc['status_mask'],
                ecc_shift=ecc['status_shift'], ecc_admitted=(1 << (ecc['max_corrected']+1))-1)
