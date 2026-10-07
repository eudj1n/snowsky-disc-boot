"""Synthetic boot-partition/OTA pages; no firmware, USB or sibling repository."""
import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from deployment import boot_evidence as boot
from test_collect_rootfs import BatchRom
from test_ram_transport import FakeClock
import test_kernel_review


class BootRom(BatchRom):
    def __init__(self,reader,transport,policy,metadata,fault=None,at=0):
        self.scopes=boot.scopes(policy);self.policy=policy;self.metadata=metadata
        super().__init__(reader,transport,self.scopes[0],fault,at)
        self.pages=[]

    def call(self,name,args):
        if name=='control_transfer' and args[2]==4 and args[3]<<16|args[4]==self.reader['load_address']:
            self.scope=self.scopes[0 if self.get(self.reader['load_address'],4)==b'boot' else 1]
            code=super().call(name,args)
            if code: return code
            raw=bytearray(self.get(self.scope['profile']['result_address'],boot.RESULT_BYTES))
            req=self.get(self.scope['profile']['request_address'],boot.REQUEST_BYTES)
            count=struct.unpack_from('<I',req,8)[0]
            for i in range(count):
                q=struct.unpack_from('<12I',req,16+i*48);n=q[8];self.pages.append(n)
                pos=16+i*4428;words=list(struct.unpack_from('<19I',raw,pos))
                data=bytearray(raw[pos+76:pos+76+2176])
                if self.scope['name']=='ota':
                    data[:2048]=b'ota:kernel'.ljust(256,b' ')+bytes([255])*(2048-256)
                    if self.fault=='unknown-selector': data[0]=0
                elif n==self.policy['page_policy']['page']: data[:2048]=self.metadata
                if self.fault=='bad-boot' and n//64==1: data[2048]=0
                if self.fault=='bad-metadata' and n==11: data[0]^=1
                if self.fault=='unstable-main' and q[7]==2: data[0]^=1
                if self.fault=='parity': data[2112]=q[7]%256
                words[12]=zlib.crc32(data)
                if self.fault=='crc': words[12]^=1
                raw[pos:pos+76]=struct.pack('<19I',*words);raw[pos+76:pos+76+2176]=data
            self.put(self.scope['profile']['result_address'],raw)
            return code
        return super().call(name,args)


class BootTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.base=boot.ram.load_profile();self.reader=boot.ram.load_reader_profile(self.base)
        self.cpu=boot.ram.load_probe_profile(self.base);self.transport=boot.ram.load_transport(self.base,self.reader)
        self.policy=boot.load_policy(self.base,self.reader)
        self.policy['profile']['partitions'][0]['size']=2*131072
        fixture=test_kernel_review.KernelReviewTests();fixture.setUp()
        self.metadata=fixture.page({0:{1:2*131072}})
        self.inputs=dict(spl=bytes(8056),payload=b'boot payload',payloads={'uboot':b'boot payload','ota':b'ota! payload'},
                         metadata=self.metadata,build_sha256='b'*64)
        self.library=self.root/'fake-lib';self.library.write_bytes(b'never loaded')
        self.index=0
        sync=patch.object(boot.os,'fsync');sync.start();self.addCleanup(sync.stop)

    def plan(self): return boot.make_plan(self.base,self.cpu,self.reader,self.transport,self.policy,self.inputs)

    def run_rom(self,fault=None,at=0,clock=None,skip_bootstrap=False):
        self.index+=1;out=self.root/str(self.index);fake=BootRom(self.reader,self.transport,self.policy,self.metadata,fault,at)
        plan=self.plan();clock=clock or FakeClock()
        def invoke():
            return boot.acquire(plan,boot.fingerprint(plan),self.base,self.cpu,self.reader,self.transport,
                                self.policy,self.inputs,self.library,out,loader=lambda _:fake,clock=clock,sleep=clock.sleep)
        if skip_bootstrap:
            with patch.object(boot.collector,'bootstrap',side_effect=lambda *a:fake.put(self.reader['load_address'],b'boot')):
                result=invoke()
        else: result=invoke()
        self.assertEqual(json.loads((out/'result.json').read_text()),result)
        return result,out,fake

    def test_capture_exact_scope_and_single_spl_without_active_boot_claim(self):
        r,out,fake=self.run_rom()
        self.assertEqual(r['status'],'boot-evidence-collected',r.get('error'))
        self.assertEqual(r['records_completed'],149)
        self.assertEqual((out/'uboot-good-blocks.bin').stat().st_size,2*131072)
        self.assertEqual(fake.execute_addresses.count(self.transport['spl_entry']),1)
        self.assertEqual(len(fake.execute_addresses),6) # SPL + two marker phases, metadata, two data batches.
        self.assertLessEqual(len(fake.calls),r['plan']['protocol_call_limit'])
        spans=boot.scopes(self.policy)
        self.assertTrue(all(n<128 or spans[1]['first_page']<=n<spans[1]['end_page'] and n%64==0 for n in fake.pages))
        self.assertEqual(set(x['target'] for x in r['ota_selectors'].values()),{'rootfs'})
        for flag in ('writer_execution_attempted','nand_writes','active_boot_verified','flash_ready'):self.assertFalse(r[flag])

    def test_bad_boot_blocks_are_preserved_in_map_and_never_read_as_data(self):
        r,out,fake=self.run_rom('bad-boot')
        self.assertEqual(r['status'],'boot-evidence-collected',r.get('error'))
        self.assertEqual(r['uboot_bad_blocks'],[1]);self.assertEqual(r['uboot_good_blocks'],[0])
        self.assertEqual((out/'uboot-good-blocks.bin').stat().st_size,131072)
        self.assertEqual(fake.pages.count(64),2)
        self.assertFalse(any(65<=n<128 for n in fake.pages))

    def test_unknown_selector_and_parity_do_not_infer_primary_boot(self):
        for fault in ('unknown-selector','parity'):
            r,_,_=self.run_rom(fault)
            self.assertEqual(r['status'],'boot-evidence-collected',r.get('error'))
            self.assertFalse(r['active_boot_verified'])
            if fault=='unknown-selector':self.assertTrue(all(x['target'] is None for x in r['ota_selectors'].values()))

    def test_stale_invalid_ecc_crc_configuration_and_metadata_abort(self):
        for fault in ('nonce','ecc','crc','otp','counters','page','batch-error','incomplete','tail','unstable-main','bad-metadata'):
            with self.subTest(fault=fault):
                r,_,_=self.run_rom(fault)
                self.assertEqual(r['status'],'failed')
                self.assertFalse(r['active_boot_verified'])

    def test_all_collection_transfer_failures_stop_without_replay(self):
        # Right after a batch's execution the host asks the ROM until it answers (plan, stage 4c): a
        # timeout there is the batch still running and the session asks again; anywhere else it stops.
        _,_,baseline=self.run_rom(skip_bootstrap=True)
        calls=baseline.calls
        polls={i+1 for i in range(1,len(calls)) if calls[i][0]==calls[i-1][0]=='control_transfer'
               and calls[i][1][2]==0 and calls[i-1][1][2]==4}
        self.assertTrue(polls)
        for position in range(1,len(baseline.calls)+1):
            with self.subTest(position=position):
                r,_,fake=self.run_rom('timeout',position,skip_bootstrap=True)
                self.assertFalse(r['nand_writes'])
                if position in polls:
                    self.assertEqual(r['status'],'boot-evidence-collected',r.get('error'))
                    self.assertEqual(len(fake.calls),len(baseline.calls)+1,'one more ask, nothing replayed')
                    continue
                self.assertEqual(r['status'],'failed')
                self.assertEqual(len(fake.calls),position)

    def test_deadline_and_interruption_leave_partial_evidence(self):
        clock=FakeClock();original=clock.sleep
        def slow(seconds):original(seconds+180)
        clock.sleep=slow
        r,_,_=self.run_rom(clock=clock)
        self.assertEqual(r['status'],'failed')
        clock=FakeClock()
        def interrupt(_):raise KeyboardInterrupt()
        clock.sleep=interrupt
        r,_,_=self.run_rom(clock=clock)
        self.assertEqual(r['status'],'failed');self.assertIn('KeyboardInterrupt',r['error'])

    def test_changed_plan_payload_or_partition_never_loads_usb(self):
        plan=self.plan();self.inputs['payloads']['ota']=b'changed'
        with self.assertRaises(boot.ram.probe.ProbeError):
            boot.acquire(plan,boot.fingerprint(plan),self.base,self.cpu,self.reader,self.transport,self.policy,self.inputs,
                         self.library,self.root/'no-output',loader=lambda _:self.fail('USB forbidden'))
        self.assertFalse((self.root/'no-output').exists())
        self.policy['profile']['partitions'][0]['size']+=131072
        with self.assertRaises(boot.ram.probe.ProbeError):self.plan()

    def test_selector_is_exact_and_never_defaults_on_erased_or_ambiguous_data(self):
        p=self.policy['profile']
        for token,target in p['selectors'].items():
            self.assertEqual(boot.selector(token.encode().ljust(256,b' '),p)['target'],target)
        for raw in (b'',bytes(256),bytes([255])*256,b'ota:kernel2-extra'.ljust(256,b' '),
                    b'ota:kernel\0'.ljust(256,b' '),b'xota:kernel'.ljust(256,b' '),
                    b'ota:kernel ota:kernel2'.ljust(256,b' ')):
            if not raw:
                with self.assertRaises(boot.ram.probe.ProbeError):boot.selector(raw,p)
            else:self.assertIsNone(boot.selector(raw,p)['target'])

    def test_profile_drift_bounds_and_future_version(self):
        import shutil
        shutil.copytree(ROOT/'firmware',self.root/'firmware')
        base=boot.ram.load_profile();reader=boot.ram.load_reader_profile(base)
        path=self.root/'firmware/boot'/f'v{base["version"]}.json';original=json.loads(path.read_text())
        for key,value in [('page_policy_sha256','0'*64),('session_budget_ms',True),('selector_bytes',4096),
                          ('writes_admitted',True),('selectors',{}),('partitions',[]),('source_pins',{})]:
            path.write_text(json.dumps({**original,key:value}))
            with self.subTest(key=key),self.assertRaises(boot.ram.probe.ProbeError):boot.load_policy(base,reader,self.root/'firmware')
        future={**base,'version':'9.99'}
        path.with_name('v9.99.json').write_text(json.dumps({**original,'version':'9.99'}))
        with patch.object(boot.ram,'load_metadata_policy',return_value=self.policy['page_policy']), \
             patch.object(boot,'load_collector_policy',return_value={'profile':self.policy['buffers']}):
            self.assertEqual(boot.load_policy(future,reader,self.root/'firmware')['profile']['version'],'9.99')

    def test_synthetic_build_inputs_pins_elf_and_compiled_scope_manifest(self):
        diskos=self.root/'diskos';out=self.root/'build';out.mkdir();diskos.mkdir()
        reader=copy.deepcopy(self.reader);cpu=copy.deepcopy(self.cpu)
        for name in reader['source_pins']:
            path=diskos/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(bytes(8056) if name.endswith('.bin') else b'synthetic source')
            reader['source_pins'][name]=boot.ram.sha(path.read_bytes())
        path=diskos/'src/usbboot/usbboot.c';path.parent.mkdir(parents=True);path.write_bytes(b'synthetic usb')
        cpu['reference_source_sha256']=boot.ram.sha(path.read_bytes())
        manifest=dict(schema_version=1,purpose='boot-evidence',firmware_sha256=boot.fingerprint(self.base),
                      reader_sha256=boot.fingerprint(reader),policy_sha256=boot.fingerprint(self.policy),
                      sources=boot.source_pins(),scopes=boot.scopes(self.policy),nand_writes=False,artifacts={})
        for name in ('uboot','ota'):
            dest=out/name;dest.mkdir();payload=b'abcd';elf=bytearray(88);elf[:7]=b'\x7fELF\x01\x01\x01';addr=reader['load_address']
            struct.pack_into('<HHIIIIIHHHHHH',elf,16,2,8,1,addr,52,0,0,52,32,1,0,0,0)
            struct.pack_into('<8I',elf,52,1,84,addr,addr,4,4,7,16);elf[84:]=payload
            for filename,data in [('identity.elf',elf),('identity.bin',payload),('identity_layout.h',b'fixture'),('identity.ld',b'fixture')]:
                (dest/filename).write_bytes(data)
            manifest['artifacts'][name]={n:boot.ram.sha((dest/n).read_bytes()) for n in boot.ARTIFACTS}
        def save(): (out/'build.json').write_text(json.dumps(manifest))
        def prepare(): return boot.prepare(self.base,cpu,reader,self.transport,self.policy,diskos,out,self.metadata)
        save();self.assertEqual(prepare()['payloads'],{'uboot':b'abcd','ota':b'abcd'})
        original=copy.deepcopy(manifest)
        for key,value in [('purpose','rootfs'),('nand_writes',True),('scopes',[]),('sources',{}),('policy_sha256','0'*64)]:
            manifest={**original,key:value};save()
            with self.subTest(key=key),self.assertRaises(boot.ram.probe.ProbeError):prepare()
        manifest=original;save();(out/'ota/identity.bin').write_bytes(b'changed')
        with self.assertRaises(boot.ram.probe.ProbeError):prepare()


if __name__=='__main__':unittest.main()
