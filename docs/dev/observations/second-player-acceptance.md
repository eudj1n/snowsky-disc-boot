# The second player's acceptance — 2026-10-18

The owner's second DISC, factory-clean and updated to V2.57, holds a HeYangTek
HYF2GQ4U NAND ([chip review](../nand/nand-chip-review.md#a-second-chip-heyangtek-hyf2gq4u--2026-10-18))
and an SPL built on another day ([bootloader review](../nand/bootloader-review.md#the-second-players-spl--2026-10-18)).
Three runs of `install.py --boot-build --accept-chip` from the branch
`second-chip-heyangtek`, the payloads built on the owner's computer with the
boot layer's toolchain (the XTX ones byte for byte the release's):

| Run | Outcome |
| --- | --- |
| `install-20261018-174318` (the installer's archive of 2.57.9) | Stopped at the first read: `NR_ID_UNKNOWN`, Read ID `C9 52 C9`. Nothing written. |
| `install-20261018-190821` | The chip named (`hyf2gq4u`), the partition table, the bootloader and stock's first blocks read (ECC clean throughout); the review refused the bootloader bytes (its build date). Nothing written. |
| `install-20261018-193049` | The same reads; the review admitted the bootloader and the write without a history (`exercised` shared from the XTX record); the image `disc-boot-v257-review-only.bin` (build `8eb15a998d85`) written once (session `db699985-07bb-4867-bb61-4582a6749b51`, `writer-completion-observed`); the restart payload ran and the player left the bus; the new system's first start read its root device `/dev/mtdblock_bbt_ro2` and found the written image (`rootfs-check.json`: match, 100,663,296 bytes, 12.7 s), fetched over the USB console. |

Two things went wrong in the last run after the write, neither on the player:

- `restart_player.py` did not return: libusb 1.0.30's darwin backend deadlocked
  in `libusb_exit` (its main thread waiting for the hotplug thread, which
  waited for the mutex the main thread held) as the player detached, so the
  result was never written and the installer showed "Restarting the player"
  after the player had restarted. The process was stopped by hand; the
  tool's close is bounded since (`CLOSE_SECONDS`), and a close that does not
  return is noted and left to the process's end.
- `audit_usb_write.py` refused the journal: it compared the fresh NAND
  observation with the first chip's ID, written into it. The audits take the
  build's page policy since; the run's journal passes
  (`saved-candidate-write-trace-matches`, 9,444 calls, one writer execution).

The run's evidence stays in `work/install-20261018-193049/` (ignored). What
that write ran is recorded in `firmware/images/v2.57.json`, `exercised.hyf2gq4u`:
the shared ABI of the XTX record, the metadata payload
`e653da91310d07012d0f084c8c745e915cfcc1f979ea27a4eaad334c0b78abe7` and the
page policy `bc657742a262a0431a080a7adaa5e5a874a9421c5e5042406324d11749cd7255`.
