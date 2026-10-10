# Bounded rootfs collector

The collector implements the live transport needed by the independent
[logical readback verifier](logical-readback.md). Both the two-block probe and a
separately authorized **full 96 MiB collection** have completed. All official
rootfs bytes match; the full image differs in its FF tail versus the prepared
restore image's zero padding, so strict full-image equality is rejected.
[Full observation and stock boot confirmation](../observations/rootfs-full-observation.md).
Repeated-read OOB differences fall within internal ECC parity; their cause
remains unresolved. Write and installation acceptance remain open.

## Separate compiled scopes

`firmware/collectors/v<version>.json` binds the existing reviewed chip/kernel/ECC
policy to two non-overlapping uncached RAM buffers and finite session budgets.
The writer profile supplies the first block and full scan ceiling. Firmware
selection is the normal reviewed-profile mechanism; there is no version check
in the C payload or collector algorithm.

| Scope | Physical blocks | Logical data | Maximum batches | USB protocol calls | Transfer-session budget |
| --- | --- | ---: | ---: | ---: | ---: |
| `rootfs-probe` | `[80, 82)` | 256 KiB | 3 | 297 | 120 seconds |
| `rootfs` | `[80, 912)` | 96 MiB | 794 | 42,220 | 3,600 seconds |

These numbers describe the selected profile. The probe has no spare block: if
either marker is bad or ambiguous, it stops without moving to another block.
The full mode scans the writer's 64-block reserve and selects its first 768 good
blocks. Neither mode enters kernel, recovery rootfs, calibration or userdata.
The saved metadata page must still place the complete writer range inside rootfs.
This validates the supplied page, not a fresh on-device table or active-slot check.

The two modes produce different binaries with distinct compiled page bounds.
Preflight rejects substitution of a full-mode build into a probe plan or vice
versa. Existing identity and single-metadata-page builds retain their original
request admission; the old metadata CLI cannot execute this batch ABI.

## Batch ABI and device behavior

The request buffer is 3,088 bytes: four uint32 fields (magic `0x3151424e`, version
1, count, reserved zero) and 64 existing 48-byte page requests. Count must be
1–64. Used requests must be page reads with the same nonzero nonce, consecutive
nonzero sequences without wrap and pages within the compiled interval. Unused
request slots must be zero. The payload validates the entire request before
the first MMIO operation, including a malformed last slot.

The result buffer is 283,408 bytes: four uint32 fields (completion magic
`0x3152424e`, version 1, completed count, result code) followed by 64 existing
4,428-byte results. The payload first clears the output, executes pages in order,
and stops at the first failure. Each page retains the ID/ECC/OTP checks and SFC
cleanup of the physically tested single-page reader. A MIPS memory barrier
precedes publishing the batch magic last. Unused results remain zero.

Only ID, feature/status reads, page-to-cache and cache-read commands exist. No
NAND reset, feature change, unlock, write-enable, program or erase is added.
The command range is enforced on the device as well as by host ordering.

## The read by digest (`rootfs-digest`)

`device/usbboot/digest.c` is built as the `rootfs-digest` payload
(2026-10-05). It answers the full read's batch with 128 bytes a page instead
of 4,428: the full result's counters and status, the first 8 OOB bytes (the
bad-block marker), and the SHA-256 of the page's 2,048 main bytes, from the
payload's own SHA-256 without libc. A batch's result is therefore 8 KiB
instead of 277, and takes 17 calls instead of 53; a whole read is 794
batches, 13,588 calls and 8.9 MB, against the full read's 42,220 calls and
227.5 MB. The full read's payload stays byte for byte `b1743a1d…`.
`collect_rootfs.py --mode rootfs-digest` reads it, and `readback.py verify
--digest` compares every page with the image.

It keeps the reads' batches (owner, 2026-10-05): the progress then counts real
work against the plan, and a stalled call stays a bounded timeout; it never
makes one long digest of the whole image. Sampling blocks was weighed and
refused: a NAND write fails in one block or page, and a squashfs shows that
only when the file is read.

On the player (2026-10-05/06) it agreed page by page with the full read and
the image each time it ran. It never replaced the full read, which would first
have needed an offline audit of its journal, and the installer no longer runs
it (owner, 2026-10-06); since 2026-10-07/08 the identity check and the first
start's check take the place of the full reads.

Each record's `cycles` word was meant to hold the page's CP0 Count ticks.
Three reads on the player gave every page 0 (2026-10-06), although the payload
reads Count before and after each page: Count stands still in the payloads'
context, probably because the ROM or the SPL leaves Cause.DC set (not
settled). A payload that needs time must clear Cause.DC first or use the
SoC's own timer, `core-ost` at `0x12000000` (`0x12100000` per core) in the
stock kernel's tree. The host times a batch by its held request instead
(`batch_ready_ms`, [RAM transport](../usb-boot/ram-transport.md)).

## Host lifecycle and retained evidence

`scripts/deployment/collect_rootfs.py` uses the existing exclusive libusb ROM
connection and exact-transfer rules. It performs one SPL SRAM comparison and
execution, checks DDR diagnostics, then tests all six reserved RAM regions with
two nonce/address-derived pattern passes. The two new regions are request RAM
at `0xa0b00000` (3,088 bytes) and result RAM at `0xa0b10000` (283,408 bytes).
The total tested DRAM is 356,508 bytes. All regions are written before any is
compared in each pass, to detect aliases. Transfers are split at 65,536 bytes.
The batch payload is uploaded and compared once.

For each batch the host saves/fsyncs the exact request, uploads and compares
request and zero result, executes once, waits the existing two-second settle
interval, checks the exact ROM CPU reply and retrieves the full result. Completion,
count, nonce/sequence/page, CRC, padding, ID, feature/status and counters must all
pass. A timeout is uncertain execution, not permission to repeat the batch.

Two explicitly planned marker observations per block have different sequences;
they are not retries. Marker disagreements stop before the next batch. After
the marker phase, the data pass checks the selected blocks' first-page marker
again. Busy, undocumented/uncorrectable ECC or ambiguous marker results never
become guessed bad blocks. The strict ECC limitation for marker reads described
in the verifier still applies; no automatic alternate marker policy is used.

Validated request/result pairs are appended to `records.bin`, in the verifier's
order. `logical-image.bin` contains selected main bytes only. Each completed
batch is flushed/fsynced. A failed or partial batch does not enter the validated
record stream; its raw USB returns remain in `transfers.jsonl` and readback files.
The fresh output directory also retains request/result reports, session UUID,
nonce, dependency hash, plans and the exact per-batch request files.

Each session has finite profile-selected time, protocol-call and batch budgets;
there are no retries, reconnects or resume. USB transfer timeouts are capped by
the remaining session time. As in the prior synchronous transport, filesystem
operations and USB enumeration/open/claim/release do not have hard wall-clock
interrupt guarantees. Full capture can take tens of minutes with the current
settle interval; it is not implicitly included in probe authorization.

Success is `rootfs-probe-collected` or `rootfs-collected`, with capture/image
hashes, bad blocks, logical mapping and ECC histogram. It does not assert image
equality, active slot or installation success. Full-mode records can subsequently
be checked by the independent verifier against the reviewed image and this
session's nonce. Probe records are deliberately incomplete for full-image
verification. `hardware_qualified` and `flash_ready` remain false.

## Offline reproduction and proposed physical probe

```sh
python3 scripts/deployment/build_identity.py --mode rootfs-probe \
  --diskos /path/to/diskos --output build/rootfs-probe-new
python3 scripts/deployment/collect_rootfs.py plan --mode rootfs-probe \
  --build build/rootfs-probe-new --diskos /path/to/diskos \
  --metadata-page /path/to/observed-metadata-main.bin
```

`plan` never loads libusb. Only after separate owner authorization, `acquire`
uses the same arguments plus trusted `--libusb`, a fresh `--output` directory and
the exact `--approved-plan-sha256`. Old CPU/RAM/metadata authorizations do not
cover this new operation. The caller must explicitly connect the intended unit
in mask-ROM; no device-mode change is attempted by the host.

A probe session's audit reconstructs it offline from its saved files alone and
makes no USB calls (save the `plan` output as the plan file first):

```sh
python3 scripts/deployment/audit_usb_probe.py \
  --run work/rootfs-probe-<n> --plan work/rootfs-probe-plan-<n>.json \
  --build build/rootfs-probe-new --diskos /path/to/diskos \
  --output work/rootfs-probe-<n>/offline-review.json
```

It checks the plan's scope against the collector, writer and page profiles
read as files, the payload and the SPL by their digests, every record, the
marker pages and the pages of the first good blocks, the first blocks
(`logical-image.bin`), each batch and the USB journal call by call (the entry's
DDR diagnostic, with the SPL run only when it is not clean, and the held
completion asks). Its report, `saved-probe-trace-matches`, gives the first
blocks' SHA-256: what the installation without a history compares with the
images known (`firmware/images`, 2026-10-08). The boot evidence has its own
audit (`audit_usb_boot.py`, [boot selection](../boot/boot-selection.md)); both share
the reconstruction of records and calls in `usb_trace_audit.py`. Synthetic
sessions of the fake ROM run in GitHub Actions (`test_audit_usb_probe`).

The proposed first step uses `build/rootfs-probe-001/` and
`work/rootfs-probe-plan-001.json`, review hash:
`bb003279bc07b1b6c473800675e0032f4b5badc580ab6ae7f2ff31e54481868f`.
The binary is 6,320 bytes, SHA-256:
`901a799a597da3427ff96cb3915d7aa591bba04a118d4505298c699def34b39a`.
It can read only physical pages `[5120, 5248)`: four marker observations and 128
data observations, with one SPL and three payload executions. Successful output
is 590,832 record bytes plus 262,144 logical bytes, in addition to retained USB
readbacks. It leaves the player in ROM after volatile RAM changes; subsequent
normal stock boot must again be checked manually.

The separate full-mode plan is `work/rootfs-full-plan-001.json`, hash
`bf0d6e2877d36529ef49ecc2f1a0c3040814ce2343c6db64213d72ab25325894`.
It is an offline artifact, not a proposal to run the full collection before
qualifying the probe. The probe subsequently executed as recorded below, followed
by the separately authorized [full observation](../observations/rootfs-full-observation.md).

## Proposed full read after OOB review

The [offline OOB classification](oob-review.md) now supports proposing the
existing full main-data/marker readback scope. On 2026-09-24, offline preflight
rechecked the build, source pins, diskOS inputs and saved partition metadata.
The resulting plan exactly matches `work/rootfs-full-plan-001.json`:
`bf0d6e2877d36529ef49ecc2f1a0c3040814ce2343c6db64213d72ab25325894`.
The 6,320-byte payload remains
`6ef81a3bdd014bac7af20014524f92721e904a1046af06e4bfe8dd4cbfc0d9cd`.

The proposed single session reads paired first-page markers over blocks
`[80, 912)`, then main/OOB pages of the first 768 good blocks: 50,816 records,
794 batches and 96 MiB of logical main data. It performs one SPL execution,
reserved-RAM checks and up to 794 reader executions. It has no NAND program,
erase or feature-write command. A failure stops without replay/resume; another
session requires a new concrete authorization.

The existing two-second batch settles alone total 26 minutes 28 seconds; USB,
RAM checks, NAND reads and durable file writes add time. The transfer-session
budget is 60 minutes, not a completion guarantee or a hard interrupt for blocking
filesystem/enumeration operations. Retain at least 2 GiB free for records,
logical image, raw transfer returns and journal. Local preparation found 53 GiB
free. Maintain stable power and connection throughout the session. After it,
manually return to stock boot and check operation as with the probe.

Successful collection must be followed by the independent full-image verifier
against the reviewed stock restore image. A successful USB session alone does
not satisfy that check. Raw parity qualification, active boot selection and
installation/recovery remain separate. This was the proposal before execution.
The owner subsequently authorized one session; [its full evidence](../observations/rootfs-full-observation.md)
records successful collection and the rejected exact stock-image comparison.
It does not authorize another session or physical write.

## Verification evidence — 2026-09-24

Nine new firmware-free Python tests cover complete ROM bootstrap/collection,
all 53 new batch USB failure boundaries without replay, later-batch partial
results, stale/corrupt/invalid page results, marker changes/disagreement, exhausted
probe capacity, time/call budgets, plan drift before USB loading, profile/RAM
bounds and future firmware audit rebinding. A synthetic full-mode capture with
a skipped block passes the independent logical readback verifier.
They passed on macOS/Python 3.13 and Linux/Python 3.11; the full host suite passed
185 Python tests, eight JavaScript tests and native C assertions. Existing
GitHub Actions discovers the new tests; no hosted execution is claimed.

C/MMIO tests cover 64-page completion, invalid final requests before hardware,
compiled bounds, sequence/nonce/opcode validation, nonzero unused slots, partial
failure with no later page and cleared unused output. Host C tests and
AddressSanitizer/UndefinedBehaviorSanitizer pass. Both MIPS payloads are static
MIPS32/o32 soft-float with no undefined symbols, BSS or GOT; compiler-reported
static frames sum to less than 9 KiB against the existing private 32 KiB stack.
Actual saved metadata/profile/build checks produced both offline plans.
The static NAND-core and batch/MMIO test executables also passed under MIPS/QEMU.
The prior identity and metadata modes rebuilt successfully (5,568 and 5,656
bytes respectively); the new probe and full payloads are each 6,320 bytes.

Logs are ignored under `work/collector-*`; binaries remain under `build/`.
No firmware bytes were added to tests/Git and external repositories are unchanged.
The service/browser is unaffected; this stage's new runtime validation is
synthetic ROM/SFC plus freestanding MIPS execution, not a browser test.

## First authorized attempt — no ROM target

The owner authorized one probe invocation under plan
`bb003279bc07b1b6c473800675e0032f4b5badc580ab6ae7f2ff31e54481868f`.
On 2026-09-24, from 03:56:12.853067 to 03:56:12.869383 UTC, offline preflight
passed but discovery found zero matching ROM devices. It stopped before opening
or claiming a target. No CPU-info, RAM transfer, SPL execution, batch execution
or NAND read occurred. Cleanup reported no errors and no retry was made.

Session: `0eecaf03-82d6-4997-8767-a1c3965c4a3b`.
Private evidence: `work/rootfs-probe-observation-001/`. Result SHA-256:
`4ce3bb6eb1baf924ed4474c6f6501cb64ca458a6ac1374d73d197d8d2025d2dd`.
The journal contains only `usb-discovery-attempt`; `offline-review.json` verifies
the reviewed plan/session binding, zero protocol calls/executions and absence of
capture records. It performs no new USB access.

This does not distinguish an unplugged player from normal stock USB mode or
another enumeration issue. `physical_device_accessed: null` records discovery
without an opened target. Physical probe qualification remains pending. Confirm
the intended player is connected in mask-ROM and request the next invocation
before retrying the same plan into a fresh evidence directory. No executable or
profile was changed for this evidence-only stage; synthetic acceptance remains
as recorded above.

Routine no-target attempts will henceforth remain in ignored runtime logs only,
without per-attempt documentation or commits, as requested by the owner.

## Authorized two-block observation — 2026-09-24

After the owner requested another check under the same probe plan, one invocation
completed from 03:59:04.466104 to 03:59:16.494586 UTC (about 12.03 seconds).
Session: `887eacc4-7356-40f4-a607-f692ffe36027`.
Private evidence: `work/rootfs-probe-observation-002/`. Result SHA-256:
`59da117136225a76b906cbc487e5185ba1cfc519fdd931bdc7632c994311f29e`.

- SPL SRAM comparison, clean stage-9 DDR diagnostics and both pattern passes
  across all 356,508 reserved RAM bytes succeeded.
- One SPL and three batch executions returned to ROM. All five CPU replies
  were exactly `X2000`; all 297 protocol attempts have matching returns.
- The four marker observations agreed on FF for physical blocks 80 and 81.
  Their data-pass first-page markers remained FF. No bad block was skipped.
- All 132 request/result pairs passed sequence/nonce, completion, length and
  CRC checks. ID was `0b 12 00`, A0=`0x38`, B0=`0x10`, C0=`0x00` throughout;
  every ECC nibble was zero. Readiness polls ranged from two to four per page.
- All 262,144 logical main-data bytes match the beginning of the reviewed stock
  restore image exactly. The complete reference restore file was independently
  rechecked against its build-report hash and the pinned official content.
- USB release/cleanup reported no error. No NAND write, automatic retry,
  additional acquisition or reset occurred. The player was left responding in ROM.

An offline review reconstructed every RAM pattern, upload and readback, all
three batch buffers and the record stream, then compared the logical prefix.
It made no USB calls. `offline-review.json` records the checks and limitations.

| Artifact | SHA-256 |
| --- | --- |
| 590,832-byte record stream | `1aee3a94f353b3f35b0a80ebf8165a5d85efe445f53c384d92cc6edb4818bc12` |
| 262,144-byte logical prefix | `dccfa97506e44bc8ed82512efb6bca7ed0b6acbf1393963d89fe244db9501c90` |
| USB journal | `a4510f3386e5ea42862c63bccb62ba6bfdae3e636c9f8204134927db62800fe7` |

### OOB observation and acceptance boundary

An additional comparison of repeated first-page records found equal main bytes
and equal first 64 OOB bytes, but 51 differing bytes in OOB offsets 64–115 for
each marker pair (pages 5120 and 5184). The first observation agrees with the
later data-pass observation, while the second marker observation has another
tail pattern. This was observed in retained bytes, not inferred from CRC/ECC.

The initial offline-review assertion that the entire repeated 128-byte OOB must
match therefore failed. The final review explicitly checks the stable main/marker
scope and records the differing offsets with `full_oob_stability_verified: false`.
It does not discard the differing records, relax the acquisition checks or
attribute a cause without evidence. Spare/ECC visibility or controller behavior
requires further review before treating the full returned OOB as stable raw NAND.

The collector's success status means its specified page/marker contract passed;
it never required all OOB bytes to match. The preserved acquisition report keeps
`image_match_verified: false` because the later independent comparison establishes
only this prefix, not the whole 96 MiB image. Active boot selection, full bad-block
mapping/readback, upper-OOB interpretation and installation/recovery remain open.
Stock boot after this particular probe was subsequently confirmed by the owner
as recorded below; it is distinct from the earlier metadata-diagnostic check.

No production code or profile changed for this evidence stage. The prior
synthetic regression still applies; validation here is the physical run and
independent saved-evidence review. No proprietary captures are committed.

## Owner-confirmed stock boot after the probe

On 2026-09-24 the owner reported that the player restarted and operates in its
normal stock mode after this batch/rootfs probe. This closes the return-to-stock
check for session `887eacc4-7356-40f4-a607-f692ffe36027`.

Evidence is the owner's direct observation; no boot log, automated playback
check or additional device access was performed. It does not resolve the upper
OOB differences or qualify full-image readback, installation or recovery.
Only documentation changed for this confirmation.
