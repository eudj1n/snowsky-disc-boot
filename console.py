#!/usr/bin/env python3
"""The player's USB console from this computer (plan; contract, "USB console").

With the card's marker (.disc/dev/usb-console) the player offers a root shell over a USB ACM
port. This tool finds the port, runs commands and returns their output and exit status, gives
an interactive shell, sends a file into the player's /tmp with its SHA-256 checked there, and
reads a summary of facts. It never reads the serial number, the MAC address or tokens, and
refuses commands that name them. It writes nothing to the player but what it is asked to send
or run.

    python3 console.py find
    python3 console.py run 'uname -r' 'cat /proc/uptime'
    python3 console.py shell                     # Ctrl-] leaves
    python3 console.py send build/mips/tool /tmp/tool
    python3 console.py facts
"""
import argparse
import base64
import glob
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import select
import sys
import termios
import time
import tty

# Never read through this tool: the player's identity and secrets (owner's rule).
PRIVATE = re.compile(r'sn\.txt|/sn\b|serial|\bmac\b|/mac/|address\b|token|secret|\.key\b|/dev/mtd', re.I)
KILL_LINE = '\x15'
PORTS = ('/dev/cu.usbmodemdisc_web_debug*', '/dev/cu.usbmodem*', '/dev/ttyACM*')


class ConsoleError(Exception):
    pass


def find_ports():
    found = []
    for pattern in PORTS:
        for port in sorted(glob.glob(pattern)):
            if port not in found:
                found.append(port)
    return found


class Console:
    """One open port: raw 115200 8N1, lines ended with \\n (the player's shell wants them)."""

    def __init__(self, port):
        self.port = port
        try:
            self.fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as error:
            raise ConsoleError(f'{port}: {error.strerror} (is another program, such as screen, holding it?)')
        attrs = termios.tcgetattr(self.fd)
        attrs[0] = attrs[1] = attrs[3] = 0
        attrs[2] = termios.CLOCAL | termios.CREAD | termios.CS8
        attrs[4] = attrs[5] = termios.B115200
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)

    def close(self):
        os.close(self.fd)

    def write(self, text):
        data = text.encode()
        while data:
            try:
                data = data[os.write(self.fd, data):]
            except BlockingIOError:
                select.select([], [self.fd], [], 1)

    def read(self, seconds):
        out, end = b'', time.monotonic() + seconds
        while time.monotonic() < end:
            ready, _, _ = select.select([self.fd], [], [], min(0.2, max(0, end - time.monotonic())))
            if ready:
                try:
                    out += os.read(self.fd, 65536)
                except BlockingIOError:
                    pass
        return out.decode(errors='replace')

    def run(self, command, timeout=30):
        """The command's output and exit status, its end found by a marker of this call only."""
        if PRIVATE.search(command):
            raise ConsoleError('refused: the command names the serial number, the MAC address or a secret')
        nonce = secrets.token_hex(4)
        self.read(0.1)
        # Ctrl-U first: what a shell session left typed but unsent is erased, never run.
        self.write(f'{KILL_LINE}{command}; printf "\\n__disc_end_{nonce}_%s__\\n" "$?"\n')
        out, end = '', time.monotonic() + timeout
        pattern = re.compile(rf'__disc_end_{nonce}_(\d+)__')
        while time.monotonic() < end:
            out += self.read(0.3)
            found = pattern.search(out)
            if found:
                body = out[:found.start()]
                body = re.sub(r'^(# )+', '', body.replace('\r', '')).rstrip('\n')
                return body, int(found.group(1))
        raise ConsoleError(f'no answer within {timeout} s to: {command}')

    def send(self, local, remote, chunk=512):
        """A file into the player at remote (under /tmp), gzipped and in base64 lines, then its SHA-256 checked."""
        if not remote.startswith('/tmp/') or '..' in remote:
            raise ConsoleError('files go under /tmp/ only')
        data = Path(local).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        text = base64.b64encode(gzip.compress(data, mtime=0)).decode()
        staging = f'{remote}.b64'
        self.run(f'rm -f {remote} {staging}')
        for i in range(0, len(text), chunk):
            self.write(f"printf '%s' '{text[i:i + chunk]}' >> {staging}\n")
            self.read(0.05)
        out, status = self.run(f'base64 -d < {staging} | gunzip > {remote} && rm -f {staging} && sha256sum {remote}', timeout=60)
        if status or not out.strip().startswith(digest):
            raise ConsoleError(f'the copy on the player does not match: {out.strip()[:80]}')
        return dict(remote=remote, bytes=len(data), sha256=digest)

    def shell(self, echo=True):
        """Keys to the player and its answers back, until Ctrl-]. The player's shell does not echo
        what it is sent, so the keys are shown here (Backspace rubs out, Enter starts a line)."""
        stdin, stdout = sys.stdin.fileno(), sys.stdout.fileno()
        saved = termios.tcgetattr(stdin)
        try:
            tty.setraw(stdin)
            self.write('\n')
            while True:
                ready, _, _ = select.select([stdin, self.fd], [], [])
                if self.fd in ready:
                    try:
                        os.write(stdout, os.read(self.fd, 65536).replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
                    except BlockingIOError:
                        pass
                    except OSError:
                        # The player's shell exited (exit) or the cable was pulled: the port is gone.
                        os.write(stdout, b'\r\nThe console closed.\r\n')
                        return
                if stdin in ready:
                    key = os.read(stdin, 1024)
                    if b'\x1d' in key:
                        self.write(KILL_LINE)          # nothing half typed stays for the next command
                        os.write(stdout, b'\r\n')
                        return
                    if echo:
                        shown = key.replace(b'\r', b'\r\n').replace(b'\x7f', b'\b \b')
                        os.write(stdout, shown)
                    self.write(key.replace(b'\r', b'\n').replace(b'\x7f', b'\b').decode(errors='replace'))
        finally:
            termios.tcsetattr(stdin, termios.TCSADRAIN, saved)


# The read-only summary (what the sessions of 2026-10-03 took by hand): never identity or secrets.
FACTS = {
    'kernel': 'uname -r',
    'uptime': 'cut -d" " -f1 /proc/uptime',
    'firmware': 'grep -E "^[A-Z_]*VERSION[A-Z_]*=|^BUILD_TYPE=" /etc/product_version/version.in 2>/dev/null | tr -d "\\r"',
    'boot': '[ -x /opt/disc-boot/disc-boot ] && /opt/disc-boot/disc-boot status || echo none',
    # The early hook's output and exit status (the owner's player, 2026-10-05: no run folder, no way to see why).
    'early': 'tail -n 8 /run/disc-boot-early.log 2>/dev/null || echo none; ls -d /run/disc-boot 2>/dev/null || echo "no run folder"',
    # The pair as stock's watch loop sees it: pgrep -x matches argv[0] before the name (2026-10-05).
    'pair': 'for p in /proc/[0-9]*; do c=$(cat $p/comm 2>/dev/null); case "$c" in mq_ui|mq_player) echo "$c ${p#/proc/} argv0 $(tr "\\0" " " <$p/cmdline | cut -d" " -f1)";; esac; done; '
            'echo "pgrep -x: mq_ui $(pgrep -x mq_ui | wc -l), mq_player $(pgrep -x mq_player | wc -l)"; tail -n 3 /usr/data/fiio/log/process_failed.txt 2>/dev/null; true',
    # The boot log that survives a reset: each boot's section, the decision, each start of the pair.
    'bootlog': 'tail -n 24 /usr/data/disc-boot/boot.log 2>/dev/null || echo none',
    'keys': 'devmem 0x10010100 32 2>/dev/null',
    'watchdog': 'w=$(for p in /proc/[0-9]*; do for f in $p/fd/*; do case "$(readlink $f 2>/dev/null)" in *watchdog*) echo "${p#/proc/} $(cat $p/comm)";; esac; done; done); echo "${w:-nobody holds it open (stock\'s player runs cmd_watchdog per call)}"',
    'input': 'for p in /proc/[0-9]*; do for f in $p/fd/*; do t=$(readlink $f 2>/dev/null); case "$t" in */input/event*) echo "$(cat $p/comm) $t";; esac; done; done; true',
    'memory': 'grep -E "^(MemTotal|MemAvailable):" /proc/meminfo',
    'userdata': 'df -k /usr/data | tail -1',
    'partitions': 'cat /proc/mtd',
}


def facts(console):
    out = {}
    for name, command in FACTS.items():
        text, status = console.run(command)
        out[name] = text.strip() if not status else f'(exit {status}) {text.strip()}'
    if out['boot'].startswith('{'):
        try:
            out['boot'] = json.loads(out['boot'])
        except ValueError:
            pass
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--port', help='The console port (default: the first found)')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('find', help='The console ports on this computer')
    r = sub.add_parser('run', help='Run commands and print their output')
    r.add_argument('commands', nargs='+')
    r.add_argument('--timeout', type=float, default=30)
    sh = sub.add_parser('shell', help='An interactive shell (Ctrl-] leaves)')
    sh.add_argument('--no-echo', action='store_true', help='Do not show the keys here (for a shell that echoes them)')
    s = sub.add_parser('send', help='Send a file into the player\'s /tmp, checked by SHA-256')
    s.add_argument('local')
    s.add_argument('remote')
    sub.add_parser('facts', help='A read-only summary of the player')
    args = parser.parse_args()
    if args.command == 'find':
        print(json.dumps(dict(ports=find_ports()), indent=2))
        return 0
    port = args.port or next(iter(find_ports()), None)
    if not port:
        print('No console port: the player needs the card\'s marker (.disc/dev/usb-console) and the cable to this computer.',
              file=sys.stderr)
        return 2
    try:
        console = Console(port)
        try:
            if args.command == 'run':
                status = 0
                for command in args.commands:
                    text, status = console.run(command, args.timeout)
                    if text:
                        print(text)
                    if status:
                        print(f'(exit {status})', file=sys.stderr)
                return status
            if args.command == 'shell':
                console.shell(echo=not args.no_echo)
                return 0
            if args.command == 'send':
                print(json.dumps(console.send(args.local, args.remote), indent=2))
                return 0
            print(json.dumps(facts(console), indent=2))
            return 0
        finally:
            console.close()
    except ConsoleError as error:
        print(f'console: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
