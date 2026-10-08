"""Synthetic installation bindings and session provenance; never opens a USB device."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from deployment import installation_review as install
from deployment import writer_transport as writer
import test_writer_transport


class InstallationReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_writer_transport.WriterTransportTests();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups);self.w=self.fixture
        self.w.layout['physical_write_admitted']=True;self.w.bind_review()
        self.bundle=self.w.inputs['installation_review']

    def validate(self):
        w=self.w
        return install.validate_binding(self.bundle,w.base,w.cpu,w.reader,w.transport,w.layout,w.inputs)

    def test_exact_binding_and_separate_target_contract(self):
        self.assertEqual(self.validate(),writer.fingerprint(self.bundle))
        self.w.inputs['target']='restore'
        with self.assertRaises(writer.ram.probe.ProbeError):self.validate()

    def test_changed_or_missing_bundle_cannot_be_enabled_by_boolean_alone(self):
        for value in (None,{},dict(self.bundle,flash_ready=True)):
            with self.subTest(value=bool(value)),self.assertRaises(writer.ram.probe.ProbeError):
                w=self.w;install.validate_binding(value,w.base,w.cpu,w.reader,w.transport,w.layout,w.inputs)
        w=self.w;del w.inputs['installation_review']
        with self.assertRaises(writer.ram.probe.ProbeError):
            writer.make_plan(w.base,w.cpu,w.reader,w.transport,w.layout,w.inputs,'write')

    def test_current_bytes_profile_build_and_reader_drift_refused(self):
        w=self.w
        for key,value in [('image',bytes(len(w.inputs['image']))),('payload',b'changed'),('writer',b'changed'),
                          ('metadata',bytes(len(w.inputs['metadata']))),('build_sha256','0'*64),
                          ('image_name','different.bin'),('readback_plan',dict(operation='different'))]:
            old=w.inputs[key];w.inputs[key]=value
            with self.subTest(key=key),self.assertRaises(writer.ram.probe.ProbeError):self.validate()
            w.inputs[key]=old
        w.layout['staging_budget_ms']-=1
        with self.assertRaises(writer.ram.probe.ProbeError):self.validate()

    def test_source_change_refused_even_when_all_binary_inputs_match(self):
        with patch.object(install,'source_pins',return_value={}):
            with self.assertRaises(writer.ram.probe.ProbeError):self.validate()

    def test_missing_or_unreviewed_source_state_stops_before_write_planning(self):
        for state in ({},dict(kind='unknown',freshness_verified=False),
                      dict(kind='recorded-stock',freshness_verified=True),
                      dict(kind='installed-candidate',freshness_verified=False,new_image_staged=True)):
            self.bundle['source_state']=state
            self.w.layout['installation_review_sha256']=writer.fingerprint(self.bundle)
            with self.subTest(state=state),self.assertRaises(writer.ram.probe.ProbeError):self.validate()

    def test_the_previous_write_is_proven_by_its_readback_or_its_first_start_check(self):
        image=dict(name='candidate.bin',bytes=4,sha256='a'*64)
        state=lambda **proof:dict(kind='installed-candidate',freshness_verified=False,new_image_staged=False,
                                  previous_review_sha256='b'*64,image=image,**proof)
        readback=dict(status='saved-logical-readback-matches',image_sha256='a'*64,freshness_verified=False)
        check=dict(schema=1,expected='a'*64,actual='a'*64,match=True,error=None)
        cases=[(state(exact_readback=readback),True),(state(exact_readback=None,first_start_check=check),True),
               (state(exact_readback=None,first_start_check=dict(check,actual='c'*64,match=False)),False),
               (state(exact_readback=None,first_start_check=dict(check,expected='c'*64,actual='c'*64)),False),
               (state(exact_readback=None,first_start_check=None),False),(state(exact_readback=None),False)]
        for value,accepted in cases:
            self.bundle['source_state']=value
            self.w.layout['installation_review_sha256']=writer.fingerprint(self.bundle)
            with self.subTest(state=value,accepted=accepted):
                if accepted:self.assertEqual(self.validate(),writer.fingerprint(self.bundle))
                else:
                    with self.assertRaises(writer.ram.probe.ProbeError):self.validate()

    def test_rebound_invalid_boot_scope_and_tail_contract_refused(self):
        original=copy.deepcopy(self.bundle)
        for change in ('boot','readback','scope','freshness'):
            self.bundle=copy.deepcopy(original)
            if change=='boot':self.bundle['boot']['selected_rootfs']='rootfs2'
            if change=='readback':self.bundle['exact_readback']['candidate']['operation']='offline-stock-preinstall'
            if change=='scope':self.bundle['physical_device_accessed']=True
            if change=='freshness':self.bundle['freshness_verified']=True
            self.w.layout['installation_review_sha256']=writer.fingerprint(self.bundle)
            with self.subTest(change=change),self.assertRaises(writer.ram.probe.ProbeError):self.validate()

    def test_no_currency_confirmation_or_wrong_library_stops_before_usb_and_output(self):
        w=self.w;p=writer.make_plan(w.base,w.cpu,w.reader,w.transport,w.layout,w.inputs,'write')
        def run(current):
            return writer.acquire(p,writer.fingerprint(p),w.base,w.cpu,w.reader,w.transport,w.layout,w.inputs,
                                  w.library,w.root/'must-not-exist',loader=lambda _:self.fail('USB forbidden'),
                                  evidence_current=current)
        with self.assertRaises(writer.ram.probe.ProbeError):run(False)
        w.library.write_bytes(b'changed library')
        with self.assertRaises(writer.ram.probe.ProbeError):run(True)
        self.assertFalse((w.root/'must-not-exist').exists())

    def test_closed_profile_remains_closed_with_valid_bundle_and_confirmation(self):
        w=self.w;w.layout['physical_write_admitted']=False
        p=writer.make_plan(w.base,w.cpu,w.reader,w.transport,w.layout,w.inputs,'write')
        with self.assertRaises(writer.ram.probe.ProbeError):
            writer.acquire(p,writer.fingerprint(p),w.base,w.cpu,w.reader,w.transport,w.layout,w.inputs,
                           w.library,w.root/'closed',loader=lambda _:self.fail('USB forbidden'),evidence_current=True)
        self.assertFalse((w.root/'closed').exists())

    def test_admission_fields_do_not_form_hash_cycle(self):
        before=install.layout_contract(self.w.layout)
        self.w.layout.update(physical_write_admitted=False,installation_review_sha256='0'*64)
        self.assertEqual(before,install.layout_contract(self.w.layout))
        with self.assertRaises(writer.ram.probe.ProbeError):self.validate()

    def session(self,kind='boot'):
        folder=self.w.root/kind;folder.mkdir()
        plan=dict(nand_writes=False,synthetic=True,protocol_call_limit=20,image_sha256='b'*64,metadata_sha256='c'*64)
        record=dict(plan=plan,approved_plan_sha256=writer.fingerprint(plan),session_id='synthetic-'+kind,nonce_hex='01'*16,
                    status={'boot':'boot-evidence-collected','stock':'rootfs-collected','stage':'writer-staging-verified'}[kind],
                    cleanup_errors=[],physical_device_accessed=True,writer_execution_attempted=False,
                    capture_sha256=writer.ram.sha(b'generated records'),records_completed=4,batch_executions=1,full_staging_patterns_verified=True,
                    image_ram_verified=True,writer_ram_verified=True,completion_poison_verified=True)
        for name,value in [('result.json',record),('request.json',record),('dependency.json',dict(sha256='a'*64))]:
            (folder/name).write_text(json.dumps(value))
        (folder/'transfers.jsonl').write_text('{"synthetic":true}\n')
        (folder/'records.bin').write_bytes(b'generated records')
        audit=dict(result_sha256=install.file_pin(folder/'result.json',4096)['sha256'],
                   journal_sha256=install.file_pin(folder/'transfers.jsonl',4096)['sha256'],
                   session_id=record['session_id'],plan_sha256=writer.fingerprint(plan),
                   capture_sha256=record['capture_sha256'],status='saved-stage-trace-matches' if kind=='stage' else 'saved-boot-trace-matches',
                   records=4,calls=15,protocol_calls=15,spl_executions=1,batch_executions=1,
                   image_sha256='b'*64,metadata_sha256='c'*64,writer_executions=0,nand_writes=False)
        name='offline-journal-review.json' if kind=='stock' else 'offline-review.json'
        (folder/name).write_text(json.dumps(audit))
        return folder,name

    def test_all_session_types_bind_original_plans_journals_and_audits(self):
        for kind in ('boot','stock','stage'):
            folder,_=self.session(kind);result,pins=install.evidence(folder,kind)
            self.assertEqual(result['session_id'],pins['session_id']);self.assertIn('result.json',pins['files'])

    def test_mixed_or_changed_session_evidence_rejected(self):
        for index,name in enumerate(('request.json','result.json','transfers.jsonl','records.bin','offline-review.json')):
            folder,audit=self.session('boot');path=folder/name
            original=path.read_bytes()
            if name=='request.json':
                d=json.loads(original);d['nonce_hex']='02'*16;path.write_text(json.dumps(d))
            elif name=='result.json':
                d=json.loads(original);d['cleanup_errors']=['failed'];path.write_text(json.dumps(d))
            elif name=='offline-review.json':
                d=json.loads(original);d['session_id']='other session';path.write_text(json.dumps(d))
            else:path.write_bytes(b'changed')
            with self.subTest(name=name),self.assertRaises(writer.ram.probe.ProbeError):install.evidence(folder,'boot')
            folder.rename(self.w.root/f'finished-{index}')

    def test_symlink_or_oversized_evidence_refused(self):
        folder,_=self.session();path=folder/'records.bin';saved=folder/'original';path.rename(saved);path.symlink_to(saved)
        with self.assertRaises(ValueError):install.evidence(folder,'boot')
        with self.assertRaises(ValueError):install.file_pin(saved,1)


if __name__=='__main__':unittest.main()
