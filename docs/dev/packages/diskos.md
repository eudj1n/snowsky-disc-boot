# diskOS and diskOS Disco! as `ui` packages

diskOS ([b0hemia/diskos](https://github.com/b0hemia/diskos), MIT; its UI under
`ui/` GPL-3.0) and its fork diskOS Disco! ([zmd22/diskos-disco](https://github.com/zmd22/diskos-disco),
MIT; UI GPL-3.0-or-later) are the third-party interfaces the catalog offers,
neither ticked by default (FiiO's own interface leads, owner 2026-10-09). Both
ship their own image of FiiO's rootfs; here each is a `ui` package of boot
API 1 assembled on the user's computer by the catalog's recipe
`diskos-release` ([contract](../boot/contract.md#catalogs)), so nothing of
either is redistributed from this repository.

## The recipe

`scripts/catalog.py fetch --name diskos|diskos-disco --output DIR [--from FILE|DIR ...] [--download]`
takes one of the entry's listed archives (a local file with the recorded size
and digest, or its download), reads the member the entry names (a tar.gz
member for diskOS's installer archives, a zip member for Disco!'s complete
release; told apart by the archive's content, not the file's name), checks it against
`memberSha256`, and writes the package: the binary at `diskos/mq_ui`, our
entry `mq_ui` and `package.json` (`player: diskos/mq_ui`, the entry's `title`
and `homepage`). The entry records the choice the binary insists on and
hands over:

```sh
printf 'diskos\n' > /tmp/.diskos_boot_select
: > "$DISC_BOOT_RUN/ready"
exec -a mq_ui "$DISC_BOOT_SLOT/diskos/mq_ui" "$@"
```

Both projects decide "diskOS or stock" in their own `S96diskos_select` hook,
once per boot, and write exactly `diskos` or `stock` to
`/tmp/.diskos_boot_select`; their `main.c` consumes that record and runs
stock's UI without a positive one (`boot_select_enforce`). Under the boot
layer the choice was boot's (the menu, `next`, the default), so the entry
writes the record itself. Disco! 2.6.1 keeps the rule and the path unchanged
from diskOS 1.2.x, which is why one recipe serves both.

## What the releases carry

| | diskOS 1.2.1 (2026-10-14) | diskOS Disco! 2.6.1 (2026-10-21) |
| --- | --- | --- |
| Archive the recipe reads | `diskos-installer-macos-arm64.tar.gz` (58,053,643 B) or `…-linux-x86_64.tar.gz` (59,352,500 B) | `diskos-disco-2.6.1-complete.zip` (11,818,151 B) |
| Member | `diskos-installer/payload/mq_ui` | `mq_ui` |
| The binary | 4,285,004 B, `6982cebd…`; the same bytes as the release's bare asset `diskos-ota-mq_ui` and its `diskos-ota-update.manifest` | 6,151,664 B, `8db1674c…`; the same as the bare asset `mq_ui`; `mq_ui.build.json` names the source commit, the toolchain (GCC 11.2, musl 1.2.6, static MIPS32r2 o32 FP64 NaN2008) and the digest |
| Its own installer | `my_write6` with a base gate on the kernels and the recovery rootfs; the image flashes its hooks `S96`/`S97`, `diskos-artdec`, dropbear | upstream's installer (`diskos_installer/`, `flash/my_write6.c`), plus `diskos-launch`, `diskos-bootprobe`, `diskos-rmguard`, `diskos-selected`, `S99usbserial`, and the Disc-Shelf app |

The bare `mq_ui` assets are smaller than the archives; the recipe reads a
member of an archive by design (one digest identifies the download, another
the member), so the catalog names the archives. Listing a bare file would be
a recipe change.

## As a package, what is and is not there

- Boot's slots, confirmation and rollback replace the projects' own: diskOS's
  `S97` verify-and-install into `/usr/data/mq_ui` with the `mq_player`
  symlink, and Disco!'s signed SD-card updates with a trial, `Keep` and `Go
  back` (`/usr/data/updates/`). Those menus still exist in the UI; what they
  stage is never run by boot, which starts the slot's binary. A new version
  comes through the catalog and the card, as any package.
- Helpers at fixed paths are absent: `/opt/diskos/bin/diskos-artdec` (the
  artwork decoder; Disco! 2.6.1's build record says the raw `mq_ui` works
  without the flashed helper, with its capped built-in decoder), diskOS's
  SSH (`/usr/project/diskos-debug.sh`, dropbear), Disco!'s launcher
  (`diskos-launch`: its status files `/tmp/.diskos_launch_status`,
  `/tmp/.diskos_launched` are written by the UI's own player launch, which
  the acceptance reads), and the apps in `/usr/data/apps` (Disc-Shelf,
  SongSphere, Album Roulette: separate installs on the projects' side).
- Bluetooth: Disco! drives stock's own stack by command (`brcm_patchram_plus`,
  `/usr/project/bluetoothd`, `bluealsa`, `bluealsa-cli`, `bluetoothctl`,
  `rfkill`, `hciconfig`), all present in FiiO's V2.57 rootfs; nothing of it
  comes with the package. The emulator has no Bluetooth, so this is for the
  player.
- Wi-Fi: both bring Wi-Fi up themselves (`wifi_up.sh`) and diskOS keeps
  several networks through `wpa_cli` (adds without removing,
  `enable_network all`). disc-network, under another UI, replaces the list
  with one of its own when a kept network is in range; whether it should
  change nothing under a UI that manages networks itself is open
  ([plan](../plan.md)).
- Their settings live in `/usr/data` directly (`diskos.conf`, `diskos/`,
  `boot_default_stock`, the logs `diskos_boot.log`, `diskos_crash.log`,
  `diskos_rmguard.log`), not in `$DISC_BOOT_DATA`, and are shared between
  diskOS and Disco! when both are installed: Disco!'s README asks for its
  `Reset diskOS Settings` after switching builds. `boot_default_stock` means
  nothing under boot (the record is written by the entry).
- Stock's player is started by the UI's own launcher with its card guard
  (`rm` wrapper at `/usr/data/diskos/bin`) first in `PATH`, then boot's
  guard: the acceptance's `playerPath` check.

## Observed

- diskOS 1.2.1 on the guest beside the server 2.57.8 (2026-10-14, build
  8eb15a9): installed and confirmed, two-package acceptance 5 of 6, plain
  starts 6 of 6; after a start with Play that installed from the card its UI
  ended about a minute after its start in 2 of 4 such starts (no fatal
  signal; stock's loop restarted the pair, then it ran and was confirmed),
  1.2.0 0 of 2. The cause was not found; 1.2.1 also brings Wi-Fi up itself,
  which the emulator blocks.
- 2026-10-22, both from the recipe, beside the server 2.57.10 and Disc Player
  1.1.0 on the guest (build 8eb15a9, emulator f1d5e33): diskOS 1.2.1 and
  Disco! 2.6.1 each passed the two-package acceptance 6 of 6, confirmed after
  the start with Play with no failure recorded by boot (stock's own restart
  count was not read), stock's player through the UI's own
  launcher (`guard=1 local=1`), the card kept through stock's `rm -rf` of the
  mount point (Disco!'s guard logged the refusal), the pair restarted by
  stock with the server untouched, stock mode with both installed, and a
  broken update giving way to the confirmed one. Neither has run on a player
  from the catalog yet; Disco!'s author released 2.6.1 untried on a Disc.

## Updating an entry

1. `gh release view <tag> -R <repo> --json assets` and the release's
   `SHA256SUMS`: the archive's digest and size, the member's digest (diskOS:
   `diskos-ota-update.manifest` names the UI's size and sha256, equal to
   `payload/mq_ui`'s; Disco!: `mq_ui.build.json`). Verify by downloading the
   archive and hashing the member, never from the sums alone.
2. The catalog entry: `version`, `archives`, `memberSha256`; `catalog.py check`
   and `tests/conformance/test_catalog.py`.
3. `catalog.py fetch --from <downloads>` builds the package; the two-package
   acceptance ([development](../development.md#the-image-and-its-guest)) runs it
   beside the catalog's server and app on a fresh guest; its `player.json`
   name is the entry's (`diskos` or `diskos-disco`).
4. `verified` records the date, the boot build, the emulator revision and the
   acceptance in one sentence; the owner's player comes after the guest, on
   the owner's word.
