# Captured SPL boot selection — 2026-09-24

The captured stock SPL selects **`kernel` with primary `rootfs`** for the saved
`ota:backup` state. There is no special backup/recovery branch for that token:
only the eleven-byte prefix `ota:kernel2` selects `kernel2` and secondary rootfs.
This resolves the static boot-target question for the reviewed bytes and saved
observation. It does not claim a live `/proc/cmdline` or mount observation, a
successful installation, or permission/readiness to write NAND.

## Inputs and method

The [authorized capture](boot-evidence-observation.md) supplies the complete
2,097,152-byte boot image, metadata and all eight OTA first pages, with an
independently reconstructed USB trace. The owner confirmed normal stock boot
and operation afterward. All boot blocks 0–15 have good first-page markers;
blocks 1–15 contain only `FF` main bytes. This captured range contains one SPL
image at file offset `0x800`, not a second full U-Boot command environment.

The complete boot hash is
`389703052c38dc279187a98e7a61094fd7f5c1d3103e8b929f2b11c69a68ae5a`.
A new local Ghidra 12.1.3 project used the first 32 KiB as raw MIPS little-endian,
32-bit input at base `0xb2401000`, with entry `0xb2401800`. Absolute call/data
references and the startup path agree with that base. Startup subsequently
uses a cached alias for board initialization; it does not relocate the image.
The prefix extraction and project remain ignored under `work/boot-re/`.

The external emulator's `DecAt.java` and `RefsTo.java` were reused read-only.
Independent objdump from the pinned MIPS toolchain confirmed argument registers,
branch delay slots, prefix-comparison length, NAND read size and root pointers.
Decompiler artifacts were checked against assembly: an apparent fallthrough
following `board_init` is the adjacent function, and the missing-kernel error
path ends in a non-returning loop rather than retrying another partition.

## Recovered normal NAND boot path

| Function/data | Address | Observed behavior |
| --- | --- | --- |
| SPL entry | `0xb2401800` | CP0 setup and early board/DDR initialization |
| Board initialization | `0xb2403120` | Unconditional call to the NAND selector; Linux dispatch follows image OS type |
| Select boot | `0xb240392c` | Find OTA, read 128 bytes, compare prefix, load selected kernel, return its root arguments |
| Read NAND | `0xb24034e4` | Page/cache reads, readiness/ECC handling and first-page bad-block skipping |
| Load partition table | `0xb2403790` | Read 1,024 bytes from byte offset `0x5800`; entries start after its 8-byte header |
| Find partition | `0xb24037d0` | First matching name prefix, 48-byte entries, byte offset at entry `+0x24` |
| Load kernel | `0xb2403870` | Read 64-byte header, decode load/entry/size, then read selected image |
| Decode image header | `0xb2403054` | uImage magic and big-endian fields; this is not a CRC verifier |
| Prefix comparison / strlen | `0xb2404068` / `0xb24040a8` | Bounded comparison against eleven-byte `ota:kernel2` |
| Linux handoff | `0xb2402fec` | Call image entry with argc 2 and argv containing the selected command line |
| Primary arguments | `0xb240473c` | `root=/dev/mtdblock_bbt_ro2 rootfstype=squashfs ro` |
| Secondary arguments | `0xb2404874` | `root=/dev/mtdblock_bbt_ro4 rootfstype=squashfs ro` |

The selector looks up `ota`, reads 128 bytes at that partition's starting byte
offset, and compares the first eleven bytes. On equality it looks up `kernel2`;
on inequality or a missing OTA entry it looks up `kernel`. If the selected
kernel entry is missing it prints an error and hangs. A failed image load does
not have a reviewed automatic fallback to the other pair.

The prefix lookup matters: a misleading earlier name could shadow `kernel`,
`rootfs` or `ota`. The saved table has the exact names in the expected order.
The new checker rejects such shadowing instead of relying only on the presence
of an exact name later in the table. Root block-device indexes are checked
against that same table, not inferred from a familiar device name.

The reviewed selector and board path contain no GPIO/key-based override of
primary versus secondary. The optional board hooks are empty/pass-through in
this image; the memory-argument helper replaces the reserved memory substring,
leaving the root arguments intact. USB Boot selection by mask ROM is outside
this normal NAND path. This review does not qualify every reset/wake/ROM mode.

## NAND mapping and observed `ota:backup`

The selected chip-table entry is at `0xb240509c`: manufacturer `0x0b`, device
`0x12`, 2,048-byte pages. The reader derives 64 pages per block and tests the
first spare byte of each block's first page. A non-`FF` marker skips one physical
block without consuming output bytes. The same rule applies to OTA and kernel
reads. Uncorrectable ECC status for this entry is high nibble `0xf`; the routine
retries and eventually skips a block. Busy polling/skip traversal is not bounded
by the partition extent in this stock implementation. Our offline acceptance
therefore requires trusted records and a good selectable page inside the
reviewed OTA range; it does not treat an exhausted/unknown range as primary.

For the retained observation, block 1368 is the first OTA block and is good,
with zero reported ECC errors. Its paired main bytes agree and begin with
`ota:backup`, padded with spaces to 256 bytes. It differs from `ota:kernel2`, so
SPL selects `kernel` at byte offset `0x200000` and the primary root arguments.
The observed table's partition index 2 is `rootfs`, starting at `0xa00000`.
The later seven OTA first pages are `FF`; SPL does not use them while this first
block is readable and good. The result agrees with the owner's normal stock UI
observation and the earlier byte-exact match of the official primary-rootfs data.

The stock prefix comparison would also default on malformed input and would
accept extensions beginning with `ota:kernel2`. That permissiveness is not our
acceptance policy. The offline review accepts only profile-reviewed complete
space-padded tokens: `ota:kernel`, `ota:kernel2`, and now `ota:backup`. Unknown,
erased, zero-filled, NUL-padded or extended strings still fail acceptance.
The acquisition tool's original strict classification/report stays unchanged.

## Kernel-side cross-check and limits

The previously fingerprinted official primary OTA kernel was also inspected.
Its `prom_init` at `0x809036d8` copies argv[1..argc-1] into `arcs_cmdline`.
`setup_arch` at `0x80904bb0` uses that value when `boot_command_line` is empty.
Both buffers are initially zero in the pinned image. The chosen-node callback
at `0x8091a2d8` can supply DT bootargs, but the selected embedded board DTB has no
chosen bootargs; the argc=2 path selects that embedded DTB. This supports the
SPL-to-root argument path for the reviewed official image. The existing
[kernel review](nand-kernel-review.md) establishes partition registration and
`mtdblock_bbt_ro` good-block mapping.

The physical kernel partition was not read by the boot/OTA experiment. This
kernel cross-check is explicitly against the official OTA input, and does not
prove byte equality of the installed kernel or capture a running mount table.
Keep `active_boot_verified=false`; obtain the live command line/root mount during
the separately authorized installed-companion acceptance. No captured SPL,
firmware helper or writer was executed during this offline stage.

## Reproducible offline assessment

`firmware/bootloaders/v<version>.json` binds the full captured boot hash/size,
firmware and acquisition-policy fingerprints, reviewed prefix mechanism, accepted
tokens, argument offsets and function addresses. The current profile is evidence
for this captured image/unit; it is not a promise that every player running the
same rootfs version has the same bootloader. A different image, policy or release
requires renewed review. No version-specific conditional is embedded in code.

```sh
python3 scripts/deployment/boot_review.py \
  --records work/boot-evidence-observation-001/records.bin \
  --nonce-hex 0f6d61ccbf8749aa8ffe4214f13a6b9a
```

The checker revalidates the ordered nonce-bound records, CRC/ECC/ID/configuration,
paired markers/main bytes, metadata stability and complete boot image. It checks
partition bounds/names and root indexes, then chooses the first good OTA block
and interprets only a reviewed exact token. It requires all boot blocks to be
good to retain the reviewed zero-based SPL map; a different map needs review.
It reads ordinary bounded local files only, rejects symlinks and extra/missing
records, and loads no USB library. It does not authenticate the capture's origin
or freshness; the independent physical-session audit supplies separate evidence.

Actual-input result: `offline-boot-selection-reviewed`, `static_selection_reviewed=true`,
`selected_kernel=kernel`, `selected_rootfs=rootfs`, selector block 1368/page 87552.
All 1,073 records passed again. Physical access/provenance, live active boot,
write admission and flash readiness flags remain false.

| Evidence | SHA-256 |
| --- | --- |
| Canonical bootloader profile | `84e327c832b61e4c47d89754e93e95614970f3b03edc9f5c54450bd22541e983` |
| `work/boot-re/selection-review.json` | `acabaf457ded5991eddb26ef504deebfb44d030e7b3c6832015605e0a3395905` |
| Independent SPL assembly | `67078c8fc97b4a21a96db43eb61e99daa9710ca6159b698c07a6dbfc92ab9ee8` |
| Selector/caller decompilation | `a38d6011e22704a74dc227fd1d8d4dbeed289c15d9353f6c893e439589ddf12d` |
| NAND/partition/comparison decompilation | `ad0143b00bcd145a5627608671a6dcbeeefcb915be4a880d49673034242ed93a` |
| Kernel handoff decompilation | `85cea9e4b3c2c97e9842511ed699dbcf55c26f5249cbed6579b0a9777a171d86` |

Eleven new synthetic tests pass on macOS and Linux without firmware, a player,
sibling checkout or Ghidra. They cover both boot pairs, backup, later-block
irrelevance, bad first OTA blocks, unknown/malformed tokens, framing and ECC
failures, image/policy drift, metadata/marker instability, prefix shadowing,
symlinks and future profile selection. GitHub Actions discovers these tests in
the existing firmware-free suite; no hosted Actions run is claimed.

The complete host suite passed: 238 Python tests, eight JavaScript tests and
native C assertions (`work/boot-re/conformance-approved.log`). The first sandboxed
run could not bind local sockets; the permitted rerun passed. The focused Linux
run is `work/boot-re/linux-tests.log`. This change affects only offline review;
service/browser behavior and acquisition payloads/profiles did not change.

The subsequent [installation package](installation-review.md) binds this static
assessment and capture provenance to stock/staging evidence, current exact images
and write/readback plans. Physical write admission remains closed pending
separate authorization of the concrete prepared activation/write/readback step. See the
[installation procedure](installation-procedure.md).
