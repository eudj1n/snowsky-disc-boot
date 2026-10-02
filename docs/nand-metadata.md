# Bounded metadata-page experiment

This stage implements and tests one ECC-aware NAND page read. One separately
authorized physical observation now passed on the owner's unit; see the evidence
below. General acquisition and installation remain unqualified.
The purpose is to inspect the actual partition table selected by the reviewed
stock kernel before deciding the installation/readback procedure. It is not a
full backup, block scan, active-slot detector or flash installer.

## Profile and payload boundary

`firmware/pages/v<version>.json` selects a single physical page and fingerprints
the reader, kernel and chip audits. The loader also validates the kernel's writer
binding. The current reviewed metadata offset is `0x5800`, physical page 11 at
column zero, with 2,048 main bytes and 128 spare/OOB bytes. There is no arbitrary
page/column CLI argument. A future firmware can select another reviewed location
and policy through profiles; no release-number condition is compiled into C.

The chip/kernel profiles remain unqualified (`page_reads_admitted: false`): their
offline reviews do not authorize hardware. The separate experiment policy admits
only preparation of this bounded operation, with `physical_qualified: false`.
General page reading, NAND writes and hardware qualification remain closed.

Build mode `metadata` embeds the selected page and chip policy. Its entry rejects
identity requests and other pages before MMIO. The default `identity` mode still
rejects page requests. Host preflight rejects mismatched build purposes, policies,
source hashes, artifacts and plan hashes before loading libusb.

For the XTX policy, the payload:

1. Checks ready status and reads ID twice. All three returned bytes must be
   stable; the documented two-byte manufacturer/device prefix must be `0b 12`.
2. Reads A0/B0, requires ECC enabled and OTP access disabled (`B0 & 0x50 == 0x10`).
   It does not reset, unlock or change feature/protection registers.
3. Issues one page-to-cache command `0x13` for the compiled page and polls C0
   within the existing finite readiness budget.
4. Accepts C0's ECC nibble 0 through 8. Values 9 through E are rejected as
   unreviewed/reserved; F is uncorrectable. This is deliberately stricter than
   the stock callback, which only rejects F.
5. Issues one `0x0b` cache read at column zero with eight dummy clocks. The SFC
   drains the exact 2,176 bytes in 32-word FIFO bursts under one poll budget.
   Early END, FIFO error and stalled progress fail without another command.
6. Rechecks unchanged C0/B0, computes the data CRC32, cleans up the controller
   resources and publishes the nonce/sequence-bound result last.

Only `0x9f`, `0x0f`, `0x13` and `0x0b` are admitted. There is no DMA, chained
command, NAND program/erase, write-enable, feature write or automatic retry.
The returned bytes are read with internal ECC enabled; OOB must not be described
as an ECC-disabled raw backup or as a complete bad-block map.

## Host observation and evidence

`ram_transport.py --mode metadata` repeats the profiled SPL/SRAM comparison,
single SPL execution, DDR diagnostic and two pattern passes over the reserved
70,012 RAM bytes. It uploads/compares the page payload, fresh request and zeroed
result, executes the payload once, checks the ROM CPU reply and validates the
full 4,428-byte result. ID, ECC/configuration, counters, CRC and request binding
must all agree. A successful immediate-ready fixture uses 10 NAND transactions
(including two readiness polls) and 83 USB protocol calls.

Evidence adds `metadata-request.bin`, `page_observation`, `metadata-main.bin`,
`metadata-oob.bin` and a parsed `metadata_layout` to the existing durable journal.
If page validation succeeds but partition parsing fails, the main/OOB files are
retained and the run fails. No second page is fetched to repair a missing or
cross-page table. A valid table can establish observed partition extents and
writer-range containment; it cannot establish the active boot slot, rootfs
contents, sequential good-block mapping or readiness to write. Parser provenance
and flash qualification fields remain false; the enclosing physical journal is
the separate source of acquisition provenance.

As with the prior RAM diagnostics, SPL changes volatile clocks/GPIO/DDR/watchdog
and RAM contents. The operation returns to ROM; it does not automatically boot
stock firmware. Return to stock boot requires a manual power cycle; the owner's
subsequent confirmation is recorded below.
Execution timeout means uncertain completion and stops without replay.

## Reproduction without a device

```sh
python3 scripts/deployment/build_identity.py --mode metadata \
  --diskos /path/to/diskos --output build/metadata-new
python3 scripts/deployment/ram_transport.py plan --mode metadata \
  --build build/metadata-new --diskos /path/to/diskos
bash scripts/test.sh
bash scripts/build.sh reader
```

Firmware selection uses the normal explicit `--version`, environment or active
profile mechanism. Build/plan commands never load libusb. Build into a fresh
directory after source changes; old builds and old authorizations do not cover
the new payload. Physical acquisition uses the existing `acquire` command with
mode `metadata`, that build, a fresh evidence directory, trusted libusb path and
the exact reviewed `--approved-plan-sha256`, only after owner authorization.

## Offline evidence — 2026-09-24

- The full host regression passed: 165 Python tests, 8 JavaScript tests and C
  assertions. The final focused transport rerun passed all 27 tests, including
  the subsequently added build-purpose/policy guard. Linux Python 3.11 passed
  those 27 tests plus all 4 metadata-policy tests. Both static MIPS reader/MMIO
  test binaries passed under QEMU user emulation. No physical NAND behavior is
  inferred from those synthetic runs; hosted GitHub Actions execution is pending.
- Host C tests and AddressSanitizer/UndefinedBehaviorSanitizer pass. MMIO tests
  cover all 16 ECC values, exact FIFO content, fault injection at every NAND
  transaction, cache stall/early END/FIFO failure, page/opcode/length admission,
  mismatched ID, ECC-disabled/OTP mode and cleanup before result publication.
- Firmware-free Python tests cover policy/audit changes, future firmware/location
  selection, build-mode separation, stale policy, malformed page/ECC/CRC/OTP
  results and failure at every USB protocol boundary with no replay. These run
  through the existing GitHub Actions discovery without firmware or siblings.
- The freestanding MIPS32/o32 soft-float metadata binary is 5,656 bytes, with one
  initialized load segment, no undefined symbols, BSS or GOT. Compiler-reported
  static frames sum to less than 6 KiB, within the private 32 KiB stack. The
  identity regression build is 5,568 bytes; both pass offline transport preflight.

Ignored artifacts are under `build/metadata-001/`,
`build/identity-metadata-regression/` and `work/metadata-*.log`.
Payload SHA-256:
`086ceed72594fc2d21129808f2002b1e0dd8de959522e3470e334345732ea63e`.
The proposed plan `work/metadata-plan-001.json` has review hash
`fadd03b1f5862a19a2ab3cf97d1c05d08eff6b80fee925755156a834f7a1f812`.
It selects page 11/2,176 bytes, one SPL and one payload execution, the existing
60-second transfer-session budget, zero retries and no NAND writes. The subsequent
separately authorized execution is recorded below; original offline artifacts
remain unchanged.

## Successful authorized physical observation

The owner authorized one execution of the exact plan above. It completed on
2026-09-24 from 02:34:46.546201 to 02:34:50.909001 UTC (about 4.36 seconds).
Session: `1a074b40-5ce8-4980-b9c9-649124a312a7`.
Private evidence: `work/metadata-observation-001/`. Result SHA-256:
`c5d3cbfe98d953de24814505e67656e6bf6da632048e4b9fdfca455d9a977a72`.

SPL SRAM comparison, clean stage-9 DDR diagnostics, both reserved-RAM pattern
passes and all payload/request/result upload comparisons succeeded. There was
one SPL execution and one metadata payload execution. The ROM returned the exact
`X2000` CPU signature after each. The final nonce/sequence/page-bound result
reported `NR_OK`, ID `0b 12 00`, A0=`0x38`, B0=`0x10`, C0=`0x00`, four readiness
polls and twelve NAND transactions. ECC status was zero (no errors reported).
All 2,176 bytes passed CRC and length checks; controller cleanup and USB release
reported no error. No NAND write, automatic retry or additional page read occurred.

The observed metadata contains eight non-overlapping partitions:

| Partition | Offset (MiB) | Size (MiB) | Manager mode |
| --- | ---: | ---: | ---: |
| uboot | 0 | 2 | 0 |
| kernel | 2 | 8 | 0 |
| rootfs | 10 | 128 | 0 |
| kernel2 | 138 | 8 | 0 |
| rootfs2 | 146 | 25 | 0 |
| ota | 171 | 1 | 0 |
| mac | 172 | 1 | 0 |
| userdata | 173 | 83 | 1 |

All recorded mask flags are zero. The pinned writer's 96 MiB logical image plus
8 MiB bad-block reserve spans `[10, 114)` MiB, inside the observed rootfs extent
`[10, 138)` MiB. This establishes range containment, not a sufficient count of
good blocks, matching installed contents or proof that this is the active slot.
The historical fixture's extents agree, but its synthetic userdata manager value
was 0; the physical record is 1. The captured page remains ignored, not a test
fixture or committed firmware artifact.

An independent offline review of the saved evidence verified all 83 paired USB
protocol attempts/returns, three exact CPU replies, both execution targets,
outgoing hashes, all retained readback bytes, reconstructed RAM patterns,
request nonce, completion/header/CRC/padding and parsed partition extents.
It did not open USB. Its report is `offline-review.json` in the evidence directory.

| Saved artifact | SHA-256 |
| --- | --- |
| Final 4,428-byte result | `215272a3dd9e6e58aee01fbcbdf2c2a7d5a95da63fb050bc0ecc058f3f0fe7e2` |
| Main 2,048 bytes | `2e4ef4fa5c53dcaa725a1af3530a6af32c80078c091c9cb77020c6e12ed486b2` |
| OOB 128 bytes | `2462de309abdd620a593bcd865eb17b610c4bf43a104803d3a0b1a6924ebe444` |
| Transfer journal | `f86e39a94d1660816af383c8eff2e1afdf5f668f7a20a32c78edd6eb50491faf` |

The acquisition left the player responding in ROM after volatile RAM diagnostics.
The owner's subsequent stock-boot confirmation is recorded below. Active boot selection, rootfs content
and bad-block mapping, installation/readback and recovery remain outstanding;
`hardware_qualified` and `flash_ready` deliberately remain false. No production
code or profile admission was changed for this evidence-only stage, so the prior
synthetic regression remains applicable; the new validation is the actual run
and independent saved-evidence review.

## Owner-confirmed return to stock boot

On 2026-09-24, after being asked to disconnect USB and power the player off/on
normally, the owner reported that the stock system loaded normally. This closes
the narrow return-to-stock-boot check following the RAM/identity/metadata
diagnostics on this unit.

The evidence is the owner's direct observation, not a captured boot log or a new
USB session. No further device access or firmware write was performed to record
it. It does not verify the active rootfs slot, playback or USB coexistence, nor
installation/recovery or cold boot with the companion installed. Those gates
remain open; `hardware_qualified` and `flash_ready` are unchanged. This stage
updates documentation only and does not change executable behavior.

See [kernel review](nand-kernel-review.md), [RAM transport](ram-transport.md),
[read core](nand-reader-core.md) and project plan (snowsky-disc-web `docs/plan.md`).
