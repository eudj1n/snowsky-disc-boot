"""console.py against a stand-in player: a pseudo-terminal whose other end runs each line it gets
in a shell and answers with its output and a "# " prompt, as the player's console does."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('console_tool', ROOT/'console.py')
console = importlib.util.module_from_spec(spec)
spec.loader.exec_module(console)


class StandIn:
    """The player's end of the port: lines in, a shell's answers out."""

    def __init__(self, path):
        self.master, slave = os.openpty()
        self.port = os.ttyname(slave)
        self.slave = slave
        self.env = dict(os.environ, PATH=f'{path}:{os.environ["PATH"]}')
        self.stopped = False
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        buffer = b''
        while not self.stopped:
            try:
                chunk = os.read(self.master, 65536)
            except OSError:
                return
            buffer += chunk
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                if not line.strip():
                    os.write(self.master, b'# ')
                    continue
                done = subprocess.run(['sh', '-c', line.decode()], capture_output=True, env=self.env)
                os.write(self.master, (done.stdout + done.stderr).replace(b'\n', b'\r\n') + b'# ')

    def close(self):
        self.stopped = True
        os.close(self.slave)
        os.close(self.master)


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        tools = self.root/'bin'
        tools.mkdir()
        if not shutil.which('sha256sum'):
            (tools/'sha256sum').write_text('#!/bin/sh\nexec shasum -a 256 "$@"\n')
            (tools/'sha256sum').chmod(0o755)
        self.player = StandIn(tools)
        self.addCleanup(self.player.close)
        self.console = console.Console(self.player.port)
        self.addCleanup(self.console.close)

    def test_a_command_answers_with_its_output_and_status(self):
        self.assertEqual(self.console.run('echo hello; echo world'), ('hello\nworld', 0))
        self.assertEqual(self.console.run('false')[1], 1)
        self.assertEqual(self.console.run('printf "a b"'), ('a b', 0))

    def test_it_never_reads_identity_or_secrets(self):
        for command in ('cat /usr/data/fiio/sn.txt', 'cat /sys/class/net/wlan0/address', 'cat /tmp/token', 'cat /dev/mtd6ro', 'dd if=/dev/mtd6 bs=64 count=1'):
            with self.subTest(command), self.assertRaisesRegex(console.ConsoleError, 'refused'):
                self.console.run(command)
        for name, command in console.FACTS.items():
            with self.subTest(name):
                self.assertIsNone(console.PRIVATE.search(command), 'the summary reads nothing private')

    def test_a_file_reaches_tmp_checked_by_its_digest(self):
        local = self.root/'tool'
        local.write_bytes(os.urandom(70000))
        remote = f'/tmp/disc-console-test-{os.getpid()}'
        self.addCleanup(lambda: Path(remote).unlink(missing_ok=True))
        sent = self.console.send(local, remote)
        self.assertEqual(Path(remote).read_bytes(), local.read_bytes())
        self.assertEqual(sent['bytes'], 70000)
        self.assertFalse(Path(remote + '.b64').exists())
        with self.assertRaisesRegex(console.ConsoleError, 'under /tmp/ only'):
            self.console.send(local, '/usr/data/tool')


if __name__ == '__main__':
    unittest.main()
