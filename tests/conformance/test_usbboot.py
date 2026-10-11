"""install.py's player through the reviewed tools (scripts/installer/usbboot.py), with the tools
replaced by a stand-in that leaves their outputs: the order of the steps in one entry, the first
start's check or the owner's word after the write, and each refusal."""
import argparse
import hashlib
import io
import json
from pathlib import Path
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
        self.test, self.faults, self.calls = test, set(faults), []
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
            if self.value(command, '--mode') == 'restart' and 'restart' in self.faults:
                (out/'identity.bin').write_bytes(b'restart payload')
        elif tool == 'installation_review.py' and '--allow-unknown' in command and 'unknown-image' in self.faults:
            out.mkdir(parents=True)
            for name in ('installation-review.json', 'proposed-installer-profile.json', 'postwrite-collection-plan.json'):
                (out/name).write_text('{}')
            (out/'restore-write-plan.json').write_text(json.dumps(dict(plan=dict(image_name=self.test.stock.name,
                                                                                  protocol_call_limit=100), plan_sha256='restore-plan')))
            (out/'decision.json').write_text(json.dumps(dict(status='known-image', found=dict(kind='unknown'), targets=['restore'])))
        elif tool == 'installation_review.py' and '--known' in command and 'unknown-image' in self.faults:
            return subprocess.CompletedProcess(command, 1, '', 'Installation review refused: The player holds a rootfs this '
                                               'installer does not know (another FiiO version or a changed image)\n')
        elif tool == 'installation_review.py':
            out.mkdir(parents=True)
            (out/'decision.json').write_text(json.dumps(dict(status='known-image', found=dict(kind='stock', sha256='s'),
                                                             targets=['candidate', 'restore'])))
            files = {'installation-review.json': {}, 'proposed-installer-profile.json': dict(physical_write_admitted=True),
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
        elif tool == 'ram_transport.py' and args[0] == 'plan':
            stdout = json.dumps(dict(plan=dict(protocol_call_limit=50), plan_sha256='metadata-plan'))
        elif tool == 'ram_transport.py':
            # The entry's first session (plan, stage 6): the partition table's page, with the entry's SPL.
            out.mkdir(parents=True)
            (out/'result.json').write_text(json.dumps(dict(status='nand-metadata-observed', session_id='metadata-session')))
            (out/'metadata-main.bin').write_bytes(b'page')
        elif tool == 'restart_player.py' and args[0] == 'plan':
            stdout = json.dumps(dict(plan=dict(protocol_call_limit=30), plan_sha256='restart-plan'))
        elif tool == 'restart_player.py':
            # The player restarted after the write (plan, stage 7): it leaves the bus, unless a test says otherwise.
            out.mkdir(parents=True)
            status = 'restart-not-observed' if 'no-restart' in self.faults else 'player-restarted'
            (out/'result.json').write_text(json.dumps(dict(status=status, session_id='restart-session')))
        elif tool == 'boot_evidence.py' and args[0] == 'build':
            out.mkdir(parents=True)
        elif tool == 'boot_evidence.py' and args[0] == 'plan':
            stdout = json.dumps(dict(plan=dict(protocol_call_limit=1170), plan_sha256='boot-plan'))
        elif tool == 'boot_evidence.py':
            out.mkdir(parents=True)
            (out/'result.json').write_text(json.dumps(dict(status='boot-evidence-collected', session_id='boot-session')))
            (out/'metadata-main.bin').write_bytes(Path(self.value(command, '--metadata-page')).read_bytes())
        elif tool == 'writer_transport.py':
            out.mkdir(parents=True)
            status = 'writer-outcome-unknown' if 'outcome-unknown' in self.faults else 'writer-completion-observed'
            (out/'result.json').write_text(json.dumps(dict(status=status, writer_return_observed=True, writer_execution_attempted=True,
                                                           session_id='write-session')))
            (out/'metadata-main.bin').write_bytes(b'page')
        elif tool == 'collect_rootfs.py' and args[0] == 'plan':
            stdout = json.dumps(dict(plan=dict(protocol_call_limit=100), plan_sha256='digest-plan'))
        elif tool == 'collect_rootfs.py' and self.value(command, '--mode') == 'rootfs-probe':
            # The identity probe (plan, stage 4c): the first blocks of what the player holds.
            out.mkdir(parents=True)
            (out/'result.json').write_text(json.dumps(dict(status='rootfs-probe-collected', session_id=f'identity-{len(self.calls)}')))
            (out/'logical-image.bin').write_bytes(b'stock blocks')
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
        elif tool == 'readback.py':
            matches = 'readback-differs' not in self.faults
            stdout = json.dumps(dict(status='saved-logical-readback-matches' if matches else 'saved-logical-readback-differs',
                                     nonce_hex=self.value(command, '--nonce-hex'), capture_sha256='c1'))
        elif tool.startswith('audit_usb_'):
            out.write_text('{}')
        return subprocess.CompletedProcess(command, 0, stdout, '')


CATALOG = dict(schema=1, kind='packages', entries=[dict(
    name='disc-menu', role='menu', version='9', profiles=['2.57'], bootApi=1, license='MIT', default=False,
    source=dict(url=None, sha256='0' * 64, size=1), verified=dict(date='2026-10-03', acceptance='test'))])


class ReviewedTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        artifacts = self.root/'artifacts'
        artifacts.mkdir()
        self.image, self.stock = artifacts/'disc-boot-v257-review-only.bin', artifacts/'stock-v257-restore-review-only.bin'
        for path in (self.image, self.stock):
            path.write_bytes(b'image')
        (artifacts/'report.json').write_text(json.dumps(dict(artifacts={p.name: dict(sha256='s-' + p.name) for p in artifacts.glob('*.bin')})))
        # The stand-in tools take '/diskos' as given; its pinned files are scripts/sources.py's (test_sources).
        # The payloads are built by the stand-in tools here, whatever the releases recorded carry.
        from unittest import mock
        for patcher in (mock.patch.object(flow.sources, 'differs', return_value=[]),
                        mock.patch.object(flow.Installer, 'release_payloads', return_value=None)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def reviewed(self, *faults, work='usb', **options):
        tools = Tools(self, faults)
        return usbboot.Reviewed('2.57', self.root/work, self.image.parent, '/diskos', '/libusb.dylib', run=tools, **options), tools

    def reviewed_entry(self, *faults, work='usb'):
        """The tools after the entry's evidence and its review, ready for the write."""
        reviewed, tools = self.reviewed(*faults, work=work)
        reviewed.builds()
        reviewed.entry()
        reviewed.review_known()
        return reviewed, tools

    def test_the_releases_payloads_are_taken_rather_than_built(self):
        """Plan, stage 6: with the release's prebuilt payloads nothing is compiled on the computer."""
        payloads = self.root/'payloads'
        for mode in ('metadata', 'rootfs', 'staging-check', 'rootfs-probe', 'restart', 'boot-evidence'):
            (payloads/mode).mkdir(parents=True)
            (payloads/mode/'identity.bin').write_bytes(mode.encode())
        reviewed, tools = self.reviewed(payloads=payloads)
        reviewed.builds()
        self.assertEqual(tools.calls, [])
        self.assertEqual((reviewed.probe_build/'identity.bin').read_bytes(), b'rootfs-probe')
        self.assertEqual((reviewed.staging_build/'identity.bin').read_bytes(), b'staging-check')
        self.assertEqual((reviewed.boot_build/'identity.bin').read_bytes(), b'boot-evidence')

    def test_the_entry_s_evidence_is_read_audited_and_reviewed_before_the_write(self):
        reviewed, tools = self.reviewed()
        reviewed.builds()
        page = reviewed.entry()
        decision = reviewed.review_known()
        reviewed.write()
        reviewed.audit(read=False)
        self.assertEqual(tools.calls, ['build_identity.py']*5 + ['boot_evidence.py build',
                         'ram_transport.py plan', 'ram_transport.py acquire',
                         'boot_evidence.py plan', 'boot_evidence.py acquire', 'audit_usb_boot.py',
                         'collect_rootfs.py plan', 'collect_rootfs.py acquire', 'audit_usb_probe.py',
                         'installation_review.py', 'writer_transport.py plan', 'writer_transport.py acquire', 'audit_usb_write.py'])
        self.assertEqual(decision['found']['kind'], 'stock')
        self.assertEqual(page, self.root/'usb/boot/metadata-main.bin', 'the page the boot evidence read again')
        review = next(c for c in tools.commands if Path(c[2]).name == 'installation_review.py')
        self.assertIn('--known', review)
        self.assertEqual(review[review.index('--probe-capture') + 1], str(self.root/'usb/identity'))
        for command in (c for c in tools.commands if Path(c[2]).name == 'writer_transport.py'):
            self.assertEqual(command[command.index('--installer-profile') + 1], str(self.root/'usb/package/proposed-installer-profile.json'),
                             'the run\'s admission; the tracked profile is not touched')
            self.assertEqual(command[command.index('--metadata-page') + 1], str(page))
        # The staging check's build goes to the review, the write and its audit.
        staging = str(self.root/'usb/build-staging')
        for name in ('installation_review.py', 'writer_transport.py', 'audit_usb_write.py'):
            for command in (c for c in tools.commands if Path(c[2]).name == name):
                self.assertEqual(command[command.index('--staging-build') + 1], staging, name)
        audit = next(c for c in tools.commands if Path(c[2]).name == 'audit_usb_probe.py')
        self.assertEqual(audit[audit.index('--plan') + 1], str(self.root/'usb/identity-plan.json'))
        self.assertTrue((self.root/'usb/identity-plan.json').is_file())

    def test_the_write_follows_the_review(self):
        reviewed, tools = self.reviewed()
        reviewed.builds()
        with self.assertRaisesRegex(usbboot.ReviewedError, 'follows the review'):
            reviewed.write()
        self.assertNotIn('writer_transport.py plan', tools.calls)

    def test_an_unknown_outcome_stops_everything(self):
        reviewed, tools = self.reviewed_entry('outcome-unknown')
        with self.assertRaisesRegex(usbboot.ReviewedError, 'outcome is unknown: keep the player powered'):
            reviewed.write()

    def test_a_plan_that_changed_never_reaches_the_writer(self):
        reviewed, tools = self.reviewed_entry('plan-differs')
        with self.assertRaisesRegex(usbboot.ReviewedError, 'differs from the approved one'):
            reviewed.write()
        self.assertNotIn('writer_transport.py acquire', tools.calls)

    def test_a_readback_compares_every_byte_and_one_that_differs_is_refused(self):
        reviewed, tools = self.reviewed_entry()
        written = reviewed.write()
        read = reviewed.readback()
        reviewed.audit()
        self.assertEqual((written['session'], read['image'], read['exact']),
                         ('write-session', self.image.name, 'saved-logical-readback-matches'))
        self.assertEqual(tools.calls[-5:], ['collect_rootfs.py acquire', 'readback.py plan', 'readback.py verify',
                                            'audit_usb_write.py', 'audit_usb_readback.py'])
        reviewed, tools = self.reviewed_entry('readback-differs', work='again')
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
        reviewed = usbboot.Reviewed('2.57', self.root/'usb', self.image.parent, '/diskos', '/libusb.dylib',
                                    progress=lambda label, fraction, seconds: seen.append((label, fraction)))
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
        reviewed = usbboot.Reviewed('2.57', self.root/'usb', self.image.parent, '/diskos', '/libusb.dylib',
                                    progress=lambda label, fraction, seconds: seen.append((fraction, seconds)))
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

    def install(self, words, restore=False, faults=(), work='run'):
        """install.py: the card given (not a dry run), what the owner types in order."""
        from unittest import mock
        import builtins
        catalog = self.root/'packages.json'
        catalog.write_text(json.dumps(CATALOG))
        card = self.root/'card'
        card.mkdir(exist_ok=True)
        args = argparse.Namespace(dry_run=False, yes=False, plain=True, ota=None, image=str(self.image), emulator=None, card=str(card),
                                  package=None, app=None, packages_from=[], download=False, work=str(self.root/work),
                                  catalog=str(catalog), simulate=None, simulate_small=False, fault=None, restore=restore, guest=False,
                                  diskos='/diskos', libusb='/libusb.dylib')
        tools = Tools(self, faults)
        installer = flow.Installer(args, tui.Screen(look='plain', stream=io.StringIO()), runner=tools)
        installer.interactive = True
        installer.fetch_check = getattr(self, 'fetch', lambda sha256, card: None)
        remaining = list(words)
        with mock.patch.object(builtins, 'input', side_effect=lambda prompt='': remaining.pop(0)), \
                mock.patch.object(tui, 'read_key', return_value='enter'):
            code = installer.run()
        self.assertEqual(remaining, [], 'every word was asked for')
        return code, installer, tools

    def check_matches(self, sha256, card):
        return (json.dumps(dict(schema=1, expected=sha256, actual=sha256, bytes=self.image.stat().st_size,
                                device='/dev/mtdblock_bbt_ro2', match=True, seconds=2.0, error=None, build='b')) + '\n').encode()

    def test_an_installation_is_one_entry_and_the_first_start_proves_it_without_a_question(self):
        """Owner, 2026-10-11: the first start's check that comes back and matches says the system started and holds
        the written image, so nobody is asked whether it started."""
        fetched = []

        def fetch(sha256, card):
            fetched.append((sha256, card))
            return self.check_matches(sha256, card)
        self.fetch = fetch
        code, installer, tools = self.install(['', 'CARD', 'CHECK', 'WRITE'])
        self.assertEqual((code, installer.report['status']), (0, 'prepared'))
        expected = json.loads((self.root/'card/.disc/boot/expected-rootfs.json').read_text())
        self.assertEqual(expected['sha256'], hashlib.sha256(self.image.read_bytes()).hexdigest())
        self.assertEqual(fetched, [(expected['sha256'], '/tmp/sdcard')])
        player = next(s for s in installer.report['steps'] if s['step'] == 'player')
        self.assertEqual((player['written'], player['target'], player['backup']['found']['kind']), (True, 'candidate', 'stock'))
        self.assertNotIn('ownerAnswer', player)
        proof = self.root/'run/usb/first-start'
        self.assertEqual(Path(player['firstStart']), proof.resolve())
        self.assertEqual(sorted(p.name for p in proof.iterdir()), ['fetch.json', 'rootfs-check.json'])
        fetch_record = json.loads((proof/'fetch.json').read_text())
        self.assertEqual(fetch_record['rootfs_check_sha256'], hashlib.sha256((proof/'rootfs-check.json').read_bytes()).hexdigest())
        acquired = [c for c in tools.calls if c.endswith('acquire')]
        self.assertEqual(acquired, ['ram_transport.py acquire', 'boot_evidence.py acquire', 'collect_rootfs.py acquire',
                                    'writer_transport.py acquire'], 'one entry: three reads and the write; no readback')
        self.assertNotIn('audit_usb_readback.py', tools.calls)
        screen = ' '.join(installer.screen.out.getvalue().split())
        self.assertNotIn('Did it start normally', screen)

    def test_a_check_that_does_not_come_back_asks_and_a_yes_reads_it_back(self):
        """Without the check the owner's word decides (owner, 2026-10-11); a yes is the readback in a fresh entry."""
        code, installer, tools = self.install(['', 'CARD', 'CHECK', 'WRITE', 'yes', 'READ'])
        self.assertEqual((code, installer.report['status']), (0, 'prepared'))
        acquired = [c for c in tools.calls if c.endswith('acquire')]
        self.assertEqual(acquired[-2:], ['writer_transport.py acquire', 'collect_rootfs.py acquire'], 'the write, then the readback')
        self.assertIn('audit_usb_readback.py', tools.calls)
        player = next(s for s in installer.report['steps'] if s['step'] == 'player')
        self.assertEqual((player['ownerAnswer'], player['read']['exact']), ('yes', 'saved-logical-readback-matches'))
        screen = ' '.join(installer.screen.out.getvalue().split())
        self.assertIn('check did not come back over the cable', screen)
        self.assertIn('Did it start normally', screen)

    def test_a_check_that_differs_asks_too(self):
        self.fetch = lambda sha256, card: (json.dumps(dict(schema=1, expected=sha256, actual='0' * 64, match=False,
                                                           error=None)) + '\n').encode()
        code, installer, tools = self.install(['', 'CARD', 'CHECK', 'WRITE', 'yes', 'READ'])
        self.assertEqual((code, installer.report['status']), (0, 'prepared'))
        self.assertIn('audit_usb_readback.py', tools.calls)
        screen = ' '.join(installer.screen.out.getvalue().split())
        self.assertIn('check differs: another image on its root device', screen)
        self.assertTrue((self.root/'run/usb/first-start/rootfs-check.json').is_file(), 'kept, for the record')

    def test_the_player_restarts_after_the_write_and_the_cable_stays(self):
        """Plan, stage 7: after the write, in its entry, the installer restarts the player itself, and the first
        start's check comes over the cable that stayed connected."""
        self.fetch = self.check_matches
        code, installer, tools = self.install(['', 'CARD', 'CHECK', 'WRITE'], faults=('restart',))
        self.assertEqual((code, installer.report['status']), (0, 'prepared'))
        acquired = [c for c in tools.calls if c.endswith('acquire')]
        self.assertEqual(acquired[-2:], ['writer_transport.py acquire', 'restart_player.py acquire'])
        self.assertIn('audit_usb_restart.py', tools.calls)
        screen = ' '.join(installer.screen.out.getvalue().split())
        self.assertIn('keep the cable connected', screen)
        self.assertIn('the cable still connected', screen)
        self.assertNotIn('Disconnect the cable', screen)
        player = next(s for s in installer.report['steps'] if s['step'] == 'player')
        self.assertEqual(player['restart']['outcome'], 'player-restarted', 'the report names the restart\'s outcome')

    def test_without_a_restart_the_cable_is_the_way_as_before(self):
        self.fetch = self.check_matches
        code, installer, tools = self.install(['', 'CARD', 'CHECK', 'WRITE'], faults=('restart', 'no-restart'))
        self.assertEqual((code, installer.report['status']), (0, 'prepared'), 'the write stands; no restart is no stop')
        self.assertIn('restart_player.py acquire', tools.calls)
        screen = ' '.join(installer.screen.out.getvalue().split())
        self.assertIn('Disconnect the cable', screen)
        self.assertIn('connect the cable to this computer again', screen)
        player = next(s for s in installer.report['steps'] if s['step'] == 'player')
        self.assertEqual(player['restart']['outcome'], 'restart-not-observed')

    def test_an_image_the_review_does_not_know_is_not_written(self):
        code, installer, tools = self.install(['', 'CARD', 'CHECK'], faults=('unknown-image',))
        self.assertEqual(code, 1)
        self.assertIn('does not know', installer.report['status'])
        self.assertIn('Local upgrade', installer.report['status'])
        self.assertNotIn('writer_transport.py acquire', tools.calls)

    def test_restore_takes_a_known_player_back_to_stock_in_one_entry(self):
        code, installer, tools = self.install(['CHECK', 'RESTORE', 'yes'], restore=True)
        self.assertEqual((code, installer.report['status']), (0, 'restored'))
        write = next(c for c in tools.commands if Path(c[2]).name == 'writer_transport.py' and c[3] == 'acquire')
        self.assertEqual(write[write.index('--target') + 1], 'restore')
        self.assertEqual([c for c in tools.calls if c.endswith('acquire')],
                         ['ram_transport.py acquire', 'boot_evidence.py acquire', 'collect_rootfs.py acquire',
                          'writer_transport.py acquire'], 'the reads and stock, in one entry; no readback')
        self.assertTrue(installer.report['steps'][-1]['restore']['startedNormally'], 'stock has no check: the owner\'s word')

    def test_restore_from_any_state_asks_once_more(self):
        """Owner, 2026-10-05/09: back to stock from any state. An image the review does not know admits the
        restore only, after the word STOCK that names the kernel's risk; a candidate is never written over it."""
        code, installer, tools = self.install(['CHECK', 'STOCK', 'RESTORE', 'yes'], restore=True, faults=('unknown-image',))
        self.assertEqual((code, installer.report['status']), (0, 'restored'))
        review = next(c for c in tools.commands if Path(c[2]).name == 'installation_review.py')
        self.assertIn('--allow-unknown', review)
        self.assertEqual(installer.report['steps'][-1]['restore']['found'], dict(kind='unknown'))
        code, installer, tools = self.install(['CHECK', 'other'], restore=True, faults=('unknown-image',), work='again')
        self.assertEqual(code, 1)
        self.assertNotIn('writer_transport.py acquire', tools.calls, 'no word, no write')

    def test_a_no_goes_back_to_stock_with_the_evidence_of_a_new_entry(self):
        code, installer, tools = self.install(['', 'CARD', 'CHECK', 'WRITE', 'no', 'CHECK', 'RESTORE', 'yes'])
        self.assertEqual((code, installer.report['status']), (0, 'restored'))
        self.assertEqual([c for c in tools.calls if c.endswith('acquire')],
                         ['ram_transport.py acquire', 'boot_evidence.py acquire', 'collect_rootfs.py acquire', 'writer_transport.py acquire']*2)
        back = self.root/'run/usb-back'
        self.assertTrue((back/'package/decision.json').is_file() and (back/'restore-write').is_dir())
        self.assertEqual(installer.report['wayBack']['candidateAnswer'], 'no', 'yes or no only, no words')

    def test_without_a_terminal_the_player_is_not_written(self):
        from unittest import mock
        catalog = self.root/'packages.json'
        catalog.write_text(json.dumps(CATALOG))
        for restore in (False, True):
            args = argparse.Namespace(dry_run=not restore, yes=True, plain=True, ota=None, image=str(self.image), emulator=None, card=None,
                                      package=None, app=None, packages_from=[], download=False, work=str(self.root/f'run-{restore}'),
                                      catalog=str(catalog), simulate=None, simulate_small=False, fault=None, restore=restore,
                                      guest=False, diskos='/diskos', libusb='/libusb.dylib')
            tools = Tools(self, ())
            installer = flow.Installer(args, tui.Screen(look='plain', stream=io.StringIO()), runner=tools)
            with mock.patch.object(tui, 'read_key', return_value='enter'):
                code = installer.run()
            self.assertFalse([c for c in tools.calls if c.endswith('acquire')])
            if restore:
                self.assertEqual(code, 1)
                self.assertIn('typed', installer.report['status'])
            else:
                self.assertEqual((code, installer.report['steps'][4]['written']), (0, False))


if __name__ == '__main__':
    unittest.main()
