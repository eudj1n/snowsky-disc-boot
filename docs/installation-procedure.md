# Native installation and recovery procedure

This is the concrete sequence following the [captured SPL boot-target review](bootloader-review.md).
Static primary-pair selection is resolved for the saved observation. The exact
prepared admission profile was subsequently activated for the owner's separately
authorized [candidate write](candidate-installation-observation.md); future
writes and restoration still require their own concrete authorization. Neither a successful
RAM staging result nor a recognized OTA string is permission/readiness to flash.
No installation, boot-hook write, selector change or restore is performed by
this document or by the boot-evidence acquisition.
The subsequent [boot-report image](boot-report.md) received a separate
[update review](boot-report-installation.md), authorization, completed write,
exact full readback and owner-confirmed normal boot. Its
[observation](boot-report-installation-observation.md) also records a DDR-guard
stop before NAND access and successful reading after a manual power cycle.
Admission is closed again; neither candidate's approval permits future writes.

## Scope and prerequisites

The candidate is the reviewed USB engineering image, with the companion and
opt-in diagnostic hook. The restore file is the reviewed official-rootfs image
with zero padding to the writer's exact capacity. Both are 96 MiB. Current
fingerprints are recorded in [pre-install review](preinstall-review.md).
The writer starts at primary-rootfs block 80 and scans only its profiled range;
bootloader, kernel, recovery pair, `ota`, MAC/calibration and userdata remain
outside its write scope. Bad blocks are discovered afresh, never hard-coded.

Before proposing the first write:

1. Require the reviewed static boot assessment for the actual captured SPL and
   OTA state. The current `ota:backup` observation selects the primary pair;
   retain its profile, record/metadata/boot hashes and independent USB audit.
   Establish its currency: no intervening update/selector/bootloader changes.
   A recovery, unknown or inconsistent result stops admission; never toggle the
   selector as a side effect of installing the companion. Live mount evidence
   remains a later hardware-acceptance result, not a claim of this assessment.
2. Preserve the accepted pre-install stock capture, original strict padding
   rejection, content acceptance, observed block map and provenance/session
   records. Establish their currency: no intervening OTA/update/rootfs writes.
   The stock-tail rule applies only before installation.
3. Require the [pinned installation package](installation-review.md): profiles,
   accepted boot/stock/staging evidence, candidate/restore hashes and the exact
   independent readback plans. The transport now rejects write mode without
   that exact bundle and current input/source/profile/build equality, before USB.
   Changing only `physical_write_admitted` is insufficient. The prepared profile
   activation and each physical target still require concrete authorization.
4. Revalidate both complete image files and writer exact capacity. Prepare fresh
   candidate-write, restore-write and independent full-readback plans against
   the final code/profile pins. Retain the native libusb dependency fingerprint.
5. Ensure one control owner, adequate player battery/power and stable USB. Have
   the full collector build and exact restore image locally available. A restore
   file is a prepared input; physical restoration has not yet been tested.
6. Obtain separate authorization for the exact candidate write, explicitly
   distinguishing it from RAM-only staging. Define the required post-write read
   in that concrete proposal; it must not be silently inferred from older reads.

Full-chip/userdata/calibration backup is not added as a prerequisite. The known
primary-rootfs capture and targeted boot-selection evidence serve different
purposes and must keep their separate provenance and acceptance labels.

## Candidate write and verification

After authorized activation of the exact prepared installer profile, use
`writer_transport.py` with `--mode write --target candidate`, the pinned
`--installation-review`, checked `--readback-build`, target-specific
`--confirm-reviewed-device-state` and the final approved plan. It repeats staging in that same session; it cannot reuse a
previous session's RAM. All comparisons and fresh chip/partition checks must
pass before its single writer invocation. The writer's maximum six internal
block attempts are part of the reviewed algorithm, not host retries.

| Outcome | Required action |
| --- | --- |
| Failure before writer invocation | Retain evidence; no candidate write was attempted by this session. Diagnose before proposing another session. |
| Writer invocation with unknown outcome | Do not replay, switch targets, reset or power-cycle automatically. Preserve power and resolve whether the RAM writer has returned; killing the host does not cancel it. |
| Consistent completion record and ROM response | Record completion observation only. Proceed to the separately authorized independent readback; do not claim installation success. |

For an uncertain invocation, a follow-up proposal must account for the elapsed
writer wait and specify a bounded read-only ROM/completion observation. A missing
reply or incomplete debug record leaves execution state unresolved. The host
must never start a new SPL/reader while the writer might still be running.
This branch is not an automatic recovery procedure and remains part of physical
writer qualification.

After confirmed return and authorized reacquisition, run the full independent
collector. Reconstruct the current good-block map, including new bad blocks,
and use `readback.py` against the **exact approved padded candidate**. All 96 MiB
must match; FF-tail acceptance is forbidden here. Retain nonce-bound records,
ECC counts, raw failures, plan and image hashes. Any mismatch stops acceptance.

## First boot and companion acceptance

Only after exact readback, perform the agreed normal/cold-boot checks:

- Stock UI and playback start normally, with and without Wi-Fi availability.
- The companion starts once with the reviewed loopback upstream and expected
  process identity; explicit start/stop/restart and disable behavior work.
- The deliberately provisioned SD diagnostic marker enables bounded USB ACM
  access. Absent marker/revocation restores stock USB ownership; no public
  listener or implicit takeover is introduced.
- Through that separately admitted diagnostic connection, retain `/proc/cmdline`,
  the root entry of `/proc/self/mountinfo`, `/proc/mtd`, kernel/firmware identity
  and the companion's health/process evidence. These establish running-root
  observations independently of the earlier static selector assessment.
- Verify service coexistence, control ownership, loss/recovery of connection and
  normal shutdown/cold boot. Emulator acceptance does not substitute for these.

The diagnostic marker and any writable device files need their own concrete
scope in the installation acceptance proposal. Do not run stock OTA switching
helpers to obtain diagnostics; those helpers erase/program the `ota` partition.

## Stock restoration

Request authorization for the exact stock restore target and final plan. Its
state confirmation allows a known, separately authorized candidate rootfs write,
while still requiring unchanged boot/kernel/OTA/layout and no unresolved writer.
Restore uses the same fresh metadata/staging, single invocation and uncertainty rules as
candidate writing. It is never launched automatically on a candidate failure.
After confirmed return, independently collect again and compare every byte with
the approved restore image, **including its zero padding**. The original device
had an FF tail, so pre-install content acceptance cannot verify restoration.

Confirm normal stock boot, playback and USB behavior after restoration. Keep
candidate and restore captures distinct. The engineering image's rootfs hook
should be absent after exact restoration; any separately provisioned SD marker
or writable state requires explicit cleanup scope. Claim recovery acceptance
only after this full write/readback/boot sequence has actually passed.
