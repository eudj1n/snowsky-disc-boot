"""install.py's player through the reviewed tools (scripts/installer/usbboot.py), with the tools
replaced by a stand-in that leaves their outputs: the order of the steps, the admission opened only
for the write and closed in every case, and each refusal."""
import argparse
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
        self.test, self.faults, self.calls, self.admission_at_write = test, set(faults), [], None

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
                     'candidate-write-plan.json': dict(plan=dict(image_name=self.test.image.name), plan_sha256='write-plan'),
                     'restore-write-plan.json': dict(plan=dict(image_name=self.test.stock.name), plan_sha256='restore-plan'),
                     'postwrite-collection-plan.json': dict(plan={}, plan_sha256='read-plan'),
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
        elif tool == 'collect_rootfs.py':
            out.mkdir(parents=True)
            (out/'result.json').write_text(json.dumps(dict(status='rootfs-collected', nonce_hex='n1', capture_sha256='c1',
                                                           session_id=f'read-{len(self.calls)}')))
            (out/'records.bin').write_bytes(b'records')
        elif tool == 'readback.py' and args[0] == 'plan':
            image = Path(self.value(command, '--image')).name
            stdout = json.dumps(dict(plan_sha256='exact-candidate' if image == self.test.image.name else
                                     'exact-restore' if image == self.test.stock.name else 'exact-previous'))
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
        self.assertEqual(tools.calls, ['build_identity.py', 'build_identity.py', 'installation_review.py',
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

    def test_install_py_runs_the_player_through_them(self):
        (self.root/'history.json').write_text(json.dumps(self.history.data))
        catalog = self.root/'packages.json'
        catalog.write_text(json.dumps(dict(schema=1, kind='packages', entries=[dict(
            name='disc-menu', role='menu', version='9', profiles=['2.57'], bootApi=1, license='MIT', default=False,
            source=dict(url=None, sha256='0' * 64, size=1), verified=dict(date='2026-10-03', acceptance='test'))])))
        args = argparse.Namespace(dry_run=True, yes=True, plain=True, ota=None, image=str(self.image), emulator=None, card=None,
                                  package=None, app=None, packages_from=[], download=False, work=str(self.root/'run'),
                                  catalog=str(catalog), simulate=None, simulate_small=False, fault=None, restore=False, guest=False,
                                  history=str(self.root/'history.json'), diskos='/diskos', libusb='/libusb.dylib')
        tools = Tools(self)
        installer = flow.Installer(args, tui.Screen(look='plain', stream=io.StringIO()), runner=tools)
        original = usbboot.Reviewed.__init__

        def tracked_profile(reviewed, *a, **k):
            original(reviewed, *a, **dict(k, profile=self.profile))
        usbboot.Reviewed.__init__ = tracked_profile
        self.addCleanup(lambda: setattr(usbboot.Reviewed, '__init__', original))
        self.assertEqual(installer.run(), 0, installer.report['status'])
        player = installer.report['steps'][4]
        self.assertTrue(player['written'])
        self.assertEqual(player['target'], 'candidate')
        self.assertIn('audit_usb_readback.py', tools.calls)


if __name__ == '__main__':
    unittest.main()
