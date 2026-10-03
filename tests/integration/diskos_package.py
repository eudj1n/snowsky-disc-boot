#!/usr/bin/env python3
"""diskOS's UI as a ui package of the boot layer, built locally for the two-package acceptance.

diskOS (https://github.com/b0hemia/diskos; MIT, its UI under ui/ GPL-3.0) is built
from a local checkout at a given revision with its own pinned toolchain image
(ui/Dockerfile at that revision). Nothing of it enters this repository, and the
package stays in ignored work storage: it is never passed on. The package holds:

- `mq_ui`, the entry (ours): diskOS's own image decides the UI in its S96 hook and
  records the choice, without which its UI runs stock's; under the boot layer boot
  made that choice, so the entry writes the record, says ready (diskOS has no
  readiness signal of its own) and runs diskOS's binary as `mq_ui`.
- `diskos/mq_ui`, diskOS's binary, which is also the package's player launcher: as
  `mq_player` it puts its card guard first in the player's PATH, confirms local
  playback, tells its UI and execs stock's player.

    python3 tests/integration/diskos_package.py --source <diskOS checkout> --revision <rev> \\
        --builder diskos-ui-builder:<rev> --output work/diskos-package
"""
import argparse
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'scripts'))
import catalog  # noqa: E402
import package  # noqa: E402

# The same entry the installer's recipe uses (scripts/catalog.py).
ENTRY = catalog.DISKOS_ENTRY


def static_mips(path):
    data = path.read_bytes()
    if data[:7] != b'\x7fELF\x01\x01\x01' or struct.unpack_from('<H', data, 18)[0] != 8:
        raise ValueError(f'{path.name} is not a 32-bit little-endian MIPS executable')
    phoff, count = struct.unpack_from('<I', data, 28)[0], struct.unpack_from('<H', data, 44)[0]
    if any(struct.unpack_from('<I', data, phoff + i*32)[0] == 3 for i in range(count)):
        raise ValueError(f'{path.name} needs a dynamic loader; a package must be static')


def build(source, revision, builder, output):
    if output.exists():
        raise ValueError(f'{output} exists; choose a fresh folder')
    tag = subprocess.run(['git', '-C', str(source), 'describe', '--tags', '--always', revision],
                         check=True, capture_output=True, text=True).stdout.strip()
    with tempfile.TemporaryDirectory(dir=output.parent) as temp:
        archive = subprocess.run(['git', '-C', str(source), 'archive', revision, 'ui'], check=True, capture_output=True).stdout
        subprocess.run(['tar', '-x', '-C', temp], input=archive, check=True)
        subprocess.run(['docker', 'run', '--rm', '--network', 'none', '-v', f'{temp}/ui:/src', builder], check=True)
        binary = Path(temp)/'ui/mq_ui'
        static_mips(binary)
        (output/'diskos').mkdir(parents=True)
        shutil.copyfile(binary, output/'diskos/mq_ui')
    (output/'diskos/mq_ui').chmod(0o755)
    (output/'mq_ui').write_text(ENTRY)
    (output/'mq_ui').chmod(0o755)
    version = f'{tag.lstrip("v")}+{revision[:7]}-local'
    manifest = package.describe(output, 'diskos', version, 'ui', 'mq_ui', ready=30, player='diskos/mq_ui',
                                arch='mips32el-linux-static')
    return dict(name=manifest['name'], version=manifest['version'], player=manifest['player'],
                files={path: entry['size'] for path, entry in manifest['files'].items()})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source', type=Path, required=True, help="A local diskOS checkout (left unchanged)")
    parser.add_argument('--revision', required=True)
    parser.add_argument('--builder', required=True, help="diskOS's toolchain image built from ui/Dockerfile at that revision")
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.revision, args.builder, args.output.resolve()), indent=2))
