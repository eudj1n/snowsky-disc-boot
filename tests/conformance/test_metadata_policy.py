"""Single-page policy/build checks using public profiles only; no USB or firmware."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from firmware_profile import fingerprint, load_profile, load_reader_profile
from deployment.chip_review import load_chip
from deployment.metadata_policy import load_metadata_policy, load_chips, match_chip, pinned_policy
from deployment.build_identity import prepare


class MetadataPolicyTests(unittest.TestCase):
    def setUp(self):
        self.base = load_profile((ROOT/'firmware/active-version').read_text().strip())
        self.reader = load_reader_profile(self.base)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root/'profiles'
        shutil.copytree(ROOT/'firmware', self.directory)
        self.policy = load_metadata_policy(self.base, self.reader, self.directory, 'xt26g02c')

    def save(self, folder, name, value):
        (self.directory/folder/f'{name}.json').write_text(json.dumps(value))

    def load(self, chip='xt26g02c'):
        return load_metadata_policy(self.base, self.reader, self.directory, chip)

    def test_the_chip_is_selected_among_the_kernels(self):
        """The firmware names two chips (the owner's players, 2026-10-18): the policy is built for one, and
        where several are named the caller selects it, as the identity session names the player's."""
        chips = load_chips(self.base, self.directory)
        self.assertEqual(list(chips), ['xt26g02c', 'hyf2gq4u'])
        with self.assertRaisesRegex(ValueError, 'Several reviewed chips'):
            self.load(None)
        with self.assertRaisesRegex(ValueError, 'not reviewed for this firmware'):
            self.load('gd5f2gm7')
        other = self.load('hyf2gq4u')
        self.assertEqual((other['chip']['id'], other['expected_id'], other['id_mask']), ('hyf2gq4u', 0x52c9, 0xffff))
        # HeYangTek's two status bits: 00 and 01 admitted, 10 (not corrected) and 11 (at the maximum) refused,
        # as the stock driver does (nand-chip-review.md).
        self.assertEqual((other['ecc_mask'], other['ecc_shift'], other['ecc_admitted']), (0x30, 4, 0b11))
        self.assertEqual((other['main_bytes'], other['oob_bytes'], other['total_pages'], other['page']),
                         (2048, 128, 131072, self.policy['page']))
        self.assertNotEqual(fingerprint(other), fingerprint(self.policy))
        # The identity session's match: the two documented ID bytes, the third ignored.
        self.assertEqual(match_chip(0x00c952c9, chips), 'hyf2gq4u')
        self.assertEqual(match_chip(0x00120b, chips), 'xt26g02c')
        self.assertIsNone(match_chip(0x2c2c2c, chips))
        with self.assertRaises(ValueError):
            match_chip(0x1000000, chips)
        # A profile's pin of the policy names every chip, by this policy's fingerprint for its chip.
        pins = {'xt26g02c': fingerprint(self.policy), 'hyf2gq4u': fingerprint(other)}
        self.assertTrue(pinned_policy(pins, self.policy) and pinned_policy(pins, other))
        self.assertFalse(pinned_policy({'xt26g02c': fingerprint(self.policy)}, self.policy))
        self.assertFalse(pinned_policy(fingerprint(self.policy), self.policy))
        prepare(self.reader, self.root, other)
        header = (self.root/'identity_layout.h').read_text()
        self.assertIn('#define PAGE_EXPECTED_ID 0x52c9', header)
        self.assertIn('#define PAGE_ECC_MASK 0x30', header)
        self.assertIn('#define PAGE_ECC_ADMITTED 0x3', header)

    def test_a_chips_ecc_semantics_must_be_reviewed(self):
        chip = load_chip('hyf2gq4u', self.directory)
        for change in (dict(max_corrected=3), dict(status_mask=0x70), dict(always_on=True)):
            altered = dict(chip, ecc=dict(chip['ecc'], **change))
            self.save('chips', 'hyf2gq4u', altered)
            pages = dict(self.policy['profile'])
            pages['chip_profiles_sha256'] = dict(pages['chip_profiles_sha256'], hyf2gq4u=fingerprint(altered))
            self.save('pages', 'v'+self.base['version'], pages)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'Unreviewed ECC semantics'):
                self.load('hyf2gq4u')

    def test_the_pages_profile_pins_every_chip_the_kernel_names(self):
        pages = dict(self.policy['profile'])
        for pins in ({'xt26g02c': pages['chip_profiles_sha256']['xt26g02c']},
                     dict(pages['chip_profiles_sha256'], hyf2gq4u='0'*64), pages['chip_profiles_sha256']['xt26g02c']):
            self.save('pages', 'v'+self.base['version'], dict(pages, chip_profiles_sha256=pins))
            with self.subTest(pins=pins), self.assertRaises(ValueError):
                self.load('hyf2gq4u')

    def test_generated_policy_and_identity_separation(self):
        p = self.policy
        self.assertEqual((p['expected_id'], p['id_mask']), (0x120b, 0xffff))
        self.assertEqual((p['main_bytes'], p['oob_bytes'], p['ecc_admitted']), (2048, 128, 0x1ff))
        prepare(self.reader, self.root, p)
        header = (self.root/'identity_layout.h').read_text()
        self.assertIn(f'#define METADATA_PAGE {p["page"]}', header)
        self.assertIn('#define PAGE_FEATURE_MASK 0x50', header)
        self.assertIn('#define PAGE_ECC_MASK 0xf0', header)
        prepare(self.reader, self.root)
        self.assertNotIn('METADATA_PAGE', (self.root/'identity_layout.h').read_text())

    def test_scope_page_feature_and_audit_changes_rejected(self):
        original = self.policy['profile']
        for key, value in [('schema_version', 2), ('version', '9.99'), ('scope', 'all-pages'),
                           ('physical_qualified', True), ('page', True), ('page', original['page']+1),
                           ('feature_mask', 0x10), ('feature_value', 0),
                           ('reader_profile_sha256', '0'*64), ('kernel_profile_sha256', '0'*64),
                           ('chip_profiles_sha256', {'xt26g02c': '0'*64, 'hyf2gq4u': '0'*64})]:
            self.save('pages', 'v'+self.base['version'], dict(original, **{key: value}))
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.load()

    def test_future_firmware_and_metadata_location_require_updated_audits(self):
        self.base = dict(self.base, version='9.99')
        self.reader = dict(self.reader, version='9.99')
        kernel = copy.deepcopy(self.policy['kernel'])
        kernel['version'] = '9.99'
        kernel['metadata']['offset'] += 2048
        self.save('kernels', 'v9.99', kernel)
        policy = dict(self.policy['profile'], version='9.99', page=self.policy['page']+1,
                      reader_profile_sha256=fingerprint(self.reader), kernel_profile_sha256=fingerprint(kernel))
        self.save('pages', 'v9.99', policy)
        result = self.load()
        self.assertEqual(result['page'], self.policy['page']+1)
        self.assertEqual(result['profile']['version'], '9.99')
        self.reader['id_address_bytes'] = 0
        policy['reader_profile_sha256'] = fingerprint(self.reader)
        self.save('pages', 'v9.99', policy)
        with self.assertRaisesRegex(ValueError, 'identity framing'):
            self.load()

    def test_missing_and_oversized_policy_rejected(self):
        path = self.directory/'pages'/f'v{self.base["version"]}.json'
        path.write_bytes(b' '*8193)
        with self.assertRaises(ValueError): self.load()
        path.unlink()
        with self.assertRaises(FileNotFoundError): self.load()


if __name__ == '__main__':
    unittest.main()
