"""install.py's player through the reviewed tools (scripts/installer/usbboot.py), with the tools
replaced by a stand-in that leaves their outputs: the order of the steps, the admission opened only
for the write and closed in every case, and each refusal."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from installer import flow, tui, usbboot  # noqa: E402


class Tools:
    """The reviewed tools' outputs, as the procedure reads them; faults on request."""

    def __init__(self, test, faults=()):
        self.test, self.faults, self.calls, self.admission_at_write = test, set(faults), [], None
        self.commands = []
        self.pages = [bytes([1]) * 2048, bytes([2]) * 2048]

    def value(self, command, name):
        return command[command.index(name) + 1]

    def __call__(self, command, cwd=None, capture_output=True, text=True):
        tool, args = Path(command[2]).name, command[3:]
        self.commands.append([str(part) for part in command])
        self.calls.append(tool + (f' {args[0]}' if args and not args[0].startswith('--') else ''))
        out, stdout = Path(self.value(command, '--output')) if '--output' in command else None, ''
        if tool == 'build_identity.py':
            out.mkdir(parents=True)
        elif tool == 'installation_review.py':
            out.mkdir(parents=True)
            proposed = dict(json.loads(self.test.profile.read_text()), physical_write_admitted=True, installation_review_sha256='new-pin')
            if 'profile-changes' in self.faults:
                proposed['writer'] = 'another'
            files = {'installation-review.json': {}, 'proposed-installer-profile.json': proposed,
                     'candidate-write-plan.json': dict(plan=dict(image_name=self.test.image.name, protocol_call_limit=100), plan_sha256='write-plan'),
                     'restore-write-plan.json': dict(plan=dict(image_name=self.test.stock.name, protocol_call_limit=100), plan_sha256='restore-plan'),
                     'postwrite-collection-plan.json': dict(plan=dict(protocol_call_limit=100), plan_sha256='read-plan'),
                     'candidate-exact-readback-plan.json': dict(plan_sha256='exact-candidate'),
                     'restore-exact-readback-plan.json': dict(plan_sha256='exact-restore')}
            for name, data in files.items():
                (out/name).write_text(json.dumps(data))
        elif tool == 'writer_transport.py' and args[0] == 'plan':
            package = Path(self.value(command, '--installation-review')).parent
            plan = json.loads((package/f'{self.value(command, "--target")}-write-plan.json').read_text())
            if 'plan-differs' in self.faults:
                plan['plan_sha256'] = 'other'
            stdout = json.dumps(plan)
        elif tool == 'writer_transport.py':
            self.admission_at_write = json.loads(self.test.profile.read_text())['physical_write_admitted']
            out.mkdir(parents=True)
            status = 'writer-outcome-unknown' if 'outcome-unknown' in self.faults else 'writer-completion-observed'
            (out/'result.json').write_text(json.dumps(dict(status=status, writer_return_observed=True, writer_execution_attempted=True,
                                                           session_id='write-session')))
            (out/'metadata-main.bin').write_bytes(b'page')
        elif tool == 'collect_rootfs.py' and args[0] == 'plan':
            stdout = json.dumps(dict(plan=dict(protocol_call_limit=100), plan_sha256='digest-plan'))
        elif tool == 'collect_rootfs.py' and self.value(command, '--mode') == 'rootfs-probe':
            # The identity check (plan, stage 4c): the first blocks of what the player holds, the
            # history's image unless a test says otherwise.
            out.mkdir(parents=True)
            (out/'result.json').write_text(json.dumps(dict(status='rootfs-probe-collected', session_id=f'identity-{len(self.calls)}')))
            previous = self.test.history.data.get('previousImage')
            blocks = Path(previous).read_bytes()[:262144] if previous else b'stock blocks'
            (out/'logical-image.bin').write_bytes(b'other' + blocks[5:] if 'identity-differs' in self.faults else blocks)
        elif tool == 'collect_rootfs.py' and self.value(command, '--mode') == 'rootfs-digest':
            # The read by digest beside a full read: the digests of the pages the full read left.
            out.mkdir(parents=True)
            (out/'result.json').write_text(json.dumps(dict(status='rootfs-digest-collected', nonce_hex='n2', logical_to_physical=[80, 81],
                                                           session_id=f'digest-{len(self.calls)}')))
            (out/'records.bin').write_bytes(b'digest records')
            digests = b''.join(hashlib.sha256(page).digest() for page in self.pages)
            if 'digest-differs' in self.faults:
                digests = bytes(32) + digests[32:]
            (out/'logical-digests.bin').write_bytes(digests)
        elif tool == 'collect_rootfs.py':
            out.mkdir(parents=True)
            (out/'result.json').write_text(json.dumps(dict(status='rootfs-collected', nonce_hex='n1', capture_sha256='c1',
                                                           session_id=f'read-{len(self.calls)}', logical_to_physical=[80, 81])))
            (out/'records.bin').write_bytes(b'records')
            (out/'logical-image.bin').write_bytes(b''.join(self.pages))
        elif tool == 'readback.py' and args[0] == 'plan':
            image = Path(self.value(command, '--image')).name
            stdout = json.dumps(dict(plan_sha256='exact-candidate' if image == self.test.image.name else
                                     'exact-restore' if image == self.test.stock.name else 'exact-previous'))
        elif tool == 'readback.py' and '--digest' in command:
            stdout = json.dumps(dict(status='saved-logical-digest-matches'))
        elif tool == 'readback.py':
            matches = 'readback-differs' not in self.faults or 'backup' in self.value(command, '--records')
            stdout = json.dumps(dict(status='saved-logical-readback-matches' if matches else 'saved-logical-readback-differs',
                                     nonce_hex=self.value(command, '--nonce-hex'), capture_sha256='c1'))
        elif tool.startswith('audit_usb_'):
            out.write_text('{}')
        return subprocess.CompletedProcess(command, 0, stdout, '')


class ReviewedTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.profile = self.root/'installer.json'
        self.profile.write_text(json.dumps(dict(schema_version=1, writer='diskos-my-write5-768', physical_write_admitted=False,
                                                installation_review_sha256='old-pin')))
        artifacts, previous = self.root/'artifacts', self.root/'previous'
        artifacts.mkdir()
        previous.mkdir()
        self.image, self.stock = artifacts/'disc-boot-v257-review-only.bin', artifacts/'stock-v257-restore-review-only.bin'
        for path in (self.image, self.stock, previous/'combined.bin'):
            path.write_bytes(b'image')
        for folder in (artifacts, previous):
            (folder/'report.json').write_text(json.dumps(dict(artifacts={p.name: dict(sha256='s-' + p.name) for p in folder.glob('*.bin')})))
        boot = self.root/'boot-capture'
        boot.mkdir()
        (boot/'metadata-main.bin').write_bytes(b'page')
        self.history = usbboot.History(dict(bootCapture=boot, stockCapture=self.root/'stock-capture', previousReview=self.root/'review.json',
                                            previousImage=previous/'combined.bin', writeCapture=self.root/'w', readbackCapture=self.root/'r'))
        # The stand-in tools take '/diskos' as given; its pinned files are scripts/sources.py's (test_sources).
        # The payloads are built by the stand-in tools here, whatever the releases recorded carry.
        from unittest import mock
        for patcher in (mock.patch.object(flow.sources, 'differs', return_value=[]),
                        mock.patch.object(flow.Installer, 'release_payloads', return_value=None)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def reviewed(self, *faults):
        tools = Tools(self, faults)
        return usbboot.Reviewed('2.57', self.root/'usb', self.image.parent, '/diskos', '/libusb.dylib', self.history, run=tools,
                                profile=self.profile), tools

    def test_the_releases_payloads_are_taken_rather_than_built(self):
        """Plan, stage 6: with the release's prebuilt payloads nothing is compiled on the computer."""
        payloads = self.root/'payloads'
        for mode in ('metadata', 'rootfs', 'rootfs-digest', 'staging-check', 'rootfs-probe'):
            (payloads/mode).mkdir(parents=True)
            (payloads/mode/'identity.bin').write_bytes(mode.encode())
        tools = Tools(self, ())
        reviewed = usbboot.Reviewed('2.57', self.root/'usb', self.image.parent, '/diskos', '/libusb.dylib', self.history, run=tools,
                                    profile=self.profile, payloads=payloads)
        reviewed.prepare()
        self.assertNotIn('build_identity.py', tools.calls)
        self.assertEqual((reviewed.probe_build/'identity.bin').read_bytes(), b'rootfs-probe')
        self.assertEqual((reviewed.staging_build/'identity.bin').read_bytes(), b'staging-check')

    def test_the_installation_follows_the_procedure(self):
        reviewed, tools = self.reviewed()
        reviewed.prepare()
        backup = reviewed.identity()
        written = reviewed.write()
        read = reviewed.readback()
        reviewed.audit()
        # Five payloads: the metadata read, the readback, the read by digest, the staging check and the
        # identity check's probe (plan, stage 4c); the probe read in place of a backup.
        self.assertEqual(tools.calls, ['build_identity.py']*5 + ['installation_review.py',
                                       'collect_rootfs.py plan', 'collect_rootfs.py acquire',
                                       'writer_transport.py plan', 'writer_transport.py acquire',
                                       'collect_rootfs.py acquire', 'readback.py plan', 'readback.py verify',
                                       'audit_usb_write.py', 'audit_usb_readback.py'])
        self.assertEqual(backup['matches'], 'combined.bin', 'the first blocks are the image the history says is installed')
        # The staging check's build goes to the review, the write and its audit.
        staging = str(self.root/'usb/build-staging')
        for name in ('installation_review.py', 'writer_transport.py', 'audit_usb_write.py'):
            for command in (c for c in tools.commands if Path(c[2]).name == name):
                self.assertEqual(command[command.index('--staging-build') + 1], staging, name)
        self.assertIn(['--mode', 'staging-check'], [c[c.index('--mode'):c.index('--mode')+2] for c in tools.commands
                                                    if Path(c[2]).name == 'build_identity.py'])
        self.assertTrue(tools.admission_at_write, 'admission open for the write')
        profile = json.loads(self.profile.read_text())
        self.assertEqual((profile['physical_write_admitted'], profile['installation_review_sha256']), (False, 'new-pin'))
        self.assertEqual((written['session'], read['image'], read['exact']),
                         ('write-session', self.image.name, 'saved-logical-readback-matches'))
        history = json.loads(reviewed.next_history(self.image).read_text())
        self.assertEqual(history['previousImage'], str(self.image))

    def test_a_player_that_does_not_hold_its_history_s_image_is_not_written(self):
        reviewed, tools = self.reviewed('identity-differs')
        reviewed.prepare()
        with self.assertRaises(usbboot.ReviewedError) as stopped:
            reviewed.identity()
        self.assertIn('does not hold combined.bin', str(stopped.exception))
        self.assertNotIn('writer_transport.py acquire', tools.calls)

    def test_an_unknown_outcome_stops_everything_and_closes_admission(self):
        reviewed, tools = self.reviewed('outcome-unknown')
        reviewed.prepare()
        with self.assertRaisesRegex(usbboot.ReviewedError, 'outcome is unknown: keep the player powered'):
            reviewed.write()
        self.assertFalse(json.loads(self.profile.read_text())['physical_write_admitted'])

    def test_a_plan_that_changed_never_reaches_the_writer(self):
        reviewed, tools = self.reviewed('plan-differs')
        reviewed.prepare()
        with self.assertRaisesRegex(usbboot.ReviewedError, 'differs from the approved one'):
            reviewed.write()
        self.assertNotIn('writer_transport.py acquire', tools.calls)
        self.assertFalse(json.loads(self.profile.read_text())['physical_write_admitted'])

    def test_a_proposal_that_changes_more_than_admission_is_refused(self):
        reviewed, tools = self.reviewed('profile-changes')
        reviewed.prepare()
        before = self.profile.read_text()
        with self.assertRaisesRegex(usbboot.ReviewedError, 'changes more than its admission'):
            reviewed.write()
        self.assertEqual(self.profile.read_text(), before)

    def test_a_readback_that_differs_is_refused(self):
        reviewed, tools = self.reviewed('readback-differs')
        reviewed.prepare()
        reviewed.backup()
        reviewed.write()
        with self.assertRaisesRegex(usbboot.ReviewedError, 'does not match the image'):
            reviewed.readback()

    def test_a_session_shows_its_progress(self):
        """A USB session's tool runs in the background; its journal, two lines a call, is counted
        against the plan's call limit."""
        from unittest import mock
        tools = self.root/'tools'
        tools.mkdir()
        (tools/'fake_session.py').write_text(
            'import sys, time\nfrom pathlib import Path\nout = Path(sys.argv[sys.argv.index("--output") + 1])\n'
            'out.mkdir(parents=True)\nfor k in range(5):\n'
            '    with open(out/"transfers.jsonl", "a") as j:\n        j.write("{}\\n" * 20)\n    time.sleep(0.6)\n'
            'print("done")\n')
        seen = []
        reviewed = usbboot.Reviewed('2.57', self.root/'usb', self.image.parent, '/diskos', '/libusb.dylib', self.history,
                                    profile=self.profile, progress=lambda label, fraction, seconds: seen.append((label, fraction)))
        with mock.patch.object(usbboot, 'DEPLOY', tools):
            result = reviewed.session('fake_session.py', '--output', self.root/'session', output=self.root/'session', calls=50,
                                      label='Reading')
        self.assertEqual((result.returncode, result.stdout.strip()), (0, 'done'))
        fractions = [f for label, f in seen]
        self.assertEqual(fractions, sorted(fractions), 'never backwards')
        self.assertEqual(fractions[-1], 1.0)
        self.assertTrue(any(0 < f < 1 for f in fractions), 'seen on the way')

    def test_a_held_ask_counts_by_its_expected_time(self):
        """With held asks (plan, stage 4c) the ROM is silent while the payload runs: the bar counts
        each long ask by the clock against its measured time, not the plan's limit (2026-10-07: the
        region's check held the bar still for two minutes, and the writer was counted as 15)."""
        plan = dict(completion_ask=True, staging_check_ms=600000, staging_sample_ms=60000, writer_executions=1, writer_wait_ms=900000)
        completion = dict(expected_ms=dict(staging_check=111000, staging_sample=4300, writer=246000))
        self.assertEqual(usbboot.held_asks(plan, completion), [111.0, 4.3, 246.0])
        self.assertEqual(usbboot.held_asks(dict(plan, writer_executions=0), completion), [111.0, 4.3], 'staging alone')
        self.assertIsNone(usbboot.held_asks(dict(plan, completion_ask=False)), 'the fixed waits as before')
        call = b'{"phase": "attempt", "sequence": 1, "kind": "bulk"}'
        ret = b'{"phase": "return", "sequence": 1, "code": 0}'
        ask = b'{"phase": "attempt", "sequence": 2, "kind": "control", "request": 0, "timeout_ms": 600000, "poll": true}'
        short = b'{"phase": "attempt", "sequence": 3, "kind": "control", "request": 0, "timeout_ms": 2000, "poll": true}'
        progress = usbboot.Progress(1000, [100.0, 300.0])
        progress.feed([call, ret] * 100, 10.0)             # 100 calls in 10 s: 100 s of calls in all
        before = progress.fraction(10.0)
        self.assertAlmostEqual(before, 10 / (100 + 400))
        progress.feed([ask], 10.0)
        during = [progress.fraction(t) for t in (40.0, 70.0, 100.0, 200.0)]
        self.assertEqual(during, sorted(during), 'the bar moves while the ROM is silent')
        self.assertLess(during[-1], (10 + 100) / 500 + 0.001, 'an ask past its expected time holds just short of it')
        progress.feed([ret], 120.0)
        progress.feed([short, ret] * 10, 121.0)
        self.assertGreater(progress.fraction(121.0), during[-1], 'a short ask is a call')
        self.assertLessEqual(progress.fraction(10 ** 6), 0.99)

    def test_a_write_counts_its_calls_then_the_writer_s_wait(self):
        """The writer's session (owner, 2026-10-05): staging by its calls, then the ROM's silence
        while the writer runs, counted by the clock from the writer's start in the journal."""
        entry, start = 2696937520, '{"phase": "attempt", "sequence": 9, "kind": "control", "request": 4, "parameter": %d, "timeout_ms": 5000}'
        self.assertTrue(usbboot.is_writer_start((start % entry).encode(), entry))
        self.assertFalse(usbboot.is_writer_start((start % 1).encode(), entry), 'another program start')
        self.assertFalse(usbboot.is_writer_start(b'{"phase": "return", "sequence": 9, "code": 0}', entry))
        # 11 min of staging, then 15 min of waiting: halfway through staging 13 + 15 min remain.
        f = usbboot.session_fraction(0.5, 330, 900)
        self.assertAlmostEqual(330 * (1 - f) / f, 330 + 900)
        f = usbboot.session_fraction(1.0, 960, 900, writer_started=660)
        self.assertAlmostEqual(960 * (1 - f) / f, 600)
        self.assertEqual(usbboot.session_fraction(0.4, 100), 0.4, 'a read counts its calls')
        # A held ask replaces the request after a fixed wait (stage 4c): the plan's limit is the count.
        self.assertEqual(usbboot.expected_calls(dict(protocol_call_limit=100, completion_ask=True)), 100)
        self.assertEqual(usbboot.expected_calls(dict(protocol_call_limit=100)), 100)
        from unittest import mock
        tools = self.root/'tools'
        tools.mkdir()
        (tools/'fake_writer.py').write_text(
            'import sys, time\nfrom pathlib import Path\nout = Path(sys.argv[sys.argv.index("--output") + 1])\n'
            'out.mkdir(parents=True)\nfor k in range(3):\n'
            '    with open(out/"transfers.jsonl", "a") as j:\n        j.write("{}\\n" * 20)\n    time.sleep(0.5)\n'
            f'with open(out/"transfers.jsonl", "a") as j:\n    j.write({start % entry!r} + "\\n")\n'
            'time.sleep(3)\nprint("written")\n')
        seen = []
        reviewed = usbboot.Reviewed('2.57', self.root/'usb', self.image.parent, '/diskos', '/libusb.dylib', self.history,
                                    profile=self.profile, progress=lambda label, fraction, seconds: seen.append((fraction, seconds)))
        with mock.patch.object(usbboot, 'DEPLOY', tools):
            result = reviewed.session('fake_writer.py', '--output', self.root/'w', output=self.root/'w', calls=31,
                                      label='Writing', writer=(entry, 4))
        self.assertEqual((result.returncode, result.stdout.strip()), (0, 'written'))
        fractions = [f for f, _ in seen]
        self.assertEqual(fractions, sorted(fractions), 'never backwards')
        self.assertEqual(fractions[-1], 1.0)
        waiting = [f for f, t in seen if t > 2.5 and f < 1]
        self.assertTrue(waiting and waiting[-1] > waiting[0], 'the wait moves the bar by the clock')
        self.assertTrue(all(f < 0.9 for f, t in seen if t < 1.4), 'the wait ahead is counted from the start')

    def install(self, words=None, restore=False, run=None, resume=None):
        """install.py's reviewed path with the tools replaced; words: what the owner types, in order
        (none: --yes, without questions)."""
        from unittest import mock
        import builtins
        (self.root/'history.json').write_text(json.dumps(self.history.data))
        catalog = self.root/'packages.json'
        catalog.write_text(json.dumps(dict(schema=1, kind='packages', entries=[dict(
            name='disc-menu', role='menu', version='9', profiles=['2.57'], bootApi=1, license='MIT', default=False,
            source=dict(url=None, sha256='0' * 64, size=1), verified=dict(date='2026-10-03', acceptance='test'))])))
        args = argparse.Namespace(dry_run=True, yes=words is None, plain=True, ota=None, image=str(self.image), emulator=None, card=None,
                                  package=None, app=None, packages_from=[], download=False, work=str(self.root/('again' if run or resume else 'run')),
                                  catalog=str(catalog), simulate=None, simulate_small=False, fault=None, restore=restore, guest=False,
                                  history=str(self.root/'history.json'), diskos='/diskos', libusb='/libusb.dylib', run=run,
                                  resume=resume)
        tools = self.tools = Tools(self, getattr(self, 'tools_faults', ()))
        installer = flow.Installer(args, tui.Screen(look='plain', stream=io.StringIO()), runner=tools)
        installer.interactive = words is not None
        # The first start's check over the USB console (plan, stage 4c): none unless a test gives one.
        installer.fetch_check = getattr(self, 'fetch', lambda sha256, card: None)
        self.asked = {}
        remaining = list(words or [])

        def typed(prompt=''):
            word = remaining.pop(0)
            self.asked.setdefault(word, list(tools.calls))
            return word
        original = usbboot.Reviewed.__init__

        def tracked_profile(reviewed, *a, **k):
            original(reviewed, *a, **dict(k, profile=self.profile))
        with mock.patch.object(usbboot.Reviewed, '__init__', tracked_profile), mock.patch.object(builtins, 'input', side_effect=typed), \
                mock.patch.object(tui, 'read_key', return_value='enter'):
            code = installer.run()
        self.assertEqual(remaining, [], 'every word was asked for')
        return code, installer, tools

    def test_install_py_runs_the_player_through_them(self):
        code, installer, tools = self.install()
        self.assertEqual(code, 0, installer.report['status'])
        player = installer.report['steps'][4]
        self.assertTrue(player['written'])
        self.assertEqual(player['target'], 'candidate')
        self.assertIn('audit_usb_readback.py', tools.calls)
        self.assertTrue((self.root/'run/usb/history-used.json').is_file(), 'kept for a later way back to stock')

    def test_the_owner_confirms_the_start_between_the_write_and_the_readback(self):
        """The order the next installation's review checks (installed_candidate.py): written <
        the owner's answer < the readback's start, with both sessions named; backup and write in
        one entry into USB Boot, the readback in a fresh one."""
        code, installer, tools = self.install(['CHECK', 'WRITE', 'yes', 'the stock UI came up, the volume works', 'READ'])
        self.assertEqual(code, 0, installer.report['status'])
        acquired = [c for c in self.asked['yes'] if c.endswith('acquire')]
        self.assertEqual(acquired[-1], 'writer_transport.py acquire', 'asked after the write, before the readback session')
        record = json.loads((self.root/'run/usb/read/owner-boot-confirmation.json').read_text())
        self.assertEqual((record['observation'], record['owner_answer']),
                         ('owner-confirmed-normal-first-boot', 'the stock UI came up, the volume works'))
        read = json.loads((self.root/'run/usb/read/result.json').read_text())
        self.assertEqual((record['write_session_id'], record['readback_session_id']), ('write-session', read['session_id']))
        self.assertIs(record['automated_boot_test'], False)
        self.assertTrue(record['reported_at'].endswith('Z'))

    def test_the_first_start_s_check_stands_for_the_readback(self):
        """The new system's boot layer checked the written image at its first start (plan, stage 4c):
        read over the USB console, it stands for the readback, which does not run; the history names
        that proof. A check that differs or does not come back leaves the readback to run."""
        fetched = []

        def fetch(sha256, card):
            fetched.append((sha256, card))
            return (json.dumps(dict(schema=1, expected=sha256, actual=sha256, bytes=self.image.stat().st_size,
                                    device='/dev/mtdblock_bbt_ro2', match=True, seconds=2.0, error=None, build='b')) + '\n').encode()
        self.fetch = fetch
        code, installer, tools = self.install(['CHECK', 'WRITE', 'yes', 'the menu came up'])
        self.assertEqual(code, 0, installer.report['status'])
        expected = json.loads((self.root/'run/card/.disc/boot/expected-rootfs.json').read_text())
        self.assertEqual(expected['sha256'], hashlib.sha256(self.image.read_bytes()).hexdigest())
        self.assertEqual(fetched, [(expected['sha256'], '/tmp/sdcard')])
        self.assertNotIn('audit_usb_readback.py', tools.calls)
        self.assertFalse([c for c in tools.calls if 'collect_rootfs.py acquire' in c and 'read' in c][1:])
        proof = self.root/'run/usb/first-start'
        self.assertEqual(sorted(p.name for p in proof.iterdir()), ['fetch.json', 'owner-boot-confirmation.json', 'rootfs-check.json'])
        fetch_record = json.loads((proof/'fetch.json').read_text())
        self.assertEqual(fetch_record['rootfs_check_sha256'], hashlib.sha256((proof/'rootfs-check.json').read_bytes()).hexdigest())
        history = json.loads((self.root/'run/usb/history.json').read_text())
        self.assertEqual(Path(history['readbackCapture']).resolve(), proof.resolve())

    def test_a_first_start_check_that_differs_leaves_the_readback_to_run(self):
        self.fetch = lambda sha256, card: (json.dumps(dict(schema=1, expected=sha256, actual='0' * 64, match=False, error=None)) + '\n').encode()
        code, installer, tools = self.install(['CHECK', 'WRITE', 'yes', 'fine', 'READ'])
        self.assertEqual(code, 0, installer.report['status'])
        self.assertIn('audit_usb_readback.py', tools.calls)
        history = json.loads((self.root/'run/usb/history.json').read_text())
        self.assertTrue(history['readbackCapture'].endswith('/read'), history['readbackCapture'])

    def test_no_read_by_digest_goes_with_an_installation(self):
        """Three reads by digest beside the backup brought page ticks of zero, 27 minutes each
        (owner, 2026-10-06): an installation no longer runs one."""
        code, installer, tools = self.install(['CHECK', 'WRITE', 'yes', 'fine', 'READ'])
        self.assertEqual((code, installer.report['status']), (0, 'prepared'))
        self.assertNotIn('digest', installer.report['steps'][4]['backup'])
        self.assertFalse((self.root/'run/usb/digest-backup').exists())
        self.assertNotIn('rootfs-digest', ' '.join(tools.calls))

    def test_a_no_takes_the_player_back_to_stock(self):
        """The new system did not start: straight to stock in a fresh entry (the player holds this
        run's own image, nothing to back up), the owner's look at stock, stock's readback; the
        history is the restore."""
        code, installer, tools = self.install(['CHECK', 'WRITE', 'no', 'its UI restarts without end',
                                               'RESTORE', 'yes', 'stock starts, the volume works', 'READ'])
        self.assertEqual((code, installer.report['status']), (0, 'restored'))
        self.assertEqual([c for c in tools.calls if c in ('writer_transport.py acquire', 'collect_rootfs.py acquire')],
                         ['collect_rootfs.py acquire', 'writer_transport.py acquire',
                          'writer_transport.py acquire', 'collect_rootfs.py acquire'],
                         'backup, write, stock, its readback; no readback of the failed image')
        usb = self.root/'run/usb'
        self.assertFalse((usb/'read').exists(), 'the failed image is not read back')
        record = json.loads((usb/'restore-read/owner-boot-confirmation.json').read_text())
        self.assertEqual(record['owner_answer'], 'stock starts, the volume works')
        history = json.loads((usb/'history.json').read_text())
        self.assertEqual((history['previousTarget'], Path(history['previousImage']).name, Path(history['writeCapture']).name),
                         ('restore', self.stock.name, 'restore-write'))
        self.assertNotIn('First boot', [s['step'] for s in installer.report['steps']])

    def test_a_run_takes_the_player_back_to_stock_later(self):
        """install.py --restore --run: that run's package, no review; the player holds that run's own
        image (its write observed complete), so no backup; with an unknown state, the backup first."""
        self.install()
        code, installer, tools = self.install(['RESTORE', 'yes', 'stock is back', 'READ'], restore=True, run=str(self.root/'run'))
        self.assertEqual((code, installer.report['status']), (0, 'restored'))
        self.assertNotIn('installation_review.py', tools.calls)
        usb = self.root/'run/usb'
        self.assertFalse((usb/'restore-identity').exists())
        self.assertEqual(json.loads((usb/'history.json').read_text())['previousTarget'], 'restore')
        for folder in ('restore-write', 'restore-read'):
            shutil.rmtree(usb/folder)
        (usb/'write/result.json').unlink()        # the write's outcome unknown: what the player holds is not known
        shutil.rmtree(self.root/'again')
        code, installer, tools = self.install(['CHECK', 'RESTORE', 'yes', 'stock is back', 'READ'], restore=True, run=str(self.root/'run'))
        self.assertEqual((code, installer.report['status']), (0, 'restored'))
        self.assertTrue((usb/'restore-identity/result.json').is_file())

    def test_a_write_stopped_before_the_writer_goes_on_from_its_backup(self):
        """install.py --resume (2026-10-06: the SPL's DDR check failed in the write's session): the
        same package, then in a fresh entry the player's first blocks checked again and the write."""
        self.install()
        usb = self.root/'run/usb'
        for folder in ('read', 'history.json'):
            shutil.rmtree(usb/folder) if (usb/folder).is_dir() else (usb/folder).unlink()
        (usb/'write/result.json').write_text(json.dumps(dict(status='failed-before-writer', writer_execution_attempted=False,
                                                              page_execution_attempted=False)))
        code, installer, tools = self.install(['CHECK', 'WRITE', 'yes', 'the menu, then stock', 'READ'], resume=str(self.root/'run'))
        self.assertEqual((code, installer.report['status']), (0, 'prepared'))
        acquired = [c for c in tools.calls if c.endswith('acquire')]
        self.assertEqual(acquired, ['collect_rootfs.py acquire', 'writer_transport.py acquire', 'collect_rootfs.py acquire'],
                         'the identity check, the write, the readback')
        player = next(s for s in installer.report['steps'] if s['step'] == 'player')
        self.assertEqual((player['backup']['resumes'], player['backup']['matches']), (str((self.root/'run').resolve()), 'combined.bin'))
        again = self.root/'again/usb'
        self.assertTrue((again/'identity/logical-image.bin').is_file(), 'the first blocks checked again')
        self.assertTrue((again/'history.json').is_file())
        # A write that reached the writer, or ended unknown, is no place to go on from.
        shutil.rmtree(self.root/'again')
        (usb/'write/result.json').write_text(json.dumps(dict(status='writer-outcome-unknown', writer_execution_attempted=True,
                                                              page_execution_attempted=True)))
        code, installer, tools = self.install([], resume=str(self.root/'run'))
        self.assertEqual(code, 1)
        self.assertIn('not a run whose write stopped before the writer', installer.report['status'])
        self.assertFalse([c for c in tools.calls if c.endswith('acquire')])


if __name__ == '__main__':
    unittest.main()
