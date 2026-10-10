# Native deployment preparation — V2.57

This page records the initial offline preparation. The subsequent engineering
candidate passed an authorized physical write, exact full readback and
owner-confirmed normal reboot; see the [physical observation](../observations/candidate-installation-observation.md).
The later [physical boot report](../observations/boot-report-observation.md) confirms native
process presence, a loopback listener and primary-root boot data. Native health,
stock protocol round trips and diagnostic USB acceptance remain outstanding.

The owner's player runs stock V2.57 without shell/SSH access. The selected path is
a companion-only modified rootfs, using the offline image approach investigated
in diskOS. The native deployment gate takes priority over Stage 1. This stage
produces **review-only images**, not an installer or physical acceptance.
The implementation now uses [reviewed firmware/writer profiles](firmware-compatibility.md).
V2.57 below names recorded evidence, not a hard-coded runtime restriction. The
next [writer audit and diagnostic-access design](../installer/deployment-writer-review.md)
documents the offline checker and remaining installation work.
The later [USB engineering variant](../usb-boot/usb-diagnostics.md) adds controlled local
diagnostics separately; the three-object companion-only image below is unchanged.

## What diskOS establishes

Local reference: `/Users/zhek/IdeaProjects/diskos`, revision
`646212d57425bd437468ab8896f63b16bae8f744`, specifically
`diskos_installer/imagebuild.py`, `flasher.py` and `docs/HARDWARE.md`.

- V2.57 has a pinned stock squashfs hash and size, but `TESTED_FW` contains only
  209, 228 and 240. A firmware pin is not evidence of successful installation.
- diskOS repacks a modified squashfs and uses a mask-ROM NAND writer. Its current
  image format is 768 × 131,072 bytes (96 MiB), with a matching writer-capacity
  check. This is not the discovered capacity of the physical rootfs partition.
- diskOS replaces the stock UI launch and supplies its own debug-access controls.
  Our candidate preserves stock UI/player, watchdog and their launch scripts.
  Stock V2.57 does not acquire diskOS Debug Mode merely by adding our service.
- Copying a payload to SD or `/usr/data` does not establish an execution path.
  Stock `serial_config.sh` configures a USB gadget after gaining execution; it is
  not proof that a root shell is already exposed. Normal signed OTA is not the
  installation mechanism implemented here.

## Offline candidate and restore artifacts

`scripts/deployment/build_candidate.py` uses the pinned emulator's offline
firmware verification/decryption utilities. It reconstructs the official rootfs,
requires its exact size/hash, and extracts a fresh tree without emulator patches.
It checks a static MIPS32 little-endian ELF, then adds exactly:

| Object | Purpose |
| --- | --- |
| `/opt/disc-web` | Companion directory |
| `/opt/disc-web/disc-service` | Existing static executable with embedded assets |
| `/etc/init.d/S99disc-web` | Optional, non-blocking loopback-only launch hook |

The builder rejects changed/removed stock objects and any extra additions. It
re-packs with LZO/128 KiB blocks, extracts again, and compares every recorded
path, type, permission, uid/gid, content hash, symlink target and device number.
It does not claim preservation of timestamps or xattrs: packing explicitly
disables xattrs. Only after that verification does it publish zero-padded
candidate and stock restore images. Oversize images and existing destinations
are rejected; no image is truncated to fit.

Recorded build on 2026-09-23:

| Measurement | Result |
| --- | --- |
| Original squashfs | 80,596,992 bytes |
| Candidate squashfs | 80,957,440 bytes |
| Padded image size, each | 100,663,296 bytes |
| Stock objects preserved by the inventory comparison | 3,492 |
| Added objects | Exactly the three above |
| Native executable | 683,216 bytes; unchanged from Stage 0 |
| Full extraction comparison | Passed |

SHA-256 fingerprints:

```text
stock squashfs:
111e4dd7ee3d7ff91ba7e61181690be7ffd22bd1bbd13ad513f5f015ffb302ae
disc-web-v257-review-only.bin:
ccd07485e923489b998cd533321d00fa1f135e23469ed91aae63871b55211d15
stock-v257-restore-review-only.bin:
e75d85bddb7b9fe304dfe334058693fb522a93cb1048ddfb5417f136f204eae0
```

Local images and reports live in ignored `work/deployment-v257/`; they must not
be committed or distributed as project assets. `report.json` explicitly records
`hardwareQualified: false` and `flashAuthorized: false`. Squashfs build timestamps
can change candidate hashes on a subsequent build; use that build's report.

The restore image is made from official firmware. **It is not a live NAND,
userdata or calibration backup.** Historical diskOS hardware notes describe a
128 MiB primary rootfs and a much smaller recovery rootfs; this does not establish
two interchangeable slots or the owner's current bad-block map.

## Boot, manual lifecycle and fallback

Stock `rcS` executes `S??*` hooks in order. `S21mount_ubifs` mounts `/usr/data`;
`S98FIIO` starts the stock initialization in the background. The added S99 hook
starts only our executable. It binds `127.0.0.1:7870`, fixes the stock upstream to
loopback, and does not acquire the TCP control channel before explicit Connect.
It adds no SSH server, USB configuration, network setup or production interpreter.

The hook uses stock BusyBox `start-stop-daemon`, a volatile `/run` pidfile and an
executable match. Failed startup does not stop the remaining init sequence.
There is no restart loop or watchdog for the companion. The persistent opt-out
marker is `/usr/data/disc-web.disabled`; it is checked before launching.

The following procedure is conditional on **already established root access and
an installed candidate**. Neither prerequisite exists on the owner's stock device
yet. It is a lifecycle design, not a bootstrap instruction:

```sh
/etc/init.d/S99disc-web start
/etc/init.d/S99disc-web stop
# Disable subsequent boot starts, then stop the current process:
touch /usr/data/disc-web.disabled
/etc/init.d/S99disc-web stop
# Re-enable and explicitly start:
rm /usr/data/disc-web.disabled
/etc/init.d/S99disc-web start
```

Stop requests SIGTERM; native shutdown is bounded by the Stage 0 contract. A
manual restart must wait for process exit/port release before starting again.
The marker is not an end-user recovery button without a working access channel.

## Tests and their boundary

Six synthetic conformance tests cover ELF shape/dependencies/truncation, stock
fingerprint rejection, zero-padding without overwrite/truncation, exact rootfs
delta and symlinks without following them into the host filesystem.

`tests/integration/deployment_boot.py` runs the packed candidate's stock BusyBox
and our MIPS executable in private PID, mount and network namespaces. It does
not execute stock driver init or touch USB. It verified disabled launch,
loopback health, native SIGTERM/relaunch and preservation of an unrelated process
when its PID was placed in the service pidfile.

Its result is **`launch-verified-management-unqualified`**. The initial duplicate
start assertion failed: under this qemu-user/binfmt setup `/proc/PID/cmdline`
starts with `/usr/bin/qemu-mipsel-static`. BusyBox `-x` matches the first command,
so it cannot recognize the target as a native hardware exec would. The production
executable guard is retained. The test uses SIGTERM on its known namespace-local
child for cleanup; repeated-start idempotency and hook-managed stop/restart are
reported as `null`, not passed. Native process management must be qualified on a
native kernel before accepting the hook for installation.

Reproduce with a disposable stack from development (snowsky-disc-web `docs/development.md`), Python
3.11+ and the already-built MIPS executable. The output directory must be fresh:

```sh
bash scripts/test.sh
DISC_CONTAINER=$(python3 -c 'import json; print(json.load(open("work/emulator.json"))["id"] + "-emu")')
docker exec -e PYTHONPATH=/repo "$DISC_CONTAINER" python3 -B \
  /platform/scripts/deployment/build_candidate.py --version 2.57 \
  --ota /ota --binary /platform/build/mips/disc-service \
  --hook /platform/device/deployment/S99disc-web \
  --output /work/disc-deployment-v257
docker exec "$DISC_CONTAINER" timeout 45 unshare --mount --net --pid --fork \
  python3 -B /platform/tests/integration/deployment_boot.py --output /work/disc-deployment-v257
```

The hook test changes the extracted verification fixture only, not the packed
images. Preserve images and reports on the host before deleting the disposable
stack. Native runtime/assets did not change; the previous MIPS transport and
browser results still apply. This stage adds no full-kernel or hardware proof.

The profile-enabled rebuild is stored separately in `work/deployment-profiled/`.
It includes profile fingerprints and passed the offline reviewer; older evidence
above is retained unchanged. The new candidate SHA-256 is
`1db46df5fe2b9a22c836957aef6bdb1eb938d1806e158d176bda59252b2bdf31`;
the restore image hash is unchanged. The isolated hook result remains
`launch-verified-management-unqualified`.

## Remaining deployment decisions and physical acceptance

1. Review the diskOS writer/bootstrap for this exact V2.57 target: writer
   capacity, partition selection, bad-block handling, live backup/readback and
   an independently usable restore route. Produce a concrete device-specific
   procedure before seeking authorization for any physical connection/write.
   Do not bypass diskOS's untested-firmware guard to infer acceptance.
2. Qualify the separate opt-in [USB engineering image](../usb-boot/usb-diagnostics.md) on
   hardware: enumeration, card activation/revocation and stock mode coexistence.
   The ordinary companion-only candidate above contains no diagnostic bootstrap.
3. Verify actual ISA/FPU/NaN mode, kernel/syscalls, `/proc` process matching and
   native execution. The binary is MIPS1/o32 soft-float static PIE; the earlier
   hard-float build crashed in the Linux 4.4.94 FPU emulator's stack trampoline
   (corrected analysis (snowsky-disc-web `docs/combined-browser-observation.md`)). Static musl
   avoids the glibc loader, but QEMU's successful execution cannot certify the
   physical kernel. Historical X2000 hardware notes are
   supporting evidence, not a V2.57 qualification.
4. Measure physical baseline/free memory, service RSS, CPU and FDs while stock
   playback runs. Set hardware budgets from those measurements; the emulator's
   roughly 12 MiB QEMU RSS is not the player's service RSS. Repeat idle, reconnect,
   unavailable Wi-Fi, suspend/power and stock coexistence acceptance.
5. With separately authorized installation, verify cold boot, duplicate start,
   matching-process stop, disable/re-enable persistence, failure isolation and
   stock restore/readback. Record physical version and outcomes before closing
   the gate. Keep userdata, calibration, kernel and recovery partitions outside
   any rootfs-only write scope.

For the first diagnostic run, loopback plus a controlled local access channel is
the proposed boundary. General LAN use remains blocked on possession pairing,
credential provisioning/revocation and a TLS decision. Host/Origin checks do
not authenticate an owner. A future pairing design needs an explicit device-side
confirmation mechanism compatible with the stock UI, authenticated HTTP/WS,
request-origin protections and a usable revoke/reset path. No such mechanism
is claimed implemented here.
