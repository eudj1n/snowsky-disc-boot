# Boot-selection evidence and bounded acquisition

The stock OTA scripts explicitly use a selector in the `ota` partition. Their
read-side helper associates `ota:kernel2` with `kernel2`/`rootfs2`; the companion
installer targets `rootfs`. The physical selector and stock bootloader have now been
[captured and independently validated](boot-evidence-observation.md). The observed
`ota:backup` has now been [reviewed against its actual SPL consumer](bootloader-review.md)
and selects the primary kernel/rootfs pair. Physical write admission remains closed.

This stage implements and tests a bounded **read-only NAND acquisition** for
those missing inputs. It does not access the player during build, planning or
conformance tests. Acquisition needs separate authorization of its exact plan.

## What the local inputs establish

The official main-system OTA contains the kernel and rootfs; the update package
also supplies a separate recovery kernel/rootfs. It does not supply the installed
bootloader. The locally available diskOS SPL source archive does not contain
this stock `ota:kernel` selector implementation. Its generic OTA/NVRAM algorithm
must not be substituted for the player's boot policy.

The reviewed main-rootfs scripts have these fingerprints:

| Path inside rootfs | SHA-256 |
| --- | --- |
| `etc/ota_bin/ota_local_method.sh` | `1c739cf0e197293cd678a9bd064c5db8a4f93abec68d90529ca43c7411054394` |
| `etc/ota_bin/ota_utils.sh` | `7b725f425465434e0dcef665bca908463974b1c7dbac47dd17d04752ffa8d0bd` |
| `etc/ota_bin/recovery_to_main_os.sh` | `434ea93fa61a70956fabbe895d4850f9bf2ee2cb4a9d1d88496e65c8a4c05474` |

The copies already retained by the reference project match those in the locally
unpacked selected firmware and an independent extraction directly from the
reviewed stock-restore squashfs. They were read and hashed, never sourced or executed.

- `mtd_read_str` resolves the named partition through `/proc/mtd` and invokes
  `nanddump` for its first 256 bytes.
- `local_get_current_kernel_dev_path` and `local_get_current_rootfs_dev_path`
  use a substring match for `ota:kernel2`. With that match they select the
  secondary pair; otherwise they select the primary pair.
- The update-target helpers choose the opposite pair. `local_set_next_boot_device`
  toggles the selector by calling `mtd_write_str`.
- `mtd_write_str` erases one block and programs a string padded with spaces to
  256 bytes. These are **mutating helpers**, not diagnostic commands.
- `recovery_to_main_os.sh` explicitly writes `ota:kernel` to `/dev/mtd5` and
  reboots. In the observed partition table, `mtd5` is `ota`; an inconsistent
  comment in that script does not change the actual partition mapping.

The scripts establish the userspace protocol and intended pairing. They do not
prove that a particular saved marker was consumed during boot, establish the
stock bootloader's bad-block handling, or rule out recovery overrides. After
acquisition, review the actual bootloader with the reference Ghidra helpers in
this repository's ignored work directory; do not modify the reference project.
Identify selector read/validation, kernel loading, root arguments and overrides.

Our classifier is deliberately stricter than the shell's substring test. It
recognizes only the exact profile token followed by space padding through byte
255. Erased, zero-filled, truncated, conflicting and malformed values remain
unknown; they never imply primary boot or permit writing.

## Profile and payload boundaries

`firmware/boot/v<version>.json` binds the complete chip/metadata policy, reviewed
partition extents, OTA script fingerprints, selector format and time budget.
Selection uses the existing version/profile mechanism. A new firmware release
requires renewed script/bootloader review and physical acceptance. There is no
firmware-version branch in the acquisition code.

Two separate builds reuse the freestanding batch reader. Its former nonzero-start
restriction was removed to admit a reviewed interval starting at page zero;
empty ranges and requests outside the compiled interval remain rejected before
any MMIO. New C tests check page zero, both upper edges, the excluded gap and
unchanged rejection of page zero under an older nonzero range. The
compiled page bounds differ, and both are pinned in one build manifest:

| Payload | Compiled physical page range | Host acquisition |
| --- | --- | --- |
| `uboot` | `[0, 1024)` | Paired first-page markers for 16 blocks, fresh partition page, then every page of every good block |
| `ota` | `[87552, 88064)` | Only paired first pages of its eight blocks: 16 page reads total |

The host's OTA requests are more restrictive than its partition-bounded reader:
it rejects non-marker pages. The interval between the two compiled ranges is
never admitted by either payload. Userdata, MAC/calibration, kernel and rootfs
partitions are outside this acquisition. This is not a full-chip backup.

The saved partition table must match both profile extents before USB discovery.
After the boot marker scan, a fresh metadata page must match the saved page
exactly before bulk bootloader collection proceeds. An unusable metadata block,
changed metadata, inconsistent markers or untrusted ECC/configuration stops the
session. Bad boot blocks are recorded and skipped; their contents are neither
invented nor silently substituted from another partition.

The host performs one SPL upload/comparison/execution, DDR and RAM checks, then
uploads/compares the boot reader. It scans the boot partition, reloads/compares
the OTA reader, and reads the OTA marker/selector pages. It never executes the
captured bootloader or a NAND writer. Both readers use only the existing admitted
NAND commands `0x9f`, `0x0f`, `0x13`, `0x0b`.

## Concrete bounds and outputs

For the current profile:

- At most 2,097,152 bootloader main bytes, with an explicit physical block map.
- At most 1,073 framed page/OOB records and 19 batch executions; fewer if boot
  blocks are bad. The metadata page is checked again during the data pass.
- At most 1,170 USB protocol calls and 180 seconds for the entire session.
- One SPL, at most 19 read-payload entries, no writer entry, NAND writes, retries,
  automatic reconnects or automatic power cycles.

Nonce, increasing sequence, CRC, page identity, chip ID, ECC/configuration,
transfer counts, complete batch status and zero unused tails are checked.
Repeated first-page **main bytes and marker** must agree; internal ECC-parity
bytes remain evidence and are not mistaken for user data changes.

A success result is `boot-evidence-collected`. `active_boot_verified` and
`flash_ready` remain false even if an exact selector token is recognized. Every
good OTA block's selector is retained independently. The tool does not assume
which block the stock bootloader selects or resolve conflicting selectors.

Outputs in a fresh ignored directory include:

- `request.json`, `dependency.json`, `transfers.jsonl`, raw reads and `result.json`.
- `batch-*-request.bin` and `records.bin`, retaining original physical page numbers.
- `metadata-main.bin`, requiring the planned exact bytes.
- `uboot-good-blocks.bin`, concatenating only the listed `uboot_good_blocks`.
  This is a mapped RE input, not a raw physical image or a restoration image.
- `ota-block-<physical-block>-main.bin` for each good block's paired main bytes.

Errors retain completed records and raw failed transfers. No failure causes a
write, retry or relaxed comparison. The preparation stage used no physical access;
the subsequent authorized acquisition is recorded separately.

## Reproduce preparation

```sh
python3 scripts/deployment/boot_evidence.py build \
  --diskos /path/to/diskos --output build/boot-evidence-003
python3 scripts/deployment/boot_evidence.py plan \
  --diskos /path/to/diskos --build build/boot-evidence-003 \
  --metadata-page work/writer-stage-observation-001/metadata-main.bin
```

Only after authorization, `acquire` takes those plan inputs plus `--libusb`, a
fresh `--output`, and the exact `--approved-plan-sha256`. The player must be in USB Boot for this experiment.
After the host closes normally, separately confirm normal stock reboot.

The session's audit reconstructs it offline from its saved files alone and
makes no USB calls (save the `plan` output as the plan file first):

```sh
python3 scripts/deployment/audit_usb_boot.py \
  --run work/boot-evidence-<n> --plan work/boot-evidence-plan-<n>.json \
  --build build/boot-evidence-<n> --diskos /path/to/diskos \
  --output work/boot-evidence-<n>/offline-review.json
```

It reads the reviewed profiles as files and rebuilds the page and boot
policies from them; it checks the plan against them, the build's payloads and
the SPL by their digests, every record (request, nonce, sequence, page, CRC,
chip ID, configuration, ECC, polls), the pages each scope had to read given
the markers it found (bad blocks never read as data), the metadata page, the
mapped bootloader, each OTA selector, every batch's request and result, and
the USB journal call by call: the entry's DDR diagnostic and the SPL run only
when it is not clean, the RAM patterns, both payloads, and each batch's held
completion ask when the plan asks it. Its report, `saved-boot-trace-matches`,
is the `offline-review.json` that `installation_review.py` takes for the boot
evidence. It keeps the checks of the first observation's one-off audit
(`work/audit-boot-evidence-001.py`) without its fixed page numbers and batch
counts. Synthetic sessions of the fake ROM, at the reviewed profiles' full
size, run in GitHub Actions (`test_audit_usb_boot`), with changed records,
reads, journal rows, batch requests and result fields refused.

Ten firmware-free tests cover complete scope, bad-block skipping, strict selector
classification, input/manifest/profile drift, future-version selection, framing,
ECC and stale-result failures, all collection-transfer failure boundaries,
timeouts and interruption. The fake ROM never accesses hardware. Existing
GitHub Actions discovers these tests with no proprietary inputs or credentials.

## Acceptance sequence

1. Separately authorize and collect this exact boot/OTA evidence — completed.
2. Independently validate all saved records/journal calls and reconstruct the
   bootloader with its actual block map — completed, with fingerprints in the
   [physical observation](boot-evidence-observation.md).
3. Review the actual selector consumer and boot arguments in Ghidra/assembly —
   completed for the captured normal NAND path. The [SPL review](bootloader-review.md)
   reconciles the saved selector with primary-rootfs selection and records its
   limits; live command-line/mount observation remains separate.
4. Complete the [installation/recovery procedure](installation-procedure.md),
   bind the accepted boot evidence and current image hashes, then review a
   concrete write-admission change and request that separate write authorization.

## Local acceptance — 2026-09-24

The final complete suite passed: 227 Python tests, eight JavaScript tests and
native C assertions. All ten new Python cases also passed in Linux. The expanded
C boundary tests passed as a MIPS32 executable under QEMU and with host AddressSanitizer/
UndefinedBehaviorSanitizer. No hosted GitHub Actions run or physical acquisition
is claimed.

The offline build `build/boot-evidence-003` uses the pinned toolchain image
`sha256:68e50109b799b49e4ba1acf53ca906ffb50d9d1fd27499f1a22056abc89c516b`.
Both payloads fit the 32 KiB code window; ELF/raw equality, no undefined symbols,
no BSS/GOT, entry and segment bounds passed. The largest individual compiler
stack frame is 4,472 bytes; this is not a measured total call-chain stack peak.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `uboot/identity.bin` | 6,304 | `3fd10ec5ea6b37104e99ea3cbfc0f5a863e03184adfc1dad3df944be540c6d6e` |
| `ota/identity.bin` | 6,312 | `dd98de985c61d5738b74b82e3d6755bd7207f6e3134308d343a0efd2b5fa02b1` |
| `build.json` | — | `6d3452d2310904282016b8f585b0f0f65efe9cfc0f774ab57a877d0990169c14` |

The actual-input offline plan is `work/boot-evidence-plan-003.json`, plan SHA-256
`80abd7c5a1c43851abe37478e261463b8e4748cd773261bcb0af26699a9bca31`.
It uses the exact metadata page retained during the successful complete-image
RAM staging session. Plan generation loaded no USB library.

The C source change invalidates earlier build source manifests for **new**
acquisitions; rebuild those payloads and regenerate their plans before future
use. Historical evidence and original plans remain unchanged. The new boot plan
above already binds the updated source. Physical write admission was not changed.

Runtime logs remain ignored under `work/boot-evidence-*` and
`work/boot-selection-stock-script-pins.log`. The external repositories were only
read. No player discovery, firmware script execution or selector write occurred.
