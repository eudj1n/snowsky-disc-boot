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
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

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


def folder(kind, target):
    """A session's folder in the run: write and read for the candidate, restore-write and
    restore-read for the way back to stock."""
    return kind if target == 'candidate' else f'{target}-{kind}'


def is_writer_start(line, entry):
    """The journal's line of the control call that starts the writer (program start at its entry)."""
    if b'"request": 4' not in line:
        return False
    try:
        row = json.loads(line)
    except ValueError:
        return False
    return row.get('phase') == 'attempt' and row.get('request') == 4 and row.get('parameter') == entry


def expected_calls(plan):
    """The calls a session that goes well makes, for its progress: the plan's limit (its asks
    replace the requests after a fixed wait, plan, stage 4c)."""
    return max(1, plan.get('protocol_call_limit', 0))


def writer_wait(plan):
    """The writer's entry and wait from an approved write plan, for the progress only; a plan
    without them is counted by its calls."""
    entry, wait = plan.get('writer_entry'), plan.get('writer_wait_ms')
    return (entry, wait / 1000) if isinstance(entry, int) and isinstance(wait, (int, float)) and wait > 0 else None


def session_fraction(calls_done, seconds, wait=None, writer_started=None):
    """The share of a session done. Without a wait, its calls. With the writer's wait, the time:
    while staging, the calls so far tell how long staging takes, and the wait follows; once the
    writer runs, its time gone against the staging's and the whole wait."""
    calls_done = min(1.0, calls_done)
    if not wait:
        return calls_done
    if writer_started is None:
        if calls_done <= 0:
            return 0.0
        return min(0.99, seconds / (seconds / calls_done + wait))
    return min(0.99, seconds / (writer_started + wait))


class Reviewed:
    def __init__(self, version, work, artifacts, diskos, libusb, history, run=subprocess.run, profile=None, progress=None):
        self.version, self.work, self.artifacts = version, Path(work), Path(artifacts)
        self.diskos, self.libusb, self.history, self.run = str(diskos), str(libusb), history, run
        self.profile = Path(profile or ROOT/'firmware/installers'/f'v{version}.json')
        self.package, self.meta, self.readback_build = self.work/'package', self.work/'build-metadata', self.work/'build-readback'
        self.digest_build = self.work/'build-digest'
        # The staging check (plan, stage 4c): the image region checked and the image hashed on the player.
        self.staging_build = self.work/'build-staging'
        self.log = self.work/'commands.log'
        # progress(label, fraction, seconds) while a USB session runs (the installer's screen).
        self.progress = progress

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

    def session(self, name, *args, output, calls, label, writer=None):
        """A USB session's tool in the background, its journal (two lines a call) counted against
        the plan's call limit, with the tool and its checks as they are. A write (writer: the
        approved plan's writer entry and wait) counts in two phases: the calls up to the writer's
        start, then the wait by the clock, since the ROM does not answer while the writer runs
        (owner, 2026-10-05: the bar said 13 minutes left with 15 still to come)."""
        if self.progress is None or self.run is not subprocess.run:
            return self.tool(name, *args)
        command = [sys.executable, '-B', str(DEPLOY/name), *[str(a) for a in args]]
        self.work.mkdir(parents=True, exist_ok=True)
        with open(self.log, 'a') as log:
            log.write(' '.join(command) + '\n')
        journal, lines, offset, started, partial = Path(output)/'transfers.jsonl', 0, 0, time.monotonic(), b''
        writer_started = None
        process = subprocess.Popen(command, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        while process.poll() is None:
            try:
                with open(journal, 'rb') as f:
                    f.seek(offset)
                    chunk = f.read()
                    offset += len(chunk)
                *complete, partial = (partial + chunk).split(b'\n')
                lines += len(complete)
                if writer and writer_started is None and any(is_writer_start(line, writer[0]) for line in complete):
                    writer_started = time.monotonic() - started
            except OSError:
                pass
            now = time.monotonic() - started
            self.progress(label, session_fraction(lines / 2 / calls, now, writer and writer[1], writer_started), now)
            time.sleep(1)
        out, err = process.communicate()
        self.progress(label, 1.0, time.monotonic() - started)
        return subprocess.CompletedProcess(command, process.returncode, out, err)

    def need(self, result, what):
        if result.returncode:
            raise ReviewedError(f'{what} failed: ' + ((result.stderr or result.stdout).strip().splitlines() or ['no output'])[-1])

    def plan_sha(self, name):
        return load_json(self.package/name)['plan_sha256']

    # 1. Offline

    def prepare(self):
        # The digest payload (plan, stage 4b) is built beside the reviewed ones; it runs only beside a full read.
        for mode, out in (('metadata', self.meta), ('rootfs', self.readback_build), ('rootfs-digest', self.digest_build),
                          ('staging-check', self.staging_build)):
            self.need(self.tool('build_identity.py', '--version', self.version, '--mode', mode, '--diskos', self.diskos, '--output', out),
                      f'the {mode} payload')
        history = (['--previous-review', self.history['previousReview'], '--previous-image', self.history['previousImage'],
                    '--write-capture', self.history['writeCapture'], '--readback-capture', self.history['readbackCapture']]
                   if self.history.get('previousReview') else ['--stage-capture', self.history['stageCapture']])
        self.need(self.tool('installation_review.py', '--version', self.version, '--diskos', self.diskos, '--artifacts', self.artifacts,
                            '--build', self.meta, '--staging-build', self.staging_build, '--readback-build', self.readback_build,
                            '--boot-capture', self.history['bootCapture'],
                            '--stock-capture', self.history['stockCapture'], *history, '--libusb', self.libusb, '--output', self.package),
                  'the installation package')
        # The history this package binds, for a way back to stock from this run (install.py --restore --run).
        (self.work/'history-used.json').write_text(json.dumps(self.history.data, indent=2) + '\n')
        for name in ('installation-review.json', 'proposed-installer-profile.json', 'candidate-write-plan.json', 'restore-write-plan.json',
                     'postwrite-collection-plan.json', 'candidate-exact-readback-plan.json', 'restore-exact-readback-plan.json'):
            if not (self.package/name).is_file():
                raise ReviewedError(f'the installation package lacks {name}')
        return dict(package=str(self.package), write=self.plan_sha('candidate-write-plan.json'),
                    read=self.plan_sha('postwrite-collection-plan.json'))

    # 2, 4. A collection of the primary rootfs, compared with an image

    def collect(self, output, metadata_page, label='Reading the player'):
        calls = expected_calls(load_json(self.package/'postwrite-collection-plan.json')['plan'])
        self.need(self.session('collect_rootfs.py', 'acquire', '--mode', 'rootfs', '--version', self.version, '--build', self.readback_build,
                               '--diskos', self.diskos, '--metadata-page', metadata_page, '--libusb', self.libusb,
                               '--approved-plan-sha256', self.plan_sha('postwrite-collection-plan.json'), '--output', output,
                               output=output, calls=calls, label=label),
                  'the collection')
        result = load_json(Path(output)/'result.json')
        if result.get('status') != 'rootfs-collected':
            raise ReviewedError(f'the collection ended {result.get("status")!r}')
        return result

    def digest(self, output, metadata_page, full, image=None):
        """The rootfs read again by digest, right after a full read in the same entry into USB Boot
        (plan, stage 4b): its first runs go beside the full read, which stays the evidence. Every
        page's SHA-256 must equal the full read's (full: that read's capture) and, given an image,
        the image's. Nothing here stops an installation: the outcome is only recorded."""
        out, started = Path(output), time.monotonic()
        try:
            planned = self.tool('collect_rootfs.py', 'plan', '--mode', 'rootfs-digest', '--version', self.version,
                                '--build', self.digest_build, '--diskos', self.diskos, '--metadata-page', metadata_page)
            self.need(planned, 'the digest plan')
            plan = json.loads(planned.stdout)
            self.need(self.session('collect_rootfs.py', 'acquire', '--mode', 'rootfs-digest', '--version', self.version,
                                   '--build', self.digest_build, '--diskos', self.diskos, '--metadata-page', metadata_page,
                                   '--libusb', self.libusb, '--approved-plan-sha256', plan['plan_sha256'], '--output', out,
                                   output=out, calls=expected_calls(plan['plan']), label='Reading again, by digest'),
                      'the digest read')
            result = load_json(out/'result.json')
            if result.get('status') != 'rootfs-digest-collected':
                raise ReviewedError(f'the digest read ended {result.get("status")!r}')
            digests, pages = (out/'logical-digests.bin').read_bytes(), Path(full)/'logical-image.bin'
            with open(pages, 'rb') as source:
                agrees = all(hashlib.sha256(source.read(2048)).digest() == digests[k:k+32] for k in range(0, len(digests), 32)) \
                    and len(digests) * 64 == pages.stat().st_size
            mapping = load_json(Path(full)/'result.json').get('logical_to_physical') == result.get('logical_to_physical')
            outcome = dict(status=result['status'], capture=str(out), seconds=round(time.monotonic() - started),
                           agreesWithFullRead=agrees and mapping)
            outcome['pageTicks'] = result.get('page_ticks')   # what a page takes, for the portions' wait
            if image is not None:
                verified = self.tool('readback.py', 'verify', '--digest', '--version', self.version, '--metadata-page', metadata_page,
                                     '--image', image, '--image-sha256', self.image_sha(image), '--records', out/'records.bin',
                                     '--nonce-hex', result['nonce_hex'], stdout=out/'exact-digest-review.json')
                outcome['matchesImage'] = verified.returncode == 0 and \
                    load_json(out/'exact-digest-review.json').get('status') == 'saved-logical-digest-matches'
            (out/'beside-full-read.json').write_text(json.dumps(outcome, indent=2) + '\n')
            return outcome
        except (ReviewedError, OSError, ValueError, KeyError) as error:
            return dict(status='failed', capture=str(out), seconds=round(time.monotonic() - started), error=str(error))

    def compare(self, capture, metadata_page, image, image_sha, approved=None, name='exact'):
        """name: exact-<target> for a readback, as the next installation's review reads it."""
        result = load_json(Path(capture)/'result.json')
        plan = self.tool('readback.py', 'plan', '--version', self.version, '--metadata-page', metadata_page, '--image', image,
                         '--image-sha256', image_sha, stdout=Path(capture)/f'{name}-plan.json')
        self.need(plan, 'the exact plan')
        if approved is not None and load_json(Path(capture)/f'{name}-plan.json') != load_json(approved):
            raise ReviewedError('the exact comparison plan differs from the approved one')
        review = self.tool('readback.py', 'verify', '--version', self.version, '--metadata-page', metadata_page, '--image', image,
                           '--image-sha256', image_sha, '--records', Path(capture)/'records.bin', '--nonce-hex', result['nonce_hex'],
                           stdout=Path(capture)/f'{name}-review.json')
        self.need(review, 'the exact comparison')
        exact = load_json(Path(capture)/f'{name}-review.json')
        if exact.get('status') != 'saved-logical-readback-matches' or exact.get('nonce_hex') != result['nonce_hex'] \
                or exact.get('capture_sha256') != result.get('capture_sha256'):
            raise ReviewedError('the readback does not match the image')
        return exact

    def image_sha(self, image):
        report = load_json(Path(image).parent/'report.json')
        return report['artifacts'][Path(image).name]['sha256']

    def backup(self, strict=True, name='backup'):
        """What is installed now, read in a session of its own. Before a candidate's write it must be
        the image the history says is installed; before the way back to stock, which any state may
        take (owner, 2026-10-05), it is only compared with the images it may be, for the record."""
        out = self.work/name
        page = Path(self.history['bootCapture'])/'metadata-main.bin'
        self.collect(out, page, label='Backup: reading what the player holds')
        if strict:
            previous = self.history.get('previousImage')
            if previous:
                self.compare(out, page, previous, self.image_sha(previous))
                return dict(capture=str(out), matches=Path(previous).name)
            return dict(capture=str(out), matches=None)
        return dict(capture=str(out), matches=self.known(out, page))

    def known(self, capture, page):
        """Which known image a capture holds (the run's candidate, the history's image), or None."""
        candidates = [self.artifacts/load_json(self.package/'candidate-write-plan.json')['plan']['image_name']]
        if self.history.get('previousImage'):
            candidates.append(Path(self.history['previousImage']))
        for k, image in enumerate(candidates):
            try:
                self.compare(capture, page, image, self.image_sha(image), name=f'exact-known-{k}')
                return image.name
            except (ReviewedError, OSError, KeyError):
                continue
        return None

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
        name = folder('write', target)
        (self.work/f'{folder("installer-before", target)}.json').write_text(json.dumps(tracked, indent=2) + '\n')
        plan_file = f'{target}-write-plan.json'
        args = ['--version', self.version, '--build', self.meta, '--staging-build', self.staging_build, '--diskos', self.diskos,
                '--artifacts', self.artifacts,
                '--metadata-page', Path(self.history['bootCapture'])/'metadata-main.bin',
                '--installation-review', self.package/'installation-review.json', '--readback-build', self.readback_build]
        self.profile.write_text(json.dumps(proposed, indent=2) + '\n')
        try:
            replanned = self.tool('writer_transport.py', 'plan', '--mode', 'write', '--target', target, *args,
                                  stdout=self.work/f'{name}-replanned.json')
            self.need(replanned, 'the write plan')
            mine, approved = load_json(self.work/f'{name}-replanned.json'), load_json(self.package/plan_file)
            if (mine.get('plan'), mine.get('plan_sha256')) != (approved.get('plan'), approved.get('plan_sha256')):
                raise ReviewedError('the write plan computed now differs from the approved one')
            out = self.work/name
            self.session('writer_transport.py', 'acquire', '--mode', 'write', '--target', target, *args, '--confirm-reviewed-device-state',
                         '--libusb', self.libusb, '--approved-plan-sha256', approved['plan_sha256'], '--output', out,
                         output=out, calls=expected_calls(approved['plan']),
                         label='Writing stock\'s rootfs' if target == 'restore' else 'Writing the image',
                         writer=writer_wait(approved['plan']))
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
        write = self.work/folder('write', target)
        out = self.work/folder('read', target)
        page = write/'metadata-main.bin'
        self.collect(out, page, label='Reading back what was written')
        plan = load_json(self.package/f'{target}-write-plan.json')['plan']
        image = self.artifacts/Path(plan['image_name']).name
        exact = self.compare(out, page, image, self.image_sha(image), approved=self.package/f'{target}-exact-readback-plan.json',
                             name=f'exact-{target}')
        return dict(capture=str(out), image=image.name, exact=exact['status'])

    def audit(self, target='candidate'):
        """Both journals reconstructed offline; the write's against the plan it carried out."""
        write, read = self.work/folder('write', target), self.work/folder('read', target)
        for name, run, build, extra in (('audit_usb_write.py', write, self.meta, ['--staging-build', self.staging_build]),
                                        ('audit_usb_readback.py', read, self.readback_build, [])):
            self.need(self.tool(name, '--run', run, '--package', self.package, '--artifacts', self.artifacts, '--build', build,
                                *extra, '--diskos', self.diskos, '--target', target, '--output', run/'offline-review.json'), name)
        return dict(write=str(write/'offline-review.json'), read=str(read/'offline-review.json'))

    def next_history(self, image, target='candidate'):
        """This installation, or its way back to stock, as the next one's history."""
        data = dict(self.history.data, previousReview=str(self.package/'installation-review.json'), previousImage=str(image),
                    writeCapture=str(self.work/folder('write', target)), readbackCapture=str(self.work/folder('read', target)),
                    previousTarget=target)
        data.pop('stageCapture', None)
        (self.work/'history.json').write_text(json.dumps(data, indent=2) + '\n')
        return self.work/'history.json'
