# Native image: build, review, write and verify

This is the operator command map for a **new** companion image on the same
previously reviewed player. It describes the existing tools; it is not a
one-command installer or authorization to use USB. Read the
[installation procedure](installation-procedure.md) and the current
project plan (snowsky-disc-web `docs/plan.md`) before a physical session. The candidate writes the
profiled primary rootfs only. A separate stock restore image is prepared but
never run automatically. Commands below assume macOS, zsh, Docker and a
disposable emulator stack. All generated files stay under ignored `build/` and
`work/`; keep external repositories unchanged.

The example paths identify the currently reviewed V2.57 inputs on this
workstation. Select a new release through reviewed profiles and repeat
[compatibility acceptance](../firmware/firmware-compatibility.md); do not substitute a new
OTA into an old package or plan. Set a fresh run name for each preparation.
The current installed image and completed physical history are recorded in
[UDC installation](../observations/udc-installation-observation.md). The combined ACM/webroot
offline candidate and package (snowsky-disc-web `docs/combined-update.md`) are prepared but have no
physical approval.

## 1. Select inputs and run firmware-free checks

Run from this repository root in a persistent shell. Check the selected profile,
reference revision and input paths before building. The toolchain image below
is the locally reviewed image identity, not a promise that Docker can retrieve
it elsewhere.

```sh
set -euo pipefail
cd /Users/zhek/IdeaProjects/snowsky-disc-web
export DISC_VERSION="$(cat firmware/active-version)"
export DISC_DISKOS=/Users/zhek/IdeaProjects/diskos
export DISC_REFERENCE=/Users/zhek/IdeaProjects/snowsky-disc-qemu
export DISC_OTA=/Users/zhek/Downloads/SNOWSKY_DISC_update_20260909_v257/main_os/ota_v257
# Soft-float companion toolchain (device/Dockerfile.toolchain); the earlier
# hard-float image sha256:68e50109… must not build the companion any more.
export DISC_TOOLCHAIN_IMAGE=sha256:259b24e7d8ca64a4cf039ea5ae0f4a6536ce8c60f6441d02b933de97aaea845f
export DISC_LIBUSB=/opt/homebrew/Cellar/libusb/1.0.30/lib/libusb-1.0.0.dylib
export DISC_RUN=next-reviewed-update-001
export DISC_ARTIFACTS="$PWD/work/$DISC_RUN-artifacts"
export DISC_PACKAGE="$PWD/work/$DISC_RUN-package"
export DISC_META_BUILD="$PWD/build/$DISC_RUN-metadata"
export DISC_READ_BUILD="$PWD/build/$DISC_RUN-readback"
test -d "$DISC_DISKOS"
test -d "$DISC_REFERENCE"
test -d "$DISC_OTA"
test -f "$DISC_LIBUSB"
git status --short
git -C "$DISC_REFERENCE" rev-parse HEAD
bash scripts/test.sh
DISC_TOOLCHAIN_IMAGE="$DISC_TOOLCHAIN_IMAGE" bash scripts/build.sh mips
```

The last command builds the static boot program and USB console and fails
unless they are soft-float without FPU instructions (`bash scripts/build.sh reader` builds the
NAND reader's MIPS tests). The builder is `docker build --platform
linux/amd64 -t disc-native-toolchain -f device/Dockerfile.toolchain .`
(soft-float musl.cc toolchain pinned by SHA-256). Review the resulting image
identity and reprepare the package rather than silently replacing the pinned
image. The host conformance suite is also the firmware-free GitHub Actions
boundary.

## 2. Build and check a disposable firmware image

The boot layer's image is stock plus the boot layer's own objects; it
carries no package ([contract](../boot/contract.md), "What changes against today"):
the boot program with its hooks, the `/sbin/mq_ui` and `/sbin/mq_player`
wrappers and the card guard, the USB console with its hook, and the boot
report. The disposable stack is snowsky-disc-web's emulator wrapper
(its `scripts/emulator.py up`), whose container mounts that repository at
`/platform`; this repository's sources are copied into the container's
`/work` for the build. An existing stack is pinned to its own
firmware/profile; inspect it rather than repointing it.

```sh
export DISC_CONTAINER="$(python3 -c 'import json; print(json.load(open("../snowsky-disc-web/work/emulator.json"))["id"] + "-emu")')"
docker exec "$DISC_CONTAINER" sh -c 'rm -rf /work/boot-src && mkdir -p /work/boot-src/build/mips'
tar cf - scripts device/scripts firmware tests/integration | docker exec -i "$DISC_CONTAINER" tar xf - -C /work/boot-src
docker cp build/mips/disc-usb-console "$DISC_CONTAINER:/work/boot-src/build/mips/disc-usb-console"
docker cp build/mips/disc-boot "$DISC_CONTAINER:/work/boot-src/build/mips/disc-boot"
docker exec -e PYTHONPATH=/repo "$DISC_CONTAINER" \
  python3 -B /work/boot-src/scripts/deployment/build_candidate.py \
  --version "$DISC_VERSION" --ota /ota \
  --console /work/boot-src/build/mips/disc-usb-console \
  --boot /work/boot-src/build/mips/disc-boot \
  --output "/work/$DISC_RUN"
docker exec -e CI_DISPOSABLE=1 -e FW_VERSION="$DISC_VERSION" "$DISC_CONTAINER" \
  timeout 145 unshare --mount --net --pid --fork \
  python3 -B /work/boot-src/tests/integration/boot_report.py --output "/work/$DISC_RUN"
# The hooks, the wrapper and the production boot program on the packed tree's BusyBox (about 4 min).
docker exec "$DISC_CONTAINER" timeout 420 unshare --mount --net --pid --fork \
  python3 -B /work/boot-src/tests/integration/boot_layer.py --output "/work/$DISC_RUN"
mkdir "$DISC_ARTIFACTS"
docker cp "$DISC_CONTAINER:/work/$DISC_RUN/report.json" "$DISC_ARTIFACTS/report.json"
```

Use the two filenames from `report.json`, not a version-specific filename
template. This copy loop treats artifact names as basenames and checks the
image hashes recorded by the builder:

```sh
python3 - "$DISC_ARTIFACTS/report.json" > "$DISC_ARTIFACTS/artifact-names.txt" <<'PY'
import json, pathlib, sys
report = json.loads(pathlib.Path(sys.argv[1]).read_text())
names = list(report['artifacts'])
assert len(names) == 2 and all(pathlib.Path(n).name == n for n in names)
print(*names, sep='\n')
PY
while IFS= read -r DISC_NAME; do
  docker cp "$DISC_CONTAINER:/work/$DISC_RUN/$DISC_NAME" "$DISC_ARTIFACTS/$DISC_NAME"
done < "$DISC_ARTIFACTS/artifact-names.txt"
python3 - "$DISC_ARTIFACTS" <<'PY'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1]); report = json.loads((root/'report.json').read_text())
for name, expected in report['artifacts'].items():
    data = root/name
    assert data.stat().st_size == expected['bytes']
    assert hashlib.sha256(data.read_bytes()).hexdigest() == expected['sha256']
print('Both image sizes and SHA-256 values match the build report')
PY
python3 scripts/deployment/review.py --version "$DISC_VERSION" \
  --artifacts "$DISC_ARTIFACTS" --diskos "$DISC_DISKOS" \
  > "$DISC_ARTIFACTS/offline-review.json"
```

`review.py` verifies image/source constraints offline and reports
`flashReady: false`; for the `boot` variant it also requires the console's
opt-in and no package. Run the guest checks relevant to the change (the
boot program's, once it exists). snowsky-disc-web's
`python3 scripts/emulator.py down` later removes only the recorded
disposable stack.

## 3. Prepare a new installation package offline

Build both independently checked RAM payloads. For an update to the **same
already installed** player, bind the last accepted installation/write/readback
history and the original boot/stock captures. These current example paths are
historical evidence, not generic defaults. Confirm they still represent this
unit and that no OTA, selector, bootloader, kernel, layout or rootfs change or
unresolved writer invocation intervened.

```sh
export DISC_BOOT_CAPTURE="$PWD/work/boot-evidence-observation-001"
export DISC_STOCK_CAPTURE="$PWD/work/rootfs-full-observation-002"
export DISC_PREVIOUS_REVIEW="$PWD/work/combined-003-package/installation-review.json"
export DISC_PREVIOUS_IMAGE="$PWD/work/combined-003-artifacts/disc-web-v257-usb-engineering-review-only.bin"
export DISC_PREVIOUS_WRITE="$PWD/work/combined-003-write"
export DISC_PREVIOUS_READ="$PWD/work/combined-003-read"
python3 scripts/deployment/build_identity.py --version "$DISC_VERSION" \
  --mode metadata --diskos "$DISC_DISKOS" --output "$DISC_META_BUILD"
python3 scripts/deployment/build_identity.py --version "$DISC_VERSION" \
  --mode rootfs --diskos "$DISC_DISKOS" --output "$DISC_READ_BUILD"
python3 scripts/deployment/installation_review.py --version "$DISC_VERSION" \
  --diskos "$DISC_DISKOS" --artifacts "$DISC_ARTIFACTS" \
  --build "$DISC_META_BUILD" --readback-build "$DISC_READ_BUILD" \
  --boot-capture "$DISC_BOOT_CAPTURE" --stock-capture "$DISC_STOCK_CAPTURE" \
  --previous-review "$DISC_PREVIOUS_REVIEW" --previous-image "$DISC_PREVIOUS_IMAGE" \
  --write-capture "$DISC_PREVIOUS_WRITE" --readback-capture "$DISC_PREVIOUS_READ" \
  --libusb "$DISC_LIBUSB" --output "$DISC_PACKAGE"
```

For the first installation on a confirmed stock player, the final four history
arguments are replaced by `--stage-capture` pointing to the accepted full RAM
staging capture. The package contains an installation review, proposed installer
profile and five exact operation plans. Preserve all seven files. Inspect the
review, candidate and restore hashes, profile/source pins, target block range,
current physical history, full reader plan and recovery image. None of these
offline commands opens USB or changes tracked admission.

## 4. Activate one reviewed physical attempt

**Stop here until the particular candidate image, player state, single write
and conditional independent readback are separately authorized.** Confirm the
same player, stable power and USB, sufficient free disk space and no unresolved
write. USB Boot entry is an operator action: power off, connect with Volume
Down held, then verify the expected USB target appears. Do not use storage or
DAC mode for the writer. Mere presence or CPU_INFO is not write permission.

The tracked `firmware/installers/v${DISC_VERSION}.json` starts with
`physical_write_admitted: false`. The package's
`proposed-installer-profile.json` has the new review pin and true admission.
After authorization, compare every field except the pin and admission flag,
save the closed tracked profile in ignored `work/`, then activate **exactly**
the proposed file. Recompute and compare both plans to the approved package
before acquisition. Reclose admission after the attempt while retaining the
new review pin. If any comparison changes, stop and prepare a new review;
never reuse an old plan hash. See [installation review](installation-review.md)
for the admission contract.

```sh
export DISC_INSTALLER_PROFILE="firmware/installers/v${DISC_VERSION}.json"
export DISC_WRITE_PLAN_SHA="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["plan_sha256"])' "$DISC_PACKAGE/candidate-write-plan.json")"
export DISC_READ_PLAN_SHA="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["plan_sha256"])' "$DISC_PACKAGE/postwrite-collection-plan.json")"
python3 - "$DISC_INSTALLER_PROFILE" "$DISC_PACKAGE/proposed-installer-profile.json" <<'PY'
import json, pathlib, sys
closed = json.loads(pathlib.Path(sys.argv[1]).read_text())
proposed = json.loads(pathlib.Path(sys.argv[2]).read_text())
for key in ('physical_write_admitted', 'installation_review_sha256'):
    closed.pop(key); proposed.pop(key)
assert closed == proposed, 'Installer profile changed outside admission/review pin'
print('Installer fields outside admission and review pin match')
PY
```

The following is the **authorized activation** command, not part of offline
preparation. Save the prior tracked profile, then activate the exact proposal.
Do not perform this step just to generate a plan.

```sh
cp "$DISC_INSTALLER_PROFILE" "$PWD/work/$DISC_RUN-installer-before.json"
cp "$DISC_PACKAGE/proposed-installer-profile.json" "$DISC_INSTALLER_PROFILE"
python3 scripts/deployment/writer_transport.py plan --mode write --target candidate \
  --version "$DISC_VERSION" --build "$DISC_META_BUILD" --diskos "$DISC_DISKOS" \
  --artifacts "$DISC_ARTIFACTS" \
  --metadata-page "$DISC_BOOT_CAPTURE/metadata-main.bin" \
  --installation-review "$DISC_PACKAGE/installation-review.json" \
  --readback-build "$DISC_READ_BUILD" > "$PWD/work/$DISC_RUN-write-replanned.json"
python3 scripts/deployment/collect_rootfs.py plan --mode rootfs \
  --version "$DISC_VERSION" --build "$DISC_READ_BUILD" --diskos "$DISC_DISKOS" \
  --metadata-page "$DISC_BOOT_CAPTURE/metadata-main.bin" \
  > "$PWD/work/$DISC_RUN-read-replanned.json"
cmp "$PWD/work/$DISC_RUN-write-replanned.json" "$DISC_PACKAGE/candidate-write-plan.json"
cmp "$PWD/work/$DISC_RUN-read-replanned.json" "$DISC_PACKAGE/postwrite-collection-plan.json"
```

Plan JSON may differ in formatting even when its canonical hash and body agree.
In that case compare parsed `plan` and `plan_sha256` fields exactly; do not
accept a hash alone. Record the actual approved plan hashes with the session.

## 5. Write once, collect independently, compare every byte

Set fresh output directories. Run a write only after the plan comparison and
current-state confirmation. The writer repeats full RAM staging and comparison,
then invokes one bounded NAND writer. It does **not** retry an unknown outcome.

```sh
export DISC_WRITE_CAPTURE="$PWD/work/$DISC_RUN-write"
export DISC_READ_CAPTURE="$PWD/work/$DISC_RUN-read"
set +e
python3 scripts/deployment/writer_transport.py acquire --mode write --target candidate \
  --version "$DISC_VERSION" --build "$DISC_META_BUILD" --diskos "$DISC_DISKOS" \
  --artifacts "$DISC_ARTIFACTS" \
  --metadata-page "$DISC_BOOT_CAPTURE/metadata-main.bin" \
  --installation-review "$DISC_PACKAGE/installation-review.json" \
  --readback-build "$DISC_READ_BUILD" --confirm-reviewed-device-state \
  --libusb "$DISC_LIBUSB" --approved-plan-sha256 "$DISC_WRITE_PLAN_SHA" \
  --output "$DISC_WRITE_CAPTURE"
DISC_WRITE_EXIT=$?
set -e
```

Inspect `result.json`, session ID/nonce, saved request/results/journal and
cleanup. A shell exit code or transport success alone is insufficient. If
writer status is `failed-before-writer`, preserve evidence and diagnose; if
`writer-outcome-unknown`, stop all new bootstrap/read/write/reset attempts
until the execution state is separately resolved. Only confirmed
`writer-completion-observed` allows the separately authorized read session.
Close tracked write admission after the one invocation **even if the command
failed or was interrupted**, preserving the new review pin. Inspect the exit
code and result after closing. Keep an interrupted/unknown writer session
powered and untouched until its execution state is resolved.

```sh
python3 - "$DISC_INSTALLER_PROFILE" "$DISC_PACKAGE/proposed-installer-profile.json" <<'PY'
import json, pathlib, sys
target = pathlib.Path(sys.argv[1]); expected = json.loads(pathlib.Path(sys.argv[2]).read_text())
assert json.loads(target.read_text()) == expected, 'Admission profile changed unexpectedly'
expected['physical_write_admitted'] = False
target.write_text(json.dumps(expected, indent=2) + '\n')
PY
printf 'Writer command exit: %s\n' "$DISC_WRITE_EXIT"
python3 - "$DISC_WRITE_CAPTURE/result.json" <<'PY'
import json, pathlib, sys
result = json.loads(pathlib.Path(sys.argv[1]).read_text())
print('Writer status:', result['status'])
print('Writer returned:', result['writer_return_observed'])
print('Session:', result['session_id'])
assert result['status'] == 'writer-completion-observed'
assert result['writer_return_observed'] is True
assert result['writer_execution_attempted'] is True
PY
test "$DISC_WRITE_EXIT" -eq 0
```

After confirmed writer return, close its USB session, re-enter USB Boot by the
reviewed operator sequence and collect a **fresh** complete rootfs. The writer
capture's metadata page is used so collection is tied to the new session.
Leaving USB Boot is a restart, and the player then boots its system, which is
already the new image (owner's observation, 2026-09-28; it happened between
the combined-007 write and read too). So the exact readback cannot keep a
damaged image from booting once; it still checks exactly what was written,
since the squashfs rootfs is mounted read-only and the boot writes nothing to
its partition. The boot ROM's USB Boot does not depend on NAND, so a player
whose new system does not start can still enter it for the restore plan.
The owner's boot confirmation (`owner-boot-confirmation.json` in the read
capture) is for the boot after the readback, as with combined-007
(`owner-confirmed-normal-first-reboot`, reported after the read session). The
order of combined-008, a confirmation of the boot between the write and the
readback, is read by the history check but kept for the public installer.

```sh
python3 scripts/deployment/collect_rootfs.py acquire --mode rootfs \
  --version "$DISC_VERSION" --build "$DISC_READ_BUILD" --diskos "$DISC_DISKOS" \
  --metadata-page "$DISC_WRITE_CAPTURE/metadata-main.bin" \
  --libusb "$DISC_LIBUSB" --approved-plan-sha256 "$DISC_READ_PLAN_SHA" \
  --output "$DISC_READ_CAPTURE"
python3 - "$DISC_READ_CAPTURE/result.json" <<'PY'
import json, pathlib, sys
result = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert result['status'] == 'rootfs-collected'
print('Read session:', result['session_id'])
PY
export DISC_CANDIDATE="$(python3 - "$DISC_ARTIFACTS" "$DISC_PACKAGE" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1]); package = pathlib.Path(sys.argv[2])
name = json.loads((package/'candidate-write-plan.json').read_text())['plan']['image_name']
assert pathlib.Path(name).name == name
print(root/name)
PY
)"
export DISC_IMAGE_SHA="$(python3 - "$DISC_CANDIDATE" "$DISC_ARTIFACTS/report.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1]); report = json.loads(pathlib.Path(sys.argv[2]).read_text())
print(report['artifacts'][path.name]['sha256'])
PY
)"
export DISC_READ_NONCE="$(python3 - "$DISC_READ_CAPTURE/result.json" <<'PY'
import json, pathlib, sys
print(json.loads(pathlib.Path(sys.argv[1]).read_text())['nonce_hex'])
PY
)"
python3 scripts/deployment/readback.py plan --version "$DISC_VERSION" \
  --metadata-page "$DISC_WRITE_CAPTURE/metadata-main.bin" \
  --image "$DISC_CANDIDATE" --image-sha256 "$DISC_IMAGE_SHA" \
  > "$DISC_READ_CAPTURE/exact-candidate-plan.json"
python3 - "$DISC_READ_CAPTURE/exact-candidate-plan.json" \
  "$DISC_PACKAGE/candidate-exact-readback-plan.json" <<'PY'
import json, pathlib, sys
actual = json.loads(pathlib.Path(sys.argv[1]).read_text())
approved = json.loads(pathlib.Path(sys.argv[2]).read_text())
assert actual == approved, 'Exact candidate comparison plan changed'
PY
python3 scripts/deployment/readback.py verify --version "$DISC_VERSION" \
  --metadata-page "$DISC_WRITE_CAPTURE/metadata-main.bin" \
  --image "$DISC_CANDIDATE" --image-sha256 "$DISC_IMAGE_SHA" \
  --records "$DISC_READ_CAPTURE/records.bin" --nonce-hex "$DISC_READ_NONCE" \
  > "$DISC_READ_CAPTURE/exact-candidate-review.json"
python3 - "$DISC_READ_CAPTURE/exact-candidate-review.json" \
  "$DISC_PACKAGE/candidate-exact-readback-plan.json" \
  "$DISC_READ_CAPTURE/result.json" <<'PY'
import json, pathlib, sys
exact = json.loads(pathlib.Path(sys.argv[1]).read_text())
approved = json.loads(pathlib.Path(sys.argv[2]).read_text())
collected = json.loads(pathlib.Path(sys.argv[3]).read_text())
assert exact['status'] == 'saved-logical-readback-matches'
assert exact['plan_sha256'] == approved['plan_sha256']
assert exact['nonce_hex'] == collected['nonce_hex']
assert exact['capture_sha256'] == collected['capture_sha256']
print('Exact candidate image and saved capture agree with the approved plan')
PY
```

Accept only an exact comparison of the entire padded candidate, matching the
reviewed exact plan, record count, nonce/session, mapping and admissible ECC.
Independently reconstruct both saved USB journals against the approved plans,
current image, build payloads and exact returned bytes. These offline commands
make no USB calls and use each new run's directory, not the prior observation:

```sh
python3 scripts/deployment/audit_usb_write.py \
  --run "$DISC_WRITE_CAPTURE" --package "$DISC_PACKAGE" \
  --artifacts "$DISC_ARTIFACTS" --build "$DISC_META_BUILD" \
  --diskos "$DISC_DISKOS" --output "$DISC_WRITE_CAPTURE/offline-review.json"
python3 scripts/deployment/audit_usb_readback.py \
  --run "$DISC_READ_CAPTURE" --package "$DISC_PACKAGE" \
  --artifacts "$DISC_ARTIFACTS" --build "$DISC_READ_BUILD" \
  --diskos "$DISC_DISKOS" --output "$DISC_READ_CAPTURE/offline-review.json"
```

The scripts reject missing/replayed calls, changed timeouts, payloads and
saved reads, extra records, unreviewed RAM entry points and a readback image
that differs from the approved candidate. They reproduced every original
field of the last successful write/readback audits except the auditor's own
script hash. Synthetic journal mutations run in GitHub Actions. Inspect both
reports and the separate exact-image review before physical acceptance. The
completed [UDC write/read audits](../observations/udc-installation-observation.md) show the
historical evidence expected. Do not mark physical acceptance on
`readback.py` or transport success alone.

Only then exit USB Boot with a normal power cycle and check stock UI, playback,
native process/health and the specific feature that motivated this image (the
boot between write and read already ran the new image once; its boot report
is early evidence, not acceptance). The
owner's boot observation is a distinct evidence item. To restore stock rootfs,
prepare and separately authorize the package's `restore-write-plan.json`; use
`--target restore` and verify the entire restore image with its own exact plan.
Never turn a failed candidate write into an automatic restore.

## Short browser iteration after SD webroot acceptance

The implemented SD webroot (snowsky-disc-web `docs/sd-webroot.md`) changes this process **after the
combined native image installs and passes hardware acceptance**. Later edits
to static HTML/CSS/JS require building the browser bundle, copying it via
USB storage or a card reader, safely ejecting it, returning storage ownership
to the player and reloading the browser. They do not require steps 2–5 above.
The actual player mount observed in the boot report is `/tmp/sdcard`, so the
profile-selected root is `/tmp/sdcard/www`, with `/Volumes/PLAY/www` as the
current Mac view. The engineering image admits remote browser requests on
port 7870 only while the exact `DISC_WEB_LAN_DEBUG` marker is present on the
mounted card. See the SD guide for publication order and marker behavior.
