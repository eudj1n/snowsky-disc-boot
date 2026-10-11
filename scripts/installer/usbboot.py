"""The player through USB Boot with the reviewed tools (docs/dev/installer/build-and-flash.md).

Nothing here touches USB itself: each step runs one of scripts/deployment/ with the arguments
the procedure gives it, keeps its output in the run folder and checks its result the way the
procedure does. The order, in one entry into USB Boot (plan, stage 6):

1. the payloads the player runs from its RAM: the release's, else built here;
2. the evidence of the entry, each read audited offline: the metadata page (the entry's only
   SPL), the boot evidence and the identity probe (the first rootfs blocks);
3. the review (installation_review.py --known): the image the player holds, known by its first
   blocks, or a refusal before anything is written; its package carries the plans and the run's
   admission (the tracked installer profile is not touched);
4. the write: its plan computed again and compared with the package's, the writer run once
   (writer_transport.py); only "writer-completion-observed" goes on, an unknown outcome stops
   everything;
5. the restart payload (restart_player.py): the player leaves USB Boot by itself;
6. the proof: the new system's first start's check, read by the installer over the USB console,
   else, in a fresh entry, the readback (collect_rootfs.py, readback.py verify);
7. the audits of the USB journals (audit_usb_write.py, audit_usb_readback.py).

The installer asks for a typed word before each session with the player; nothing is retried,
and a failed write never turns into a restore by itself. The path with a developer's history
(the last installation's review and captures) left the installer (owner, 2026-10-11); the
reviewed tools keep it for the procedure by hand.
"""
import json
from pathlib import Path
import shutil
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


LONG_ASK_MS = 10000


def held_asks(plan, completion=None):
    """The held asks a session waits on for long (plan, stage 4c), in their order, with the seconds
    each is expected to take: the completion profile's measurement, else its limit. None for a
    plan without asks (the fixed waits: counted as before)."""
    if not plan.get('completion_ask'):
        return None
    expected = (completion or {}).get('expected_ms', {})
    asks = []
    if plan.get('staging_check_ms'):
        asks += [expected.get('staging_check', plan['staging_check_ms']) / 1000,
                 expected.get('staging_sample', plan['staging_sample_ms']) / 1000]
    if plan.get('writer_executions'):
        asks.append(expected.get('writer', plan['writer_wait_ms']) / 1000)
    return asks


class Progress:
    """A session's share done: its calls by their pace, and each long held ask by the clock against
    its expected time, since the ROM is silent while the payload runs (2026-10-07: the region's
    check held the bar still for two minutes, and the writer was counted as 15 minutes)."""

    def __init__(self, calls, asks):
        self.calls, self.asks = max(1, calls), list(asks)
        self.done, self.k, self.ask_at, self.asked = 0, 0, None, 0.0

    def feed(self, lines, now):
        for line in lines:
            if b'"phase": "attempt"' in line:
                self.done += 1
                if self.ask_at is None and self.k < len(self.asks) and b'"poll": true' in line:
                    try:
                        if json.loads(line).get('timeout_ms', 0) >= LONG_ASK_MS:
                            self.ask_at = now
                    except ValueError:
                        pass
            elif self.ask_at is not None and b'"phase": "return"' in line:
                self.asked += now - self.ask_at
                self.ask_at, self.k = None, self.k + 1

    def fraction(self, now):
        current = now - self.ask_at if self.ask_at is not None else 0.0
        calls_time = max(0.0, now - self.asked - current)
        if not self.done or calls_time <= 0:
            return 0.0
        ahead = sum(self.asks[self.k:])
        total = calls_time / self.done * self.calls + self.asked + ahead
        expected_now = self.asks[self.k] if self.k < len(self.asks) else 0.0
        return min(0.99, (calls_time + self.asked + min(current, 0.95 * expected_now)) / total)


class Reviewed:
    def __init__(self, version, work, artifacts, diskos, libusb, run=subprocess.run, progress=None, payloads=None):
        # payloads: the folder of the release's prebuilt payloads (plan, stage 6), else they are built here.
        self.payloads = Path(payloads) if payloads else None
        self.version, self.work, self.artifacts = version, Path(work), Path(artifacts)
        self.diskos, self.libusb, self.run = str(diskos), str(libusb), run
        self.package, self.meta, self.readback_build = self.work/'package', self.work/'build-metadata', self.work/'build-readback'
        # The identity check's probe read (plan, stage 4c): the first rootfs blocks, in place of a backup.
        self.probe_build = self.work/'build-probe'
        # The staging check (plan, stage 4c): the image region checked and the image hashed on the player.
        self.staging_build = self.work/'build-staging'
        # The boot evidence's payloads (plan, stage 6): the review knows the image by its first blocks.
        self.boot_build = self.work/'build-boot-evidence'
        # The player restarted after the write (plan, stage 7): the watchdog started from RAM.
        self.restart_build = self.work/'build-restart'
        # The write follows the review of this entry's evidence.
        self.known = False
        self.log = self.work/'commands.log'
        # progress(label, fraction, seconds) while a USB session runs (the installer's screen).
        self.progress = progress

    def completion(self):
        """The completion profile (the asks' expected times, for the progress), if there is one."""
        path = ROOT/'firmware/completion'/f'v{self.version}.json'
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None

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

    def session(self, name, *args, output, calls, label, writer=None, asks=None):
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
        held = Progress(calls, asks) if asks is not None else None
        process = subprocess.Popen(command, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        while process.poll() is None:
            try:
                with open(journal, 'rb') as f:
                    f.seek(offset)
                    chunk = f.read()
                    offset += len(chunk)
                *complete, partial = (partial + chunk).split(b'\n')
                lines += len(complete)
                if held:
                    held.feed(complete, time.monotonic() - started)
                if writer and writer_started is None and any(is_writer_start(line, writer[0]) for line in complete):
                    writer_started = time.monotonic() - started
            except OSError:
                pass
            now = time.monotonic() - started
            share = held.fraction(now) if held else session_fraction(lines / 2 / calls, now, writer and writer[1], writer_started)
            self.progress(label, share, now)
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

    def builds(self):
        """The payloads: the release's builds, by its digest (the tools still check them against the profiles
        and sources), else built here."""
        for mode, out in (('metadata', self.meta), ('rootfs', self.readback_build), ('staging-check', self.staging_build),
                          ('rootfs-probe', self.probe_build), ('restart', self.restart_build)):
            if self.payloads:
                shutil.copytree(self.payloads/mode, out)
                continue
            self.need(self.tool('build_identity.py', '--version', self.version, '--mode', mode, '--diskos', self.diskos, '--output', out),
                      f'the {mode} payload')
        if self.payloads:
            shutil.copytree(self.payloads/'boot-evidence', self.boot_build)
        else:
            self.need(self.tool('boot_evidence.py', 'build', '--version', self.version, '--diskos', self.diskos,
                                '--output', self.boot_build), 'the boot evidence\'s payloads')

    # 2-3. The evidence of this entry, then the review

    def metadata_page(self):
        """The partition table's page the plans bind: the one the boot evidence read again in this entry."""
        return self.work/'boot'/'metadata-main.bin'

    def planned(self, name, *args, save, what):
        """A reviewed tool's plan, kept for its audit: (plan, its SHA-256)."""
        self.need(self.tool(name, 'plan', *args, stdout=save), f'{what}\'s plan')
        plan = load_json(save)
        return plan['plan'], plan['plan_sha256']

    def entry(self):
        """The reads of the one entry before the review, each a session of its own: the partition table's page
        (the entry's SPL; about 10 s), the boot evidence (the bootloader and the OTA selector; about 2 min) and the
        identity probe (the first rootfs blocks; about 10 s), the last two audited offline."""
        out = self.work/'metadata'
        plan, approved = self.planned('ram_transport.py', '--mode', 'metadata', '--version', self.version, '--build', self.meta,
                                      '--diskos', self.diskos, save=self.work/'metadata-plan.json', what='the partition table')
        self.need(self.session('ram_transport.py', 'acquire', '--mode', 'metadata', '--version', self.version, '--build', self.meta,
                               '--diskos', self.diskos, '--libusb', self.libusb, '--approved-plan-sha256', approved, '--output', out,
                               output=out, calls=expected_calls(plan), label='Reading the partition table'),
                  'the partition table\'s read')
        if load_json(out/'result.json').get('status') != 'nand-metadata-observed':
            raise ReviewedError('the partition table\'s read did not finish')
        page = out/'metadata-main.bin'
        for name, tool, mode, build, audit, status, label in (
                ('boot', 'boot_evidence.py', None, self.boot_build, 'audit_usb_boot.py', 'boot-evidence-collected',
                 'Reading the bootloader'),
                ('identity', 'collect_rootfs.py', 'rootfs-probe', self.probe_build, 'audit_usb_probe.py', 'rootfs-probe-collected',
                 'Checking what the player holds')):
            mode_args = ['--mode', mode] if mode else []
            args = [*mode_args, '--version', self.version, '--build', build, '--diskos', self.diskos, '--metadata-page', page]
            plan_file = self.work/f'{name}-plan.json'
            plan, approved = self.planned(tool, *args, save=plan_file, what=label.lower())
            out = self.work/name
            self.need(self.session(tool, 'acquire', *args, '--libusb', self.libusb, '--approved-plan-sha256', approved,
                                   '--output', out, output=out, calls=expected_calls(plan), label=label), label.lower())
            if load_json(out/'result.json').get('status') != status:
                raise ReviewedError(f'{label.lower()} did not finish')
            self.need(self.tool(audit, '--run', out, '--plan', plan_file, '--build', build, '--diskos', self.diskos,
                                '--output', out/'offline-review.json'), f'the audit of {label.lower()}')
        # The boot evidence read the page again and found it unchanged (its metadata-main.bin): the plans bind it.
        return self.metadata_page()

    def review_known(self, allow_unknown=False):
        """The review of this entry's evidence, offline in seconds: what the player holds (decision.json), or a
        refusal (an image it does not know, another bootloader) before anything is written. allow_unknown: the way
        back to stock from any state, where an image it does not know admits the restore only."""
        self.need(self.tool('installation_review.py', '--known', *(['--allow-unknown'] if allow_unknown else []),
                            '--version', self.version, '--diskos', self.diskos,
                            '--artifacts', self.artifacts, '--build', self.meta, '--staging-build', self.staging_build,
                            '--readback-build', self.readback_build, '--boot-build', self.boot_build, '--probe-build', self.probe_build,
                            '--boot-capture', self.work/'boot', '--probe-capture', self.work/'identity', '--libusb', self.libusb,
                            '--output', self.package), 'the review')
        for name in ('installation-review.json', 'proposed-installer-profile.json', 'decision.json', 'postwrite-collection-plan.json'):
            if not (self.package/name).is_file():
                raise ReviewedError(f'the installation package lacks {name}')
        decision = load_json(self.package/'decision.json')
        for target in decision.get('targets', []):
            if not (self.package/f'{target}-write-plan.json').is_file():
                raise ReviewedError(f'the installation package lacks {target}-write-plan.json')
        self.known = True
        return decision

    # After the write: the player restarted by the installer (plan, stage 7)

    def can_restart(self):
        return (self.restart_build/'identity.bin').is_file()

    def restart(self):
        """The player restarted from USB Boot after the write, in its entry: the restart payload run from RAM
        (restart_player.py) and its journal audited. Its outcome: 'player-restarted' (it left the bus),
        'restart-not-observed', 'restart-uncertain' or 'failed'. Never retried and never a reason to stop: the
        write is done, and the cable is the way out as before."""
        out = self.work/'restart'
        args = ['--version', self.version, '--build', self.restart_build, '--diskos', self.diskos]
        plan, approved = self.planned('restart_player.py', *args, save=self.work/'restart-plan.json', what='the restart')
        self.session('restart_player.py', 'acquire', *args, '--libusb', self.libusb, '--approved-plan-sha256', approved,
                     '--output', out, output=out, calls=expected_calls(plan), label='Restarting the player')
        status = load_json(out/'result.json').get('status', 'failed') if (out/'result.json').is_file() else 'failed'
        if status in ('player-restarted', 'restart-not-observed', 'restart-uncertain'):
            self.need(self.tool('audit_usb_restart.py', '--run', out, '--plan', self.work/'restart-plan.json', '--build',
                                self.restart_build, '--diskos', self.diskos, '--output', out/'offline-review.json'),
                      'the audit of the restart')
        return status

    # 6. A collection of the primary rootfs, compared with the image

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

    def compare(self, capture, metadata_page, image, image_sha, approved=None, name='exact'):
        """name: exact-<target> for a readback."""
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

    # 4. The write, once

    def write(self, target='candidate'):
        """The write, once, in the entry of its evidence: its admission is the run's (the package's proposed
        profile; the tracked one is not touched)."""
        if not self.known:
            raise ReviewedError('the write follows the review of this entry\'s evidence')
        name = folder('write', target)
        plan_file = f'{target}-write-plan.json'
        args = ['--version', self.version, '--build', self.meta, '--staging-build', self.staging_build, '--diskos', self.diskos,
                '--artifacts', self.artifacts, '--metadata-page', self.metadata_page(),
                '--installation-review', self.package/'installation-review.json', '--readback-build', self.readback_build,
                '--installer-profile', self.package/'proposed-installer-profile.json']
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
                     writer=writer_wait(approved['plan']), asks=held_asks(approved['plan'], self.completion()))
        result = load_json(out/'result.json')
        status = result.get('status')
        if status == 'writer-outcome-unknown':
            raise ReviewedError('the writer\'s outcome is unknown: keep the player powered and untouched, start nothing new, '
                                'and resolve its state by hand')
        if status != 'writer-completion-observed' or not result.get('writer_return_observed') or not result.get('writer_execution_attempted'):
            raise ReviewedError(f'the write ended {status!r}: the evidence is in {out}')
        return dict(capture=str(out), session=result.get('session_id'), plan=approved['plan_sha256'])

    # 6, 7. The readback and the audits

    def readback(self, target='candidate'):
        """In a fresh entry, after a write its first start did not prove: every byte compared with the image."""
        write = self.work/folder('write', target)
        out = self.work/folder('read', target)
        page = write/'metadata-main.bin'
        self.collect(out, page, label='Reading back what was written')
        plan = load_json(self.package/f'{target}-write-plan.json')['plan']
        image = self.artifacts/Path(plan['image_name']).name
        exact = self.compare(out, page, image, self.image_sha(image), approved=self.package/f'{target}-exact-readback-plan.json',
                             name=f'exact-{target}')
        return dict(capture=str(out), image=image.name, exact=exact['status'])

    def audit(self, target='candidate', read=True):
        """The journals reconstructed offline; the write's against the plan it carried out. Without a
        readback (the first start's check proved the write, plan, stage 4c) the write's alone."""
        write, readback = self.work/folder('write', target), self.work/folder('read', target)
        sessions = [('audit_usb_write.py', write, self.meta, ['--staging-build', self.staging_build])]
        if read:
            sessions.append(('audit_usb_readback.py', readback, self.readback_build, []))
        for name, run, build, extra in sessions:
            self.need(self.tool(name, '--run', run, '--package', self.package, '--artifacts', self.artifacts, '--build', build,
                                *extra, '--diskos', self.diskos, '--target', target, '--output', run/'offline-review.json'), name)
        return dict(write=str(write/'offline-review.json'), **({'read': str(readback/'offline-review.json')} if read else {}))
