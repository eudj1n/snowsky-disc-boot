"""In-memory ROM/libusb model only. No firmware, USB library, Docker or device."""
import ctypes as C
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from deployment import ram_transport as ram
from test_rom_probe import FakeUsb, Function


class FakeClock:
    def __init__(self): self.now = 0
    def __call__(self): return self.now
    def sleep(self, seconds): self.now += seconds


class Rom(FakeUsb):
    def __init__(self, reader, config, fault=None, at=0, matches=1):
        super().__init__(matches=matches)
        self.reader, self.config, self.fault, self.at = reader, config, fault, at
        self.calls, self.memory, self.address, self.length = [], {}, 0, 0
        self.execute_addresses = []
        for name in ('claim_interface', 'release_interface', 'bulk_transfer'):
            setattr(self, 'libusb_'+name, Function(lambda *args, name=name: self.call(name, args)))

    def address_key(self, address):
        if self.fault == 'alias' and self.reader['stack_bottom'] <= address < self.reader['stack_top']:
            return address - self.reader['stack_bottom'] + self.reader['load_address']
        return address

    def put(self, address, data):
        for i, byte in enumerate(data): self.memory[self.address_key(address+i)] = byte

    def get(self, address, length):
        return bytes(self.memory.get(self.address_key(address+i), 0) for i in range(length))

    def call(self, name, args):
        if name in ('claim_interface', 'release_interface'):
            self.events.append(name)
            assert args[1] == 0
            return -6 if self.fault == name else 0
        if name not in ('control_transfer', 'bulk_transfer'):
            return super().call(name, args)
        self.events.append(name)
        self.calls.append((name, args))
        fail = len(self.calls) == self.at
        if name == 'control_transfer':
            _, direction, request, high, low, data, length, timeout = args
            assert 0 < timeout <= 10000
            assert request in (0, 1, 2, 4)
            parameter = high<<16 | low
            if fail: return -7
            if request == 0:
                assert (direction, parameter, length) == (0xc0, 0, 8)
                reply = b'XXXXX' if self.fault == 'cpu' else b'X2000'
                C.memmove(data, reply, 5)
                return 5
            assert direction == 0x40 and length == 0
            if request == 1: self.address = parameter
            if request == 2: self.length = parameter
            if request == 4:
                self.execute_addresses.append(parameter)
                if parameter == self.config['spl_entry']:
                    diag = (0xd1a6c0de, 9, 0, 0, 0)
                    if self.fault == 'ddr': diag = (0xd1a6c0de, 9, 30, 8, 1)
                    if self.fault != 'missing-ddr':
                        self.put(self.config['diagnostic_address'], struct.pack('<5I', *diag))
                else:
                    assert parameter == self.reader['load_address']
                    q = struct.unpack('<12I', self.get(self.reader['request_address'], 48))
                    words = [0x3153524e, 1, 0x454e4f44, 0, *q[3:7], q[7], q[2], q[8],
                             0, 0, 0x563412, 0, 16, 0, 1, 5]
                    payload = bytes(4352)
                    if q[2] == 2:
                        from test_kernel_review import KernelReviewTests
                        fixture = KernelReviewTests(); fixture.setUp()
                        main = fixture.page()
                        if self.fault == 'metadata-layout': main = b'bad!'+main[4:]
                        data = main+bytes([255])*128
                        words[11:19] = [len(data), zlib.crc32(data), 0x00120b, 0x38, 16, 0, 2, 10]
                        payload = data+bytes(4352-len(data))
                        if self.fault == 'metadata-crc': words[12] ^= 1
                        if self.fault == 'metadata-ecc': words[16] = 0xf0
                        if self.fault == 'metadata-reserved-ecc': words[16] = 0x90
                        if self.fault == 'metadata-otp': words[15] = 0x50
                        if self.fault == 'metadata-page': words[10] += 1
                    if self.fault == 'stale': words[4] ^= 1
                    if self.fault == 'incomplete': words[2] = 0
                    if self.fault == 'nand-error': words[3] = 2
                    if self.fault == 'bad-id': words[13] = 0
                    if self.fault == 'counters': words[18] = 7
                    self.put(self.reader['result_address'], struct.pack('<19I', *words) + payload)
            return 0
        _, endpoint, data, length, transferred, timeout = args
        assert endpoint in (1, 129) and 0 < length <= 65536 and 0 < timeout <= 5000
        count = length
        if fail: count = 0 if self.fault == 'zero' else length//2
        transferred._obj.value = count
        assert self.length == length  # ROM length must be set for OUT as well as IN.
        if endpoint == 1:
            self.put(self.address, bytes(data[:count]))
        else:
            assert self.length == length
            raw = self.get(self.address, count)
            if self.fault == 'sram' and self.address == self.config['spl_load_address']:
                raw = bytes([raw[0]^1]) + raw[1:]
            C.memmove(data, raw, count)
        return -7 if fail and self.fault not in ('short', 'zero') else 0


class RamTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = ram.load_profile((ROOT/'firmware/active-version').read_text().strip())
        self.reader = ram.load_reader_profile(self.base)
        self.cpu = ram.load_probe_profile(self.base)
        self.config = ram.load_transport(self.base, self.reader)
        self.inputs = dict(spl=bytes(8056), payload=b'synthetic executable', build_sha256='a'*64)
        self.library = self.root/'fake-library'
        self.library.write_bytes(b'never dynamically loaded')
        self.index = 0
        # Exercise durable-journal calls but avoid thousands of test-only disk flushes.
        sync = patch.object(ram.os, 'fsync')
        self.fsync = sync.start()
        self.addCleanup(sync.stop)

    def intent(self, mode='ram-check'):
        return ram.plan(self.base, self.cpu, self.reader, self.config, self.inputs, mode)

    def run_rom(self, fake, mode='ram-check', clock=None):
        self.index += 1
        output = self.root/str(self.index)
        intent = self.intent(mode)
        clock = clock or FakeClock()
        result = ram.acquire(intent, ram.fingerprint(intent), self.cpu, self.reader, self.config,
            self.inputs, self.library, output, loader=lambda path:fake, clock=clock, sleep=clock.sleep)
        self.assertEqual(json.loads((output/'result.json').read_text()), result)
        return result, output

    def metadata(self):
        self.inputs['page_policy'] = ram.load_metadata_policy(self.base, self.reader)

    def test_metadata_success_with_fresh_request_and_separate_outputs(self):
        self.metadata()
        fake = Rom(self.reader, self.config)
        result, output = self.run_rom(fake, 'metadata')
        self.assertEqual(result['status'], 'nand-metadata-observed')
        self.assertTrue(result['page_execution_attempted'])
        self.assertFalse(result['identity_execution_attempted'])
        self.assertFalse(result['flash_ready'])
        self.assertEqual(result['page_observation']['page'], 11)
        self.assertEqual((output/'metadata-main.bin').stat().st_size, 2048)
        self.assertEqual((output/'metadata-oob.bin').read_bytes(), bytes([255])*128)
        request = (output/'metadata-request.bin').read_bytes()
        self.assertEqual(struct.unpack_from('<I', request, 8)[0], 2)
        self.assertEqual(struct.unpack_from('<I', request, 32)[0], 11)
        self.assertEqual(result['plan']['nand_commands'], ['0x9f', '0x0f', '0x13', '0x0b'])
        self.assertFalse(result['metadata_layout']['active_boot_verified'])

    def test_metadata_rejects_bad_page_ecc_otp_crc_and_layout(self):
        self.metadata()
        for fault in ('metadata-crc', 'metadata-ecc', 'metadata-reserved-ecc', 'metadata-otp',
                      'metadata-page', 'metadata-layout', 'stale', 'incomplete', 'nand-error', 'bad-id', 'counters'):
            with self.subTest(fault=fault):
                fake = Rom(self.reader, self.config, fault)
                result, output = self.run_rom(fake, 'metadata')
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(fake.execute_addresses, [self.config['spl_entry'], self.reader['load_address']])
                self.assertFalse(result['flash_ready'])
                self.assertEqual((output/'metadata-main.bin').exists(), fault == 'metadata-layout')

    def test_metadata_transfer_faults_never_replay(self):
        self.metadata()
        fake = Rom(self.reader, self.config)
        self.run_rom(fake, 'metadata')
        for position in range(1, len(fake.calls)+1):
            bad = Rom(self.reader, self.config, 'timeout', position)
            result, _ = self.run_rom(bad, 'metadata')
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(len(bad.calls), position)

    def test_metadata_policy_or_wrong_mode_is_refused_before_usb(self):
        with self.assertRaises(ram.probe.ProbeError): self.intent('metadata')
        self.metadata()
        with self.assertRaises(ram.probe.ProbeError): self.intent('identity')
        intent = self.intent('metadata')
        self.inputs['page_policy']['page'] += 1
        with self.assertRaises(ram.probe.ProbeError):
            ram.acquire(intent, ram.fingerprint(intent), self.cpu, self.reader, self.config,
                        self.inputs, self.library, self.root/'not-created',
                        loader=lambda _: self.fail('USB must not load'))
        self.assertFalse((self.root/'not-created').exists())

    def test_offline_plan_mode_and_source_pins(self):
        with patch.object(C, 'CDLL', side_effect=AssertionError('No USB')):
            p = self.intent()
        self.assertIsNone(p['payload_entry'])
        self.assertEqual(p['nand_commands'], [])
        self.assertEqual(p['vendor_requests'], [0, 1, 2, 4])
        self.assertNotEqual(ram.fingerprint(p), ram.fingerprint(self.intent('identity')))
        self.assertIn('scripts/deployment/ram_transport.py', p['transport_sources_sha256'])

    def test_ram_check_executes_only_spl_with_complete_trace(self):
        fake = Rom(self.reader, self.config)
        result, output = self.run_rom(fake)
        self.assertEqual(result['status'], 'ram-roundtrip-observed')
        self.assertEqual(fake.execute_addresses, [self.config['spl_entry']])
        self.assertFalse(result['identity_execution_attempted'])
        self.assertFalse(result['hardware_qualified'])
        self.assertEqual(fake.events[-4:], ['release_interface', 'close', 'free_device_list', 'exit'])
        events = [json.loads(line) for line in (output/'transfers.jsonl').read_text().splitlines()]
        self.assertEqual(sum(e['phase']=='attempt' for e in events), len(fake.calls))
        self.assertEqual(sum(e['phase']=='return' for e in events), len(fake.calls))
        self.assertTrue(self.fsync.called)
        for e in events:
            if 'file' in e: self.assertEqual(ram.sha((output/e['file']).read_bytes()), e['sha256'])
        for name, address, length in ram.regions(self.reader):
            pattern = ram.hashlib.shake_256(bytes.fromhex(result['nonce_hex'])+struct.pack('<I',address)).digest(length)
            self.assertEqual(fake.get(address,length), bytes(x^255 for x in pattern))

    def test_identity_success_and_nonce_isolation(self):
        results = []
        for _ in range(2):
            fake = Rom(self.reader, self.config)
            result, output = self.run_rom(fake, 'identity')
            self.assertEqual(result['status'], 'nand-identity-observed')
            self.assertEqual(result['identity']['observed_id'], 0x563412)
            self.assertEqual(fake.execute_addresses, [self.config['spl_entry'], self.reader['load_address']])
            self.assertEqual((output/'identity-request.bin').read_bytes()[12:28].hex(), result['nonce_hex'])
            results.append(result)
        self.assertNotEqual(results[0]['nonce_hex'], results[1]['nonce_hex'])
        self.assertNotEqual(results[0]['session_id'], results[1]['session_id'])

    def test_error_at_every_protocol_boundary_aborts_without_replay(self):
        baseline = Rom(self.reader, self.config)
        self.run_rom(baseline, 'identity')
        for position in range(1, len(baseline.calls)+1):
            with self.subTest(position=position):
                fake = Rom(self.reader, self.config, 'timeout', position)
                result, output = self.run_rom(fake, 'identity')
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(len(fake.calls), position)
                self.assertEqual(fake.events[-4:], ['release_interface', 'close', 'free_device_list', 'exit'])
                last = json.loads((output/'transfers.jsonl').read_text().splitlines()[-1])
                self.assertEqual(last['code'], -7)
                if fake.calls[-1][0] == 'bulk_transfer' and fake.calls[-1][1][1] == 129:
                    self.assertGreater((output/last['file']).stat().st_size, 0)

    def test_zero_short_and_error_with_partial_bulk_never_continue(self):
        baseline = Rom(self.reader, self.config)
        self.run_rom(baseline)
        positions = [i+1 for i,(name,_) in enumerate(baseline.calls) if name=='bulk_transfer']
        for fault in ('short', 'zero'):
            for position in positions:
                with self.subTest(fault=fault, position=position):
                    fake = Rom(self.reader, self.config, fault, position)
                    result, _ = self.run_rom(fake)
                    self.assertEqual(result['status'], 'failed')
                    self.assertEqual(len(fake.calls), position)

    def test_preflight_and_ram_failures_do_not_execute_identity(self):
        for fault in ('cpu', 'sram', 'ddr', 'missing-ddr', 'alias', 'claim_interface'):
            with self.subTest(fault=fault):
                fake = Rom(self.reader, self.config, fault)
                result, _ = self.run_rom(fake, 'identity')
                self.assertEqual(result['status'], 'failed')
                self.assertFalse(result['identity_execution_attempted'])
                self.assertNotIn(self.reader['load_address'], fake.execute_addresses)
                if fault in ('cpu', 'sram', 'claim_interface'): self.assertFalse(fake.execute_addresses)

    def test_result_failures_are_retained_not_accepted(self):
        for fault in ('stale', 'incomplete', 'nand-error', 'bad-id', 'counters'):
            with self.subTest(fault=fault):
                result, output = self.run_rom(Rom(self.reader, self.config, fault), 'identity')
                self.assertEqual(result['status'], 'failed')
                self.assertTrue(result['identity_execution_attempted'])
                event = json.loads((output/'transfers.jsonl').read_text().splitlines()[-1])
                self.assertEqual((output/event['file']).stat().st_size, ram.RESULT_BYTES)

    def test_unique_target_cleanup_and_release_failure(self):
        for count in (0, 2):
            fake = Rom(self.reader, self.config, matches=count)
            result, _ = self.run_rom(fake)
            self.assertEqual(result['status'], 'failed')
            self.assertFalse(fake.calls)
            self.assertNotIn('open', fake.events)
        fake = Rom(self.reader, self.config, 'release_interface')
        result, _ = self.run_rom(fake)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(result['ram_roundtrip_passed'])
        self.assertTrue(result['cleanup_errors'])
        self.assertEqual(fake.events[-3:], ['close', 'free_device_list', 'exit'])

    def test_deadline_bounds_timeout_and_stops_next_request(self):
        fake = Rom(self.reader, self.config)
        clock = FakeClock()
        original = fake.call
        def delayed(name, args):
            result = original(name, args)
            if name == 'control_transfer': clock.now += 59.8
            return result
        fake.call = delayed
        result, _ = self.run_rom(fake, clock=clock)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(fake.calls), 2)
        self.assertLessEqual(fake.calls[-1][1][-1], 200)
        self.assertFalse(fake.execute_addresses)

    def test_plan_mismatch_existing_output_and_modified_inputs_never_load(self):
        intent = self.intent()
        output = self.root/'reserved'; output.mkdir()
        for approved, dest, inputs in [('0'*64, self.root/'bad', self.inputs),
                (ram.fingerprint(intent), output, self.inputs),
                (ram.fingerprint(intent), self.root/'changed', {**self.inputs, 'payload':b'changed'})]:
            with self.subTest(dest=dest), self.assertRaises((ram.probe.ProbeError, FileExistsError)):
                ram.acquire(intent, approved, self.cpu, self.reader, self.config, inputs,
                            self.library, dest, loader=lambda path:self.fail('No library load'))

    def test_journal_failure_prevents_unrecorded_command(self):
        fake = Rom(self.reader, self.config)
        with patch.object(ram.Journal, 'begin', side_effect=OSError('disk full')):
            result, _ = self.run_rom(fake)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(fake.calls)
        self.assertEqual(fake.events[-4:], ['release_interface', 'close', 'free_device_list', 'exit'])

    def test_ranges_and_execution_scope_are_checked_before_io(self):
        output = self.root/'range'; output.mkdir()
        fake = Rom(self.reader, self.config)
        journal = ram.Journal(output)
        self.addCleanup(journal.file.close)
        session = ram.Session(fake, self.cpu, self.config, {'plan':self.intent()}, journal)
        for call in (lambda:session.read(0xb3440000,4), lambda:session.read(self.reader['result_address'],65536),
                     lambda:session.write(self.reader['load_address']-1,b'x'),
                     lambda:session.read(self.reader['load_address'],0),
                     lambda:session.execute(self.reader['load_address'],'identity_execution_attempted'),
                     lambda:session.execute(self.config['spl_entry']+4,'spl_execution_attempted'),
                     lambda:session.control(0x13)):
            with self.assertRaises(ram.probe.ProbeError): call()
        self.assertFalse(fake.calls)
        self.assertEqual(journal.sequence,0)

    def test_each_download_sets_address_and_length_before_bulk_out(self):
        fake = Rom(self.reader, self.config)
        result, _ = self.run_rom(fake, 'identity')
        self.assertEqual(result['status'], 'nand-identity-observed')
        downloads = 0
        for i,(name,args) in enumerate(fake.calls):
            if name != 'bulk_transfer' or args[1] != 1:
                continue
            downloads += 1
            previous = fake.calls[i-2:i]
            self.assertEqual([p[0] for p in previous], ['control_transfer','control_transfer'])
            self.assertEqual([p[1][2] for p in previous], [1,2])
            length_request = previous[1][1]
            self.assertEqual((length_request[3]<<16)|length_request[4], args[3])
        self.assertEqual(downloads, 12)  # SPL, two passes over four regions, three identity inputs.

    def test_missing_download_length_is_rejected_by_rom_model(self):
        fake = Rom(self.reader,self.config)
        def broken_write(session,address,data):
            session.control(1,address)
            session.bulk(len(data),data)
        with patch.object(ram.Session,'write',broken_write):
            result, _ = self.run_rom(fake)
        self.assertEqual(result['status'],'failed')
        self.assertIn('AssertionError', result['error'])
        self.assertFalse(result['spl_execution_attempted'])

    def test_interruption_retains_attempt_and_cleans_resources(self):
        fake = Rom(self.reader,self.config)
        fake.libusb_bulk_transfer = Function(lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()))
        result, output = self.run_rom(fake)
        self.assertEqual(result['status'],'failed')
        self.assertFalse(result['spl_execution_attempted'])
        event = json.loads((output/'transfers.jsonl').read_text().splitlines()[-1])
        self.assertEqual((event['phase'],event['kind']),('attempt','bulk'))
        self.assertEqual(fake.events[-4:],['release_interface','close','free_device_list','exit'])

    def test_transport_profiles_and_new_version(self):
        (self.root/'transports').mkdir()
        def load(p, base=self.base, reader=self.reader):
            (self.root/'transports'/f'v{p["version"]}.json').write_text(json.dumps(p))
            return ram.load_transport(base, reader, self.root)
        future = {**self.base, 'version':'9.99', 'rootfs_sha256':'d'*64}
        reader = {**self.reader, 'version':'9.99', 'rootfs_sha256':'d'*64}
        p = {**self.config, 'version':'9.99', 'rootfs_sha256':'d'*64, 'reader_profile_sha256':ram.fingerprint(reader)}
        self.assertEqual(load(p, future, reader), p)
        for key, value in [('reader_profile_sha256','0'*64), ('rootfs_sha256','0'*64),
                           ('spl_load_address',0xa0c00000), ('spl_entry',True), ('spl_bytes',20000),
                           ('diagnostic_address',0xb24017d0), ('control_timeout_ms',0),
                           ('bulk_timeout_ms',600000), ('session_budget_ms',True),
                           ('physical_qualified',True), ('protocol','cloner')]:
            with self.subTest(key=key), self.assertRaises(ram.probe.ProbeError):
                load({**self.config, key:value})

    def test_cli_requires_explicit_mode_and_acquisition_arguments(self):
        command = [sys.executable, str(ROOT/'scripts/deployment/ram_transport.py')]
        for args in (['plan'], ['acquire','--mode','ram-check','--build','missing','--diskos','missing'],
                     ['plan','--mode','identity','--build','missing','--diskos','missing','--libusb','missing']):
            p = subprocess.run(command+args, capture_output=True, timeout=5)
            self.assertEqual(p.returncode, 2)
            self.assertIn(b'usage:', p.stderr)


class RamInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.build, self.diskos = self.root/'build', self.root/'diskos'
        self.build.mkdir(); self.diskos.mkdir()
        self.base = ram.load_profile((ROOT/'firmware/active-version').read_text().strip())
        self.reader = ram.load_reader_profile(self.base)
        self.cpu = ram.load_probe_profile(self.base)
        self.config = ram.load_transport(self.base, self.reader)
        for name in self.reader['source_pins']:
            path = self.diskos/name;path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(bytes(8056) if name.endswith('.bin') else b'synthetic source')
            self.reader['source_pins'][name] = ram.probe.digest(path)
        usb = self.diskos/'src/usbboot/usbboot.c';usb.parent.mkdir(parents=True)
        usb.write_bytes(b'synthetic USB source')
        self.cpu['reference_source_sha256'] = ram.probe.digest(usb)
        payload = b'abcd'
        elf = bytearray(88);elf[:7] = b'\x7fELF\x01\x01\x01'
        addr = self.reader['load_address']
        struct.pack_into('<HHIIIIIHHHHHH', elf, 16, 2, 8, 1, addr, 52, 0, 0, 52, 32, 1, 0, 0, 0)
        struct.pack_into('<8I', elf, 52, 1, 84, addr, addr, 4, 4, 7, 16)
        elf[84:] = payload
        for name, data in [('identity.bin',payload), ('identity.elf',elf),
                           ('identity_layout.h',b'fixture'), ('identity.ld',b'fixture')]:
            (self.build/name).write_bytes(data)
        self.manifest = dict(schema_version=1, version=self.base['version'],
            firmware_profile_sha256=ram.fingerprint(self.base), reader_profile_sha256=ram.fingerprint(self.reader),
            reader_profile=self.reader, physical_qualified=False, device_access_performed=False,
            nand_opcodes=['0x9f','0x0f'], entry=addr, bytes=4,
            source_sha256={name:ram.probe.digest(ROOT/name) for name in ram.source_paths()},
            artifacts={p.name:ram.probe.digest(p) for p in self.build.iterdir()})
        self.save()

    def save(self):
        (self.build/'build.json').write_text(json.dumps(self.manifest))

    def prepare(self, mode='identity'):
        return ram.prepare_inputs(self.base, self.cpu, self.reader, self.config, self.build, self.diskos, mode)

    def test_metadata_build_cannot_substitute_identity_or_stale_policy(self):
        # Profile loading is tested separately; external bytes here are synthetic.
        policy = {'page': 11, 'fixture': 'synthetic reviewed policy'}
        with patch.object(ram, 'load_metadata_policy', return_value=policy):
            with self.assertRaises(ram.probe.ProbeError): self.prepare('metadata')
            self.manifest.update(purpose='metadata', page_policy=policy,
                                 nand_opcodes=['0x9f', '0x0f', '0x13', '0x0b'])
            self.save()
            self.assertEqual(self.prepare('metadata')['page_policy'], policy)
            with self.assertRaises(ram.probe.ProbeError): self.prepare('identity')
            self.manifest['page_policy'] = dict(policy, page=12)
            self.save()
            with self.assertRaises(ram.probe.ProbeError): self.prepare('metadata')

    def test_synthetic_inputs_are_sufficient_and_source_pins_match(self):
        with patch.object(C, 'CDLL', side_effect=AssertionError('No USB')):
            p = self.prepare()
        self.assertEqual(p['payload'], b'abcd')
        self.assertEqual(p['spl'], bytes(8056))
        self.assertEqual(p['build_sha256'], ram.probe.digest(self.build/'build.json'))

    def test_modified_or_symlink_artifact_rejected(self):
        path = self.build/'identity.bin'
        path.write_bytes(b'efgh')
        with self.assertRaises(ram.probe.ProbeError): self.prepare()
        path.unlink();path.symlink_to(self.build/'identity.ld')
        with self.assertRaises(ram.probe.ProbeError): self.prepare()

    def test_build_scope_source_paths_and_profile_changes(self):
        original = json.loads(json.dumps(self.manifest))
        for key, value in [('version','9.99'), ('reader_profile_sha256','0'*64),
                           ('nand_opcodes',['0xd8']), ('entry',0xa0c00030), ('bytes',True),
                           ('source_sha256',{'../escape':'0'*64}), ('artifacts',{}),
                           ('physical_qualified',True), ('device_access_performed',True)]:
            with self.subTest(key=key):
                self.manifest = {**original, key:value};self.save()
                with self.assertRaises(ram.probe.ProbeError): self.prepare()
        self.manifest = original
        self.manifest['source_sha256']['scripts/deployment/build_identity.py'] = '0'*64
        self.save()
        with self.assertRaises(ram.probe.ProbeError): self.prepare()

    def test_elf_corruption_rejected_even_with_updated_artifact_hash(self):
        elf = self.build/'identity.elf'
        raw = bytearray(elf.read_bytes());struct.pack_into('<I',raw,24,self.reader['load_address']+4)
        elf.write_bytes(raw)
        self.manifest['artifacts']['identity.elf'] = ram.probe.digest(elf);self.save()
        with self.assertRaises(ValueError): self.prepare()

    def test_external_spl_and_usb_source_changes(self):
        for name in ('flash/disc_spl_lpddr3.bin','src/usbboot/usbboot.c',
                     'spl-src/uboot-xburst-lpddr3-src.tar.gz'):
            with self.subTest(name=name):
                path = self.diskos/name;original = path.read_bytes();path.write_bytes(b'changed')
                with self.assertRaises(ram.probe.ProbeError): self.prepare()
                path.write_bytes(original)


if __name__ == '__main__': unittest.main()
