"""Version promotion and selection without firmware, Docker or external repos."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
import firmware_profile as profiles


class FirmwareProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.base = profiles.load_profile((ROOT/'firmware/active-version').read_text().strip())
        self.write(self.base)
        (self.directory/'active-version').write_text(self.base['version'])
        self.env = patch.dict('os.environ', {'FW_VERSION':''})
        self.env.start()
        self.addCleanup(self.env.stop)

    def write(self, profile):
        (self.directory/f'v{profile["version"]}.json').write_text(json.dumps(profile))

    def future(self):
        p = copy.deepcopy(self.base)
        p.update(version='9.99', main_os_version=999, rootfs_sha256='a'*64,
                 rootfs_chunks=91, rootfs_size=91234567)
        self.write(p)
        return p

    def test_default_environment_and_explicit_selection(self):
        future = self.future()
        self.assertEqual(profiles.load_profile(directory=self.directory), self.base)
        with patch.dict('os.environ', {'FW_VERSION':future['version']}):
            self.assertEqual(profiles.load_profile(directory=self.directory), future)
            self.assertEqual(profiles.load_profile(self.base['version'], self.directory), self.base)

    def test_promote_new_version_without_script_edits(self):
        future = self.future()
        (self.directory/'active-version').write_text(future['version'])
        selected = profiles.load_profile(directory=self.directory)
        self.assertEqual(selected['main_os_version'], 999)
        self.assertEqual(profiles.artifact_names(selected)[0], 'disc-web-v999-review-only.bin')

    def test_unknown_malformed_and_inventory_only_versions_refused(self):
        (self.directory/'inventory').mkdir()
        (self.directory/'inventory/v8.88.json').write_text('{}')
        for version in ('../2.57', '8.88', '2.5', 'v2.57'):
            with self.subTest(version=version), self.assertRaises(ValueError):
                profiles.load_profile(version, self.directory)

    def test_missing_identity_fingerprint_and_invalid_scenarios_refused(self):
        for key, value in [('rootfs_sha256','bad'), ('rootfs_chunks', True),
                           ('main_os_version', 1), ('stock_files', {'../escape':'a'*64}),
                           ('acceptance',['unimplemented']), ('writer','../writer')]:
            with self.subTest(key=key):
                changed = {**self.base, key:value}
                self.write(changed)
                with self.assertRaises(ValueError):
                    profiles.load_profile(self.base['version'], self.directory)

    def test_recorded_stack_ignores_new_default_and_refuses_changed_profile(self):
        state = {'firmwareVersion':self.base['version'], 'firmwareProfileSha256':profiles.fingerprint(self.base)}
        future = self.future()
        (self.directory/'active-version').write_text(future['version'])
        self.assertEqual(profiles.state_profile(state, self.directory), self.base)
        changed = {**self.base, 'rootfs_sha256':'b'*64}
        self.write(changed)
        with self.assertRaises(ValueError):profiles.state_profile(state, self.directory)
        with self.assertRaises(ValueError):profiles.state_profile({}, self.directory)

    def test_unreviewed_scenario_not_inherited(self):
        future = self.future()
        future['acceptance'] = ['smoke']
        profiles.require_scenario(future, 'smoke')
        with self.assertRaises(ValueError):profiles.require_scenario(future, 'idle')

    def test_writer_profile_is_independently_configurable(self):
        spec = importlib.util.spec_from_file_location('review_config', ROOT/'scripts/deployment/review.py')
        review = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(review)
        writer = profiles.load_writer(self.base['writer'])
        writer['logical_blocks'] = 512
        import struct
        data = bytearray(writer['capacity_instruction_offset']+4)
        struct.pack_into('<I', data, writer['capacity_instruction_offset'], writer['capacity_instruction_value']|512)
        self.assertEqual(review.writer_capacity(data, writer), 512)

    def test_guest_selection_does_not_shadow_reference_imports(self):
        spec = importlib.util.spec_from_file_location('selected_test', ROOT/'tests/integration/selected_firmware.py')
        selected = importlib.util.module_from_spec(spec)
        before = list(sys.path)
        with patch.dict('os.environ', {'CI_DISPOSABLE':'1', 'FW_VERSION':self.base['version']}):
            spec.loader.exec_module(selected)
        self.assertEqual(sys.path, before)
        self.assertEqual(selected.MAIN_OS, self.base['main_os_version'])

    def test_usb_profile_is_explicit_firmware_pinned_and_bounded(self):
        usb=profiles.load_usb_profile(self.base)
        directory=self.directory/'usb';directory.mkdir()
        target=directory/f'v{self.base["version"]}.json'
        for key,value in [('rootfs_sha256','a'*64),('session_seconds',901),
                          ('sd_mount','/tmp/sd;exec'),('udc','../../device')]:
            with self.subTest(key=key):
                target.write_text(json.dumps({**usb,key:value}))
                with self.assertRaises(ValueError):profiles.load_usb_profile(self.base,self.directory)
        target.unlink()
        with self.assertRaises(FileNotFoundError):profiles.load_usb_profile(self.base,self.directory)
        self.assertIn('usb-engineering',profiles.artifact_names(self.base,'usb-engineering')[0])
        self.assertNotIn('usb-engineering',profiles.artifact_names(self.base)[0])
        self.assertEqual(profiles.artifact_names(self.base,'product')[0], profiles.artifact_names(self.base)[0].replace('-review-only', '-product-review-only'))
        with self.assertRaises(ValueError): profiles.artifact_names(self.base, 'debug')

    def test_boot_report_policy_is_bounded_and_after_usb_deadline(self):
        usb=profiles.load_usb_profile(self.base)
        directory=self.directory/'usb';directory.mkdir()
        target=directory/f'v{self.base["version"]}.json'
        for key,value in [('delay_seconds',30),('delay_seconds',True),
                          ('wait_seconds',181),('wait_seconds',45),
                          ('max_bytes',16385),('max_bytes',False)]:
            with self.subTest(key=key,value=value):
                policy={**usb['boot_report'],key:value}
                target.write_text(json.dumps({**usb,'boot_report':policy}))
                with self.assertRaises(ValueError):profiles.load_usb_profile(self.base,self.directory)



    def test_selected_controller_name_reaches_both_generated_scripts(self):
        spec=importlib.util.spec_from_file_location('usb_profile_builder',ROOT/'scripts/deployment/build_candidate.py')
        builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
        directory=self.directory/'usb';directory.mkdir()
        target=directory/f'v{self.base["version"]}.json'
        original=profiles.load_usb_profile(self.base)
        for name in ('13500000.otg_new','next-controller.1'):
            with self.subTest(name=name):
                target.write_text(json.dumps({**original,'udc':name}))
                selected=profiles.load_usb_profile(self.base,self.directory)
                self.assertIn('--udc '+name+' ',builder.usb_hook(selected))
                self.assertIn("UDC='"+name+"'",builder.boot_report_script(selected))
                self.assertIn("PROFILE='"+profiles.fingerprint(selected)+"'",builder.boot_report_script(selected))

if __name__ == '__main__':
    unittest.main()
