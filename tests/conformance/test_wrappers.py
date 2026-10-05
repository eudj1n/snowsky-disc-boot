"""The image's /sbin/mq_ui and /sbin/mq_player fail open and start every program under the bare
name stock's watch loop looks for (owner's player, 2026-10-04 and 05).

Stock's fiio_init.sh finds the pair with pgrep -x mq_ui and pgrep -x mq_player. BusyBox's pgrep
matches argv[0] first and falls back to the process name only when the pattern is nowhere in it:
a program a shell starts by its path (/usr/bin/mq_ui) is not found, and the loop kills and
restarts the pair every few seconds. Under qemu-user cmdline begins with the interpreter, pgrep
falls back to the name, and the guest never shows it; hence this check of argv[0] itself, with
compiled stand-ins (a script's argv[0] is lost to its interpreter). The wrappers use exec -a,
which BusyBox ash (the player's shell) and bash have; dash has not and starts by path.

Run under every POSIX shell at hand (dash and BusyBox ash end a script on a failed redirection of a
special built-in and on a failed exec), with their paths moved into a folder of the test, they start
stock's program whatever the boot layer left: no run folder, a run folder that cannot be written, a
launcher that is missing. The boot layer's launcher runs only on its ui-launch."""
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

# Resolved here: the wrappers run with the PATH stock gives them, which may not hold the shell.
SHELLS = [[shutil.which(s[0]), *s[1:]] for s in (['dash'], ['busybox', 'sh'], ['bash', '--posix'], ['sh']) if shutil.which(s[0])]
COMPILER = shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')


def has_exec_a(shell):
    return subprocess.run([*shell, '-c', '(exec -a true true) 2>/dev/null']).returncode == 0
# Records which program ran, the argv[0] it was given and its arguments, then its PATH.
STUB = r'''#include <stdio.h>
#include <stdlib.h>
int main(int argc, char **argv) {
    FILE *f = fopen(OUT, "w");
    if (!f) return 1;
    fprintf(f, "%s", WHO);
    for (int i = 0; i < argc; i++) fprintf(f, " %s", argv[i]);
    fprintf(f, "\n%s\n", getenv("PATH") ? getenv("PATH") : "");
    return fclose(f) != 0;
}
'''


@unittest.skipUnless(COMPILER, 'a C compiler builds the stand-ins that record argv[0]')
class WrapperTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.ran = self.root/'ran'
        source = self.root/'stub.c'
        source.write_text(STUB)
        for who, paths in (('stock', ('usr/bin/mq_ui', 'usr/bin/mq_player')),
                           ('launcher', ('opt/disc-boot/disc-boot',))):
            first = self.root/paths[0]
            first.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run([COMPILER, f'-DOUT="{self.ran}"', f'-DWHO="{who}"', '-o', str(first), str(source)], check=True)
            for path in paths[1:]:
                shutil.copy2(first, self.root/path)
        for name in ('mq_ui', 'mq_player'):
            (self.root/'opt/disc-boot'/name).symlink_to('disc-boot')
        (self.root/'opt/disc-boot/guard').mkdir()

    def wrapper(self, text):
        """The wrapper with its absolute paths under this test's root."""
        for path in ('/run/disc-boot', '/usr/bin/', '/opt/disc-boot', '/usr/data'):
            text = text.replace(path, f'{self.root}{path}')
        out = self.root/'wrapper'
        out.write_text(text)
        out.chmod(0o755)
        return out

    def run_wrapper(self, shell, text):
        self.ran.unlink(missing_ok=True)
        # The PATH stock's fiio_init.sh runs with: the wrappers' own folder ahead of stock's.
        env = dict(os.environ, PATH=f'{self.root}/sbin:/sbin:/usr/sbin:/bin:/usr/bin')
        result = subprocess.run([*shell, str(self.wrapper(text)), '--arg'], capture_output=True, text=True, timeout=10, env=env)
        return result.returncode, self.ran.read_text().splitlines()[0] if self.ran.exists() else None

    def started_path(self):
        return self.ran.read_text().splitlines()[1].split(':')

    def named(self, shell, who, name):
        """What the stand-in records: argv[0] the bare name where the shell has exec -a."""
        path = {'stock': f'{self.root}/usr/bin/{name}', 'launcher': f'{self.root}/opt/disc-boot/{name}'}[who]
        return f'{who} {name if has_exec_a(shell) else path} --arg'

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
        self.assertTrue(any(Path(s[0]).name in ('dash', 'busybox') for s in SHELLS), 'dash or BusyBox is needed to check the device\'s semantics')

    def test_the_player_s_shell_and_bash_give_the_bare_name(self):
        for shell in SHELLS:
            if Path(shell[0]).name in ('busybox', 'bash'):
                with self.subTest(shell=Path(shell[0]).name):
                    self.assertTrue(has_exec_a(shell))
                    self.run_folder()
                    self.assertEqual(self.run_wrapper(shell, candidate.ui_wrapper()), (0, 'stock mq_ui --arg'))

    def test_stock_starts_whatever_the_boot_layer_left(self):
        for shell in SHELLS:
            for name, text in (('mq_player', candidate.player_wrapper()), ('mq_ui', candidate.ui_wrapper())):
                stock = self.named(shell, 'stock', name)
                with self.subTest(shell=Path(shell[0]).name, wrapper=name, case='no run folder'):
                    shutil.rmtree(self.root/'run', ignore_errors=True)
                    self.assertEqual(self.run_wrapper(shell, text), (0, stock))
                if os.geteuid() != 0:
                    with self.subTest(shell=Path(shell[0]).name, wrapper=name, case='a run folder that cannot be written'):
                        self.run_folder(writable=False)
                        self.assertEqual(self.run_wrapper(shell, text), (0, stock))
                with self.subTest(shell=Path(shell[0]).name, wrapper=name, case='ui-launch without the launcher'):
                    self.run_folder('ui-launch')
                    (self.root/'opt/disc-boot'/name).unlink()
                    self.assertEqual(self.run_wrapper(shell, text), (0, stock))
                    (self.root/'opt/disc-boot'/name).symlink_to('disc-boot')

    def test_the_card_guard_comes_first_in_the_started_program_s_path(self):
        guard = f'{self.root}/opt/disc-boot/guard'
        for shell in SHELLS:
            for name, text in (('mq_player', candidate.player_wrapper()), ('mq_ui', candidate.ui_wrapper())):
                for files in ((), ('ui-launch',)):
                    with self.subTest(shell=Path(shell[0]).name, wrapper=name, run=files):
                        self.run_folder(*files)
                        self.run_wrapper(shell, text)
                        path = self.started_path()
                        self.assertEqual(path[0], guard)
                        self.assertEqual(path[1], f'{self.root}/sbin', 'stock\'s own PATH follows')

    def test_each_start_is_logged_while_the_log_has_room(self):
        log = self.root/'usr/data/disc-boot/boot.log'
        log.parent.mkdir(parents=True)
        for shell in SHELLS:
            with self.subTest(shell=Path(shell[0]).name):
                log.unlink(missing_ok=True)
                self.run_folder()
                self.assertEqual(self.run_wrapper(shell, candidate.ui_wrapper()), (0, self.named(shell, 'stock', 'mq_ui')))
                self.run_folder('ui-launch')
                self.assertEqual(self.run_wrapper(shell, candidate.player_wrapper()), (0, self.named(shell, 'launcher', 'mq_player')))
                lines = [line.split(' ', 1)[1] for line in log.read_text().splitlines()]
                self.assertEqual(lines, ['mq_ui', 'mq_player ui-launch'])
                log.write_bytes(b'x' * candidate.BOOT_LOG_BYTES)
                self.assertEqual(self.run_wrapper(shell, candidate.ui_wrapper()), (0, self.named(shell, 'launcher', 'mq_ui')))
                self.assertEqual(log.stat().st_size, candidate.BOOT_LOG_BYTES, 'a full log is left as it is')

    def test_the_player_is_marked_when_stock_starts_it(self):
        for shell in SHELLS:
            with self.subTest(shell=Path(shell[0]).name):
                run = self.run_folder()
                self.run_wrapper(shell, candidate.player_wrapper())
                self.assertTrue((run/'player-ran').exists())

    def test_the_launcher_runs_only_on_ui_launch(self):
        for shell in SHELLS:
            with self.subTest(shell=Path(shell[0]).name):
                self.run_folder('ui-launch')
                self.assertEqual(self.run_wrapper(shell, candidate.ui_wrapper()), (0, self.named(shell, 'launcher', 'mq_ui')))
                self.assertEqual(self.run_wrapper(shell, candidate.player_wrapper()), (0, self.named(shell, 'launcher', 'mq_player')))
                run = self.run_folder('ui-launch')
                (run/'ui/fallback').write_text('1\n')
                self.assertEqual(self.run_wrapper(shell, candidate.player_wrapper()), (0, self.named(shell, 'stock', 'mq_player')),
                                 'after a fallback to stock\'s UI stock\'s player starts')


if __name__ == '__main__':
    unittest.main()
