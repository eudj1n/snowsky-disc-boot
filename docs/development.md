# Development

## Layout

| Path | What |
| --- | --- |
| `docs/contract.md` | The boot layer's contract with packages |
| `docs/plan.md` | The canonical plan |
| `device/src/boot.c` | `disc-boot`: modes, the service's supervisor, requests, recovery, the `mq_ui` launcher |
| `device/src/manifest.c`, `boot_util.c`, `sha256.c` | Package manifests and verification, files, JSON and state, SHA-256 |
| `device/vendor/jsmn/` | The JSON tokenizer (MIT, pinned) |
| `device/menu/` | `disc-menu`, the boot menu package: the screen (`draw.c`), the list, keys and touch, the hand-over (`menu.c`) and its font (`font.h`, generated) |
| `scripts/menu_font.py` | Rasterises Inter into `device/menu/font.h` (the toolchain has no font rasteriser) |
| `console.py` | The player's USB console: commands, a shell, a file to `/tmp`, a read-only summary |
| `install.py`, `scripts/installer/` | The guided installer: the terminal look in the menu's colours (`tui.py`), the card (`card.py`), the steps (`flow.py`) |
| `catalog/packages.json`, `scripts/catalog.py` | The packages the installer offers, and their checks and fetching |
| `device/src/usb_console.c` | The USB ACM engineering console |
| `device/acquisition/` | The NAND reader and SFC identity payloads for USB Boot sessions |
| `device/deployment/boot-report.sh` | The boot report written to the card |
| `device/deployment/card-guard.sh` | The card guard, `rm` first in the `PATH` of stock's player and UI |
| `scripts/package.py` | Packages: describe a folder, check it as `disc-boot` does, zip it, stage it on a card |
| `scripts/deployment/build_candidate.py` | The image builder (variant `boot`): stock plus the boot layer, verified offline |
| `scripts/deployment/` | Reviews, transports, readback and audits of a USB Boot installation |
| `tests/integration/` | Checks run in the disposable container ([build and flash](build-and-flash.md)) |
| `scripts/firmware_profile.py`, `firmware/` | Reviewed firmware profiles (V2.57) |
| `tests/conformance/` | Synthetic tests, runnable without firmware or a player |

## Build and test

```sh
bash scripts/test.sh          # host build of the console and NAND tests, then every conformance test
bash scripts/build.sh host    # host build only
```

The player's binaries need the pinned soft-float toolchain (Docker):

```sh
docker build --platform linux/amd64 -t disc-native-toolchain -f device/Dockerfile.toolchain .
bash scripts/build.sh mips     # the console, static and soft-float
bash scripts/build.sh reader   # the NAND reader's MIPS tests and freestanding core
```

`DISC_TOOLCHAIN_IMAGE=<image>` selects a reviewed copy of the toolchain image.

The host build also makes `disc-boot-fixture` (`-DDISC_BOOT_FIXTURE`): it
reads `DISC_BOOT_FIXTURE_ROOT` (every absolute path is taken under it, the
keys come from `fixture/keys`) and `DISC_BOOT_FIXTURE_TIMING`
(`confirm=1,grace=1,…`). The image builder refuses any binary carrying these
names. To run `test_boot` on Linux against the MIPS build, build
`../build/mips/disc-boot-fixture` with the toolchain and point
`DISC_BOOT_FIXTURE_BINARY` at a wrapper that runs it under
`qemu-mipsel-static -0 "$(basename "$0")"` (the emulator container has one),
with `DISC_BOOT_FIXTURE_FILE` naming the binary itself.

## Packages

`scripts/package.py` follows the contract's rules and messages (a test holds
it to `disc-boot verify` case by case):

```sh
# package.json for a folder: every file with its size, SHA-256 and mode (0755 when executable)
python3 scripts/package.py describe --source build/pkg --name disc-server --version 2026.10.02 \
  --role service --entry bin/disc-server --arg --listen --arg 0.0.0.0
python3 scripts/package.py check --source build/pkg          # or a zip; --role, --profile, --arch
python3 scripts/package.py zip --source build/pkg --output work/disc-server.zip
# On a mounted card, for the recovery with Play (an explicit operator step):
python3 scripts/package.py stage --package work/disc-server.zip --card /Volumes/PLAY --confirm-card-write
python3 scripts/package.py result --card /Volumes/PLAY        # the last recovery's result.json
```

A zip holds `package.json` and the files at its root with their modes, in a
fixed order with fixed times. `stage` checks the package for the player
(`mips32el-linux-static`, the active firmware profile), writes it beside its
place (`.disc/boot/install/service/`, `.disc/boot/install/menu/`, or
`.disc/boot/install/ui/<name>/` for each ui package), checks the copy (sizes
and digests; a card keeps no modes), then swaps it in; a refused package
stages nothing and leaves what was staged.

## The boot menu

`scripts/build.sh host` and `mips` build `disc-menu` beside `disc-boot` (soft-float, no FPU
instructions, as the boot program); the host build also makes `disc-menu-fixture`, whose
framebuffer and input devices are files (`DISC_MENU_FB`, `DISC_MENU_KEYS`, `DISC_MENU_TOUCH`;
`DISC_MENU_COUNTDOWN_MS`, `DISC_MENU_HELD` for the tests), checked by
`tests/conformance/test_menu.py`. Its package is the MIPS binary as the menu's entry:

```sh
mkdir -p work/<run>/menu/bin && cp build/mips/disc-menu work/<run>/menu/bin/mq_ui
python3 scripts/package.py describe --source work/<run>/menu --name disc-menu --version <version> --role menu --entry bin/mq_ui
python3 scripts/package.py zip --source work/<run>/menu --output work/<run>/disc-menu.zip
```

The font header is generated, not edited: `python3 scripts/menu_font.py --font
<Inter[opsz,wght].ttf> --output device/menu/font.h` (Pillow with FreeType; the source font's
SHA-256 and the versions used are written into the header, docs/provenance.md).

## The installer

`python3 install.py` runs the guided installer in the terminal (the boot menu's colours;
`--plain` or a non-terminal output gives plain text). This part checks the computer, builds
the image from FiiO's update in the emulator's image (or takes one with `--image`), offers
`catalog/packages.json` by role (a service and a boot menu, one at most each; any number of
UIs) and the chosen server's `catalog/apps.json`, and puts the packages,
the default apps (`Apps/`) and the console's marker on the card after the typed confirmation.
Archives come from local files with their catalog digest (`--from`) or, once published,
`--download`. `--dry-run` stages into the run's own folder (`work/install-*/card`) and writes
nothing else; `--yes` answers nothing and takes the defaults and the options given. The run's
report is `work/install-*/report.json`.

The player's step runs against a simulated player with `--simulate` (`scripts/installer/device.py`):
a NAND file with its spare area in the reviewed chip's and writer's geometry (2,048 blocks of
128 KiB; the image's 768 logical blocks from block 80, bad ones skipped within a reserve of 64).
It identifies the chip, backs up the whole NAND into the run folder, writes after the typed
`WRITE`, reads back and compares every byte; `--restore` writes the stock restore image built
beside the image the same way (`RESTORE`). `--fault no-device`, `bad-blocks=N,M`,
`write-stops=N` and `readback-flip=N` show how each failure stops the run with the backup's place
and nothing retried. With `--history` (this player's `history.json`: its boot and stock captures, and the last
installation's review, image, write and readback, or the stage capture of a first one),
`--diskos` (the checkout at the writer's pinned revision, which the tools check) and
`--libusb`, the step runs the reviewed tools in `scripts/installer/usbboot.py`, the order of
[build and flash](build-and-flash.md) steps 3 to 5: the package offline, then after `BACKUP` a
session that collects the primary rootfs and compares it with the history's image, after
`WRITE` the profile's admission (only it and the review pin may change), both plans computed
again and compared, one writer call and the admission closed in every case, then after `READ`
a fresh collection compared every byte with the image by its approved exact plan, and both USB
journals audited. An unknown writer outcome stops everything. The run writes the next
`history.json`. Before its first use on a player two points want a review: the backup session
is collected with the boot capture's metadata page, as the write plan is, while the procedure's
readback uses the write session's; and the diskOS checkout must be at the writer's pinned
revision (`646212d`; a checkout moved elsewhere fails the tools' pin checks).
Without either, the step says how the player is written instead. `tests/conformance/test_installer.py` runs both
on a card folder and a 64-block simulated player.

## The player's console

`python3 console.py` works the player's USB console (the card's `.disc/dev/usb-console` marker,
the cable to this computer): `find` lists the ports, `run` gives each command's output and exit
status (its end found by a marker of that call), `shell` is interactive (Ctrl-] leaves), `send`
puts a file into `/tmp` gzipped in base64 lines and checks its SHA-256 there, and `facts` reads
a summary (firmware, boot status, the keys' word, the watchdog's holder, the input devices,
memory, `/usr/data`, the partitions). It refuses any command that names the serial number, a
MAC address, a token or a raw partition. `tests/conformance/test_console.py` runs it against a
pseudo-terminal whose other end is a shell.

## The image and its guest

The review image is built offline from the selected firmware's OTA, in the
emulator's image with its checkout on `PYTHONPATH`; it never reaches a
player from here:

```sh
bash scripts/build.sh mips
docker run --rm --network none -e PYTHONPATH=/repo -v <emulator>:/repo:ro -v <firmware>/main_os/ota_v<version>:/ota:ro \
  -v "$PWD:/src:ro" -v "$PWD/work/<run>:/out" --entrypoint python3 snowsky-disc-qemu-ci:<revision> \
  -B /src/scripts/deployment/build_candidate.py --ota /ota --console /src/build/mips/disc-usb-console \
  --boot /src/build/mips/disc-boot --output /out/build
```

`scripts/guest.py` runs such an image on a disposable stock-init guest of the
emulator, at a revision reviewed for the firmware (`firmware/emulator-revisions.json`,
kept apart from the profiles: their fingerprint is pinned by other reviews).
Build the emulator's image for that revision under a tag of its own first
(`docker build -t snowsky-disc-qemu-ci:<revision> <emulator>/emulator/docker`).

```sh
python3 scripts/guest.py up --reference <emulator> --image work/<run>/disc-boot-v<version>-review-only.bin \
  --ota <firmware>/main_os/ota_v<version>
python3 scripts/guest.py run -- python3 -B /boot/tests/integration/boot_guest.py --output /work/boot-guest.json
python3 scripts/guest.py stage --package <zip>        # with the guest off: onto the card, for Play
python3 scripts/guest.py power on --hold play         # also reboot, off, cut [--unsynced], status
python3 scripts/guest.py power on --network isolated  # a player without a network (only loopback)
python3 scripts/guest.py status | down
```

`boot_guest.py` is the guest acceptance (plan, stage 2): about 40 minutes,
since a version is confirmed after 180 s of running. The server repository
drives the same wrapper with a record of its own (`--state`).

The two-package acceptance (plan, stage 3) runs our server beside diskOS's
UI. diskOS is built locally from a checkout with its own toolchain image
(`ui/Dockerfile` at the same revision; built natively, `--platform
linux/arm64` on Apple silicon, it takes about ten minutes); the package stays
in ignored `work/` and is never passed on:

```sh
docker build --platform linux/arm64 -t diskos-ui-builder:<rev> <diskos-export>/ui
python3 tests/integration/diskos_package.py --source <diskos checkout> --revision <rev> \
  --builder diskos-ui-builder:<rev> --output work/<run>/diskos
python3 scripts/guest.py run -- python3 -B /boot/tests/integration/two_packages.py \
  --server /boot/work/<run>/server.zip --ui /boot/work/<run>/diskos --app /boot/work/<run>/page.zip \
  --output /work/two-packages.json
```

The server's package comes from snowsky-disc-server (`scripts/build_package.py
--debug`), the page's release zip from snowsky-disc-player.

## Related repositories

- snowsky-disc-server: the gateway (the `service` package), its catalogs
  and the emulator wrapper (`scripts/emulator.py`) used for guest acceptance
  today.
- snowsky-disc-web: the frozen history of the gateway and the combined
  images; the running emulator stack still mounts it as `/platform`.
- snowsky-disc-qemu: the external emulator; never modified from here.
- snowsky-disc-player: the page, a card app of the server.
