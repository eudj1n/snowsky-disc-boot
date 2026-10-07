"""Synthetic ROM and small images only; no proprietary files or physical USB."""
import copy
import hashlib
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
    def __init__(self, reader, config, layout, policy, fault=None, at=0, staging_busy=2, writer_busy=3):
        super().__init__(reader, config, fault, at)
        self.layout, self.policy = layout, policy
        # The staging check and the writer still run for this many asks after their execution.
        self.staging_busy, self.writer_busy, self.busy_left, self.polling = staging_busy, writer_busy, 0, False
        self.staging_runs = []
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

    def staging(self):
        """The staging check (device/acquisition/staging.c) as the fake runs it: the pattern written in
        full and read back through the region's own mapping (so an alias shows), or the region hashed."""
        magic, version, op, address, length, seed, nonce = struct.unpack_from('<6I16s', self.get(self.reader['request_address'], 48))
        code, checked, digest = 0, 0, bytes(32)
        self.staging_runs.append((op, address, length))
        if op == 1:
            for invert in (0, 0xffffffff):
                x, words = seed, []
                for _ in range(length//4):
                    x ^= (x << 13) & 0xffffffff; x ^= x >> 17; x ^= (x << 5) & 0xffffffff
                    words.append(x ^ invert)
                data = struct.pack(f'<{len(words)}I', *words)
                for offset in range(0, length, 65536): self.put(address+offset, data[offset:offset+65536])
                back = b''.join(self.get(address+offset, min(65536, length-offset)) for offset in range(0, length, 65536))
                if back != data or self.fault == 'staging-mismatch':
                    code = 2; break
                checked += length//4
        else:
            region = b''.join(self.get(address+offset, min(65536, length-offset)) for offset in range(0, length, 65536))
            digest, checked = hashlib.sha256(region).digest(), length
            if self.fault == 'hash-mismatch': digest = hashlib.sha256(region[1:]).digest()
        self.put(self.reader['result_address'], struct.pack('<4I16s4I32s', 0x52475453, 1, op, code, nonce, address, length,
                                                            checked, 0, digest))

    def call(self, name, args):
        entry = self.reader['load_address']+self.layout['writer_entry_offset']
        if name == 'control_transfer' and args[2] == 0 and self.polling:
            if self.busy_left:
                self.calls.append((name,args)); self.events.append(name)
                self.busy_left -= 1
                if len(self.calls) == self.at: return -7
                return -7
            self.polling = False
        if (name == 'control_transfer' and args[2] == 4 and args[3]<<16 | args[4] == self.reader['load_address']
                and struct.unpack_from('<I', self.get(self.reader['request_address'], 4))[0] == 0x51475453):
            self.calls.append((name,args)); self.events.append(name)
            self.execute_addresses.append(self.reader['load_address'])
            if len(self.calls) == self.at: return -7
            self.staging()
            self.polling, self.busy_left = True, self.staging_busy
            return 0
        if name == 'control_transfer' and args[2] == 4 and args[3]<<16 | args[4] == entry:
            self.calls.append((name,args)); self.events.append(name)
            # A timed-out execute can still have reached the device.
            self.execute_addresses.append(entry)
            if len(self.calls) == self.at: return -7
            self.polling, self.busy_left = True, self.writer_busy
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
                          target='candidate',image_review_sha256='b'*64,
                          staging_payload=b'synthetic staging check code',staging_build_sha256='c'*64,
                          completion=ram.load_completion(self.base,self.transport))
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

    def rom(self, fault=None, at=0, **busy):
        return WriterRom(self.reader,self.transport,self.layout,self.policy['writer'],fault,at,**busy)

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
        # The metadata read, then the staging check twice: the image region's pattern, the hash of the
        # image's sample (here the whole small image: the sample is at most the image).
        self.assertEqual(fake.execute_addresses,[self.transport['spl_entry']]+[self.reader['load_address']]*3)
        image, length = self.layout['image_address'], len(self.inputs['image'])
        self.assertEqual(fake.staging_runs,[(1,image,length),(2,image,length)])
        self.assertEqual(result['plan']['staging_sample_bytes'],length)
        self.assertIsNotNone(result['image_region_check_ms']); self.assertIsNotNone(result['image_hash_sample_ms'])
        for flag in ('image_ram_verified','image_hash_sample_verified','writer_ram_verified','full_staging_patterns_verified',
                     'completion_poison_verified'):
            self.assertTrue(result[flag])
        for flag in ('writer_execution_attempted','postwrite_verified','boot_verified','flash_ready'):
            self.assertFalse(result[flag])
        self.assertEqual(fake.get(self.layout['image_address'],len(self.inputs['image'])),self.inputs['image'])
        self.assertEqual((output/'metadata-main.bin').read_bytes(),self.inputs['metadata'])
        poison = (output/'writer-debug-poison.bin').read_bytes()
        self.assertEqual([struct.unpack_from('<I',poison,i*4)[0] for i in (0,9,16)],[0xeeeeeeee]*3)
        self.assertFalse(result['plan']['nand_writes'])
        self.assertLess(len(fake.calls),result['plan']['protocol_call_limit'])

    def test_the_hash_on_the_player_is_measured_on_a_sample_of_the_image(self):
        """The staging check's code runs uncached, so the image is compared over USB as before and the
        player hashes only its first staging_sample_bytes, whose time the result keeps."""
        self.inputs['completion'] = {**self.inputs['completion'],'staging_sample_bytes':65536}
        fake = self.rom(); result,_ = self.run_rom(fake)
        self.assertEqual(result['status'],'writer-staging-verified',result.get('error'))
        image = self.layout['image_address']
        self.assertEqual(fake.staging_runs,[(1,image,len(self.inputs['image'])),(2,image,65536)])
        self.assertEqual(result['plan']['staging_sample_bytes'],65536)
        self.assertTrue(result['image_hash_sample_verified'])
        self.assertIsInstance(result['image_hash_sample_ms'],(int,float))
        # A sample the player hashes otherwise stops before the writer.
        self.layout['physical_write_admitted'] = True
        result,_ = self.run_rom(self.rom('hash-mismatch'),'write')
        self.assertIn('sample hashes differently',result['error'])
        self.assertFalse(result['writer_execution_attempted'])
        self.assertTrue(result['image_ram_verified'],'the image itself was compared over USB first')

    def test_candidate_and_restore_have_one_invocation_without_claiming_readback(self):
        self.layout['physical_write_admitted'] = True  # Synthetic profile only.
        for target in ('candidate','restore'):
            self.inputs.update(target=target,image_name='synthetic-'+target+'.bin')
            fake = self.rom(); clock = FakeClock()
            result,output = self.run_rom(fake,'write',clock)
            self.assertEqual(result['status'],'writer-completion-observed',result.get('error'))
            self.assertEqual(fake.execute_addresses,[self.transport['spl_entry']]+[self.reader['load_address']]*3+[self.plan()['writer_entry']])
            # Asked every writer_poll_ms (plan, stage 4c): the writer's return ends the wait, not its whole length.
            self.assertLess(clock.now,self.layout['writer_wait_ms']/1000)
            self.assertIsNotNone(result['writer_ms'])
            self.assertTrue(result['writer_return_observed'])
            self.assertEqual((output/'writer-result.bin').stat().st_size,1024)
            for flag in ('postwrite_verified','boot_verified','flash_ready'): self.assertFalse(result[flag])
            self.assertFalse(result['writer_result']['freshnessVerified'])

    def test_error_at_every_transfer_stops_without_replay_or_restore(self):
        self.layout['physical_write_admitted'] = True
        baseline = self.rom(); self.run_rom(baseline,'write')
        entry = self.plan()['writer_entry']
        calls = baseline.calls
        invocation = next(i+1 for i,(name,a) in enumerate(calls)
                          if name == 'control_transfer' and a[2] == 4 and a[3]<<16 | a[4] == entry)
        # The asks after the staging check's and the writer's executions (plan, stage 4c): a timeout
        # there is the payload still running, and the session asks again. The SPL's and the
        # metadata payload's executions keep their fixed wait and one request.
        asks, polled, loads = set(), False, 0
        for i,(name,a) in enumerate(calls):
            if name == 'control_transfer' and a[2] == 4:
                target = a[3]<<16 | a[4]
                loads += target == self.reader['load_address']
                polled = target == entry or (target == self.reader['load_address'] and loads > 1)
            elif name == 'control_transfer' and a[2] == 0 and polled:
                asks.add(i+1)
            else:
                polled = False
        self.assertTrue(asks)
        for position in range(1,len(baseline.calls)+1):
            with self.subTest(position=position):
                fake = self.rom('timeout',position)
                result,_ = self.run_rom(fake,'write')
                if position in asks:
                    self.assertEqual(result['status'],'writer-completion-observed',result.get('error'))
                    # A busy ask was a timeout already; at the answering one, one more ask follows.
                    self.assertIn(len(fake.calls),(len(calls),len(calls)+1),'asked on, nothing replayed')
                    self.assertEqual(fake.execute_addresses.count(entry),1)
                    continue
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
        self.assertIn('staged image differs',result['error'])
        self.assertFalse(result['writer_execution_attempted'])
        # The image region failing its pattern on the player stops before the image goes there.
        result,_ = self.run_rom(self.rom('staging-mismatch'),'write')
        self.assertIn('failed its check on the player',result['error'])
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

    def test_interrupted_wait_retains_uncertainty_and_does_not_retry(self):
        self.layout['physical_write_admitted'] = True
        clock = FakeClock(); original = clock.sleep
        def interrupt(seconds):
            if clock.now >= 4: raise KeyboardInterrupt()
            original(seconds)
        clock.sleep = interrupt
        fake = self.rom(staging_busy=0, writer_busy=1000); result,_ = self.run_rom(fake,'write',clock)
        self.assertEqual(result['status'],'writer-outcome-unknown')
        self.assertEqual(fake.calls[-1][1][2],0,'interrupted among the asks')
        self.assertEqual(fake.execute_addresses.count(self.plan()['writer_entry']),1,'never run again')
        self.assertTrue(result['writer_execution_attempted'])
        self.assertFalse(result['writer_return_observed'])

    def test_failed_asks_during_the_write_are_no_outcome_and_the_wait_is_a_time(self):
        """An ask changes nothing on the player: during the writer's wait a failed ask is no answer,
        and asks that fail at once still wait out their interval, so the wait keeps its length."""
        self.layout['physical_write_admitted'] = True
        fake = self.rom(staging_busy=0, writer_busy=10**6); original = fake.call
        def failing(name, args):
            result = original(name, args)
            return -1 if name == 'control_transfer' and args[2] == 0 and fake.polling and result == -7 else result
        fake.call = failing
        clock = FakeClock(); result,_ = self.run_rom(fake,'write',clock)
        self.assertEqual(result['status'],'writer-outcome-unknown')
        self.assertIn('did not return within the settle',result['error'])
        self.assertGreaterEqual(clock.now,self.layout['writer_wait_ms']/1000)

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

    def test_the_profiles_an_installation_history_pins_stay_as_recorded(self):
        """The transport profile and the installer's RAM contract are pinned by every installation's
        recorded evidence (its boot and stock captures, its review): a change refuses the next
        installation of every installed player until fresh evidence is taken. On 2026-10-07 a
        polling setting added to the transport profile did that to the owner's player; these are the
        fingerprints its history of 2026-10-06 pins."""
        contract = writer.installation_review.layout_contract(self.layout_file())
        self.assertEqual(writer.fingerprint(self.transport), '9aac8148f9d03898e74a9dcd62e16e6ee6b6f1a8b3711602b2329abc1251eb58')
        self.assertEqual(writer.fingerprint(contract), '254adf51331ec062b0011e9a5b1d205e2c1c663dd50f33cbc5286d535ee4c5b9')

    def layout_file(self):
        return json.loads((ROOT/f'firmware/installers/v{self.base["version"]}.json').read_text())

    def test_the_asks_come_from_the_completion_profile_apart_from_the_pinned_ones(self):
        """How often and how long the ROM is asked lives in firmware/completion/ (plan, stage 4c): the
        transport and installer profiles, which an installation's recorded evidence pins, stay as
        they were, and a completion profile out of its bounds or for another transport is refused."""
        ram = writer.ram
        good = json.loads((ROOT/'firmware/completion/v2.57.json').read_text())
        self.assertNotIn('completion_poll_ms', self.transport)
        for key in ('writer_poll_ms','staging_check_ms','staging_poll_ms','staging_sample_ms','staging_sample_bytes'):
            self.assertNotIn(key, self.layout)
        plan = self.plan()
        self.assertEqual(plan['completion_profile_sha256'], writer.fingerprint(self.inputs['completion']))
        self.assertEqual((plan['writer_poll_ms'], plan['staging_poll_ms']), (good['writer_poll_ms'], good['staging_poll_ms']))
        (self.root/'completion').mkdir()
        for key, value in [('staging_sample_bytes',65536+64),('staging_sample_bytes',0),('staging_sample_bytes',None),
                           ('staging_sample_ms',500),('writer_poll_ms',50),('completion_poll_ms',2000),
                           ('transport_profile_sha256','0'*64),('version','9.99')]:
            with self.subTest(key=key, value=value), self.assertRaises(ram.probe.ProbeError):
                (self.root/'completion/v2.57.json').write_text(json.dumps({**good, key: value}))
                ram.load_completion(self.base, self.transport, self.root)
        # Its checks must fit the staging budget the installer profile sets.
        self.inputs['completion'] = {**good, 'staging_check_ms': self.layout['staging_budget_ms']-good['staging_sample_ms']}
        with self.assertRaises(ram.probe.ProbeError): self.plan()

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
        with patch.object(writer.ram,'prepare_inputs',side_effect=lambda *a:dict(page_policy=self.policy,payload=b'staging',build_sha256='c'*64)), \
             patch.object(writer.review,'review',return_value=report):
            for target,name in zip(('candidate','restore'),names):
                result = writer.prepare(self.base,self.cpu,self.reader,self.transport,self.root,
                                        diskos,artifacts,target,self.inputs['metadata'],self.root)
                self.assertEqual(result['image'],(artifacts/name).read_bytes())
                self.assertEqual(result['image_name'],name)
                self.assertEqual(result['target'],target)
            (artifacts/names[0]).write_bytes(b'changed after review')
            with self.assertRaises(writer.ram.probe.ProbeError):
                writer.prepare(self.base,self.cpu,self.reader,self.transport,self.root,
                               diskos,artifacts,'candidate',self.inputs['metadata'],self.root)
            binary.write_bytes(b'changed after review')
            with self.assertRaises(writer.ram.probe.ProbeError):
                writer.prepare(self.base,self.cpu,self.reader,self.transport,self.root,
                               diskos,artifacts,'restore',self.inputs['metadata'],self.root)

    # The write's journal reconstructed offline by audit_usb_write.py, as the installer runs it
    # after a write: the staging check's two runs and every ask included (plan, stage 4c).

    def files(self):
        import subprocess
        folders = {name: self.root/name for name in ('build', 'staging', 'diskos', 'artifacts', 'package')}
        for folder in folders.values(): folder.mkdir(exist_ok=True)
        raw = json.dumps(dict(page_policy=self.policy, purpose='metadata')).encode()
        (folders['build']/'build.json').write_bytes(raw); (folders['build']/'identity.bin').write_bytes(self.inputs['payload'])
        self.inputs['build_sha256'] = writer.ram.sha(raw)
        raw = json.dumps(dict(purpose='staging-check', nand_opcodes=[])).encode()
        (folders['staging']/'build.json').write_bytes(raw); (folders['staging']/'identity.bin').write_bytes(self.inputs['staging_payload'])
        self.inputs['staging_build_sha256'] = writer.ram.sha(raw)
        (folders['diskos']/'flash').mkdir(exist_ok=True)
        (folders['diskos']/'flash/disc_spl_lpddr3.bin').write_bytes(self.inputs['spl'])
        (folders['diskos']/'flash/my_write5_dram.bin').write_bytes(self.inputs['writer'])
        (folders['artifacts']/self.inputs['image_name']).write_bytes(self.inputs['image'])
        return folders, subprocess

    def audit(self, folders, subprocess, output, plan):
        (folders['package']/'candidate-write-plan.json').write_text(json.dumps(dict(plan=plan, plan_sha256=writer.fingerprint(plan))))
        (folders['package']/'proposed-installer-profile.json').write_text(json.dumps(self.layout))
        report = self.root/f'audit-{output.name}.json'
        return subprocess.run([sys.executable, str(ROOT/'scripts/deployment/audit_usb_write.py'), '--run', str(output),
                               '--package', str(folders['package']), '--artifacts', str(folders['artifacts']),
                               '--build', str(folders['build']), '--staging-build', str(folders['staging']),
                               '--diskos', str(folders['diskos']), '--output', str(report)], capture_output=True, text=True)

    def test_the_write_audit_reconstructs_the_staging_check_and_the_asks(self):
        self.layout['physical_write_admitted'] = True
        self.inputs['completion'] = {**self.inputs['completion'],'staging_sample_bytes':65536}
        folders, subprocess = self.files()
        fake = self.rom(); result, output = self.run_rom(fake, 'write')
        self.assertEqual(result['status'], 'writer-completion-observed', result.get('error'))
        plan = json.loads((output/'request.json').read_text())['plan']
        done = self.audit(folders, subprocess, output, plan)
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        report = json.loads(done.stdout)
        self.assertEqual(report['status'], 'saved-candidate-write-trace-matches')
        self.assertEqual(report['executions'], [self.transport['spl_entry']] + [self.reader['load_address']]*3 + [plan['writer_entry']])
        # One ask fewer in the journal than the session made: refused.
        rows = (output/'transfers.jsonl').read_text().splitlines()
        polls = [i for i, line in enumerate(rows) if '"poll": true' in line and '"attempt"' in line]
        del rows[polls[0]:polls[0]+2]
        (output/'transfers.jsonl').write_text('\n'.join(rows) + '\n')
        self.assertNotEqual(self.audit(folders, subprocess, output, plan).returncode, 0)


if __name__ == '__main__': unittest.main()
