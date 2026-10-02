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
from deployment.metadata_policy import load_metadata_policy
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
        self.policy = load_metadata_policy(self.base, self.reader, self.directory)

    def save(self, folder, name, value):
        (self.directory/folder/f'{name}.json').write_text(json.dumps(value))

    def load(self):
        return load_metadata_policy(self.base, self.reader, self.directory)

    def test_generated_policy_and_identity_separation(self):
        p = self.policy
        self.assertEqual((p['expected_id'], p['id_mask']), (0x120b, 0xffff))
        self.assertEqual((p['main_bytes'], p['oob_bytes'], p['ecc_admitted']), (2048, 128, 0x1ff))
        prepare(self.reader, self.root, p)
        header = (self.root/'identity_layout.h').read_text()
        self.assertIn(f'#define METADATA_PAGE {p["page"]}', header)
        self.assertIn('#define PAGE_FEATURE_MASK 0x50', header)
        prepare(self.reader, self.root)
        self.assertNotIn('METADATA_PAGE', (self.root/'identity_layout.h').read_text())

    def test_scope_page_feature_and_audit_changes_rejected(self):
        original = self.policy['profile']
        for key, value in [('schema_version', 2), ('version', '9.99'), ('scope', 'all-pages'),
                           ('physical_qualified', True), ('page', True), ('page', original['page']+1),
                           ('feature_mask', 0x10), ('feature_value', 0),
                           ('reader_profile_sha256', '0'*64), ('kernel_profile_sha256', '0'*64),
                           ('chip_profile_sha256', '0'*64)]:
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
