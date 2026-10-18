# Observed NAND and diskOS writer comparison

The saved identity observation matches the **XTX XT26G02C family**. This is an
offline documentation/source comparison, not page-read or installation acceptance.
The player was not accessed again. The existing SPL, identity payload and firmware
profiles are unchanged.

## Documented chip facts

Source: XTX's **XT26G02C datasheet Rev 1.8, October 25, 2022**, available as a
[manufacturer-authored document on a mirror](https://pdf.elecfans.com/p/10947115.html).
The [manufacturer product page](https://www.xtxtech.com/en/Products/info.aspx?productModel=XT26G02CWSIGA)
also lists this family. We reviewed Rev 1.8; this does not claim review of the
newer revision listed by the manufacturer.

| Fact | Datasheet section |
| --- | --- |
| ID `0b 12`; only two bytes documented | 7.6, Table 6 |
| 2,048 main + 128 spare bytes/page, 64 pages/block, 2,048 blocks | 7.8, 10 |
| Factory marker: first page, column 2,048; non-FF means bad | 10, Table 10 |
| ECC always on; clearing B0 bit 4 cannot establish raw access | 7.5, 11 |
| C0 high nibble: 0 clean, 1–8 corrected, F uncorrectable; 9–E unspecified | 8, Table 8 |
| Spare 0x800–0x83f is protected metadata; 0x840–0x873 ECC parity; 0x874–0x87f unprotected metadata | 11, Table 11 |

The observed third ID byte `00` establishes neither package nor silicon revision.
Identity-only C0=`00` says nothing about errors on pages not yet read.

## Comparison with the pinned writer

The source under review is diskOS `flash/my_write5.c`, SHA-256
`08f31458fd63f6cc20d3ee7a878013da07f50e607f99a7b0f7f523bcfb074ec4`.
Its comments refer to GD5F2GM7. A different vendor alone does not prove the writer
is unusable. These specific comparisons matter:

- Main-page/block geometry and the factory-marker location agree. The writer's
  scan `[80, 912)` fits the documented chip capacity. This does **not** establish
  the active rootfs partition or the stock kernel's bad-block translation.
- `my_write` does not check chip identity before changing NAND configuration.
  A future installer must bind a fresh identity to its reviewed chip/writer pair.
- `oob_read1` reads the first marker twice. The writer attempts ECC disable but
  treats its readback as informational. Its comment about raw marker behavior is
  an experiment on another chip; two identical reads do not prove raw access.
  The XTX marker location agrees, but interpretation under always-on ECC still
  needs to agree with the stock driver's handling.
- `read_page` polls completion through SFC but does not classify ECC status.
  Full-page `memcmp` is useful write verification; it does not report correction
  margins or establish an independent ECC-aware readback path.
- `program_page` issues WREN before PROGRAM LOAD. The chip document describes
  LOAD → WREN → EXECUTE. This ordering difference needs resolution, rather than
  being labelled a proven failure without evidence.

The conclusion is **matching geometry with unresolved operational semantics**.
The next offline work is stock NAND-driver/partition review, then a concrete
decision on reusing or minimally adapting the writer and its readback procedure.
A full raw NAND backup is not silently added as a prerequisite: it is not part
of the diskOS installer, and always-on ECC also limits what “raw” would mean.

**Subsequent kernel review:** the pinned OTA kernel now confirms the chip entry,
first-page marker/skip policy and WREN-before-LOAD order. Its partition data comes
from NAND metadata at `0x5800`, not a fixed OTA table. See
[stock driver findings and saved-page parser](nand-kernel-review.md). The generic
chip-comparison report does not consume this separate firmware-specific review;
its remaining-review list describes the earlier documentation comparison.

## Reproducible offline comparison

`firmware/chips/xt26g02c.json` records reviewed facts and pins the writer audit to
the entire selected writer profile. It is independent of firmware version and
cannot enable physical operations. New firmware still requires its own profile
and compatibility acceptance; changing writer inputs requires another source audit.

```sh
python3 scripts/deployment/chip_review.py --version 2.57 --chip xt26g02c \
  --request work/identity-observation-001/identity-request.bin \
  --result work/identity-observation-001/read-083.bin \
  --diskos /path/to/diskos > work/identity-observation-001/chip-review.json
```

The version and saved paths above reproduce this observation, not an acquisition
command. The tool validates exact ABI framing, nonce/sequence, completion, ID
prefix, byte-sized registers and non-busy status. It hashes the input records,
checks all selected writer source pins and exact binary capacity, and reports
individual comparisons. Exit zero means a report was produced; `flash_ready`
and `page_reads_admitted` remain false even when geometry matches.

It does not authenticate the supplied records, audit their transfer journal,
prove which firmware was running, or establish physical provenance. Those remain
the separate [retained run evidence](../usb-boot/ram-transport.md#successful-authorized-identity-observation).
No USB dependency is imported and no helper is executed.

## Acceptance — 2026-09-24

The actual saved request/result passed this comparison with all five diskOS
source pins intact. Geometry, marker location and capacity checks were true;
identity admission, read-ECC handling and ECC-disable assumptions remain open.

- Raw result SHA-256: `7ee51a155fe28ce32d960fdc582d84213dbb4eaf449b2dca0d620a0d1284a3e4`.
- Chip profile fingerprint: `7c42f3a4eb97dfb3dbb9d716d8364a2ef46baa712ad166e7ade0629311abcfb2`.
- Generated report: ignored `work/identity-observation-001/chip-review.json`.
- Eleven new synthetic tests cover unknown IDs, the undocumented third byte,
  stale/failed/malformed records, changed writer pins, mismatched geometry/markers,
  forbidden admission flags, bounded files and a future-firmware CLI fixture.
  They require no firmware, network, USB library, device or sibling checkout.
- All eleven passed on macOS/Python 3.13 and Linux/Python 3.11. The full host
  suite passed: 145 Python tests, eight JavaScript tests and native C assertions
  (`work/chip-review-conformance.log`). The existing GitHub Actions discovery
  includes the new tests; a hosted run has not been observed.

Physical page reads, stock cold boot, installation and recovery remain open.

## A second chip: HeYangTek HYF2GQ4U — 2026-10-18

The owner's second player, factory-clean and updated to V2.57, answered the
installer's first read with `NAND core failed with code 5` (`NR_ID_UNKNOWN`,
run `install-20261018-174318`): its Read ID is `C9 52 C9`, not the XTX `0B 12`.
FiiO puts at least two SPI NAND chips in the DISC. `C9` is **HeYangTek**, `52`
its **HYF2GQ4U** (2 Gbit); the third byte repeats the first, as the family's
Read ID streams the two bytes. The player was read only (the SPL, the DDR
diagnostic and the RAM round trips passed); nothing was written.

### Documented chip facts

Source: HeYangTek's **HY SPI 2G datasheet v2.7, 2022-04-26** (HYF2GQ4UAACAE
and HYF2GQ4UADCAE, the A-series; SHA-256
`b90a3edbdc57671e30495acb2e3d7a7f1c7efc1c2ecb9d60352c940ce62a8d69`, marked
confidential by its author and not kept here). The family's HY SPI 1G v2.3
(HYF1GQ4UDACAE, `C9 21`) agrees on the register map, the Read ID framing and
the ECC status. The **HY SPI 2G v1.3.8** datasheet that is easy to find
describes the T-series (HYF2GQ4UT*CAE, ID `01 25`, 6-bit ECC, "ECC_EN must
always be set to 1"): another die, not this chip.

| Fact | Datasheet section |
| --- | --- |
| MID `C9`, DID `52`; Read ID with one address byte: `00H` the manufacturer, `01H` the device | Parts No. table; Read ID (9FH), ID Definition Table |
| 2,048 main + 128 spare bytes/page, 64 pages/block, 2,048 blocks, at least 2,008 valid, the first guaranteed valid; 256 MiB | Features; Valid Blocks |
| B0H: OTP_PRT bit 7, OTP_EN bit 6, ECC_EN bit 4, QE bit 0 — the same bits the policy requires (`B0 & 0x50 == 0x10`) | Feature Register Table |
| C0H: ECCS1, ECCS0 at bits 5:4; `00` no bit errors, `01` corrected, `10` not corrected, `11` corrected at the ECC maximum "according to the extended register" | Status Register |
| ECC 14 bits per 512 bytes: four sectors of 512 user + 8 meta + 24 ECC bytes, the first 4 meta bytes of each unprotected; no byte map of the spare area is given, so there is no `firmware/oob/hyf2gq4u.json` and `oob_review.py` refuses this chip | 2KB/Page and ECC |
| ECC can be disabled through ECC_EN (`always_on: false`); the writer's attempt to disable it is informational as before | ECC (Internal) |
| Factory marker: a non-`FF` word in the first spare bytes of a block's first page; the stock driver scans the first spare byte | Invalid block (bad block) |

The U-Boot `heyang` driver agrees (`SPINAND_READID_METHOD_OPCODE_ADDR, 0x52`,
`NAND_MEMORG(1, 2048, 128, 64, 2048, 40, 1, 1, 1)`); its `NAND_ECCREQ(8, 512)`
understates the chip's 14 bits, which changes nothing here.

### The stock kernel's driver

The pinned V2.57 kernel registers manufacturer `0xC9` as `yhy_midc9_nand_init`
(`0x8091efc4`) with three chips, `HYF1GQ4U` (`0x21`), `HYF2GQ4U` (`0x52`) and
`HYF4GQ4U` (`0xd4`); the HYF2GQ4U entry is at `0x808f6d5c`, its parameters at
`0x808f6d98`: 2048 main, 131072 block, 128 spare, 268435456 capacity, the XTX
part's geometry. Its `deal_ecc_status` (`0x804f12f0`) takes bits 5:4 of C0 and
returns `-EBADMSG` for `10` **and** `11`, success for `00` and `01`, so a page
the chip corrected at its maximum is unreadable to stock. The reader admits the
same two values (`ecc_admitted` `0b11`): what stock refuses, we refuse. (The
kallsyms table of this kernel is offset by one entry: a name's address is the
one printed for the entry before it; `kernel_review.py` reads the chip table
by address and is unaffected.)

### What changes

- `firmware/chips/hyf2gq4u.json` records the facts above; its ECC semantics are
  the second reviewed set (`metadata_policy.HYF_ECC`). Fingerprint:
  `firmware/pages/v2.57.json`, `chip_profiles_sha256.hyf2gq4u`.
- `firmware/kernels/v2.57.json` names the chips of the firmware (`chips`, each
  by its entry in the kernel's table); the page policy is built for one of them,
  and every profile that pins the policy pins one fingerprint a chip.
- The installer's entry starts with the identity session (`ram_transport.py
  --mode identity`), which names the player's chip among the reviewed ones; the
  payloads that read pages are compiled for that chip (`--chip`, the release's
  `<chip>/<mode>`). A chip the profiles do not know stops the run before any
  page is read, naming its two ID bytes.
- No player has run the write on this chip yet: the installation review refuses
  the write until the owner's acceptance (`install.py --accept-chip`, the first
  write, recorded afterwards in `firmware/images`, `exercised.hyf2gq4u`).

### Comparison with the writer

diskOS's `my_write5` checks no chip ID and reads no ECC status (the writer
audit, unchanged); its commands, geometry and marker scan are the same for both
chips, and the stock kernel programs both through the same SFC path. The
comparison's open items are the XTX ones.

The acceptance ran the same day: [the second player's acceptance](../observations/second-player-acceptance.md)
(the entry's reads, the write, the first start's check matching the written
image), after which `exercised.hyf2gq4u` names what that write ran; the exact
datasheet was reviewed against the profile that evening.
