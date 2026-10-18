# Player variants: what FiiO ships in the DISC

What two players taught about the hardware behind one firmware version, and
what to expect from the next player. The facts come from the sessions recorded
in `observations/` and the reviews in `nand/`; this page draws the conclusion
they share, so that the next variant is recognised for what it is.

## The two players seen

Both players run FiiO's V2.57 (the second was updated to it, factory-clean,
before its first session). Everything the boot layer reads from them before a
write is the same, except the NAND chip and the bootloader's build date.

| | The first player (2026-09-24 on) | The second player (2026-10-18) |
| --- | --- | --- |
| SPI NAND | XTX XT26G02C | HeYangTek HYF2GQ4U |
| Read ID | `0B 12` | `C9 52` |
| Geometry | 2,048 + 128 bytes a page, 64 pages a block, 2,048 blocks | the same |
| ECC status (C0) | high nibble: 0 clean, 1–8 corrected, F uncorrectable | bits 5:4: `00` clean, `01` corrected, `10` uncorrectable, `11` corrected at the maximum |
| ECC strength | 8 bits per 512 bytes | 14 bits per 512 bytes |
| SPL | `SPL 2013.07 (Jan 09 2026 - 15:02:40)` | `SPL 2013.07 (May 07 2026 - 17:14:17)`: the same program, ten bytes differ (the date string, two header bytes) |
| Partition table (the metadata page at `0x5800`) | main bytes `2e4ef4fa…` | the same bytes |
| Stock rootfs | first blocks `dccfa975…` | the same |
| Kernel | OTA V2.57 (`xImage` `1a9484ef…`) | the same OTA |

Sources: the [chip review](../nand/nand-chip-review.md) (both chips' datasheet
facts and the kernel's handling of each), the [bootloader review](../nand/bootloader-review.md#the-second-players-spl--2026-10-18)
(the byte comparison of the two SPL builds), the [second player's acceptance](../observations/second-player-acceptance.md)
(the reads and the write).

## Another supplier, not another revision

The second chip is not a revision of the first: XTX and HeYangTek are
different manufacturers, their parts carry different IDs, correction strength
and status encodings, and only their geometry and command set coincide. This
is a second-source substitution on FiiO's production line: a 2 Gbit SPI NAND of
the same geometry from whichever vendor was available. A user cannot tell the
two players apart; the boot layer can, from the first Read ID.

The firmware was built for this. The V2.57 kernel is Ingenic's X2000 BSP, and
its SPI NAND driver registers sixteen vendor drivers, each a `<vendor>_nand_init`
in the kernel's symbol table: ATO, Dosilicon, FudanMicro (`fm`), Foresee (`fs`),
GigaDevice (`gd`), ISSI, Macronix (`mxic`), `tc`, Toshiba, Winbond, `xcsp`,
XTX (three: the generic `xtx`, `xtx_mid0b` for manufacturer `0x0b`, `xtx_mid2c`
for `0x2c`), HeYangTek (`yhy_midc9`), Zbit (`zb`) and Zetta. Each has its own
`get_cdt_params` (the commands) and `deal_ecc_status` (its reading of C0), so a
player with any chip of that table boots stock. Which of them FiiO actually
mounts is known only from players seen: two so far. The table says what the
kernel would accept, not what exists.

The second player is a later production run, not an older or newer firmware:
its SPL was built on 2026-05-07, four months after the first player's, from the
same sources (the images differ in the date and two header bytes only), and
its stock partition table and rootfs blocks are byte for byte the first
player's. Nothing else about the board was observed to differ: no bad blocks
in the regions read, ECC clean throughout, the same partitions, the same boot
selector. The SPL's date string is, so far, the only way to date a player; the
profile `firmware/bootloaders/v2.57.json` lists each reviewed build's digest
with its date.

## What a chip changes for the boot layer, and what it does not

Changes, and is therefore reviewed a chip (`firmware/chips/<id>.json`, the
[chip review](../nand/nand-chip-review.md)):

- the Read ID the identity session matches (`id_prefix_hex`);
- the meaning of the ECC status register: which values the stock driver
  accepts, which it refuses (`-EBADMSG`), and whether ECC can be switched off
  (`ecc`, one reviewed set a vendor in `metadata_policy.REVIEWED_ECC`);
- the spare-area byte map, if the datasheet gives one (XTX does, so the OOB
  review runs on it; HeYangTek does not, so `oob_review.py` refuses the chip);
- the kernel's entry for the chip (`firmware/kernels/<v>.json`, `chips`), and
  the fingerprint every profile pins for the page policy built for the chip.

Does not change, and is therefore shared:

- the geometry, the factory-marker location and the command set, hence the
  reader core's reads and diskOS's writer (its audit is the same for both);
- the partition table, the metadata page's format, the boot selector and the
  bootloader's handling (the SPL's chip table lists both: the XTX entry
  reviewed at `0xb240509c` and, 72 bytes after it, `C9 52`);
- the image written, the installer's flow and the release: the payloads that
  read pages are compiled a chip (`<chip>/<mode>` in the archive); the
  identity, staging-check and restart payloads are common.

## The next variant

A third chip announces itself the way the second did: the installer's identity
session, the first thing the entry runs, stops before any page is read and
names the two ID bytes (`NR_ID_UNKNOWN`, code 5, in the run's report). If the
manufacturer byte is in the kernel's table above, the player boots stock, and
the work is the one the second chip took, in this order:

1. The datasheet of the exact part (the family's other datasheets may describe
   another die: HeYangTek's T-series shares the name, not the ID). Record its
   facts in `firmware/chips/<id>.json` and the review in the chip review page;
   a confidential datasheet stays out of the repository, its digest and
   sections recorded.
2. The stock driver's `deal_ecc_status` for the vendor: what it refuses, we
   refuse. Add the ECC semantics to `metadata_policy.REVIEWED_ECC` only if they
   are a new encoding.
3. The chip's entry in the kernel's table (`kernel_review.py` on the pinned
   `xImage` finds the address; a wrong one fails as "Kernel device ID
   mismatch"), then the pins of every profile, one a chip.
4. A player's acceptance: `install.py --accept-chip` admits the first write on
   a chip with no history; its reads, write and first-start check are recorded
   in `observations/`, and what the write ran in `firmware/images`, `exercised`.
5. A release, whose payloads carry the new chip's builds.

A bootloader built on yet another date fails the review as "Unreviewed
bootloader bytes": compare the image with a reviewed one and, if only the
date and header bytes differ, list its digest in `boot_sha256` with its date.
