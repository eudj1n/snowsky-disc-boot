"""Fake libusb ABI only: no library loading, enumeration or physical USB."""
import ctypes as C
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('rom_probe', ROOT/'scripts/deployment/rom_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class Function:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


class FakeUsb:
    def __init__(self, matches=1, reply=b'X2000', code=5, failure=None):
        self.matches, self.reply, self.code, self.failure = matches, reply, code, failure
        self.events = []
        self.devices = (C.c_void_p * (matches + 2))(*range(1, matches + 2), None)
        for name in ('init', 'exit', 'get_device_list', 'free_device_list',
                     'get_device_descriptor', 'get_bus_number', 'get_device_address',
                     'get_port_numbers', 'open', 'close', 'control_transfer'):
            setattr(self, 'libusb_' + name, Function(lambda *args, name=name: self.call(name, args)))

    def call(self, name, args):
        self.events.append(name)
        if self.failure == name:
            return -3
        if name == 'init':
            args[0]._obj.value = 100
            return 0
        if name == 'get_device_list':
            C.cast(args[1], C.POINTER(C.POINTER(C.c_void_p)))[0] = C.cast(self.devices, C.POINTER(C.c_void_p))
            return self.matches + 1  # Includes one unrelated device.
        if name == 'get_device_descriptor':
            desc = args[1]._obj
            desc.length, desc.type = 18, 1
            desc.vid, desc.pid = (0xa108, 0xeaef) if args[0] <= self.matches else (1, 2)
            return 0
        if name == 'get_bus_number':return 3
        if name == 'get_device_address':return 5
        if name == 'get_port_numbers':
            args[1][0], args[1][1] = 2, 4
            return 2
        if name == 'open':
            args[1]._obj.value = 200
            return 0
        if name == 'control_transfer':
            self.transfer = args
            if self.code > 0:
                C.memmove(args[5], self.reply, min(self.code, len(self.reply), 8))
            return self.code
        return None


class RomProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.firmware = probe.load_profile('2.57')
        self.profile = probe.load_probe_profile(self.firmware)
        self.library = self.root/'fake-library'
        self.library.write_bytes(b'synthetic library; never dynamically loaded')

    def acquire(self, fake, name='capture'):
        return probe.acquire(self.firmware, self.profile, self.library, self.root/name,
                             loader=lambda path:fake)

    def test_descriptor_abi_layout(self):
        self.assertEqual(C.sizeof(probe.Descriptor), 18)
        self.assertEqual(probe.Descriptor.vid.offset, 8)
        self.assertEqual(probe.Descriptor.pid.offset, 10)

    def test_plan_never_loads_usb_and_request_is_fixed(self):
        with patch.object(C, 'CDLL', side_effect=AssertionError('Do not load USB')):
            result = probe.plan(self.firmware, self.profile)
        self.assertEqual(result['transfer'], dict(requestType=192, request=0, value=0,
                         index=0, length=8, timeoutMs=1000, attempts=1))
        self.assertFalse(result['physicalDeviceAccessed'])
        self.assertFalse(result['flashReady'])

    def test_cli_plan_and_missing_acquisition_arguments(self):
        command = [sys.executable, str(ROOT/'scripts/deployment/rom_probe.py')]
        result = subprocess.run([*command, 'plan', '--version', '2.57'], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['physicalDeviceAccessed'])
        for args in (['acquire'], ['plan', '--output', str(self.root/'unused')],
                     ['acquire', '--libusb', str(self.library)]):
            result = subprocess.run([*command, *args], capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 2)
        self.assertFalse((self.root/'unused').exists())

    def test_one_control_in_and_cleanup_with_raw_reply(self):
        fake = FakeUsb(reply=b'\x00\xffABC\x00\x10\x80', code=8)
        result = self.acquire(fake)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(fake.transfer[1:5], (192, 0, 0, 0))
        self.assertEqual(fake.transfer[6:], (8, 1000))
        self.assertEqual(fake.events.count('control_transfer'), 1)
        self.assertEqual(fake.events[-3:], ['close', 'free_device_list', 'exit'])
        self.assertEqual(result['replyHex'], '00ff414243001080')
        self.assertEqual(result['connection'], dict(bus=3, address=5, ports=[2, 4]))
        self.assertFalse(result['cpuIdentityVerified'])
        self.assertFalse(result['freshnessAuthenticated'])
        self.assertFalse(result['nandAccessed'])

    def test_zero_or_multiple_devices_never_open(self):
        for count in (0, 2):
            fake = FakeUsb(matches=count)
            result = self.acquire(fake, str(count))
            self.assertEqual(result['status'], 'failed')
            self.assertNotIn('open', fake.events)
            self.assertNotIn('control_transfer', fake.events)
            self.assertEqual(fake.events[-2:], ['free_device_list', 'exit'])
            self.assertTrue(result['usbDiscoveryAttempted'])
            self.assertFalse(result['deviceOpened'])
            self.assertIsNone(result['physicalDeviceAccessed'])

    def test_short_timeout_and_disconnect_never_retry(self):
        for code in (0, 4, 7, 9, -7, -4):
            fake = FakeUsb(code=code)
            result = self.acquire(fake, str(code))
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['transferResult'], code)
            self.assertEqual(fake.events.count('control_transfer'), 1)
            self.assertTrue(result['physicalDeviceAccessed'])
            self.assertEqual(fake.events[-3:], ['close', 'free_device_list', 'exit'])

    def test_initialization_enumeration_descriptor_path_and_open_errors(self):
        for failure in ('init', 'get_device_list', 'get_device_descriptor', 'get_port_numbers', 'open'):
            fake = FakeUsb(failure=failure)
            result = self.acquire(fake, failure)
            self.assertEqual(result['status'], 'failed')
            self.assertNotIn('control_transfer', fake.events)
            self.assertFalse(result['vendorRequestAttempted'])
            if failure != 'init':self.assertEqual(fake.events[-1], 'exit')
            self.assertNotIn('close', fake.events)

    def test_fresh_output_reserved_before_library_load(self):
        output = self.root/'existing';output.mkdir()
        sentinel = output/'result.json';sentinel.write_text('preserve')
        with self.assertRaises(FileExistsError):
            probe.acquire(self.firmware, self.profile, self.library, output,
                          loader=lambda path:self.fail('Must not load USB'))
        self.assertEqual(sentinel.read_text(), 'preserve')

    def test_success_and_failure_evidence_are_bound_to_local_run(self):
        first = self.acquire(FakeUsb(), 'one')
        second = self.acquire(FakeUsb(code=-7), 'two')
        self.assertNotEqual(first['sessionId'], second['sessionId'])
        for name, result in (('one', first), ('two', second)):
            request = json.loads((self.root/name/'request.json').read_text())
            saved = json.loads((self.root/name/'result.json').read_text())
            self.assertEqual(request['status'], 'incomplete')
            self.assertEqual(saved, result)
            self.assertEqual(request['sessionId'], saved['sessionId'])
            self.assertEqual(saved['probeProfileSha256'], probe.fingerprint(self.profile))
            self.assertEqual(saved['library']['sha256'], probe.digest(self.library))
            self.assertEqual((self.root/name).stat().st_mode & 0o777, 0o700)

    def test_library_load_failure_preserves_failed_result(self):
        def reject(path):raise OSError('synthetic load failure')
        result = probe.acquire(self.firmware, self.profile, self.library, self.root/'load', loader=reject)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['vendorRequestAttempted'])
        self.assertTrue((self.root/'load/result.json').is_file())
        self.assertFalse(result['usbDiscoveryAttempted'])

    def test_interrupt_does_not_retry_and_records_uncertain_attempt(self):
        fake = FakeUsb()
        def interrupt(*args):raise KeyboardInterrupt()
        fake.libusb_control_transfer = Function(interrupt)
        result = self.acquire(fake)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(result['vendorRequestAttempted'])
        self.assertNotIn('transferResult', result)
        self.assertEqual(fake.events[-3:], ['close', 'free_device_list', 'exit'])

    def test_exact_signature_only_without_padding_or_prefix_acceptance(self):
        result = self.acquire(FakeUsb())
        self.assertEqual(result['status'], 'cpu-signature-matched')
        self.assertTrue(result['cpuSignatureMatched'])
        self.assertFalse(result['cpuIdentityVerified'])
        for code, raw in ((5, '5832303031'), (4, '58323030'), (6, '583230303000'),
                          (8, '5832303030000000'), (5, '583230303000'), (True, '58')):
            with self.subTest(code=code, raw=raw), self.assertRaises(probe.ProbeError):
                probe.match_reply(self.profile, code, raw)

    def test_offline_review_preserves_original_failure_and_binds_both_profiles(self):
        self.acquire(FakeUsb())
        path = self.root/'capture/result.json'
        saved = json.loads(path.read_text())
        saved.update(status='failed', probeProfileSha256='b'*64,
                     error='Original exact-eight-byte policy rejected length 5')
        path.write_text(json.dumps(saved))
        before = path.read_bytes()
        with patch.object(C, 'CDLL', side_effect=AssertionError('No USB')):
            result = probe.review_result(path, probe.digest(path), self.firmware, self.profile)
        self.assertEqual(result['status'], 'saved-cpu-signature-matched')
        self.assertEqual(result['originalStatus'], 'failed')
        self.assertEqual(result['sourceProbeProfileSha256'], 'b'*64)
        self.assertEqual(result['reviewedProbeProfileSha256'], probe.fingerprint(self.profile))
        self.assertFalse(result['physicalDeviceAccessed'])
        self.assertFalse(result['flashReady'])
        self.assertEqual(path.read_bytes(), before)

    def test_offline_review_rejects_hash_mismatch_and_inconsistent_observation(self):
        self.acquire(FakeUsb())
        path = self.root/'capture/result.json'
        original = json.loads(path.read_text())
        with self.assertRaises(probe.ProbeError):
            probe.review_result(path, '0'*64, self.firmware, self.profile)
        for key, value in (('status','incomplete'), ('matchingDevices',True), ('deviceOpened',False),
                           ('transferResult',7), ('replyHex','5832303031'), ('nandAccessed',True),
                           ('firmwareProfileSha256','a'*64), ('sessionId','invalid'),
                           ('finishedAt','1999-01-01T00:00:00+00:00')):
            path.write_text(json.dumps(dict(original, **{key:value})))
            with self.subTest(key=key), self.assertRaises((probe.ProbeError, ValueError)):
                probe.review_result(path, probe.digest(path), self.firmware, self.profile)

    def test_review_cli_never_overwrites_existing_evidence(self):
        self.acquire(FakeUsb())
        path = self.root/'capture/result.json'
        output = self.root/'review'
        command = [sys.executable, str(ROOT/'scripts/deployment/rom_probe.py'), 'review', '--version', '2.57',
                   '--result', str(path), '--sha256', probe.digest(path), '--output', str(output)]
        result = subprocess.run(command, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout)
        before = (output/'review.json').read_bytes()
        result = subprocess.run(command, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertEqual((output/'review.json').read_bytes(), before)

    def test_profile_selection_is_firmware_pinned_and_bounded(self):
        # A new reviewed firmware can select this protocol without changing code.
        import firmware_profile
        folder = self.root/'probes';folder.mkdir()
        firmware = dict(self.firmware, version='9.99', rootfs_sha256='a'*64)
        profile = dict(self.profile, version='9.99', rootfs_sha256='a'*64, vid=123, pid=456)
        path = folder/'v9.99.json';path.write_text(json.dumps(profile))
        self.assertEqual(firmware_profile.load_probe_profile(firmware, self.root), profile)
        for key, value in (('rootfs_sha256','b'*64), ('protocol','arbitrary-write'),
                           ('timeout_ms',0), ('timeout_ms',5001), ('vid',True), ('pid',65536),
                           ('accepted_reply_hex',[]), ('accepted_reply_hex',['58']*2),
                           ('accepted_reply_hex',['58'*9]), ('accepted_reply_hex',['0X'])):
            path.write_text(json.dumps(dict(profile, **{key:value})))
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                firmware_profile.load_probe_profile(firmware, self.root)
        path.unlink()
        with self.assertRaises(FileNotFoundError):firmware_profile.load_probe_profile(firmware, self.root)


if __name__ == '__main__':unittest.main()
