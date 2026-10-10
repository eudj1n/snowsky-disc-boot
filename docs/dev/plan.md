# Plan

The plan of open work for snowsky-disc-boot (owner, 2026-10-10): an item
leaves it once it is done, since the commits keep its history; what later
work needs lives in the documents ([docs/dev/README.md](README.md),
[development](development.md) first). The contract it implements: [boot
layer contract](boot/contract.md). Decisions and evidence from before this
repository stay in snowsky-disc-web's `docs/plan.md` (its "Boot layer" and
"Public installer" sections).

## The player and USB Boot

- [ ] Open facts for the device (2026-10-03): what stock's "Reset all"
  removes in `/usr/data` on the player itself (the guest's reading keeps
  `/usr/data/disc-boot`: contract, "Facts this rests on"), and memory and
  priority during playback (the idle baseline is in the contract).
  disc-health's journal on the owner's player can answer the second, and
  with it whether a service's `nice` +10 and 16 MiB suffice.
- [ ] The power key's hold seen on the player (owner, 2026-10-06). The menu
  switches the player off on `0x108`, the code snowsky-disc-qemu reads in
  stock's UI; the menu writes each key's code to its output, so one hold
  while it asks, read over the USB console, settles it. The emulator does
  not model the hold.
- [ ] `userdata` read through USB Boot (2026-10-05), for a player whose
  system never stays up: then the boot log (`/usr/data/disc-boot/boot.log`)
  and stock's `/usr/data/fiio/log/process_failed.txt` are out of the USB
  console's reach. It needs a reader of `userdata`'s raw pages (today's
  readers enter only the rootfs) and its UBIFS taken apart offline.
- [ ] The restart payload's fallback bounded by a clock that runs in USB
  Boot (2026-10-11): it waits on CP0 Count, which stands still in the
  payloads' context, so if the watchdog's restart never came it might not
  return to the ROM ([restart](usb-boot/restart.md#the-payload)). Bound the
  wait by an iteration count or the SoC's OST, with `restart_test.c`.

## The installer

- [ ] Keep, simplify or retire the developers' `--history` path (owner,
  2026-10-07). Since the seventh write (2026-10-08) every installation, the
  owner's included, runs without a history and is decided by digests in one
  USB Boot entry; only the history path still pins the transport and
  installer profiles byte for byte, moves the review pin after each write
  and keeps stock and boot captures.
- [ ] No question about the first start when its check comes back (owner,
  2026-10-08, to weigh): the system started and holds the written image. Ask
  yes or no only when the check does not come back; the answer then decides
  the way back to stock.
- [ ] The installation's card after the write, over the cable (owner,
  2026-10-09). Today the card is staged before USB Boot, through a card
  reader or the player's Working mode → USB Storage (2026-10-10). The goal:
  stage the packages, the apps and the console marker after the write, while
  the new system runs with the cable connected, and read the first start's
  check from the card. Settle on the player first that USB Storage and the
  USB console take the port in turn, and a first start's check with no
  expected digest on the card, where the installer compares the result.
- [ ] The read by digest's leftovers in the installer: `Reviewed.digest()`
  in `scripts/installer/usbboot.py` and its payload build are unused since
  2026-10-06 (the payload itself still ships in `disc-usb-payloads`); remove
  them, or keep them as a developer's check, and say which.

## Packages and the menu

- [ ] Forget one kept network from the menu's Services screen (owner,
  2026-10-09). Today only removing disc-network with its data, or
  everything ours, forgets the networks.
- [ ] Updates over Wi-Fi from the server's page (owner, 2026-10-09). The
  controller asks boot to install a package it staged on the card, as the
  menu does (`disc-boot install <folder>`), and boot starts a new service or
  server at once, as it has since 2.57.7. What remains here is the
  contract's rule for that request: who may ask, for which roles, and with
  what consent. The server's side (the catalog, each package by its digest,
  signed by our key) is in snowsky-disc-server's plan; it builds on
  "Packages released on their own" (Later).
- [ ] The clean test on the owner's player (owner, 2026-10-08/09):
  "Everything ours" in the menu, then FiiO's Local upgrade, then the user's
  installation from that new state, the first installation of the packages
  on a card without `.disc` (neither the Local upgrade nor stock's "Reset
  all" removes `/usr/data/disc-boot`, so this is the only way to that
  state). Reinstall diskOS then too: the player's diskOS 1.2.0 package
  predates `title` and `homepage`, so the manager shows it as `diskos`
  without a link.

## Later

- Packages released on their own, without a cascade (owner, 2026-10-10; to
  do later, not started). Today a new server, disc-health or disc-network
  needs a boot release, whose packages change only in their manifests
  (2.57.8: the menu and disc-health 2.57.7's binaries under a new version
  and digest, reinstalled on players). Three couplings cause it: the own
  packages take the boot release's version (`release.py`); the catalog lives
  in this repository and the installer's archive; `install.py --packages`
  reads the archive's copy. Proposed: a boot release only for what is
  written to the player (`disc-boot`, the USB console, the payloads, the
  installer); each package its own version, tag (`disc-health-v1.0.1` and
  the like) and build id from its own sources; the catalog published apart,
  at a stable address, signed with our ed25519 key (verified by CI without
  secrets, and later by the server on the player), each entry with its
  digest, compatibility (profile, `bootApi`, a minimum boot layer when
  needed) and acceptance; `install.py --packages` and later the server's
  page read the live catalog, the archive keeping a snapshot and the
  packages for a first installation offline. Open decisions: the catalog in
  a repository of its own (`snowsky-disc-catalog`, recommended) or a file
  on `2.x` here; signed (recommended) or not; per-package semantic versions
  (recommended) or `2.57.N`; the menu and the services kept here with tags
  of their own (recommended) or split; the order (the catalog and the
  versions, then `--packages` on the live catalog, then updates over Wi-Fi).
  Updates over Wi-Fi (above) build on it.

- diskOS Disco! as another interface before the large public release (owner,
  2026-10-09): zmd22's fork of diskOS (https://zmd22.github.io/diskos-disco/,
  https://github.com/zmd22/diskos-disco; 1.2.1 for V2.57; its UI under
  GPL-3.0-or-later, installer and documents under MIT), whose release carries
  the built `payload/mq_ui` as diskOS's does. As a `ui` package assembled on
  the user's computer by a recipe like diskOS's (its `mq_ui` from the release
  by its digest, the boot layer's entry beside it, nothing of it
  redistributed from here), its boot choice and card protection checked
  against what diskOS 1.2.0 expects, then the guest's two-package acceptance
  beside the server and diskOS, and the owner's player.

- An update of the image by the running system from the card (owner,
  2026-10-08): FiiO's own update takes about 2 minutes, ours over USB Boot
  about 10. The new rootfs would be written on the player as FiiO's recovery
  does, with the first start's check as its proof; USB Boot stays the first
  installation and the way back from any state. To weigh against the reasons
  the contract gives for closing this on 2026-10-02 (the end of "Owner's
  decisions"): `rootfs2` is FiiO's recovery, not a second slot; rewriting the
  running main rootfs risks a player that only USB Boot recovers; stock's
  update paths check FiiO's signature (`/etc/ota_bin/ecc_public.pem`).

- The emulator repository keeps only its qemu part (owner, 2026-10-02): a
  separate session after the boot layer's emulator work, one PR into its
  `2.x` (where `codex/disc-web` was merged as #36, `f70a066`, keeping our
  reference revisions `a0cf54d` and `992d156` reachable). What our
  repositories import from it and must keep or move along:
  `emulator.runtime` (keys, peripherals, boot_ready), `emulator/scripts`
  (`lib.sh` and the boot scripts), `firmware.tools.firmware_inventory` (this
  builder's OTA input), `controller.fiio_link`, `research.diagnostics`
  (`probe_keys`, `player_memory`) and `ci/cleanup.sh`. The DISC Web
  prototype, `experiments/`, `library/` and the speech work are not used.

- A full-image digest on the player in USB Boot (owner, 2026-10-05). Today
  an image is known by its first 256 KiB before a write and proven by the
  first start's check after it; the readback through the ROM (about 25
  minutes) runs only when that check does not come back. A SHA-256 of every
  block computed on the player, with only the digests sent over USB, would
  remove both limits: before a write the whole rootfs must hash to stock or
  one of our images, and after it to the written image, from any state. Two
  candidates: diskOS 1.2.0's `my_write6`, which hashes on the device, as the
  writer to review; or the payloads run from the cached alias (kseg0), which
  first needs a review of the cache state the ROM and the SPL leave (the
  transport has no cache flush request); uncached, a whole image's hash takes
  about 6.7 minutes (the 1 MiB sample took 4.2 s, 2026-10-07; [writer
  transport](usb-boot/writer-transport.md#the-hash-on-the-player)). Sampling
  blocks was weighed and refused (owner, 2026-10-05): a NAND write fails in
  one block or page, and a squashfs shows the damage only when that file is
  read.

- A measured writer: our reproducible build of `my_write5` with
  `my_write6`'s timing in its erase, program and verify of each block, to see
  where the writer's 246 s go (the chip's timings say about 1–1.5 minutes).
  Count stands still in USB Boot payloads: clear Cause.DC or use the OST
  ([rootfs collector](nand/rootfs-collector.md#the-read-by-digest-rootfs-digest)).

- Ready-to-run installers for macOS and Windows (Windows needs a WinUSB
  driver for the USB Boot device); a browser installer over WebUSB.

- An optional "official" label for packages signed by us.
