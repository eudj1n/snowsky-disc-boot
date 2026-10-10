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
the separate [retained run evidence](ram-transport.md#successful-authorized-identity-observation).
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
