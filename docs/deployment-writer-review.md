# Offline installation and recovery review

This continues [native deployment preparation](native-deployment.md). The local
diskOS checkout at `646212d57425bd437468ab8896f63b16bae8f744` was inspected without
executing its installer, native USB helper or debug-access scripts. Firmware
and writer selection is now [profile based](firmware-compatibility.md).

## Reviewed writer behavior

The current `diskos-my-write5-768` profile describes `flash/my_write5.c` and the
4,704-byte `my_write5_dram.bin` with SHA-256
`4f23a3c99bdba4ac9e674efe73340d73014e4d99bcfd481082b06482b87c4442`.
The source, binary, SPL, host wrapper and USB source are pinned separately.
These hashes identify reviewed inputs; they do not prove a reproducible binary
build or successful use on the owner's device.

| Parameter | Current profile |
| --- | --- |
| NAND assumption | GD5F2GM7; 2,048-byte pages, 64 pages/block |
| Logical image | 768 × 128 KiB = 96 MiB |
| Start block | 80, byte offset `0x00a00000` |
| Scan/write ceiling | Block 912 exclusive, byte offset `0x07200000` |
| Bad-block reserve | 64 blocks |
| Whole-block attempts | At most 6 |

The header contains historical self-test comments; the active `my_write` function
is a real rootfs writer. It does not discover `/proc/mtd`, the active boot slot
or a target partition and it does not validate NAND identity in that entry path.
It initializes SFC/NAND, scans each candidate block's first-page spare marker
twice, and aborts when reads disagree or fail. It skips factory-bad blocks,
erases/programs good blocks and reads back all 64 pages against the staged data.
Persistent failures abort rather than inventing a different logical mapping.

The later kernel review confirms that its sequential good-block translation
agrees with this marker location and mapping rule. The actual metadata observation
places the writer range inside primary rootfs. The subsequent
[full physical collection](rootfs-full-observation.md) establishes the observed
block map (383 and 716 skipped) and exact official rootfs content. Active boot
selection still needs evidence; the strict padded-image comparison failed
because the player returns FF after the official rootfs rather than zeros.
The writer's per-page comparison is not a pre-write backup or an independent
post-write logical-image dump.

The later [physical identity and chip comparison](nand-chip-review.md) identified
the XTX XT26G02C family on the owner's unit. Documented geometry and marker
location agree. The [stock kernel review](nand-kernel-review.md) also establishes
its XTX ECC/marker handling and matching program command order. A physical
metadata-page read and owner-confirmed return to stock boot subsequently passed.
These update the initial unobserved-NAND state without qualifying writes.

The existing flasher checks capacity before starting, but its pre-write predicate
accepts a larger writer as well as an exact one; its post-write predicate demands
equality. Our offline reviewer requires **exact equality before any future write**:
a larger writer could consume data beyond the staged image. The instruction
offset/opcode is meaningful only for the separately pinned writer binary.

The wrapper's host timeout/cancellation kills the host process group. The writer
has already been transferred to player RAM and executes independently; no
device-side abort is established by killing `usbboot`. An interrupted or missing
readback is an unknown outcome, not permission to unplug or immediately retry.
A future installer must preserve logs and resolve the device state explicitly.

## Offline checker

```sh
python3 scripts/deployment/review.py --version 2.57 \
  --artifacts work/deployment-profiled \
  --diskos /path/to/diskos > work/deployment-profiled/review.json
```

The version above is an example; omit it to use the selected default or pass
another reviewed profile. This command reads ordinary local files only. It
does not import diskOS code, enumerate USB, run helper binaries or open devices.

It verifies the selected profile hashes in the build report, image size/hash,
squashfs magic, zero padding, independently pinned stock restore content, source
pins and exact writer capacity. A successful review still reports `flashReady:
false` and lists unresolved physical/bootstrap requirements. It trusts the local
builder's full-tree comparison report; it is not a second squashfs extractor or
an authenticity signature for an untrusted report/image pair.

An optional saved 1,024-byte writer record can be supplied with `--debug FILE
--usbboot-exit CODE`. The parser rejects a failed host transfer, incomplete or
poisoned records, wrong start/capacity/progress, inconsistent NAND/ECC state,
bad-block lists/spans, and impossible retry counters, even if a success sentinel
is present. Ten synthetic tests cover these cases and the file checks.

A structurally consistent old record could still be stale: it has no image hash
or session nonce. The result is `consistent-writer-record`, with freshness and
boot verification explicitly false. A future acquisition layer must bind the
record to the exact device/run and preserve transfer evidence. Local request IDs
alone cannot make a device record fresh.

## Diagnostic access decision

The opt-in **USB CDC-ACM mode in a separate engineering image** is implemented
and tested offline. [Its design and evidence](usb-diagnostics.md) document the
exact card marker, bounded supervisor, Ghidra findings, MIPS/PTY tests and packed
image checks. The default companion image does not contain it; no Wi-Fi or stock
password change is required by the diagnostic design.

Stock USB ownership prevents or revokes our session; the helper never forcefully
unbinds a stock gadget. It executes a fixed shell, never card scripts. The local
root console is engineering access, not consumer pairing or a LAN service.
Physical enumeration and stock DAC/storage coexistence remain unqualified.

## Concrete remaining installation sequence

1. Extend the bounded host transport for the reviewed writer and independent
   reader workflow. Direct macOS libusb CPU/SPL/RAM/metadata access has passed;
   the writer invocation/completion route is not yet implemented or qualified.
   Reusing the unqualified diskOS helper bundle is not a prerequisite for the
   direct transport approach.
2. Use the completed offline diagnostic-image/delta checks and preserved matching
   official-stock restore as inputs; physical activation/disable still needs
   acceptance after a reviewed and separately authorized installation.
3. Complete partition/active-slot and bad-block mapping review, then establish
   independent readback. Explicitly settle whether a live backup is in scope. The
   inspected installer saves stock from the OTA; it supplies no qualified live
   NAND backup flow. The generic cloner `--dump-partition` code is not proof that
   a matching read protocol is available from a bare stock mask-ROM device.
   [Subsequent RAM transport work](ram-transport.md) passed separately authorized
   CPU/SPL/RAM, identity and metadata-page observations. The independent
   [saved-image verifier](logical-readback.md) now has synthetic acceptance and
   exact-image/partition plan checks. The [bounded collector](rootfs-collector.md)
   is implemented with synthetic ROM/SFC acceptance and separate MIPS probe/full
   builds. The separately authorized full collection and stock reboot now passed;
   all official rootfs bytes match, but exact equality with the zero-padded
   restore image was rejected. A [separate reviewed stock contract](preinstall-review.md)
   now accepts exact official content and its profile-selected tail, preserving
   strict complete-image verification after either candidate or restore writing.
   Full-chip backup remains deferred, not a new prerequisite. The
   [acquisition review](native-acquisition.md) also records
   unchecked cloner ACK/CRC handling and remaining backup limitations.
4. Only then present the exact physical action and its target to the owner.
   A later write must target the qualified primary rootfs range, preserve
   userdata/calibration/kernel/recovery, and prohibit replay on uncertain results.
5. Capture fresh writer completion, independently read back the logical image,
   then verify native startup/coexistence/cold boot and the diagnostic opt-out.
   Exercise the stock restore path using the same qualified geometry and verify
   its readback/boot. An image on disk does not close the recovery gate.

The initial offline image stage is complete; hardware compatibility, diagnostic
bootstrap, readback, backup scope and installation/recovery remain open in the plan.

The [installation/restoration sequence](preinstall-review.md#installation-and-restoration-design)
now records the writer entry, completion buffer, full image staging range and
the distinct pre-installation/post-write acceptance rules. This is a design for
the pending writer transport, not a physical flashing command or authorization.
