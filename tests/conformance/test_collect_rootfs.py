"""Firmware-free batch collector with an in-memory ROM and generated NAND."""
import copy
import ctypes as C
import hashlib
import json
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
import zlib
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from deployment import collect_rootfs as collect
from deployment.collector_policy import load_collector_policy
from deployment import readback
from deployment.build_identity import prepare
from test_ram_transport import Rom, FakeClock
import test_kernel_review


class BatchRom(Rom):
    def __init__(self, reader, config, scope, fault=None, at=0):
        super().__init__(reader, config, fault, at)
        self.scope = scope
        self.ram_bytes, self.sram = bytearray(0x300000), bytearray(0x8000)
        self.batch_count = 0

    def region(self, address):
        if 0xb2400000 <= address < 0xb2408000: return self.sram, address-0xb2400000
        assert 0xa0a00000 <= address < 0xa0d00000
        return self.ram_bytes, address-0xa0a00000

    def put(self, address, data):
        region, offset = self.region(address)
        assert offset+len(data) <= len(region)
        region[offset:offset+len(data)] = data

    def get(self, address, length):
        region, offset = self.region(address)
        return bytes(region[offset:offset+length])

    def call(self, name, args):
        if name == 'control_transfer' and args[2] == 4 and args[3]<<16 | args[4] == self.reader['load_address']:
            self.calls.append((name,args)); self.events.append(name)
            if len(self.calls) == self.at: return -7
            self.execute_addresses.append(self.reader['load_address']); self.batch_count += 1
            raw = self.get(self.scope['profile']['request_address'], collect.REQUEST_BYTES)
            magic, version, count, reserved = struct.unpack_from('<4I', raw)
            assert (magic,version,reserved) == (0x3151424e,1,0) and 1 <= count <= 64
            results = []
            for i in range(count):
                q = struct.unpack_from('<12I', raw, 16+i*48)
                page = q[8]
                assert self.scope['first_page'] <= page < self.scope['end_page']
                marker = 0 if self.fault == 'bad-marker' and page//64 == 80 else 255
                if self.fault == 'ambiguous' and q[7] == 2: marker = 0
                if self.fault == 'changed-marker' and self.batch_count > 1: marker = 0
                data = struct.pack('<I', page)*512+bytes([marker])+bytes([255])*127
                words = [0x3153524e,1,0x454e4f44,0,*q[3:7],q[7],2,page,2176,zlib.crc32(data),0x120b,0x38,16,0,2,10]
                if self.fault == 'nonce': words[4] ^= 1
                if self.fault == 'ecc': words[16] = 0xf0
                if self.fault == 'crc': words[12] ^= 1
                if self.fault == 'otp': words[15] = 0x50
                if self.fault == 'counters': words[18] = 11
                if self.fault == 'page': words[10] += 1
                results.append(struct.pack('<19I', *words)+data+bytes(4352-2176))
            output = (struct.pack('<4I',0x3152424e,1,count,1 if self.fault == 'batch-error' else 0)+b''.join(results)).ljust(collect.RESULT_BYTES,b'\0')
            if self.fault == 'incomplete': output = bytes(4)+output[4:]
            if self.fault == 'tail': output = output[:-1]+b'X'
            self.put(self.scope['profile']['result_address'], output)
            return 0
        return super().call(name,args)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = collect.ram.load_profile()
        self.cpu = collect.ram.load_probe_profile(self.base)
        self.reader = collect.ram.load_reader_profile(self.base)
        self.config = collect.ram.load_transport(self.base,self.reader)
        self.policy = collect.ram.load_metadata_policy(self.base,self.reader)
        self.scope = load_collector_policy(self.base,self.reader,'rootfs-probe')
        self.inputs = dict(spl=bytes(8056),payload=b'synthetic payload',build_sha256='a'*64,
                           page_policy=self.policy,collector_policy=self.scope)
        fixture=test_kernel_review.KernelReviewTests();fixture.setUp();self.metadata=fixture.page()
        self.library=self.root/'lib';self.library.write_bytes(b'fake')
        self.index=0
        sync=patch.object(collect.os,'fsync');sync.start();self.addCleanup(sync.stop)

    def plan(self):
        return collect.make_plan(self.base,self.cpu,self.reader,self.config,self.inputs,self.metadata)

    def run_fake(self, fault=None, at=0, skip_bootstrap=False):
        fake=BatchRom(self.reader,self.config,self.scope,fault,at)
        plan=self.plan();clock=FakeClock();self.index+=1;output=self.root/str(self.index)
        def invoke():
            return collect.acquire(plan,collect.fingerprint(plan),self.base,self.cpu,self.reader,
                self.config,self.inputs,self.metadata,self.library,output,loader=lambda _:fake,clock=clock,sleep=clock.sleep)
        if skip_bootstrap:
            with patch.object(collect,'bootstrap'): result=invoke()
        else: result=invoke()
        return result,output,fake

    def test_probe_exact_bounds_records_and_single_bootstrap(self):
        result,path,fake=self.run_fake()
        self.assertEqual(result['status'],'rootfs-probe-collected')
        self.assertEqual(result['batch_executions'],3)
        self.assertEqual(result['records_completed'],132)
        self.assertEqual(len(fake.calls),297)
        self.assertEqual(result['plan']['protocol_call_limit'],297)
        self.assertEqual(fake.execute_addresses,[self.config['spl_entry']]+[self.reader['load_address']]*3)
        expected=b''.join(struct.pack('<I',page)*512 for page in range(5120,5248))
        self.assertEqual((path/'logical-image.bin').read_bytes(),expected)
        self.assertEqual((path/'records.bin').stat().st_size,132*4476)
        self.assertEqual(result['logical_image_sha256'],hashlib.sha256(expected).hexdigest())
        self.assertFalse(result['image_match_verified']);self.assertFalse(result['flash_ready'])

    def test_every_batch_usb_boundary_stops_without_replay(self):
        for position in range(1,54):
            with self.subTest(position=position):
                result,path,fake=self.run_fake('timeout',position,True)
                self.assertEqual(result['status'],'failed')
                self.assertEqual(len(fake.calls),position)
                self.assertEqual(result['records_completed'],0)

    def test_ddr_calibration_failure_preserves_raw_diagnostic_and_never_starts_reader(self):
        diagnostic=struct.pack('<5I',0xd1a6c0de,9,30,0x12,1)
        original=BatchRom.put
        def inject(rom,address,data):
            original(rom,address,diagnostic if address==rom.config['diagnostic_address'] else data)
        with patch.object(BatchRom,'put',inject):result,path,fake=self.run_fake()
        self.assertEqual(result['status'],'failed')
        self.assertIn('SPL DDR diagnostic failed',result['error'])
        self.assertEqual(result['records_completed'],0)
        self.assertEqual(result['batch_executions'],0)
        self.assertFalse(result['page_execution_attempted'])
        self.assertFalse(result['ram_roundtrip_passed'])
        self.assertEqual(fake.execute_addresses,[self.config['spl_entry']])
        self.assertEqual(len(fake.calls),12)
        self.assertEqual((path/'read-012.bin').read_bytes(),diagnostic)
        self.assertEqual(result['cleanup_errors'],[])
        self.assertIn('release_interface',fake.events)
        self.assertFalse((path/'records.bin').exists())
        self.assertFalse((path/'logical-image.bin').exists())

    def test_invalid_results_marker_ambiguity_and_no_spare_probe_block(self):
        for fault in ('nonce','ecc','crc','otp','counters','page','batch-error','incomplete','tail',
                      'ambiguous','bad-marker','changed-marker'):
            with self.subTest(fault=fault):
                result,path,fake=self.run_fake(fault,skip_bootstrap=True)
                self.assertEqual(result['status'],'failed')
                self.assertLessEqual(result['batch_executions'],2)
                self.assertFalse(result['flash_ready'])

    def test_full_mode_small_fixture_matches_independent_readback(self):
        self.scope.update(mode='rootfs',end_page=5120+4*64,logical_blocks=2)
        self.policy['writer'].update(logical_blocks=2,bad_block_reserve=2)
        result,path,fake=self.run_fake('bad-marker',skip_bootstrap=True)
        self.assertEqual(result['status'],'rootfs-collected')
        expected=b''.join(struct.pack('<I',page)*512 for page in range(5184,5312))
        image=self.root/'expected';image.write_bytes(expected)
        plan=readback.make_plan(self.base,self.reader,self.policy,self.metadata,hashlib.sha256(expected).hexdigest())
        verified=readback.verify(path/'records.bin',image,plan,self.base,self.reader,self.policy,self.metadata,
                                 bytes.fromhex(result['nonce_hex']))
        self.assertEqual(verified['logical_to_physical'],[81,82])

    def test_preflight_plan_drift_does_not_load_usb_or_create_output(self):
        plan=self.plan();self.inputs['payload']=b'changed'
        with self.assertRaises(collect.ram.probe.ProbeError):
            collect.acquire(plan,collect.fingerprint(plan),self.base,self.cpu,self.reader,self.config,
                            self.inputs,self.metadata,self.library,self.root/'no',loader=lambda _:self.fail('USB'))
        self.assertFalse((self.root/'no').exists())

    def test_later_batch_failure_preserves_only_completed_records(self):
        for position, completed, image_bytes in [(95,4,0),(148,68,131072)]:
            result,path,fake=self.run_fake('timeout',position,True)
            self.assertEqual(result['status'],'failed')
            self.assertEqual(len(fake.calls),position)
            self.assertEqual(result['records_completed'],completed)
            self.assertEqual((path/'records.bin').stat().st_size,completed*4476)
            self.assertEqual((path/'logical-image.bin').stat().st_size,image_bytes)

    def test_future_firmware_requires_rebound_audits(self):
        profiles=self.root/'future';shutil.copytree(ROOT/'firmware',profiles)
        base=dict(self.base,version='9.99');reader=dict(self.reader,version='9.99')
        kernel=dict(self.policy['kernel'],version='9.99')
        page=dict(self.policy['profile'],version='9.99',reader_profile_sha256=collect.fingerprint(reader),
                  kernel_profile_sha256=collect.fingerprint(kernel))
        for folder,value in [('kernels',kernel),('pages',page)]:
            (profiles/folder/'v9.99.json').write_text(json.dumps(value))
        policy=collect.ram.load_metadata_policy(base,reader,profiles)
        scope=dict(self.scope['profile'],version='9.99',policy_sha256=collect.fingerprint(policy))
        path=profiles/'collectors/v9.99.json';path.write_text(json.dumps(scope))
        self.assertEqual(load_collector_policy(base,reader,'rootfs-probe',profiles)['first_page'],5120)
        scope['policy_sha256']=self.scope['profile']['policy_sha256'];path.write_text(json.dumps(scope))
        with self.assertRaises(ValueError):load_collector_policy(base,reader,'rootfs-probe',profiles)

    def test_deadline_and_call_budget_stop(self):
        with patch.object(collect.Journal,'begin',side_effect=collect.ram.probe.ProbeError('budget')):
            result,_,fake=self.run_fake()
        self.assertEqual(result['status'],'failed');self.assertFalse(fake.calls)
        self.scope['session_budget_ms']=1
        result,_,fake=self.run_fake(skip_bootstrap=True)
        self.assertEqual(result['status'],'failed');self.assertEqual(result['batch_executions'],1)

    def test_profile_bounds_pins_and_distinct_build_macros(self):
        profiles=self.root/'profiles';shutil.copytree(ROOT/'firmware',profiles)
        original=json.loads((profiles/'collectors'/f'v{self.base["version"]}.json').read_text())
        for key,value in [('request_address',self.reader['load_address']),('result_address',0xa2000000),
                          ('request_address',original['result_address']),('probe_blocks',3),
                          ('physical_qualified',True),('policy_sha256','0'*64),('full_budget_ms',3600001)]:
            path=profiles/'collectors'/f'v{self.base["version"]}.json'
            path.write_text(json.dumps(dict(original,**{key:value})))
            with self.subTest(key=key),self.assertRaises(ValueError):
                load_collector_policy(self.base,self.reader,'rootfs-probe',profiles)
        prepare(self.reader,self.root,self.policy,self.scope)
        text=(self.root/'identity_layout.h').read_text()
        self.assertIn('#define ROOTFS_FIRST_PAGE 0x1400',text)
        self.assertIn('#define ROOTFS_END_PAGE 0x1480',text)
        self.assertNotIn('METADATA_PAGE',text)
        full=load_collector_policy(self.base,self.reader,'rootfs')
        self.assertEqual(full['end_page'],912*64)


if __name__ == '__main__': unittest.main()
