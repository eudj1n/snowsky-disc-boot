"""The image's /sbin/mq_ui and /sbin/mq_player fail open (owner's player, 2026-10-05): run under
every POSIX shell at hand (dash and BusyBox ash end a script on a failed redirection of a
special built-in and on a failed exec), with their paths moved into a folder of the test, they
start stock's binary whatever the boot layer left: no run folder, a run folder that cannot be
written, a launcher that is missing. The boot layer's launcher runs only on its ui-launch."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('candidate', ROOT/'scripts/deployment/build_candidate.py')
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)

SHELLS = [s for s in (['dash'], ['busybox', 'sh'], ['bash', '--posix'], ['sh']) if shutil.which(s[0])]
STUB = '#!/bin/sh\necho "$0 $*" > "{out}"\n'


class WrapperTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.ran = self.root/'ran'
        for path in ('usr/bin/mq_ui', 'usr/bin/mq_player', 'opt/disc-boot/mq_ui', 'opt/disc-boot/mq_player'):
            (self.root/path).parent.mkdir(parents=True, exist_ok=True)
            (self.root/path).write_text(STUB.format(out=self.ran))
            (self.root/path).chmod(0o755)
        (self.root/'opt/disc-boot/guard').mkdir()

    def wrapper(self, text):
        """The wrapper with its absolute paths under this test's root."""
        for path in ('/run/disc-boot', '/usr/bin/', '/opt/disc-boot'):
            text = text.replace(path, f'{self.root}{path}')
        out = self.root/'wrapper'
        out.write_text(text)
        out.chmod(0o755)
        return out

    def run_wrapper(self, shell, text):
        self.ran.unlink(missing_ok=True)
        result = subprocess.run([*shell, str(self.wrapper(text)), '--arg'], capture_output=True, text=True, timeout=10)
        return result.returncode, self.ran.read_text().strip() if self.ran.exists() else None

    def run_folder(self, *files, writable=True):
        run = self.root/'run/disc-boot'
        if run.exists():
            run.chmod(0o755)
            shutil.rmtree(run)
        (run/'ui').mkdir(parents=True)
        for name in files:
            (run/name).write_text('ui\n')
        if not writable:
            run.chmod(0o555)
            self.addCleanup(lambda: run.chmod(0o755))
        return run

    def test_there_is_a_shell_that_ends_on_a_failed_special_built_in(self):
        self.assertTrue(any(s[0] in ('dash', 'busybox') for s in SHELLS), 'dash or BusyBox is needed to check the device\'s semantics')

    def test_stock_starts_whatever_the_boot_layer_left(self):
        for shell in SHELLS:
            for name, text in (('mq_player', candidate.player_wrapper()), ('mq_ui', candidate.ui_wrapper())):
                stock = f'{self.root}/usr/bin/{name} --arg'
                with self.subTest(shell=shell[0], wrapper=name, case='no run folder'):
                    shutil.rmtree(self.root/'run', ignore_errors=True)
                    self.assertEqual(self.run_wrapper(shell, text), (0, stock))
                if os.geteuid() != 0:
                    with self.subTest(shell=shell[0], wrapper=name, case='a run folder that cannot be written'):
                        self.run_folder(writable=False)
                        self.assertEqual(self.run_wrapper(shell, text), (0, stock))
                with self.subTest(shell=shell[0], wrapper=name, case='ui-launch without the launcher'):
                    self.run_folder('ui-launch')
                    (self.root/f'opt/disc-boot/{name}').chmod(0o644)
                    self.assertEqual(self.run_wrapper(shell, text), (0, stock))
                    (self.root/f'opt/disc-boot/{name}').chmod(0o755)

    def test_the_player_is_marked_when_stock_starts_it(self):
        for shell in SHELLS:
            with self.subTest(shell=shell[0]):
                run = self.run_folder()
                self.run_wrapper(shell, candidate.player_wrapper())
                self.assertTrue((run/'player-ran').exists())

    def test_the_launcher_runs_only_on_ui_launch(self):
        for shell in SHELLS:
            with self.subTest(shell=shell[0]):
                self.run_folder('ui-launch')
                self.assertEqual(self.run_wrapper(shell, candidate.ui_wrapper()), (0, f'{self.root}/opt/disc-boot/mq_ui --arg'))
                self.assertEqual(self.run_wrapper(shell, candidate.player_wrapper()),
                                 (0, f'{self.root}/opt/disc-boot/mq_player --arg'))
                run = self.run_folder('ui-launch')
                (run/'ui/fallback').write_text('1\n')
                self.assertEqual(self.run_wrapper(shell, candidate.player_wrapper()), (0, f'{self.root}/usr/bin/mq_player --arg'),
                                 'after a fallback to stock\'s UI stock\'s player starts')


if __name__ == '__main__':
    unittest.main()
