"""The player through USB Boot with the reviewed tools (docs/build-and-flash.md, steps 3 to 5).

Nothing here touches USB itself: each step runs one of scripts/deployment/ with the arguments
the procedure gives it, keeps its output in the run folder and checks its result the way the
procedure does. The order:

1. prepare, offline: the RAM payloads (build_identity.py) and the installation package
   (installation_review.py), bound to this player's history (the last installation's review,
   image, write and readback, or the stage capture of a first one) and its boot capture;
2. backup: a separate session collects the primary rootfs (collect_rootfs.py) and it is
   compared, every byte, with the image the history says is installed (readback.py);
3. write: the proposed installer profile may differ from the tracked one only in its admission
   and review pin; it is activated, both plans are computed again and compared with the
   package's, the writer runs once (writer_transport.py), and admission is closed again in
   every case; only "writer-completion-observed" goes on, an unknown outcome stops everything;
4. readback: a fresh session collects the rootfs, the exact plan must equal the package's, and
   every byte of the image is compared (readback.py verify);
5. audit: both USB journals are reconstructed offline (audit_usb_write.py, audit_usb_readback.py).

The installer asks for a typed word before each session with the player; nothing is retried,
and a failed write never turns into a restore by itself.
"""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT/'scripts/deployment'


class ReviewedError(Exception):
    pass


def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise ReviewedError(f'{path}: {error}')


class History:
    """This player's installation history: its boot capture, and the last installation (review,
    image, write and readback captures) or, before a first one, the stage capture. Each successful
    installation writes the next one (history.json in its run folder)."""

    KEYS = ('bootCapture', 'stockCapture')
    PREVIOUS = ('previousReview', 'previousImage', 'writeCapture', 'readbackCapture')

    def __init__(self, data):
        if not all(data.get(k) for k in self.KEYS):
            raise ReviewedError('the history names the boot capture and the stock capture')
        if not (all(data.get(k) for k in self.PREVIOUS) or data.get('stageCapture')):
            raise ReviewedError('the history names the last installation or the stage capture of a first one')
        self.data = {k: str(v) for k, v in data.items()}

    @classmethod
    def load(cls, path):
        return cls(load_json(path))

    def __getitem__(self, key):
        return self.data[key]

    def get(self, key):
        return self.data.get(key)


class Reviewed:
    def __init__(self, version, work, artifacts, diskos, libusb, history, run=subprocess.run, profile=None):
        self.version, self.work, self.artifacts = version, Path(work), Path(artifacts)
        self.diskos, self.libusb, self.history, self.run = str(diskos), str(libusb), history, run
        self.profile = Path(profile or ROOT/'firmware/installers'/f'v{version}.json')
        self.package, self.meta, self.readback_build = self.work/'package', self.work/'build-metadata', self.work/'build-readback'
        self.log = self.work/'commands.log'

    # One reviewed tool, as the procedure runs it

    def tool(self, name, *args, stdout=None):
        command = [sys.executable, '-B', str(DEPLOY/name), *[str(a) for a in args]]
        self.work.mkdir(parents=True, exist_ok=True)
        with open(self.log, 'a') as log:
            log.write(' '.join(command) + '\n')
        result = self.run(command, cwd=str(ROOT), capture_output=True, text=True)
        if stdout:
            Path(stdout).write_text(result.stdout)
        return result

    def need(self, result, what):
        if result.returncode:
            raise ReviewedError(f'{what} failed: ' + ((result.stderr or result.stdout).strip().splitlines() or ['no output'])[-1])

    def plan_sha(self, name):
        return load_json(self.package/name)['plan_sha256']

    # 1. Offline

    def prepare(self):
        for mode, out in (('metadata', self.meta), ('rootfs', self.readback_build)):
            self.need(self.tool('build_identity.py', '--version', self.version, '--mode', mode, '--diskos', self.diskos, '--output', out),
                      f'the {mode} payload')
        history = (['--previous-review', self.history['previousReview'], '--previous-image', self.history['previousImage'],
                    '--write-capture', self.history['writeCapture'], '--readback-capture', self.history['readbackCapture']]
                   if self.history.get('previousReview') else ['--stage-capture', self.history['stageCapture']])
        self.need(self.tool('installation_review.py', '--version', self.version, '--diskos', self.diskos, '--artifacts', self.artifacts,
                            '--build', self.meta, '--readback-build', self.readback_build, '--boot-capture', self.history['bootCapture'],
                            '--stock-capture', self.history['stockCapture'], *history, '--libusb', self.libusb, '--output', self.package),
                  'the installation package')
        for name in ('installation-review.json', 'proposed-installer-profile.json', 'candidate-write-plan.json', 'restore-write-plan.json',
                     'postwrite-collection-plan.json', 'candidate-exact-readback-plan.json', 'restore-exact-readback-plan.json'):
            if not (self.package/name).is_file():
                raise ReviewedError(f'the installation package lacks {name}')
        return dict(package=str(self.package), write=self.plan_sha('candidate-write-plan.json'),
                    read=self.plan_sha('postwrite-collection-plan.json'))

    # 2, 4. A collection of the primary rootfs, compared with an image

    def collect(self, output, metadata_page):
        self.need(self.tool('collect_rootfs.py', 'acquire', '--mode', 'rootfs', '--version', self.version, '--build', self.readback_build,
                            '--diskos', self.diskos, '--metadata-page', metadata_page, '--libusb', self.libusb,
                            '--approved-plan-sha256', self.plan_sha('postwrite-collection-plan.json'), '--output', output),
                  'the collection')
        result = load_json(Path(output)/'result.json')
        if result.get('status') != 'rootfs-collected':
            raise ReviewedError(f'the collection ended {result.get("status")!r}')
        return result

    def compare(self, capture, metadata_page, image, image_sha, approved=None):
        result = load_json(Path(capture)/'result.json')
        plan = self.tool('readback.py', 'plan', '--version', self.version, '--metadata-page', metadata_page, '--image', image,
                         '--image-sha256', image_sha, stdout=Path(capture)/'exact-plan.json')
        self.need(plan, 'the exact plan')
        if approved is not None and load_json(Path(capture)/'exact-plan.json') != load_json(approved):
            raise ReviewedError('the exact comparison plan differs from the approved one')
        review = self.tool('readback.py', 'verify', '--version', self.version, '--metadata-page', metadata_page, '--image', image,
                           '--image-sha256', image_sha, '--records', Path(capture)/'records.bin', '--nonce-hex', result['nonce_hex'],
                           stdout=Path(capture)/'exact-review.json')
        self.need(review, 'the exact comparison')
        exact = load_json(Path(capture)/'exact-review.json')
        if exact.get('status') != 'saved-logical-readback-matches' or exact.get('nonce_hex') != result['nonce_hex'] \
                or exact.get('capture_sha256') != result.get('capture_sha256'):
            raise ReviewedError('the readback does not match the image')
        return exact

    def image_sha(self, image):
        report = load_json(Path(image).parent/'report.json')
        return report['artifacts'][Path(image).name]['sha256']

    def backup(self):
        """What is installed now, read in a session of its own and compared with the history's image."""
        out = self.work/'backup'
        page = Path(self.history['bootCapture'])/'metadata-main.bin'
        self.collect(out, page)
        previous = self.history.get('previousImage')
        if previous:
            sha = self.image_sha(previous)
            self.compare(out, page, previous, sha)
            return dict(capture=str(out), matches=Path(previous).name)
        return dict(capture=str(out), matches=None)

    # 3. The write, once

    def admission(self):
        """The proposal may change only the admission and the review pin of the tracked profile."""
        tracked, proposed = load_json(self.profile), load_json(self.package/'proposed-installer-profile.json')
        strip = lambda p: {k: v for k, v in p.items() if k not in ('physical_write_admitted', 'installation_review_sha256')}  # noqa: E731
        if strip(tracked) != strip(proposed):
            raise ReviewedError('the proposed installer profile changes more than its admission and review pin')
        return tracked, proposed

    def close_admission(self, proposed):
        current = load_json(self.profile)
        if current != proposed and current != dict(proposed, physical_write_admitted=False):
            raise ReviewedError('the installer profile changed during the write: review it by hand')
        self.profile.write_text(json.dumps(dict(proposed, physical_write_admitted=False), indent=2) + '\n')

    def write(self, target='candidate'):
        tracked, proposed = self.admission()
        (self.work/'installer-before.json').write_text(json.dumps(tracked, indent=2) + '\n')
        plan_file = f'{target}-write-plan.json'
        args = ['--version', self.version, '--build', self.meta, '--diskos', self.diskos, '--artifacts', self.artifacts,
                '--metadata-page', Path(self.history['bootCapture'])/'metadata-main.bin',
                '--installation-review', self.package/'installation-review.json', '--readback-build', self.readback_build]
        self.profile.write_text(json.dumps(proposed, indent=2) + '\n')
        try:
            replanned = self.tool('writer_transport.py', 'plan', '--mode', 'write', '--target', target, *args,
                                  stdout=self.work/'write-replanned.json')
            self.need(replanned, 'the write plan')
            mine, approved = load_json(self.work/'write-replanned.json'), load_json(self.package/plan_file)
            if (mine.get('plan'), mine.get('plan_sha256')) != (approved.get('plan'), approved.get('plan_sha256')):
                raise ReviewedError('the write plan computed now differs from the approved one')
            out = self.work/'write'
            self.tool('writer_transport.py', 'acquire', '--mode', 'write', '--target', target, *args, '--confirm-reviewed-device-state',
                      '--libusb', self.libusb, '--approved-plan-sha256', approved['plan_sha256'], '--output', out)
        finally:
            self.close_admission(proposed)
        result = load_json(out/'result.json')
        status = result.get('status')
        if status == 'writer-outcome-unknown':
            raise ReviewedError('the writer\'s outcome is unknown: keep the player powered and untouched, start nothing new, '
                                'and resolve its state by hand')
        if status != 'writer-completion-observed' or not result.get('writer_return_observed') or not result.get('writer_execution_attempted'):
            raise ReviewedError(f'the write ended {status!r}: the evidence is in {out}')
        return dict(capture=str(out), session=result.get('session_id'), plan=approved['plan_sha256'])

    # 4, 5. The readback and the audits

    def readback(self, target='candidate'):
        write = self.work/'write'
        out = self.work/'read'
        page = write/'metadata-main.bin'
        self.collect(out, page)
        plan = load_json(self.package/f'{target}-write-plan.json')['plan']
        image = self.artifacts/Path(plan['image_name']).name
        exact = self.compare(out, page, image, self.image_sha(image), approved=self.package/f'{target}-exact-readback-plan.json')
        return dict(capture=str(out), image=image.name, exact=exact['status'])

    def audit(self):
        for name, run, build in (('audit_usb_write.py', self.work/'write', self.meta), ('audit_usb_readback.py', self.work/'read', self.readback_build)):
            self.need(self.tool(name, '--run', run, '--package', self.package, '--artifacts', self.artifacts, '--build', build,
                                '--diskos', self.diskos, '--output', run/'offline-review.json'), name)
        return dict(write=str(self.work/'write/offline-review.json'), read=str(self.work/'read/offline-review.json'))

    def next_history(self, image):
        """This installation as the next one's history."""
        data = dict(self.history.data, previousReview=str(self.package/'installation-review.json'), previousImage=str(image),
                    writeCapture=str(self.work/'write'), readbackCapture=str(self.work/'read'))
        data.pop('stageCapture', None)
        (self.work/'history.json').write_text(json.dumps(data, indent=2) + '\n')
        return self.work/'history.json'
