# Combined engineering image installation observation — 2026-09-24

The owner authorized checking availability and flashing the same physical player
with the same reviewed firmware profile. The exact combined package (snowsky-disc-web `docs/combined-update.md`)
was selected only after one profiled Ingenic USB Boot device appeared. The
regenerated write and full-read plans matched the packaged plans. This was one
candidate write followed by a separately entered, read-only USB Boot session;
there was no automatic write replay. Runtime evidence stays in ignored
`work/combined-003-write/` and `work/combined-003-read/`.

| Item | Observed value |
| --- | --- |
| Candidate size and SHA-256 | 100,663,296 bytes; `2f88117a28174289ca700c2aa4dc13bfcede3b63c402579fe0b24adf766e7c0c` |
| Installation review | `ba809b2b5b858a20e484ae6803c46115eaeb7e5235514bf051b236e0a2f2b41b` |
| Write plan | `0f1f0868c11807bed9ed3410b11b4ed9bd81dc633ee0995d920a39fb4f1c4ed7` |
| Full collection plan | `eb1faaec76055a758f49fe319e6738e69c6edb70aa8e88f3992627449becc2dd` |
| Exact candidate comparison plan | `d4d4847f51add729f60c3dbb86a9eba11ab90148b1a476059867c121aa11b21c` |

## Single write and independent audit

Write session `a7f82921-890a-42a2-8965-78253ae07939` ran from
`2026-09-24T15:28:14Z` to `15:53:55Z`. Fresh staging and RAM checks passed;
the writer returned a completion record for 768 logical blocks mapped through
physical block 849, skipping bad blocks 383 and 716, with no block retries or
cleanup errors. The wrapper closed physical-write admission when it exited.

The offline auditor reconstructed all 27,844 saved USB calls and confirmed one
writer execution, the selected image and plan. Its status was
`saved-candidate-write-trace-matches`; result SHA-256 was
`bc7188d11b7f6526a83bb23dbdd7b8d4b31a86012a4f18c761429b2eae601566`
and journal SHA-256 was
`8f0a4b20715594b09f30495ab661cb358d931826a52b9bf31c0a10a5c3d3cda6`.
The writer's own report cannot establish postwrite contents or normal boot.

## Full independent readback

After the owner confirmed a full power-off and fresh USB Boot entry, read-only
session `508986fe-d215-4c43-a01c-d8cddb9b1c75` ran from
`2026-09-24T15:56:30Z` to `16:33:48Z`. All 794 batches and 50,816 records
completed without cleanup errors. The saved logical image's SHA-256 exactly
matched the candidate. The independent comparator checked request identity,
page order, CRC, ECC, good-block mapping and all 100,663,296 candidate bytes;
its status was `saved-logical-readback-matches`. The capture SHA-256 was
`10c3eb9361e3da5458ee4214bc6e59839c2b50cd752dba3205210f54f948e2d0`.
ECC observations were 50,809 code 0 and seven corrected code 1; there were no
other codes.

The separate offline USB audit reconstructed all 42,220 calls, including
9,553 raw reads, 794 reader executions and no writer invocation. Its status
was `saved-postwrite-trace-matches`; result SHA-256 was
`14d9b65e5f206d5400f8cec6f8dd42628f8d5e8a86ee8f144d1aac3367ec5760`
and journal SHA-256 was
`3d0aa8580f33a322f0cd83f946fc67c11836ae1da8bcb4ebc22aec5301d1716f`.
Neither offline audit reopened the device.

## Owner-confirmed stock boot and remaining checks

After the read-only USB session closed, the owner rebooted normally and
reported: “Да, работает штатно” (operating normally). The report is retained
in the ignored readback folder as `owner-boot-confirmation.json`, bound to the
write and read sessions. This is owner observation of the stock UI, not an
automated process, playback or live-root test.

## Physical USB ACM acceptance

The first cable connection after the stock UI had already booted exposed no
USB device; the helper has a bounded startup window. The owner then rebooted
normally with the cable attached and again confirmed the stock screen. macOS
enumerated `Local root diagnostic console` and created
`/dev/cu.usbmodemdisc_web_debug1` and its `tty` counterpart. The host opened
that exact port, performed four bounded read-only commands and closed it.

`id` returned `uid=0(root) gid=0(root)`. `/proc/cmdline` reported the expected
read-only NAND root `root=/dev/mtdblock_bbt_ro2`; `/proc/mtd` listed the stock
named partitions. `/run/disc-usb.log` showed the expected
`13500000.otg_new` controller, the card mount becoming ready, then
`console started`. The raw bounded command output is retained in ignored
`work/combined-003-acm-probe.json`. This confirms enumeration and a working
local root console, but not yet USB disconnection/revocation or coexistence
with stock USB storage/DAC mode.

Wi-Fi webroot serving and native HTTP health remain separate acceptance
checks. The completed NAND verification alone does not establish that the
companion process started or a browser reached it.

## SD bundle and LAN marker publication

In stock USB-storage mode, macOS mounted the previously identified PLAY card
as exFAT at `/Volumes/PLAY`, volume UUID
`C42F22D8-EFD8-3C81-809C-358185EC8AA2`. Its exact USB-console marker was
still present. Neither `www` nor `DISC_WEB_LAN_DEBUG` existed before this step.
The owner separately authorized creation of the static web release and LAN
marker, readback and safe ejection.

The host bundle builder selected release `fe0753a8ec469745`. Five release
files, `www/active.json` and the exact 25-byte
`DISC_WEB_LAN_DEBUG` marker were created without replacing existing files.
Each file was read back, then an independent comparison matched all six web
files against the host bundle. The manifest SHA-256 was
`7757a95c126c490acb122a5b4e952ab25439dd46c9981a2840879c78a462dad6`;
the LAN marker SHA-256 was
`4cf6bea463d4bc90c0fdbf09919e6a1b5640373b1fd5fb5c9fe11863c82d4811`.
The publication receipt is retained in ignored
`work/combined-003-card-publish.json`. `diskutil eject /Volumes/PLAY`
succeeded. This records card contents, not player remount or browser access;
those checks remain open.
