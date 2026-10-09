"""The installation review without a history (plan, stage 6): the boot evidence and the identity
probe of one USB Boot entry, from the fake ROMs and audited, an image known by its first blocks and
the write's ABI a player ran. No firmware, USB or sibling repository."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'scripts'))
from deployment import installation_review as install
from deployment import writer_transport as writer
import test_audit_usb_boot
import test_audit_usb_probe
import test_writer_transport

ram = writer.ram


class KnownReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # One entry: the boot evidence, then the probe, both after the entry's SPL (its clean diagnostic).
        cls.boot = test_audit_usb_boot.BootAuditTests('run')
        cls.boot.setUp()
        cls.probe = test_audit_usb_probe.ProbeAuditTests('run')
        cls.probe.setUp()
        cls.probe.library = cls.boot.library
        cls.captures = {}
        for kind, fixture in (('boot', cls.boot), ('probe', cls.probe)):
            out = fixture.session(clean=True)
            (out/'offline-review.json').write_text(json.dumps(fixture.check(out)))
            cls.captures[kind] = out
        cls.plans = {k: json.loads((v/'result.json').read_text())['plan'] for k, v in cls.captures.items()}

    @classmethod
    def tearDownClass(cls):
        cls.boot.doCleanups()
        cls.probe.doCleanups()

    def setUp(self):
        w = self.w = test_writer_transport.WriterTransportTests('run')
        w.setUp()
        self.addCleanup(w.doCleanups)
        w.inputs['readback_plan'] = dict(operation='rootfs', synthetic=True)
        restore = dict(w.inputs, image=b'hsqs'+bytes([1])*(len(w.inputs['image'])-4),
                       image_name='synthetic-restore.bin', target='restore')
        self.inputs = dict(candidate=w.inputs, restore=restore)
        blocks = json.loads((self.captures['probe']/'result.json').read_text())['logical_image_sha256']
        policy, layout = w.inputs['page_policy'], w.layout
        regions = writer.validate_layout(w.base, w.reader, w.transport, policy, layout)
        self.known = dict(first_blocks_bytes=262144, images=[],
                          stock=dict(name='stock.bin', bytes=len(restore['image']), sha256=ram.sha(restore['image']),
                                     first_blocks_sha256=blocks),
                          exercised=dict(writer_sha256=ram.sha(w.inputs['writer']), spl_sha256=ram.sha(w.inputs['spl']),
                                         metadata_payload_sha256=ram.sha(w.inputs['payload']),
                                         staging_payload_sha256=ram.sha(w.inputs['staging_payload']),
                                         firmware_profile_sha256=writer.fingerprint(w.base),
                                         cpu_profile_sha256=writer.fingerprint(w.cpu),
                                         reader_profile_sha256=writer.fingerprint(w.reader),
                                         transport_profile_sha256=writer.fingerprint(w.transport),
                                         page_policy_sha256=writer.fingerprint(policy),
                                         completion_profile_sha256=writer.fingerprint(w.inputs['completion']),
                                         installer_contract_sha256=writer.fingerprint(install.layout_contract(layout)),
                                         image_bytes=len(w.inputs['image']),
                                         ram_regions=[dict(name=n, address=a, bytes=s) for n, a, s in regions],
                                         writer_entry=w.reader['load_address']+layout['writer_entry_offset']))
        selected = dict(selected_route='primary', selected_rootfs='rootfs', metadata_sha256=ram.sha(w.inputs['metadata']),
                        static_selection_reviewed=True)
        assess = patch.object(install.boot_review, 'assess', return_value=selected)
        assess.start()
        self.addCleanup(assess.stop)
        bootloader = patch.object(install.boot_review, 'load_bootloader', return_value={})
        bootloader.start()
        self.addCleanup(bootloader.stop)

    def assemble(self, captures=None, plans=None, known=None, allow_unknown=False):
        w = self.w
        return install.assemble_known(w.base, w.cpu, w.reader, w.transport, w.layout, self.inputs,
                                      captures or self.captures, plans or self.plans, self.boot.library,
                                      known or self.known, allow_unknown)

    def test_a_known_image_in_one_entry_makes_a_bundle_the_write_takes(self):
        bundle = self.assemble()
        state = bundle['source_state']
        self.assertEqual((state['kind'], state['found']['kind'], state['same_entry_required']), ('known-image', 'stock', True))
        self.assertEqual(sorted(bundle['images']), ['candidate', 'restore'])
        self.assertEqual(set(bundle['evidence']), {'boot', 'probe'})
        w = self.w
        layout = dict(w.layout, physical_write_admitted=True, installation_review_sha256=writer.fingerprint(bundle))
        for target in ('candidate', 'restore'):
            inputs = dict(self.inputs[target], installation_review=bundle)
            self.assertEqual(install.validate_binding(bundle, w.base, w.cpu, w.reader, w.transport, layout, inputs),
                             writer.fingerprint(bundle))

    def test_a_release_image_is_known_too(self):
        known = copy.deepcopy(self.known)
        release = dict(known['stock'], release='2.57.5', name='disc-boot.bin', sha256='d'*64)
        known['stock']['first_blocks_sha256'] = 'e'*64
        known['images'] = [release]
        found = self.assemble(known=known)['source_state']['found']
        self.assertEqual((found['kind'], found['release']), ('release', '2.57.5'))

    def test_the_packages_own_image_is_known_on_the_way_back(self):
        known = copy.deepcopy(self.known)
        known['stock']['first_blocks_sha256'] = 'e'*64
        probe = self.captures['probe']/'logical-image.bin'
        candidate = probe.read_bytes()+self.inputs['candidate']['image'][262144:]
        self.inputs['candidate'] = dict(self.inputs['candidate'], image=candidate)
        found = self.assemble(known=known)['source_state']['found']
        self.assertEqual((found['kind'], found['sha256']), ('candidate', ram.sha(candidate)))

    def test_an_unknown_image_stops_before_anything_is_written(self):
        known = copy.deepcopy(self.known)
        known['stock']['first_blocks_sha256'] = 'e'*64
        with self.assertRaisesRegex(ram.probe.ProbeError, 'does not know'):
            self.assemble(known=known)

    def test_the_way_back_to_stock_is_admitted_from_any_state(self):
        """Owner, 2026-10-05/09: back to stock from any state. With allow_unknown an image the installer does
        not know is named so, and the bundle admits the restore only, never the candidate."""
        known = copy.deepcopy(self.known)
        known['stock']['first_blocks_sha256'] = 'e'*64
        bundle = self.assemble(known=known, allow_unknown=True)
        state = bundle['source_state']
        self.assertEqual((state['found']['kind'], state['targets']), ('unknown', ['restore']))
        self.assertNotIn('sha256', state['found'])
        w = self.w
        layout = dict(w.layout, physical_write_admitted=True, installation_review_sha256=writer.fingerprint(bundle))
        restore = dict(self.inputs['restore'], installation_review=bundle)
        self.assertEqual(install.validate_binding(bundle, w.base, w.cpu, w.reader, w.transport, layout, restore),
                         writer.fingerprint(bundle))
        with self.assertRaisesRegex(ram.probe.ProbeError, 'Invalid known-image source state'):
            install.validate_binding(bundle, w.base, w.cpu, w.reader, w.transport, layout,
                                     dict(self.inputs['candidate'], installation_review=bundle))
        self.assertEqual(self.assemble()['source_state']['targets'], ['candidate', 'restore'], 'a known image admits both')

    def test_the_write_abi_must_be_one_a_player_ran(self):
        for key in ('writer_sha256', 'staging_payload_sha256', 'installer_contract_sha256', 'writer_entry'):
            known = copy.deepcopy(self.known)
            known['exercised'][key] = 'f'*64 if key.endswith('sha256') else 0
            with self.subTest(key=key), self.assertRaisesRegex(ram.probe.ProbeError, 'No player has run'):
                self.assemble(known=known)

    def test_the_way_back_must_be_stock(self):
        known = copy.deepcopy(self.known)
        known['stock']['sha256'] = 'f'*64
        with self.assertRaisesRegex(ram.probe.ProbeError, 'not the reviewed stock image'):
            self.assemble(known=known)

    def test_sessions_must_carry_out_the_reviewed_plans_in_one_entry(self):
        plans = dict(self.plans, probe=dict(self.plans['probe'], build_sha256='0'*64))
        with self.assertRaisesRegex(ram.probe.ProbeError, 'did not carry out the reviewed plan'):
            self.assemble(plans=plans)
        with patch.object(install, 'moment', side_effect=lambda v, n=iter([4, 5, 1, 2]): next(n)):
            with self.assertRaisesRegex(ram.probe.ProbeError, 'not of one USB Boot entry'):
                self.assemble()
        boot = json.loads((self.captures['boot']/'result.json').read_text())
        for change in (dict(spl_skipped=False), dict(connection=dict(boot['connection'], address=6))):
            with self.subTest(change=change):
                with self.assertRaisesRegex(ram.probe.ProbeError, 'not of one USB Boot entry'):
                    install.same_entry(dict(boot, **change), json.loads((self.captures['probe']/'result.json').read_text()))

    def test_the_probe_blocks_are_recomputed_from_its_records(self):
        result = json.loads((self.captures['probe']/'result.json').read_text())
        with patch.object(install.readback, 'first_blocks', return_value=dict(
                image_sha256='e'*64, capture_sha256=result['capture_sha256'], image_bytes=262144)):
            with self.assertRaisesRegex(ram.probe.ProbeError, 'first blocks differ'):
                self.assemble()

    def test_an_invalid_known_state_is_refused_by_the_write(self):
        bundle = self.assemble()
        w = self.w
        for change in (dict(same_entry_required=False), dict(found=dict(bundle['source_state']['found'], kind='other')),
                       dict(first_blocks_sha256='e'*64), dict(freshness_verified=True)):
            changed = dict(bundle, source_state=dict(bundle['source_state'], **change))
            layout = dict(w.layout, physical_write_admitted=True, installation_review_sha256=writer.fingerprint(changed))
            with self.subTest(change=change), self.assertRaises(ram.probe.ProbeError):
                install.validate_binding(changed, w.base, w.cpu, w.reader, w.transport, layout,
                                         dict(self.inputs['candidate'], installation_review=changed))


    def test_the_command_line_keeps_the_two_ways_apart(self):
        base = ['--diskos', 'd', '--artifacts', 'a', '--build', 'b', '--staging-build', 's', '--readback-build', 'r',
                '--boot-capture', 'c', '--libusb', 'l', '--output', 'o']
        fresh = ['--known', '--probe-capture', 'p', '--boot-build', 'bb', '--probe-build', 'pb']
        history = ['--previous-review', 'x', '--previous-image', 'x', '--write-capture', 'x', '--readback-capture', 'x']
        for args in (fresh[:3], fresh+['--stock-capture', 's'], fresh+['--stage-capture', 's'], fresh+history,
                     ['--stock-capture', 's', '--stage-capture', 's', '--probe-capture', 'p'], ['--stage-capture', 's'],
                     ['--stock-capture', 's', '--stage-capture', 's', '--allow-unknown']):
            with self.subTest(args=args), patch.object(sys, 'argv', ['installation_review.py', *base, *args]), \
                    patch('sys.stderr'), self.assertRaises(SystemExit) as stop:
                install.main()
            self.assertEqual(stop.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
