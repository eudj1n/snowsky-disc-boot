"""Firmware-free update history tests, including a real synthetic page comparison."""
import copy
import json
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'scripts'))
from deployment import installation_review as install
from deployment import installed_candidate as update
import test_readback

REJECTION = (ValueError, install.ram.probe.ProbeError)


class InstalledCandidateTests(unittest.TestCase):
    def setUp(self):
        self.r = test_readback.ReadbackTests(); self.r.setUp()
        self.addCleanup(self.r.doCleanups); r = self.r
        r.capture(); self.exact = r.verify()
        self.current = {key: 'a'*64 for key in update.STABLE_CONTEXT}
        self.current['layout'] = dict(writer_wait_ms=900000)
        self.current['additional_profile_pins'] = {k:'a'*64 for k in update.STABLE_PROFILES}
        self.current['additional_profile_pins']['usb'] = 'b'*64
        self.current['metadata_sha256'] = install.ram.sha(r.metadata)
        self.current['page_policy_sha256'] = install.fingerprint(r.policy)
        self.current.update(build_sha256='e'*64,image_review_sha256='e'*64)
        self.image = dict(name=r.image.name, bytes=r.image.stat().st_size, sha256=r.digest)
        self.prior = dict(schema_version=1, status='installation-inputs-reviewed', context=copy.deepcopy(self.current),
            images=dict(candidate=self.image, restore=dict(name='restore.bin', bytes=8192, sha256='f'*64)),
            evidence=dict(boot={'old':'boot'}, stock={'old':'stock'}),
            boot=dict(static_selection_reviewed=True, selected_rootfs='rootfs'),
            libusb_sha256='d'*64, physical_device_accessed=False, flash_ready=False, freshness_verified=False,
            postwrite_collection=dict(operation='rootfs-collect', nand_writes=False, protocol_call_limit=100),
            exact_readback=dict(candidate=r.plan))
        self.prior['source_pins']={'synthetic/source.py':'a'*64}
        self.prior['context']['additional_profile_pins']['usb'] = 'c'*64
        self.wp = dict(operation='writer-write', target='candidate', nand_writes=True, writer_executions=1,
            host_retries=0, reconnect=False, physical_write_admitted=True,
            installation_review_sha256=install.fingerprint(self.prior), image_sha256=r.digest,
            image_bytes=8192, image_name=r.image.name, protocol_call_limit=100, metadata_sha256=install.ram.sha(r.metadata))
        self.wp.update({k:self.prior['context'][v] for k,v in update.PLAN_CONTEXT.items()})
        self.wp['transport_sources_sha256']=self.prior['source_pins'].copy()
        self.wp['installer_profile_sha256']=install.fingerprint(dict(self.prior['context']['layout'],
            physical_write_admitted=True,installation_review_sha256=install.fingerprint(self.prior)))
        self.write = self.session('write', self.wp, 'writer-completion-observed', 1, 2)
        self.read = self.session('read', self.prior['postwrite_collection'], 'rootfs-collected', 3, 4)
        self.write.update(writer_execution_attempted=True, writer_return_observed=True,
            full_staging_patterns_verified=True, image_ram_verified=True, writer_ram_verified=True,
            completion_poison_verified=True)
        self.read.update(capture_sha256=self.exact['capture_sha256'], logical_image_sha256=r.digest,
            records_completed=12, batch_executions=1, bad_blocks=[3], logical_to_physical=[4,5],
            ecc_histogram=self.exact['ecc_histogram'])
        self.owner = dict(observation='owner-confirmed-normal-first-reboot', reported_at='2026-01-01T00:00:05+00:00',
            owner_answer='Normal boot', write_session_id='write', readback_session_id='read',
            automated_boot_test=False, native_process_verified=False, live_root_verified=False)
        w = [0]*256
        for i,v in {0:0x4004e005,1:2,5:3,6:2,9:0x55555555,10:1,11:6,12:0x73717368,
                    15:16,16:0x600df10c,20:1,21:16,40:3}.items(): w[i]=v
        self.debug = struct.pack('<256I',*w)
        from deployment.review import check_debug
        self.write['writer_result'] = check_debug(self.debug,0,r.policy['writer'])
        self.wdir=r.root/'write'; self.rdir=r.root/'read'; self.wdir.mkdir();self.rdir.mkdir()
        (self.wdir/'writer-result.bin').write_bytes(self.debug)
        (self.rdir/'records.bin').write_bytes(r.path.read_bytes())
        self.prior_path=r.root/'prior.json'; self.target='candidate'; self.persist()

    def session(self, name, plan, status, start, end):
        return dict(plan=plan, approved_plan_sha256=install.fingerprint(plan), session_id=name,
            nonce_hex=self.r.nonce.hex() if name=='read' else '01'*16, status=status,
            started_at=f'2026-01-01T00:00:0{start}+00:00', finished_at=f'2026-01-01T00:00:0{end}+00:00',
            cleanup_errors=[], physical_device_accessed=True)

    def persist(self):
        self.prior_path.write_text(json.dumps(self.prior))
        for folder,result,status in ((self.wdir,self.write,f'saved-{self.target}-write-trace-matches'),
                                      (self.rdir,self.read,'saved-postwrite-trace-matches')):
            for name,value in [('result.json',result),('request.json',result),('dependency.json',{'sha256':'d'*64})]:
                (folder/name).write_text(json.dumps(value))
            (folder/'transfers.jsonl').write_text('{"synthetic":true}\n')
            audit=dict(status=status, session_id=result['session_id'], plan_sha256=result['approved_plan_sha256'],
                result_sha256=install.file_pin(folder/'result.json',100000)['sha256'],
                journal_sha256=install.file_pin(folder/'transfers.jsonl',100000)['sha256'], calls=10,
                image_sha256=self.r.digest, metadata_sha256=install.ram.sha(self.r.metadata),
                writer_executions=1 if folder==self.wdir else 0, nand_writes=folder==self.wdir,
                spl_executions=1, records=12, batch_executions=1, capture_sha256=self.exact['capture_sha256'])
            (folder/'offline-review.json').write_text(json.dumps(audit))
        (self.rdir/'owner-boot-confirmation.json').write_text(json.dumps(self.owner))
        (self.rdir/f'exact-{self.target}-review.json').write_text(json.dumps(self.exact))

    def assess(self):
        r=self.r
        return update.assess(self.prior_path,r.image,self.wdir,self.rdir,self.current,
            self.prior['images']['restore'],self.prior['evidence'],r.base,r.reader,r.policy,r.metadata,'d'*64)

    def test_accepts_known_candidate_and_keeps_historical_scope(self):
        state,plan,pins=self.assess()
        self.assertEqual(state['kind'],'installed-candidate')
        self.assertEqual(state['image'],self.image)
        self.assertEqual(state['exact_readback'],self.exact)
        self.assertFalse(state['new_image_staged']); self.assertFalse(state['freshness_verified'])
        self.assertEqual(plan,self.wp)
        self.assertIn('writer-result.bin',pins['write']['files'])

    def first_start(self, **changes):
        """The previous write's proof from its first start (plan, stage 4c): the boot layer's check of
        the root device, fetched over the USB console, and the owner's word."""
        folder = self.r.root/'first-start'
        folder.mkdir(exist_ok=True)
        record = dict(schema=1, expected=self.r.digest, actual=self.r.digest, bytes=self.image['bytes'],
                      # The fixture's table has rootfs alone, index 0 (the player's: 2, mtdblock_bbt_ro2).
                      device='/dev/mtdblock_bbt_ro0', match=True, seconds=1.5, error=None, build='2506f166236f')
        record.update(changes.pop('record', {}))
        (folder/'rootfs-check.json').write_text(json.dumps(record))
        fetch = dict(via='usb-console', fetched_at='2026-01-01T00:00:06+00:00', write_session_id='write',
                     rootfs_check_sha256=install.file_pin(folder/'rootfs-check.json', 100000)['sha256'])
        fetch.update(changes.pop('fetch', {}))
        owner = dict(observation='owner-confirmed-normal-first-boot', reported_at='2026-01-01T00:00:05+00:00',
                     owner_answer='it is ok', write_session_id='write', automated_boot_test=False, native_process_verified=False)
        owner.update(changes.pop('owner', {}))
        (folder/'fetch.json').write_text(json.dumps(fetch)); (folder/'owner-boot-confirmation.json').write_text(json.dumps(owner))
        r = self.r
        return update.assess(self.prior_path, r.image, self.wdir, folder, self.current, self.prior['images']['restore'],
                             self.prior['evidence'], r.base, r.reader, r.policy, r.metadata, 'd'*64)

    def test_accepts_the_first_start_check_in_place_of_a_readback(self):
        state, plan, pins = self.first_start()
        self.assertEqual((state['kind'], state['exact_readback'], state['first_start_check']['match']), ('installed-candidate', None, True))
        self.assertEqual(sorted(pins['first_start']['files']), ['fetch.json', 'owner-boot-confirmation.json', 'rootfs-check.json'])
        self.assertNotIn('readback', pins)

    def test_a_first_start_check_that_does_not_prove_the_written_image_is_refused(self):
        other = '0' * 64
        for changes in (dict(record=dict(actual=other)), dict(record=dict(expected=other, actual=other)), dict(record=dict(match=False)),
                        dict(record=dict(device='/dev/mtdblock_bbt_ro4')), dict(record=dict(bytes=4096)), dict(record=dict(error='short')),
                        dict(fetch=dict(fetched_at='2026-01-01T00:00:01+00:00')), dict(fetch=dict(rootfs_check_sha256=other)),
                        dict(fetch=dict(write_session_id='other')), dict(fetch=dict(via='card')),
                        dict(owner=dict(write_session_id='other')), dict(owner=dict(owner_answer=' ')),
                        dict(owner=dict(reported_at='2026-01-01T00:00:01+00:00'))):
            with self.subTest(changes=changes), self.assertRaises(install.ram.probe.ProbeError):
                self.first_start(**changes)

    def test_accepts_a_previous_way_back_to_stock(self):
        """The last installation was the restore (2026-10-05): its plan, image and exact readback are
        the review's restore ones, and the next review starts from it."""
        self.target = 'restore'
        self.prior['images'] = dict(candidate=dict(name='other.bin', bytes=8192, sha256='e'*64), restore=self.image)
        self.prior['exact_readback'] = dict(candidate={'other': 'plan'}, restore=self.r.plan)
        self.wp.update(target='restore', installation_review_sha256=install.fingerprint(self.prior))
        self.wp['installer_profile_sha256'] = install.fingerprint(dict(self.prior['context']['layout'],
            physical_write_admitted=True, installation_review_sha256=install.fingerprint(self.prior)))
        self.write['approved_plan_sha256'] = install.fingerprint(self.wp)
        (self.rdir/'exact-candidate-review.json').unlink()
        self.persist()
        state, plan, pins = self.assess()
        self.assertEqual((state['kind'], state['target'], state['image']), ('installed-candidate', 'restore', self.image))
        self.assertIn('exact-restore-review.json', pins['readback']['files'])
        self.wp['target'] = 'other'
        self.write['approved_plan_sha256'] = install.fingerprint(self.wp)
        self.persist()
        with self.assertRaisesRegex(Exception, 'Expected one admitted candidate or restore write'):
            self.assess()

    def test_unknown_write_or_missing_staging_cannot_be_repaired_by_readback(self):
        for key,value in [('status','writer-outcome-unknown'),('writer_return_observed',False),
                          ('writer_execution_attempted',False),('image_ram_verified',False),('cleanup_errors',['close'])]:
            original=copy.deepcopy(self.write);self.write[key]=value;self.persist()
            with self.subTest(key=key),self.assertRaises(REJECTION):self.assess()
            self.write=original

    def test_rejects_other_read_session_owner_or_wrong_chronology(self):
        for obj,key,value in [(self.owner,'write_session_id','other'),(self.owner,'live_root_verified',True),
                               (self.owner,'reported_at','2026-01-01T00:00:03+00:00'),
                               (self.read,'started_at','2026-01-01T00:00:01+00:00')]:
            old=obj[key];obj[key]=value;self.persist()
            with self.subTest(key=key),self.assertRaises(REJECTION):self.assess()
            obj[key]=old

    def test_accepts_a_first_boot_confirmed_between_write_and_readback(self):
        # Combined-008: leaving USB Boot after the write booted the new system before the readback.
        self.read.update(started_at='2026-01-01T00:00:04+00:00', finished_at='2026-01-01T00:00:06+00:00')
        self.owner = dict(observation='owner-confirmed-normal-first-boot', reported_at='2026-01-01T00:00:03+00:00',
            owner_answer='Normal boot', write_session_id='write', readback_session_id='read',
            automated_boot_test=False, native_process_verified=False)
        self.persist()
        self.assertEqual(self.assess()[0]['owner_boot'], self.owner)
        for key,value in [('reported_at','2026-01-01T00:00:05+00:00'),('reported_at','2026-01-01T00:00:07+00:00'),
                          ('reported_at','2026-01-01T00:00:01+00:00'),('live_root_verified',True),
                          ('native_process_verified',True),('observation','owner-confirmed-normal-boot')]:
            old=self.owner.get(key);self.owner[key]=value;self.persist()
            with self.subTest(key=key,value=value),self.assertRaises(REJECTION):self.assess()
            self.owner[key]=old
            if old is None: del self.owner[key]

    def test_rejects_prior_review_substitution_and_changed_hardware_contract(self):
        for key in update.STABLE_CONTEXT:
            old=self.current[key];self.current[key]='changed'
            with self.subTest(key=key),self.assertRaises(REJECTION):self.assess()
            self.current[key]=old
        self.prior['images']['candidate']['sha256']='0'*64;self.persist()
        with self.assertRaises(REJECTION):self.assess()

    def test_rechecks_pages_not_just_successful_saved_json(self):
        frames=self.r.capture();self.r.replace_data(frames,9,2047,99)
        (self.rdir/'records.bin').write_bytes(self.r.path.read_bytes())
        self.read['capture_sha256']=install.file_pin(self.rdir/'records.bin',100000)['sha256']
        self.exact['capture_sha256']=self.read['capture_sha256'];self.persist()
        with self.assertRaisesRegex(ValueError,'Image mismatch'):self.assess()

    def test_mismatched_block_map_or_exact_report_is_rejected(self):
        self.read['logical_to_physical']=[3,4];self.persist()
        with self.assertRaises(REJECTION):self.assess()
        self.read['logical_to_physical']=[4,5]
        self.exact['image_sha256']='0'*64;self.persist()
        with self.assertRaises(REJECTION):self.assess()

    def test_changed_journal_or_request_is_rejected(self):
        for name in ('transfers.jsonl','request.json'):
            self.persist();(self.wdir/name).write_text('{}')
            with self.subTest(name=name),self.assertRaises((*REJECTION,KeyError)):self.assess()

    def test_rejects_wrong_previous_image_even_with_successful_reports(self):
        self.r.image.write_bytes(bytes(8192))
        with self.assertRaises(REJECTION):self.assess()

    def test_historical_plan_cannot_substitute_reader_or_sources(self):
        for key,value in [('metadata_payload_sha256','0'*64),('installer_profile_sha256','0'*64),
                          ('transport_sources_sha256',{'synthetic/source.py':'b'*64})]:
            old=self.wp[key];self.wp[key]=value
            self.write['approved_plan_sha256']=install.fingerprint(self.wp);self.persist()
            with self.subTest(key=key),self.assertRaises(REJECTION):self.assess()
            self.wp[key]=old

    def test_changed_boot_profile_or_missing_boot_report_refused(self):
        for key in update.STABLE_PROFILES:
            self.current['additional_profile_pins'][key]='changed'
            with self.subTest(key=key),self.assertRaises(REJECTION):self.assess()
            self.current['additional_profile_pins'][key]='a'*64
        (self.rdir/'owner-boot-confirmation.json').unlink()
        with self.assertRaises(OSError):self.assess()

    def test_saved_completion_must_match_raw_writer_record(self):
        self.write['writer_result']['physicalEndExclusive']=7;self.persist()
        with self.assertRaises(REJECTION):self.assess()


if __name__=='__main__':unittest.main()
