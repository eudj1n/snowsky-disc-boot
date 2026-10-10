# Physical acquisition preparation

The first host probe is implemented and tested synthetically. It can request one
CPU-info reply from mask-ROM without uploading executable code or accessing NAND.
One separately authorized observation on the owner's player returned five bytes,
`X2000`; the original exact-eight-byte policy rejected that reply. The revised
profile now matches those retained bytes offline without another USB request. Live NAND
acquisition, partition/active-slot confirmation and recovery qualification remain open.

An [offline identity-only SFC/RAM payload](nand-identity.md) is now implemented
and tested with fake MMIO. The [host RAM transport](../usb-boot/ram-transport.md) provides
separate RAM-check/identity modes. After correcting the download-length omission,
a separately authorized physical RAM-check passed SPL readback/execution, DDR
diagnostics and two reserved-region RAM passes. A subsequent separately authorized
identity run returned matching `0b 12 00` ID observations and a valid request-bound
record after ROM return. NAND geometry/ECC, physical page reading and stock
cold-boot acceptance remain outstanding.

## Why the existing dump command is insufficient

This review used diskOS revision `646212d57425bd437468ab8896f63b16bae8f744`, notably
`src/usbboot/usbboot.c`, `diskos_installer/flasher.py`, `flash/my_write5.c`,
`flash/assemble_stage1.py`, its README and hardware notes. The inspected USB source
has SHA-256 `d7392778901d955461102d31d9e93bf012648e94f64ad279e5774a53cdb48b90`.
No diskOS installer/helper was executed and no implementation source was copied.

| Route | Observed behavior | Admission |
| --- | --- | --- |
| Mask-ROM `GET_CPU_INFO` | Vendor/device control-IN, request 0, eight-byte requested length | Five-byte `X2000` observed; exact signature admitted by the revised profile |
| `--upload` | Bulk-IN from an addressed memory buffer after address/length setup | A RAM transfer, not a NAND dump by itself |
| `--dump-partition` | Configures a running cloner policy, initializes it, issues its `VR_READ` protocol | Requires a separately qualified resident cloner; not established on stock mask-ROM |
| Installer SPL + `my_write5_dram.bin` | Initializes RAM, loads writer/image, executes NAND programming and reads writer status | A write path; never use it to discover geometry or obtain a pre-write backup |
| Engineering USB console | Runs only after the modified rootfs is already installed | Cannot supply the backup needed before the first installation |

The cloner dump implementation also logs but does not validate the eight-byte
ACK/CRC response, ignores the trailing ACK result/content, and can loop without
progress on a successful zero-byte bulk transfer. Its output uses truncating file
creation and has no acquisition manifest with device/run/range/ECC provenance.
Changing its storage-medium option does not fix these gaps or supply the missing
device-side reader. The source comment referring to a proven `my_read.c` is not
a source/binary artifact present in this checkout and cannot establish a reader.

## Fixed ROM probe

`scripts/deployment/rom_probe.py` is an original Python host utility using the
local libusb C ABI via `ctypes`. It adds no Python package dependency and nothing
to the player image. Import and `plan` never load libusb or enumerate devices:

```sh
python3 scripts/deployment/rom_probe.py plan --version 2.57
```

Selection follows explicit `--version`, `FW_VERSION`, then `firmware/active-version`.
`firmware/probes/v<version>.json` must match the selected firmware/rootfs profile.
It supplies reviewed VID/PID, timeout and protocol-source provenance. The initial
profile selects `a108:eaef`, 1,000 ms and exact response hex `5832303030` in
`accepted_reply_hex`. Only the known CPU-info protocol is
implemented; a profile cannot select an arbitrary USB opcode or OUT transfer.

The requested firmware version is a host-side selection, **not firmware detected
over USB**. Eight bytes of CPU information do not identify a unique player, NAND,
firmware release, partition map or active boot slot. Even a complete reply reports
`cpuIdentityVerified: false` and `flashReady: false` until separately interpreted
and qualified. Arbitrary reply bytes are retained as hex without string truncation.

Acquisition reserves a fresh private directory before loading an explicitly
selected trusted libusb library. It enumerates descriptors, requires exactly one
matching VID/PID, records bus/address/port path, opens that device and performs
exactly one application-level vendor request:

```text
bmRequestType = 0xc0     IN | vendor | device
bRequest      = 0        GET_CPU_INFO
wValue        = 0
wIndex        = 0
wLength       = 8
timeout       = 1000 ms  (selected profile)
attempts      = 1
```

There are no interface claims/detaches, configuration changes, resets, bulk
transfers, RAM downloads, NAND commands or retries in this utility. libusb/the OS
may perform normal USB enumeration/control operations; the single-request claim
refers to the vendor operation issued by our application. Device-open permissions
were exercised by the first observation below; wider host/device compatibility
remains unqualified. The timeout
bounds the control request, not every OS enumeration/open operation.

Zero/multiple targets, descriptor/port-path errors, failed open, timeout,
disconnect and responses outside the exact profile signatures fail. The eight-byte
request buffer does not require accepting arbitrary eight-byte content. No trimming,
prefix matching or implicit zero padding is used. The opened handle,
enumeration references and library context are released on normal/error paths.
There is no automatic fallback to another transport or mode.

## Evidence and session identity

Every acquisition creates these files without replacing an existing directory:

- `request.json`: incomplete intent, UUID session ID, UTC start, exact request,
  firmware/probe profile hashes and probe source hash, written before library load.
- `dependency.json`: resolved local libusb path and file hash, written before
  loading the library. This identifies the selected file, not every transitive
  OS dependency or a qualified redistributable tool bundle.
- `result.json`: reply bytes/transfer result when available, connection location,
  completion time and success/failure. Ordinary failures and interruption preserve
  a result; force-kill, native crash or disk failure can leave incomplete evidence.

Without a completed result, the operation's outcome is unknown; preflight fields
in `request.json` do not prove that no request was later attempted. Preserve that
directory and inspect it instead of silently retrying. An exact reviewed signature
is labeled `cpu-signature-matched`, with `cpuSignatureMatched: true`; this is not
unique-device authentication, firmware compatibility or backup success.

Discovery and opening are recorded separately. Initialization may discover USB
devices, so a failure before opening the selected target leaves
`physicalDeviceAccessed: null`, not a claim that no device was accessed. A loaded
library failure before initialization records `usbDiscoveryAttempted: false`.

The session ID correlates local artifacts. Mask-ROM does not echo a nonce and the
CPU reply is not a unique device identifier, so `freshnessAuthenticated` remains
false. USB bus/address/port identifies this connection only. Hashes detect changes
against retained evidence; they do not authenticate an untrusted evidence bundle.

## Physical observation procedure

After separate authorization for **one CPU-info observation only**, the proposed
step is to power off the owner's stock player, hold Volume Down while connecting
USB (the diskOS README describes a dark screen), and run only this probe. The
first ROM connection is recorded below; return to stock operation is unverified.
No installer, stage-1/SPL, writer or cloner command belongs in this step.

For this host, a local arm64 libusb 1.0.30 installation is available. Conditional
command, to be used only for the authorized observation and with a fresh output:

```sh
python3 scripts/deployment/rom_probe.py acquire --version 2.57 \
  --libusb /opt/homebrew/opt/libusb/lib/libusb-1.0.0.dylib \
  --output work/rom-cpu-observation-001
```

The operator records the physical player and mode entry separately; the tool
cannot establish ownership or distinguish two identical boards from CPU bytes.
A failure ends this step. Interpret the saved result before deciding any next
physical operation; CPU observation does not authorize loading code or flashing.

## First authorized observation — 2026-09-24

The owner reported the player connected and explicitly authorized CPU_INFO.
At `2026-09-24T00:22:57Z`, the probe discovered exactly one `a108:eaef` device,
opened it and issued one vendor request. libusb returned **5**, with reply hex
`5832303030`, ASCII **`X2000`**. There was no transport timeout/error code, but the
probe exited 1 with `status: failed` because its original policy required exactly eight
bytes. This establishes a responding ROM endpoint, not successful acceptance of
the original response-length contract or verification of NAND/firmware identity.

The source reference's exact-eight-byte expectation does not describe this
observed response. The original failure report remains intact. No second
request, RAM upload, interface claim/reset or NAND
operation was executed. Handles/context were closed by the probe's cleanup path.

Private evidence: `work/rom-cpu-observation-001/{request,dependency,result}.json`.
Session `00dde6fa-fe76-40d5-90a6-36cd54dbd258`; result SHA-256:
`2ac6c87232b7c0efd79ab8c30abaa3eb963db58b7e682f2905dc76b548f447b4`.
The original probe/profile fingerprints are retained in that result. The
synthetic tests include both this exact signature and shorter, longer, padded and
different CPU strings. No retry was needed to add or run these regressions.

## Offline signature review — 2026-09-24

The revised firmware probe profile admits exactly `5832303030`. Neither arbitrary
short replies nor the previously accepted arbitrary eight-byte replies pass.
Review retained evidence using its independently recorded hash:

```sh
python3 scripts/deployment/rom_probe.py review --version 2.57 \
  --result work/rom-cpu-observation-001/result.json \
  --sha256 2ac6c87232b7c0efd79ab8c30abaa3eb963db58b7e682f2905dc76b548f447b4 \
  --output work/rom-cpu-review-001
```

`review` never loads libusb. It checks the saved hash, firmware selection,
protocol/target/request, single opened device, attempted request, completion,
scope and exact reply bytes. It writes to a fresh directory only. The original
profile fingerprint/status are retained alongside the new profile fingerprint;
the original result is never rewritten. A matching result reports
`saved-cpu-signature-matched`, `originalStatus: failed`, and `flashReady: false`.
The recorded review in `work/rom-cpu-review-001/review.json` passed. This is an
offline interpretation of an earlier observation, not a new physical acceptance run.

## Work still needed for a real backup and installation

The subsequent [NAND core milestone](nand-reader-core.md) implements synthetic
identity/page/OOB reads and offline record validation. It has no real controller
adapter or RAM entry/return path and cannot acquire this player's NAND yet.

1. Audit/build a RAM-only reader and its exact SPL/DDR inputs. The present writer
   must not be repurposed merely by its historical “self-test” comments. Prove
   the reader has no erase/program/OTP/protection-unlock paths, review volatile
   controller/ECC configuration, and bound every wait and transfer. Test a fake
   controller first, then separately authorize RAM execution on hardware.
2. Observe NAND identity and geometry with the qualified reader. Reconcile
   page/OOB/block sizes, capacity and ECC semantics against reviewed chip support.
   Keep unknown identity or geometry as a refusal. The historical partition table
   and the writer's compiled offsets are hypotheses, not observations of this unit.
3. Acquire the entire physical data/OOB address range needed for recovery,
   including bootloader, both kernels/root filesystems, OTA state, calibration and
   userdata. Preserve physical addresses, bad-block markers and per-page read/ECC
   status; distinguish corrected data from genuinely raw reads. Never replace
   failed reads with zeroes or claim skipped blocks were backed up. Bind chunk
   sequence/range and a nonce to a reader protocol designed to echo them.
4. Repeat acquisition independently, compare immutable-region contents and
   address/status coverage, and retain both captures plus provenance. Resolve
   inconsistent/bad-block/ECC observations before accepting a backup. Two host
   copies of the same transfer are not two independent reads.
5. Establish partition boundaries and boot selection from observed boot/OTA
   metadata and reviewed parser/RE evidence. Derive the logical rootfs using the
   kernel/writer's qualified bad-block policy while retaining the physical dump.
   Confirm the proposed writer range lies inside the intended rootfs partition
   with reserve; never infer interchangeable slots from names alone.
6. Only then review a concrete rootfs write and independently usable recovery
   procedure. After a separately authorized write, acquire and compare an
   independent logical readback with the exact selected image, then check boot,
   companion/USB lifecycle and stock coexistence. Exercise restore separately.

These are acceptance requirements, not an implemented NAND command sequence.
CPU probing, a saved official OTA restore image and writer completion records do
not close the live-backup or recovery gates.

## Validation boundary

Sixteen synthetic tests use a fake libusb ABI and no real library or USB devices.
They cover exact control-IN fields, sole-device selection, arbitrary reply bytes,
short/error replies without retries, resource cleanup, interruption, fresh output,
local evidence/session binding, CLI separation and new-firmware profile selection.
They pass on macOS and Linux and run in the existing firmware-free GitHub Actions
suite without proprietary inputs, libusb installation or administrator privileges.

The local libusb header independently confirms the descriptor size/field offsets;
its arm64 library exports the required functions. This checks host ABI assumptions,
not a physical transaction. See validation (snowsky-disc-web `docs/validation.md`) for recorded checks.
