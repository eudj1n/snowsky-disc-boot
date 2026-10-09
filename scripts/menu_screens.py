#!/usr/bin/env python3
"""The boot menu's screens as pictures (docs/assets/menu/), drawn by the menu itself.

Runs the host build of the menu with its fixture switches (build/host/disc-menu-fixture: a
framebuffer file, key and touch files) in each state a user meets, takes the panel's frame and
writes it as a PNG of the round panel. Nothing here needs a player or the emulator.

    bash scripts/build.sh host
    python3 scripts/menu_screens.py [--output docs/assets/menu]
"""
import argparse
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import time
import zlib

ROOT = Path(__file__).resolve().parents[1]
MENU = ROOT/'build/host/disc-menu-fixture'
PANEL = 360
POWER_HOLD = 0x108


def png(path, rgba):
    """A minimal PNG writer (RGBA, no filter), so that no imaging library is needed."""
    raw = b''.join(b'\0' + rgba[y * PANEL * 4:(y + 1) * PANEL * 4] for y in range(PANEL))
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', PANEL, PANEL, 8, 6, 0, 0, 0))
                     + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


def frame(fb):
    """The canvas from the framebuffer (turned 180 degrees, BGRX), the round panel's outside clear."""
    data, out = fb.read_bytes(), bytearray(PANEL * PANEL * 4)
    r2 = (PANEL / 2) ** 2
    for y in range(PANEL):
        for x in range(PANEL):
            o = ((PANEL - 1 - y) * PANEL + (PANEL - 1 - x)) * 4
            b, g, r = data[o:o + 3]
            inside = (x - PANEL / 2 + 0.5) ** 2 + (y - PANEL / 2 + 0.5) ** 2 <= r2
            out[(y * PANEL + x) * 4:(y * PANEL + x) * 4 + 4] = bytes((r, g, b, 255 if inside else 0))
    return bytes(out)


def scene(name, output, choices, install=None, keys=(), wait=0.6, countdown=60000):
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root/'status/ui').mkdir(parents=True)
        (root/'run').mkdir()
        (root/'fb0').write_bytes(bytes(PANEL * PANEL * 4 * 3))
        (root/'keys').write_bytes(b'')
        (root/'touch').write_bytes(b'')
        # As boot offers them (write_choices): FiiO's own first, then the installed UIs.
        entries = [dict(ui='stock', version='2.57')]
        entries += [dict(ui=ui, title=title, version=version, confirmed=True) for ui, title, version in choices[1]]
        (root/'status/ui/choices.json').write_text(json.dumps(dict(schema=1, default=choices[0], entries=entries)))
        if install:
            (root/'status/install.json').write_text(json.dumps(dict(schema=1, **install)))
        env = dict(DISC_BOOT_STATUS=str(root/'status'), DISC_BOOT_RUN=str(root/'run'), DISC_MENU_FB=str(root/'fb0'),
                   DISC_MENU_KEYS=str(root/'keys'), DISC_MENU_TOUCH=str(root/'touch'), DISC_MENU_COUNTDOWN_MS=str(countdown))
        menu = subprocess.Popen([str(MENU)], env=env, stderr=subprocess.DEVNULL)
        try:
            time.sleep(wait)
            for code in keys:
                with open(root/'keys', 'ab') as f:
                    f.write(struct.pack('<iiHHi', 0, 0, 1, code, 1) + struct.pack('<iiHHi', 0, 0, 1, code, 0))
                time.sleep(0.4)
            png(output/f'{name}.png', frame(root/'fb0'))
        finally:
            if menu.poll() is None:
                menu.kill()
            menu.wait()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--output', type=Path, default=ROOT/'docs/assets/menu')
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    # One interface beside FiiO's own (diskOS, a project of its own), FiiO's chosen last time.
    installed = ('stock', [('diskos', 'diskOS', '1.2.0')])
    scene('choose', args.output, installed, wait=1.5, countdown=5000)
    scene('reading-the-card', args.output, installed, install=dict(state='waiting', done=0, total=0, current=None))
    scene('installing', args.output, installed, install=dict(state='installing', done=1, total=3, current='disc-menu'))
    scene('starting', args.output, installed, keys=(0xfa,))
    scene('switching-off', args.output, installed, keys=(POWER_HOLD,))
    print(json.dumps(sorted(p.name for p in args.output.glob('*.png'))))


if __name__ == '__main__':
    main()
