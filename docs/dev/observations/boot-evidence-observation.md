# Authorized bootloader and OTA observation — 2026-09-24

The separately authorized read completed on the owner's player. It retained all
2 MiB of the `uboot` partition and paired first-page reads from the eight `ota`
blocks. All records passed integrity/ECC checks. No NAND program/erase, writer
execution, selector update or captured-bootloader execution occurred.

## Scope and result

Before USB discovery, the regenerated plan matched the approved plan, build and
source pins passed, and the libusb dependency matched the reviewed SHA-256
`d4d61d9f4e5291e64c09783cb61bd1b4c67e202b514f40989504a5852f7560b9`.
One matching ROM device was opened; there were no retries or reconnects.

- Session: `fbd0b1bd-d7f9-428a-bf23-8a9a480b9479`.
- Nonce: `0f6d61ccbf8749aa8ffe4214f13a6b9a`.
- Plan SHA-256: `80abd7c5a1c43851abe37478e261463b8e4748cd773261bcb0af26699a9bca31`.
- UTC interval: `2026-09-24T06:36:56.380455` to `06:37:53.286146`.
- Duration: 56.91 seconds, below the 180-second limit.
- Result: `boot-evidence-collected`, with no cleanup errors.
- 1,151 USB calls, below the 1,167-call limit; 21 exact `X2000` replies.
- One SPL execution at `0xb2401800`, followed by 19 read-payload executions at
  `0xa0c00000`; the OTA payload replaced the boot payload after boot collection.
- 1,073 records: 32 boot markers, one fresh metadata page, 1,024 boot data pages
  and 16 OTA marker/selector pages.

SPL comparison, final DDR stage 9 and both RAM pattern passes succeeded.
Every page reported NAND ID `0b 12 00`, A0=`0x38`, B0=`0x10`, C0=`0x00`:
no corrected or uncorrectable ECC status was reported. There were two readiness
polls/ten transfers on 1,023 records and four polls/twelve transfers on 50.
The fresh metadata page matched the earlier reviewed partition page exactly.

Boot blocks 0–15 and OTA blocks 1368–1375 all had paired `FF` first-page markers.
Paired main bytes agreed; boot data-pass first pages agreed with their marker
scan. The complete boot image therefore maps directly to physical bytes
`[0, 2097152)` on this observation. It remains an RE input, not an admitted
restoration artifact. Only the first pages of OTA blocks were read; no claim is
made about their remaining pages or the rest of the NAND chip.

## Observed selector and static triage

OTA block 1368 begins with **`ota:backup`**, space-padded to 256 bytes; the
remaining main bytes in that page are `FF`. The main bytes of the first pages
of blocks 1369–1375 are entirely `FF`. Both reads of each page agree.

The existing strict classifier correctly leaves all eight values unknown: its
reviewed tokens are `ota:kernel` and `ota:kernel2`. The observed backup token is
not silently mapped to either rootfs and does not open physical write admission.
The main-rootfs shell helpers' fallback for strings without `ota:kernel2` is
insufficient to establish how the bootloader actually handles this state.

A byte-level scan of the retained boot image finds an SPL build string at file
offset `0x3809` identifying U-Boot SPL 2013.07, built Jan 09 2026 at 15:02:40.
It also finds `ota:kernel2` at `0x3934` and root argument strings referring to
`/dev/mtdblock_bbt_ro2` and `/dev/mtdblock_bbt_ro4`. These are file offsets and
static strings, not resolved execution addresses or evidence of a selected
branch. Selector comparison, partition loading, bad-block behavior and recovery
or hardware overrides still need actual Ghidra/assembly review.

## Independent offline validation

A separate reconstruction checked every journal call against an independently
generated expected sequence, including all outgoing RAM digests and all 254
saved incoming transfers (11,561,989 bytes). It regenerated nonce-derived RAM
patterns, both compared payloads, every batch request and zeroed result buffer,
and the expected execution addresses. No extra calls were present.

The review independently checked request identity/nonce/sequence, result framing,
CRC, padding, chip configuration/ECC/counters, batch completion, bounds, metadata,
paired markers/main bytes, every boot-image byte and each saved OTA page.
It reconstructed the 2 MiB image and all batch results from `records.bin` and
matched those bytes against the original USB reads. This validation opened no
USB connection and did not execute acquisition code.

Runtime evidence is ignored under `work/boot-evidence-observation-001/`; the
independent audit is `work/audit-boot-evidence-001.py`. No capture or proprietary
binary is committed.

| Artifact | SHA-256 |
| --- | --- |
| `result.json` | `d2364615eb6aeeab22bad841ebde70341a92ed0e3c355c5be33f95fb91034b0f` |
| `transfers.jsonl` | `a419ae6bdcd853df2a4211463b0d1e9d53f0cdc6430bc2fa7268a53835f468b8` |
| `records.bin` | `d4190baf522dced13907b6f4a57ad7c9b5ff66a6e9863889cbbb83fca409c1da` |
| `uboot-good-blocks.bin` | `389703052c38dc279187a98e7a61094fd7f5c1d3103e8b929f2b11c69a68ae5a` |
| `metadata-main.bin` | `2e4ef4fa5c53dcaa725a1af3530a6af32c80078c091c9cb77020c6e12ed486b2` |
| `ota-block-1368-main.bin` | `abc96f159e7a8f34ce7b232310023150cd7c20e3f21b1eadc520b7fdf8aff540` |
| `offline-review.json` | `422b6620af534c3711efab1837f6db66e526f542437a397ec713eb3e18cd8293` |
| Audit script | `5b2fad6bb9c08d32c3bdc7d4c3db5e5579356d15e6e1749aa1cc5bfe1e2b60c1` |

The unchanged collector previously passed 227 Python tests, eight JavaScript
tests and native C assertions, including ten new boot-evidence cases and Linux,
MIPS/QEMU and sanitizer checks described in [preparation](../boot/boot-selection.md).
This stage adds physical and saved-evidence acceptance; no transport behavior,
profile admission or runtime source changed.

## Remaining acceptance

The host closed the USB session normally. On 2026-09-24 the owner confirmed
normal stock boot and operation after leaving USB Boot and rebooting following
this acquisition. This is direct owner observation, separate from the USB trace.
The saved transport flags `active_boot_verified` and `flash_ready` remain false;
the original evidence is not rewritten by the manual confirmation.

The subsequent [SPL review](../nand/bootloader-review.md) resolves the captured
`ota:backup` to the primary pair without rewriting this acquisition's original
unknown-selector classification. Live command-line/root-mount evidence remains
separate. Installation, writer/restore execution,
exact post-write readback and native companion operation on hardware remain open.
Follow the [installation and recovery procedure](../installer/installation-procedure.md);
physical write admission remains closed.
