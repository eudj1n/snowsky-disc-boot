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
        self.pages = [bytes([1]) * 2048, bytes([2]) * 2048]

    def value(self, command, name):
        return command[command.index(name) + 1]

    def __call__(self, command, cwd=None, capture_output=True, text=True):
        tool, args = Path(command[2]).name, command[3:]
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

    def reviewed(self, *faults):
        tools = Tools(self, faults)
        return usbboot.Reviewed('2.57', self.root/'usb', self.image.parent, '/diskos', '/libusb.dylib', self.history, run=tools,
                                profile=self.profile), tools

    def test_the_installation_follows_the_procedure(self):
        reviewed, tools = self.reviewed()
        reviewed.prepare()
        backup = reviewed.backup()
        written = reviewed.write()
        read = reviewed.readback()
        reviewed.audit()
        self.assertEqual(tools.calls, ['build_identity.py', 'build_identity.py', 'build_identity.py', 'installation_review.py',
                                       'collect_rootfs.py acquire', 'readback.py plan', 'readback.py verify',
                                       'writer_transport.py plan', 'writer_transport.py acquire',
                                       'collect_rootfs.py acquire', 'readback.py plan', 'readback.py verify',
                                       'audit_usb_write.py', 'audit_usb_readback.py'])
        self.assertEqual(backup['matches'], 'combined.bin', 'the backup is what the history says is installed')
        self.assertTrue(tools.admission_at_write, 'admission open for the write')
        profile = json.loads(self.profile.read_text())
        self.assertEqual((profile['physical_write_admitted'], profile['installation_review_sha256']), (False, 'new-pin'))
        self.assertEqual((written['session'], read['image'], read['exact']),
                         ('write-session', self.image.name, 'saved-logical-readback-matches'))
        history = json.loads(reviewed.next_history(self.image).read_text())
        self.assertEqual(history['previousImage'], str(self.image))

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

    def install(self, words=None, restore=False, run=None):
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
                                  package=None, app=None, packages_from=[], download=False, work=str(self.root/('again' if run else 'run')),
                                  catalog=str(catalog), simulate=None, simulate_small=False, fault=None, restore=restore, guest=False,
                                  history=str(self.root/'history.json'), diskos='/diskos', libusb='/libusb.dylib', run=run)
        tools = self.tools = Tools(self, getattr(self, 'tools_faults', ()))
        installer = flow.Installer(args, tui.Screen(look='plain', stream=io.StringIO()), runner=tools)
        installer.interactive = words is not None
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
        code, installer, tools = self.install(['BACKUP', 'WRITE', 'yes', 'the stock UI came up, the volume works', 'READ'])
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

    def test_the_read_by_digest_goes_beside_the_backup_only_and_never_stops(self):
        """One read by digest a session, beside the backup (owner, 2026-10-05): it measures what a
        page takes; after the readback it would add half an hour for nothing new."""
        code, installer, tools = self.install(['BACKUP', 'WRITE', 'yes', 'fine', 'READ'])
        player = installer.report['steps'][4]
        self.assertTrue(player['backup']['digest']['agreesWithFullRead'])
        self.assertTrue((self.root/'run/usb/digest-backup/beside-full-read.json').is_file())
        self.assertNotIn('digest', player['read'])
        self.assertFalse((self.root/'run/usb/digest-read').exists())
        shutil.rmtree(self.root/'run')
        self.tools_faults = {'digest-differs'}
        code, installer, tools = self.install(['BACKUP', 'WRITE', 'yes', 'fine', 'READ'])
        self.assertEqual((code, installer.report['status']), (0, 'prepared'), 'the full read stands')
        self.assertFalse(installer.report['steps'][4]['backup']['digest']['agreesWithFullRead'])

    def test_a_no_takes_the_player_back_to_stock(self):
        """The new system did not start: straight to stock in a fresh entry (the player holds this
        run's own image, nothing to back up), the owner's look at stock, stock's readback; the
        history is the restore."""
        code, installer, tools = self.install(['BACKUP', 'WRITE', 'no', 'its UI restarts without end',
                                               'RESTORE', 'yes', 'stock starts, the volume works', 'READ'])
        self.assertEqual((code, installer.report['status']), (0, 'restored'))
        self.assertEqual([c for c in tools.calls if c in ('writer_transport.py acquire', 'collect_rootfs.py acquire')],
                         ['collect_rootfs.py acquire', 'collect_rootfs.py acquire', 'writer_transport.py acquire',
                          'writer_transport.py acquire', 'collect_rootfs.py acquire'],
                         'backup and its digest read, write, stock, its readback; no readback of the failed image')
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
        self.assertFalse((usb/'restore-backup').exists())
        self.assertEqual(json.loads((usb/'history.json').read_text())['previousTarget'], 'restore')
        for folder in ('restore-write', 'restore-read'):
            shutil.rmtree(usb/folder)
        (usb/'write/result.json').unlink()        # the write's outcome unknown: what the player holds is not known
        shutil.rmtree(self.root/'again')
        code, installer, tools = self.install(['BACKUP', 'RESTORE', 'yes', 'stock is back', 'READ'], restore=True, run=str(self.root/'run'))
        self.assertEqual((code, installer.report['status']), (0, 'restored'))
        self.assertTrue((usb/'restore-backup/result.json').is_file())

if __name__ == '__main__':
    unittest.main()
