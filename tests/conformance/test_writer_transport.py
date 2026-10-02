"""Synthetic ROM and small images only; no proprietary files or physical USB."""
import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from deployment import writer_transport as writer
from test_ram_transport import Rom, FakeClock
import test_kernel_review


class WriterRom(Rom):
    def __init__(self, reader, config, layout, policy, fault=None, at=0):
        super().__init__(reader, config, fault, at)
        self.layout, self.policy = layout, policy
        self.regions = [(0xb2400000, bytearray(0x8000)),
                        (0xa0a00000, bytearray(0x300000)),
                        (layout['image_address'], bytearray(policy['logical_blocks']*policy['block_bytes']))]

    def region(self, address):
        for base, data in self.regions:
            if base <= address < base+len(data):
                offset = address-base
                if self.fault == 'image-alias' and base == self.layout['image_address']:
                    offset %= 65536
                return data, offset
        raise AssertionError(f'Unexpected address {address:x}')

    def put(self, address, data):
        region, offset = self.region(address)
        assert offset+len(data) <= len(region)
        region[offset:offset+len(data)] = data

    def get(self, address, length):
        region, offset = self.region(address)
        return bytes(region[offset:offset+length])

    def call(self, name, args):
        entry = self.reader['load_address']+self.layout['writer_entry_offset']
        if name == 'control_transfer' and args[2] == 4 and args[3]<<16 | args[4] == entry:
            self.calls.append((name,args)); self.events.append(name)
            # A timed-out execute can still have reached the device.
            self.execute_addresses.append(entry)
            if len(self.calls) == self.at: return -7
            if self.fault != 'poison-result':
                p = self.policy
                w = [0]*256
                w[0], w[1], w[5], w[6] = 0x4004e005, p['logical_blocks'], p['start_block'], p['logical_blocks']
                w[9], w[11], w[12] = 0x55555555, p['start_block']+p['logical_blocks'], 0x73717368
                w[15], w[16], w[21] = 16, 0x600df10c, 16
                if self.fault == 'wrong-capacity': w[6] += 1
                self.put(self.layout['debug_address'], struct.pack('<256I',*w))
            return 0
        return super().call(name,args)


class WriterTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        ram = writer.ram
        self.base = ram.load_profile()
        self.cpu = ram.load_probe_profile(self.base)
        self.reader = ram.load_reader_profile(self.base)
        self.transport = ram.load_transport(self.base,self.reader)
        self.policy = ram.load_metadata_policy(self.base,self.reader)
        self.layout = writer.load_layout(self.base,self.reader,self.transport,self.policy)
        self.layout['physical_write_admitted']=False
        # Preserve production ABI/geometry, using just two generated logical blocks.
        self.policy = copy.deepcopy(self.policy)
        self.policy['writer']['logical_blocks'] = 2
        p = self.policy['writer']
        binary = bytearray(4704)
        struct.pack_into('<I',binary,p['capacity_instruction_offset'],p['capacity_instruction_value'] | 2)
        p['source_pins'][p['writer_file']] = ram.sha(binary)
        self.rebind()
        fixture = test_kernel_review.KernelReviewTests(); fixture.setUp()
        self.inputs = dict(spl=bytes(8056),payload=b'synthetic metadata code',build_sha256='a'*64,
                          page_policy=self.policy,writer=bytes(binary),metadata=fixture.page(),
                          image=b'hsqs'+bytes(2*131072-4),image_name='synthetic-candidate.bin',
                          target='candidate',image_review_sha256='b'*64)
        self.library = self.root/'fake-library'; self.library.write_bytes(b'not loaded')
        self.index = 0
        sync = patch.object(ram.os,'fsync'); sync.start(); self.addCleanup(sync.stop)

    def rebind(self):
        self.layout.update(page_policy_sha256=writer.fingerprint(self.policy),
                           writer_profile_sha256=writer.fingerprint(self.policy['writer']))

    def bind_review(self):
        install=writer.installation_review
        self.inputs['readback_plan']=dict(operation='rootfs',synthetic=True)
        bundle=dict(schema_version=1,status='installation-inputs-reviewed',
            source_state=dict(kind='recorded-stock',freshness_verified=False),
            context=install.context(self.base,self.cpu,self.reader,self.transport,self.layout,self.inputs),
            source_pins=install.source_pins(),libusb_sha256=writer.ram.sha(self.library.read_bytes()),
            images={self.inputs['target']:dict(name=self.inputs['image_name'],bytes=len(self.inputs['image']),sha256=writer.ram.sha(self.inputs['image']))},
            boot=dict(static_selection_reviewed=True,selected_rootfs='rootfs'),
            postwrite_collection=self.inputs['readback_plan'],
            exact_readback={self.inputs['target']:install.readback.make_plan(self.base,self.reader,self.policy,self.inputs['metadata'],writer.ram.sha(self.inputs['image']))},
            physical_device_accessed=False,flash_ready=False,freshness_verified=False)
        self.inputs['installation_review']=bundle
        self.layout['installation_review_sha256']=writer.fingerprint(bundle)

    def plan(self, mode='stage'):
        if mode=='write':self.bind_review()
        return writer.make_plan(self.base,self.cpu,self.reader,self.transport,self.layout,self.inputs,mode)

    def rom(self, fault=None, at=0):
        return WriterRom(self.reader,self.transport,self.layout,self.policy['writer'],fault,at)

    def run_rom(self, fake, mode='stage', clock=None):
        self.index += 1; output = self.root/str(self.index)
        plan = self.plan(mode); clock = clock or FakeClock()
        result = writer.acquire(plan,writer.fingerprint(plan),self.base,self.cpu,self.reader,self.transport,
                                self.layout,self.inputs,self.library,output,loader=lambda _:fake,
                                clock=clock,sleep=clock.sleep,evidence_current=mode=='write')
        self.assertEqual(json.loads((output/'result.json').read_text()),result)
        return result,output

    def test_stage_compares_entire_image_and_never_invokes_writer(self):
        fake = self.rom(); result,output = self.run_rom(fake)
        self.assertEqual(result['status'],'writer-staging-verified',result.get('error'))
        self.assertEqual(fake.execute_addresses,[self.transport['spl_entry'],self.reader['load_address']])
        for flag in ('image_ram_verified','writer_ram_verified','full_staging_patterns_verified','completion_poison_verified'):
            self.assertTrue(result[flag])
        for flag in ('writer_execution_attempted','postwrite_verified','boot_verified','flash_ready'):
            self.assertFalse(result[flag])
        self.assertEqual(fake.get(self.layout['image_address'],len(self.inputs['image'])),self.inputs['image'])
        self.assertEqual((output/'metadata-main.bin').read_bytes(),self.inputs['metadata'])
        poison = (output/'writer-debug-poison.bin').read_bytes()
        self.assertEqual([struct.unpack_from('<I',poison,i*4)[0] for i in (0,9,16)],[0xeeeeeeee]*3)
        self.assertFalse(result['plan']['nand_writes'])
        self.assertLess(len(fake.calls),result['plan']['protocol_call_limit'])

    def test_candidate_and_restore_have_one_invocation_without_claiming_readback(self):
        self.layout['physical_write_admitted'] = True  # Synthetic profile only.
        for target in ('candidate','restore'):
            self.inputs.update(target=target,image_name='synthetic-'+target+'.bin')
            fake = self.rom(); clock = FakeClock()
            result,output = self.run_rom(fake,'write',clock)
            self.assertEqual(result['status'],'writer-completion-observed',result.get('error'))
            self.assertEqual(fake.execute_addresses,[self.transport['spl_entry'],self.reader['load_address'],self.plan()['writer_entry']])
            self.assertGreaterEqual(clock.now,self.layout['writer_wait_ms']/1000)
            self.assertTrue(result['writer_return_observed'])
            self.assertEqual((output/'writer-result.bin').stat().st_size,1024)
            for flag in ('postwrite_verified','boot_verified','flash_ready'): self.assertFalse(result[flag])
            self.assertFalse(result['writer_result']['freshnessVerified'])

    def test_error_at_every_transfer_stops_without_replay_or_restore(self):
        self.layout['physical_write_admitted'] = True
        baseline = self.rom(); self.run_rom(baseline,'write')
        entry = self.plan()['writer_entry']
        invocation = next(i+1 for i,(name,a) in enumerate(baseline.calls)
                          if name == 'control_transfer' and a[2] == 4 and a[3]<<16 | a[4] == entry)
        for position in range(1,len(baseline.calls)+1):
            with self.subTest(position=position):
                fake = self.rom('timeout',position)
                result,_ = self.run_rom(fake,'write')
                self.assertEqual(len(fake.calls),position)
                self.assertEqual(result['status'],'writer-outcome-unknown' if position >= invocation else 'failed-before-writer')
                self.assertLessEqual(fake.execute_addresses.count(entry),1)
                self.assertFalse(result['postwrite_verified'])

    def test_alias_ecc_stale_metadata_and_final_image_corruption_stop_before_writer(self):
        self.layout['physical_write_admitted'] = True
        for fault in ('image-alias','metadata-ecc','metadata-otp','stale','bad-id'):
            with self.subTest(fault=fault):
                result,_ = self.run_rom(self.rom(fault),'write')
                self.assertEqual(result['status'],'failed-before-writer')
                self.assertFalse(result['writer_execution_attempted'])
        fake = self.rom(); original = fake.put
        def corrupt(address,data):
            original(address,data)
            if address == self.layout['image_address'] and data.startswith(b'hsqs'):
                original(address,b'X')
        fake.put = corrupt
        result,_ = self.run_rom(fake,'write')
        self.assertIn('Staged image',result['error'])
        self.assertFalse(result['writer_execution_attempted'])

    def test_metadata_change_even_with_valid_partition_table_stops_before_full_staging(self):
        data = bytearray(self.inputs['metadata']); data[-1] ^= 1
        self.inputs['metadata'] = bytes(data)
        result,_ = self.run_rom(self.rom())
        self.assertIn('Partition metadata changed',result['error'])
        self.assertFalse(result['full_staging_patterns_verified'])

    def test_poison_and_invalid_completion_never_claim_success(self):
        self.layout['physical_write_admitted'] = True
        for fault in ('poison-result','wrong-capacity','release_interface'):
            result,_ = self.run_rom(self.rom(fault),'write')
            self.assertEqual(result['status'],'writer-outcome-unknown')
            self.assertTrue(result['writer_execution_attempted'])
            self.assertFalse(result['postwrite_verified'])

    def test_interrupted_wait_retains_uncertainty_and_does_not_poll_or_retry(self):
        self.layout['physical_write_admitted'] = True
        clock = FakeClock(); original = clock.sleep
        def interrupt(seconds):
            if clock.now >= 4: raise KeyboardInterrupt()
            original(seconds)
        clock.sleep = interrupt
        fake = self.rom(); result,_ = self.run_rom(fake,'write',clock)
        self.assertEqual(result['status'],'writer-outcome-unknown')
        self.assertEqual(fake.calls[-1][1][2],4)
        self.assertTrue(result['writer_execution_attempted'])
        self.assertFalse(result['writer_return_observed'])

    def test_expired_staging_and_insufficient_writer_budget_fail_before_invocation(self):
        self.layout['physical_write_admitted'] = True
        for advance in (901,1799):
            clock = FakeClock(); original = writer.stage
            def delayed(*args):
                original(*args); clock.now += advance
            with patch.object(writer,'stage',delayed):
                result,_ = self.run_rom(self.rom(),'write',clock)
            self.assertEqual(result['status'],'failed-before-writer')
            self.assertFalse(result['writer_execution_attempted'])

    def test_closed_admission_or_changed_plan_rejected_before_usb_and_output(self):
        for mode,approved in [('write',None),('stage','0'*64)]:
            p = self.plan(mode); output = self.root/'not-created'
            with self.assertRaises(writer.ram.probe.ProbeError):
                writer.acquire(p,approved or writer.fingerprint(p),self.base,self.cpu,self.reader,self.transport,
                               self.layout,self.inputs,self.library,output,loader=lambda _:self.fail('No USB'))
            self.assertFalse(output.exists())
        p = self.plan(); self.inputs['image'] = bytes(len(self.inputs['image']))
        with self.assertRaises(writer.ram.probe.ProbeError):
            writer.acquire(p,writer.fingerprint(p),self.base,self.cpu,self.reader,self.transport,
                           self.layout,self.inputs,self.library,self.root/'changed',loader=lambda _:self.fail('No USB'))

    def test_layout_rejects_overlap_relocation_pin_size_and_budget_changes(self):
        original = self.layout.copy()
        for key,value in [('writer_stack_bottom',self.reader['load_address']),('image_address',0xa2000000),
                          ('debug_address',0xa0a01000),('writer_stack_top',0xa0bfffe0),('writer_entry_offset',64),
                          ('dram_end',0xa1010000),('physical_write_admitted',1),('writer_wait_ms',True),
                          ('page_policy_sha256','0'*64),('session_budget_ms',20000)]:
            with self.subTest(key=key), self.assertRaises(writer.ram.probe.ProbeError):
                self.layout = {**original,key:value}; self.plan()
        self.layout = original
        self.inputs['writer'] = b'changed'*200
        with self.assertRaises(writer.ram.probe.ProbeError): self.plan()

    def test_future_version_rebinds_profiles_without_script_changes(self):
        self.base = {**self.base,'version':'9.99'}
        self.layout.update(version='9.99',firmware_profile_sha256=writer.fingerprint(self.base))
        (self.root/'installers').mkdir()
        (self.root/'installers/v9.99.json').write_text(json.dumps(self.layout))
        self.assertEqual(writer.load_layout(self.base,self.reader,self.transport,self.policy,self.root),self.layout)
        self.assertEqual(self.plan()['version'],'9.99')

    def test_short_final_transfer_and_journal_failure_stop_before_writer(self):
        fake = self.rom(); self.run_rom(fake)
        result,_ = self.run_rom(self.rom('short',len(fake.calls)))
        self.assertEqual(result['status'],'failed-before-writer')
        self.assertFalse(result['writer_execution_attempted'])
        fake = self.rom()
        with patch.object(writer.Journal,'begin',side_effect=OSError('disk full')):
            result,_ = self.run_rom(fake)
        self.assertFalse(fake.calls)
        self.assertEqual(result['status'],'failed-before-writer')

    def test_prepare_selects_exact_candidate_or_restore_and_rechecks_bytes(self):
        artifacts, diskos = self.root/'artifacts', self.root/'diskos'
        artifacts.mkdir(); diskos.mkdir()
        binary = diskos/self.policy['writer']['writer_file']
        binary.parent.mkdir(parents=True); binary.write_bytes(self.inputs['writer'])
        names = writer.artifact_names(self.base,'usb-engineering')
        images = {}
        for index,name in enumerate(names):
            data = b'hsqs'+bytes([index])*20
            (artifacts/name).write_bytes(data); images[name] = dict(sha256=writer.ram.sha(data))
        report = dict(variant='usb-engineering',images=images)
        with patch.object(writer.ram,'prepare_inputs',side_effect=lambda *a:dict(page_policy=self.policy)), \
             patch.object(writer.review,'review',return_value=report):
            for target,name in zip(('candidate','restore'),names):
                result = writer.prepare(self.base,self.cpu,self.reader,self.transport,self.root,
                                        diskos,artifacts,target,self.inputs['metadata'])
                self.assertEqual(result['image'],(artifacts/name).read_bytes())
                self.assertEqual(result['image_name'],name)
                self.assertEqual(result['target'],target)
            (artifacts/names[0]).write_bytes(b'changed after review')
            with self.assertRaises(writer.ram.probe.ProbeError):
                writer.prepare(self.base,self.cpu,self.reader,self.transport,self.root,
                               diskos,artifacts,'candidate',self.inputs['metadata'])
            binary.write_bytes(b'changed after review')
            with self.assertRaises(writer.ram.probe.ProbeError):
                writer.prepare(self.base,self.cpu,self.reader,self.transport,self.root,
                               diskos,artifacts,'restore',self.inputs['metadata'])


if __name__ == '__main__': unittest.main()
