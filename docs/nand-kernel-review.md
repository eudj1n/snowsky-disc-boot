# Stock kernel NAND and partition review

The selected OTA kernel explicitly supports the observed XTX `0b 12` chip.
Its geometry, first-page spare marker and programming command order agree with
the pinned diskOS writer. The actual partition table is stored on NAND, not in
this OTA kernel. Physical partition contents and boot selection remain unobserved.

Only local files were accessed. No player enumeration, NAND read, feature write
or flashing occurred. External repositories were unchanged.

## Inputs and reproducible checks

Source: `main_os/ota_v257` in the owner's unpacked official update. All five
encrypted xImage chunks matched `manifest.sha256`; concatenated plaintext matched
the full digest in the first chunk's filename. This checks integrity, not the
vendor signature. Decryption follows the reference repository's
`firmware/tools/firmware_inventory.py` OpenSSL recipe; firmware stays uncommitted.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Decrypted xImage | 4,423,744 | `1a9484efcb64c391b23910306e867cb628af07e059d0d1ccc13e85269e030ed2` |
| Expanded kernel | 10,170,640 | `89dda840fd9cfed6d9b905ff0718f5aa35a5cb4ce9736e904cccfb4000c6380f` |
| Embedded DTB | 30,331 | `e7a7bbe3c43aa2c488831bab796904b97de63f710e27713274d719feb8fc0e6c` |

`firmware/kernels/v2.57.json` binds the kernel to the selected firmware's rootfs
fingerprint and writer profile. Future releases require their own reviewed
kernel profile; the tool contains no firmware-version predicate.

```sh
python3 scripts/deployment/kernel_review.py --version 2.57 \
  --ximage work/nand-re/xImage > work/nand-re/kernel-review.json
```

The checker validates the exact xImage hash, legacy uImage header/data CRCs and
Linux/MIPS wrapper type. The wrapper contains gzip at file offset `0x4260` despite
its outer compression field being zero. Bounded decompression must produce the
exact reviewed kernel. The tool checks the chip table's ID/geometry and the DTB's
partition-source properties. It never executes kernel code.

Actual-input checks passed. The ignored report is `work/nand-re/kernel-review.json`;
kernel profile fingerprint:
`121a5a4fcdb9b1e7b5200992b7922f1a86f1dd45f5fa006d5306f3f1e14932d0`.

## RE method and addresses

Import the expanded image as raw MIPS little-endian 32-bit, base `0x80010000`, in
a **new local** Ghidra project. Reuse the emulator repository's
`research/ghidra/DecAt.java`. Projects, symbols and decompiler output stay ignored.
Ghidra 12.1.3/Java 21 and independent MIPS objdump were used.

There are 53,223 kallsyms entries: address array at file `0x7009a0`, count at
`0x734940`, compressed names at `0x734950`, markers at `0x7ce7e0`, token table at
`0x7ceb20`, token index at `0x7ceeb0`. These offsets apply only to the pinned
kernel. Symbols establish function entries; assembly confirms arguments omitted
by decompilation, especially MIPS o32 stack arguments. Decompiler tail-call
merging must not be mistaken for a single function's body.

| Function/data | Address | Purpose |
| --- | --- | --- |
| `xtx_mid0b_nand_init` | `0x8091ed94` | Manufacturer 0x0b registration and callbacks |
| Chip entry / parameters | `0x808f6bc4` / `0x808f6c3c` | ID 0x12; 2048 main, 131072 block, 128 spare, 268435456 capacity |
| `xtx_mid0b_get_cdt_params` | `0x804f0df8` | Commands for IDs 0x11/0x12 |
| XTX `deal_ecc_status` | `0x804f0d70` | C0 high-nibble error handling |
| `nand_common_get_feature` | `0x804f1bf0` | C0 polling and chip ECC dispatch |
| `ingenic_sfcnand_read_oob` | `0x804eed88` | Spare read through ordinary read path |
| `nand_default_bbt` / `create_bbt` | `0x803cabec` / `0x803c9188` | RAM bad-block map |
| `mtdblock_bbt_ro` read callback | `0x803c2598` | Partition-relative good-block mapping |
| `nand_create_cdt_table` | `0x804ef1a8` | Linked command descriptors |
| `ingenic_sfc_nand_write` | `0x804ee214` | Program chain and status check |
| `sfc_res_init` | `0x804ed9c8` | DT properties |
| `ingenic_sfc_nand_probe` | `0x804efc44` | Default metadata offset and partition registration |
| `flash_part_from_chip` | `0x804eead0` | Serialized partition parser |
| Embedded board DTB | `0x809b5600` | SFC node and metadata selection |

## Resolved behavior

**ECC:** the 0x0b/0x12 callback rejects high nibble `0xf` with target `-EBADMSG`
(`-77` on this MIPS ABI). It returns success otherwise, without corrected-bit
counts or rejection of the datasheet's unspecified 9–E range. A future reader
should retain status and reject undocumented values. The diskOS writer currently
does not perform this per-read ECC check.

**Markers:** the probe supplies zero-initialized chip options, first-spare-byte
position zero and a one-byte marker. `nand_default_bbt`/`create_bbt` scan the first
page of each block through the ordinary spare-read path, without an ECC-disable
prerequisite. The scan tolerates ECC warning/uncorrectable return codes for its
marker comparison but stops for other negative I/O errors. Non-FF markers mean
bad blocks. `mtdblock_bbt_ro` compacts the good physical blocks within each
partition and translates logical reads through that list. This agrees with
diskOS's marker location and sequential skip rule. It proves neither raw access
nor the player's actual bad-block map.

**Programming:** the single-lane chain starts at descriptor 11:
WREN → LOAD → EXECUTE → ready poll. Quad mode starts at 15 with the same order.
This matches diskOS's WREN-before-LOAD sequence, resolving the earlier question
about its agreement with the stock implementation. It is code evidence, not
physical program/erase acceptance.

## Layout is supplied by NAND

The enabled DT node `/ahb2/sfc@0x13440000` has `ingenic,use_ofpart_info = 0`
and `ingenic,spiflash_param_offset = 0`. `sfc_res_init` reads these properties;
the NAND probe substitutes `0x5800` for a nonpositive offset and selects
`flash_part_from_chip`.

The serialized header is eight little-endian bytes: `nand` magic `0x646e616e`
and a uint32 count. Each following 48-byte record contains:

| Offset | Field |
| --- | --- |
| 0..31 | NUL-terminated partition name |
| 32 | uint32 size in bytes |
| 36 | uint32 physical offset |
| 40 | uint32 mask flags |
| 44 | uint32 manager mode |

Assembly confirms an initial **12-byte** native-structure read, followed by
records read from `metadata_offset + 8`. The third native word is replaced by
an allocated pointer; this is not a serialized 12-byte header. The external
U-Boot `arch/mips/include/asm/arch-x2000/spinand.h` confirms the field names.
The kernel copies size/offset/mask flags into MTD partition structures.

`0x5800` is main-data page **11**, column zero. The historical eight-partition
layout would fit in 392 bytes, but is **only a synthetic fixture** here. It does
not establish the physical table or currently selected boot rootfs.

## Saved-page parser and next step

The checker optionally accepts one saved **main-data-only** page:

```sh
python3 scripts/deployment/kernel_review.py --version 2.57 \
  --ximage /path/to/reviewed/xImage --metadata-page /path/to/saved-main-page.bin
```

It rejects malformed names/counts, duplicate names, overlaps, unaligned or
out-of-chip ranges, unresolved size sentinels and unknown manager modes. The
writer scan, including its bad-block reserve, must start at and remain inside
the target rootfs. UBI-managed or flag-masked targets are refused. No additional
pages are fetched. A bare page cannot establish its origin, ECC status, active
boot slot or bad-block map; `flash_ready` remains false.

Next: implement and synthetically validate a bounded ECC-aware page-read mode,
then prepare a separately authorized metadata-page observation. This is a small
read-only step, not an implicit full-chip backup. Actual metadata, boot-time
partition overrides, rootfs bad-block mapping and readback/recovery must be
resolved before proposing a write. Stock cold boot after RAM diagnostics also
remains unobserved.

## Tests

Twelve new tests passed on macOS/Python 3.13 and Linux/Python 3.11. Synthetic
fixtures cover uImage CRC/decompression bounds, malformed FDT, geometry/source
changes, future firmware selection, invalid partition records, overlaps, writer
reserve overruns and unsupported target modes. No proprietary inputs, sibling
checkout, network, USB library or player are required.

The full host suite passed: 157 Python tests, eight JavaScript tests and native
C assertions (`work/nand-re/conformance.log`). Existing GitHub Actions discovery
includes the new tests; no hosted run is claimed.
