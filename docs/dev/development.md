# Development

## Layout

| Path | What |
| --- | --- |
| `README.md`, `CHANGELOG.md`, `docs/install.md`, `docs/way-back.md` | For users (the installer's archive carries them, with `docs/assets/`) |
| `docs/dev/boot/contract.md` | The boot layer's contract with packages |
| `docs/dev/plan.md` | The plan of open work |
| `docs/dev/` | For developers, by area ([docs/dev/README.md](README.md)): this page, the plan, `boot/`, `packages/`, `installer/`, `usb-boot/`, `nand/`, `firmware/` |
| `docs/dev/observations/` | The records of sessions on players (writes, readbacks, boot evidence) |
| `device/boot/boot.c` | `disc-boot`: modes, the service's supervisor, requests, recovery, the `mq_ui` launcher |
| `device/common/` | What the programs share: package manifests and verification (`manifest.c`), files, JSON and state (`boot_util.c`), SHA-256 (`sha256.c`) |
| `device/vendor/jsmn/` | The JSON tokenizer (MIT, pinned) |
| `device/menu/` | `disc-menu`, the boot menu package: the screen (`draw.c`), the list, keys and touch, the hand-over (`menu.c`) and its font (`font.h`, generated) |
| `device/health/`, `device/network/` | The services `disc-health` and `disc-network` (boot API 2), built beside the menu and released as packages |
| `scripts/menu_font.py` | Rasterises Inter into `device/menu/font.h` (the toolchain has no font rasteriser) |
| `console.py` | The player's USB console: commands, a shell, a file to `/tmp`, a read-only summary |
| `install.py`, `scripts/installer/` | The guided installer: the terminal look in the menu's colours (`tui.py`), the card (`card.py`), the steps (`flow.py`) |
| `catalog/packages.json`, `scripts/catalog.py` | The packages the installer offers, and their checks and fetching |
| `device/console/usb_console.c` | `disc-usb-console`, the USB ACM engineering console |
| `device/usbboot/` | The programs the installer runs from the player's RAM in USB Boot: the NAND reader, the SFC identity, the batches, the read by digest, the staging check |
| `device/scripts/boot-report.sh` | The boot report written to the card (in the image) |
| `device/scripts/card-guard.sh` | The card guard, `rm` first in the `PATH` of stock's player and UI (in the image) |
| `device/tests/`, `device/Makefile`, `device/Dockerfile.toolchain` | The C tests, the build and the cross toolchain's recipe |
| `scripts/package.py` | Packages: describe a folder, check it as `disc-boot` does, zip it, stage it on a card |
| `scripts/deployment/build_candidate.py` | The image builder (variant `boot`): stock plus the boot layer, verified offline |
| `scripts/deployment/` | Reviews, transports, readback and audits of a USB Boot installation |
| `tests/integration/` | Checks run in the disposable container ([build and flash](installer/build-and-flash.md)) |
| `scripts/firmware_profile.py`, `firmware/` | Reviewed firmware profiles (V2.57) |
| `tests/conformance/` | Synthetic tests, runnable without firmware or a player |

`device/` holds what runs on the player (C, built for MIPS); what runs on the computer (the
installer, the reviews and the tools, Python) is in `install.py`, `console.py` and `scripts/`.

## Build and test

```sh
bash scripts/test.sh          # host build of the console and NAND tests, then every conformance test
bash scripts/build.sh host    # host build only
```

Tests wait for the event they check, not for a fixed time, and their bounds are generous for a
busy computer: the boot fixture gives the menu 20 s and a package 30 s to be ready, and uses short
bounds only where a test checks the bound itself. A test that fails only under the full suite's
load is reproduced by running the suite many times at once (16 runs, 2026-10-09). GitHub's
runners differ from macOS in two ways that matter: their `/bin/sh` is dash, which ends a script
on a failed redirection of a special built-in, and musl.cc refuses them, so the toolchain's recipe
falls back to the Internet Archive's capture of the same compiler, checked by the same SHA-256.

The player's binaries need the pinned soft-float toolchain (Docker):

```sh
docker build --platform linux/amd64 -t disc-native-toolchain -f device/Dockerfile.toolchain .
bash scripts/build.sh mips     # the console, static and soft-float
bash scripts/build.sh reader   # the NAND reader's MIPS tests and freestanding core
```

`DISC_TOOLCHAIN_IMAGE=<image>` selects a reviewed copy of the toolchain image. Its recipe
pins the Debian base by digest and the musl.cc compiler by SHA-256, and `disc-boot` carries
as its build id the last commit that changed its sources (`device/boot`, `device/console`,
`device/common`, `device/menu`, `device/vendor`, `device/Makefile`), so the same sources give the same bytes in any later
commit and on GitHub's runners. The services (`device/health`, `device/network`) are packages known by
their digests and carry no build id: a change of theirs leaves the image as it was.

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
it to `disc-boot verify` case by case; only `homepage` is the tools' own
check, as boot ignores it):

```sh
# package.json for a folder: every file with its size, SHA-256 and mode (0755 when executable)
python3 scripts/package.py describe --source build/pkg --name disc-server --version 2026.10.02 \
  --role controller --entry bin/disc-server --arg --listen --arg 0.0.0.0 \
  --homepage https://github.com/eudj1n/snowsky-disc-server
python3 scripts/package.py check --source build/pkg          # or a zip; --role, --profile, --arch
python3 scripts/package.py zip --source build/pkg --output work/disc-server.zip
# On a mounted card, for the recovery with Play (an explicit operator step):
python3 scripts/package.py stage --package work/disc-server.zip --card /Volumes/PLAY --confirm-card-write
python3 scripts/package.py result --card /Volumes/PLAY        # the last recovery's result.json
```

`describe` gives the controller and a service `bootApi` 2 and the other roles
1, the lowest each needs; a service may name its `memory` (`--memory`, 1-64
MiB). A zip holds `package.json` and the files at its root with their modes,
in a fixed order with fixed times. `stage` checks the package for the player
(`mips32el-linux-static`, the active firmware profile), writes it beside its
place (`.disc/boot/install/controller/`, `.disc/boot/install/menu/`, or
`.disc/boot/install/service/<name>/` and `.disc/boot/install/ui/<name>/` for
each service and ui package; the server of boot API 1, role `service` with
`bootApi` 1, is the controller), checks the copy (sizes and digests; a card
keeps no modes), then swaps it in; a refused package stages nothing and
leaves what was staged.

## The health journal

`scripts/build.sh host` and `mips` build `disc-health` beside the menu (a service of boot API 2;
[disc-health](packages/health.md)); the host build also makes `disc-health-fixture`, whose player is a
folder (`DISC_BOOT_FIXTURE_ROOT`, the kernel's ring as `fixture/kmsg`, `DISC_HEALTH_INTERVAL`),
checked by `tests/conformance/test_health.py`. A release builds its package
(`disc-health-<version>.zip`, `release.py build`), which the catalog offers by default.

## Several Wi-Fi networks

`scripts/build.sh host` and `mips` build `disc-network` (a service of boot API 2;
[disc-network](packages/network.md)); the host build also makes `disc-network-fixture`, run by
`tests/conformance/test_network.py` against a stand-in of stock's `wpa_cli`
(`tests/integration/wpa_cli_stand_in.sh`). A release builds its package (`disc-network-<version>.zip`, a memory
bound of 32 MiB) from 2.57.8, which the catalog offers by default; 2.57.7 left it out while it gave the kept
networks back beside stock's (the owner's player, 2026-10-09: stock's UI fails with more; [disc-network](packages/network.md)).

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

Its screens, drawn by the menu itself through the fixture build (`python3
scripts/menu_screens.py` after `scripts/build.sh host` writes them into `docs/assets/menu/`):

| Choose | Reading the card | Installing | Starting | Switching off |
| --- | --- | --- | --- | --- |
| ![](../assets/menu/choose.png) | ![](../assets/menu/reading-the-card.png) | ![](../assets/menu/installing.png) | ![](../assets/menu/starting.png) | ![](../assets/menu/switching-off.png) |

Its other screens ([contract](boot/contract.md#the-menus-screens)): a package waiting on the card, the
services, the packages, a removal's question, and everything of ours going:

| Waiting | Services | Packages | Remove | Everything |
| --- | --- | --- | --- | --- |
| ![](../assets/menu/waiting.png) | ![](../assets/menu/services.png) | ![](../assets/menu/packages.png) | ![](../assets/menu/remove.png) | ![](../assets/menu/everything-goes.png) |

Its look (owner, 2026-10-03, chosen from three mockups): every entry shown at once in a list,
the selected one in a pill with an accent dot and its version at the right; always in English,
each package by its `title`, else its name, and stock's UI as "FiiO" with the firmware's
version; the 5 s countdown as a ring along the panel's edge; the player page's dark theme for
colours (`#181614` ground, `#ece8e3` text, `#ff795a` accent, `#3a3530` selection) and Inter
(OFL) for the face. The panel is round, 360×360 and mounted turned 180°; `/dev/fb0` is XRGB8888
with three buffers, and each frame is drawn into a hidden buffer and shown with
`FBIOPAN_DISPLAY`, which avoids tearing and is the emulator's evidence of a frame. The menu
draws with a small renderer of its own (integer arithmetic, the ring's geometry computed once);
its font is rasterised at build time and covers Latin only, since the menu is always in English
and titles are ASCII. LVGL was not used: a list of a few entries needs neither its size nor its
widgets.

The font header is generated, not edited: `python3 scripts/menu_font.py --font
<Inter[opsz,wght].ttf> --output device/menu/font.h` (Pillow with FreeType; the source font's
SHA-256 and the versions used are written into the header, docs/dev/firmware/provenance.md).

## The installer

`python3 install.py` runs the guided installer in the terminal (the boot menu's colours;
`--plain` or a non-terminal output gives plain text). This part checks the computer, builds
the image from FiiO's update on the computer itself, without root, with squashfs-tools 4.6 or later
and openssl (the build in the emulator's Docker image went once the guest and the player had run
this one, 2026-10-08; only `--guest` needs Docker and the emulator), with the boot layer's programs
of the newest release recorded for the firmware (`disc-boot-<version>-mips.tar.gz`, a local file by its record's
digest or its download, checked; a local build of `build/mips` only with `--boot-build`), or takes
one with `--image`; the update
may be given as its own folder, `main_os` or `main_os/ota_v<version>`, and its rootfs chunks are
counted against the profile before the build), offers
`catalog/packages.json` by role (one boot menu and one server at most; any number of UIs and
services) and the chosen server's `catalog/apps.json`, and puts the packages,
the default apps (`Apps/`) and the console's marker on the card after the typed confirmation.
The card is any mounted card: one in a card reader, or the player itself in stock's Working
mode → USB Storage (owner, 2026-10-10). Before it stages this run's packages, the installer
removes from `.disc/boot/install/` every package folder an earlier run staged and this run did
not choose, and the controller's old `install/service/` of boot API 1 (2026-10-07: a server
staged by a run that stopped at its review was installed by the next run's Play).
Archives come from local files with their catalog digest (`--from`, and the downloads of
earlier runs in `work/downloads`) or are downloaded from their published address, with the
certificate checked (the system's bundle where a Python build has none of its own) and kept only
when their size and digest match; `--offline` never downloads. When an archive is neither local
nor downloadable, the guided run asks for the file or a folder to look in (dropped into the
terminal), before the card. `--dry-run` stages into the run's own folder (`work/install-*/card`) and writes
nothing else; `--yes` answers nothing and takes the defaults and the options given. The run's
report is `work/install-*/report.json`.

`--packages` stages packages only: no FiiO update, no image and no player. It checks Python
alone (3.11 or later), refuses the player's options (`--restore`, `--resume`, `--run`,
`--guest`, `--simulate`, `--history`, `--image`, `--ota`, `--boot-build`, `--fault`), offers
the catalog's packages and the chosen server's apps, takes each by its digest and stages them
on the card. The player then installs them from the menu's Packages screen, or with Play at
power-on while the menu is not installed yet; a service or the server starts its new version at
once, a menu at the next start.

The player's step runs against a simulated player with `--simulate` (`scripts/installer/device.py`):
a NAND file with its spare area in the reviewed chip's and writer's geometry (2,048 blocks of
128 KiB; the image's 768 logical blocks from block 80, bad ones skipped within a reserve of 64).
It identifies the chip, backs up the whole NAND into the run folder, writes after the typed
`WRITE`, reads back and compares every byte; `--restore` writes the stock restore image built
beside the image the same way (`RESTORE`). `--fault no-device`, `bad-blocks=N,M`,
`write-stops=N` and `readback-flip=N` show how each failure stops the run with the backup's place
and nothing retried.

Without `--history` (a user's installation, 2026-10-08), in a terminal and not a dry run, the
player is read and written in one entry into USB Boot, and no history is kept. After `CHECK`,
three reads, each a session of its own: the partition table's page (`ram_transport.py --mode
metadata`, the entry's only SPL), the boot evidence (`boot_evidence.py`, the bootloader and the
OTA selector) and the identity probe (the first rootfs blocks), the last two audited offline
(`audit_usb_boot.py`, `audit_usb_probe.py`). The review (`installation_review.py --known`, seconds,
offline) knows what the player holds by its first blocks, stock or one of ours (`firmware/images`
and the releases' images), or stops before anything is written and names FiiO's Local upgrade as
the way back; `decision.json` says what it found. The write (`WRITE`) takes the admission the
review computed for the run (`--installer-profile`, the tracked profile untouched) and runs only
in that entry, then restarts the player from USB Boot itself ([restart](usb-boot/restart.md)), so the
cable stays connected; its first start's check proves it as below, the readback following only when
the check does not come back. The user answers yes or no about the start, without the words a
history keeps for the developers. A no takes the player back to stock with the evidence of a new entry
(`usb-back/`, where the review knows this run's own image); `--restore` writes stock's rootfs the
same way. Without a terminal (`--yes`) the card is staged and the player is not written.
The payloads come from the boot release (`disc-usb-payloads-<v>.tar.gz`, the boot evidence's
among them) or are built here with `--boot-build`.

With `--history` (this player's `history.json`: its boot and stock captures, and the last
installation's review, image, write and readback, or the stage capture of a first one),
diskOS's pinned files (`--diskos`, or fetched) and libusb (found, or `--libusb`), the step runs the
reviewed tools in `scripts/installer/usbboot.py`, the order of
[build and flash](installer/build-and-flash.md) steps 3 to 5 as the owner runs it: the package offline,
then, in one entry into USB Boot, the identity check (`CHECK`: the first rootfs blocks by the
reviewed probe read, about a minute, which must be the history's image's; a full backup restored
nothing, the way back being stock) and the write (`WRITE`: the profile's admission, only it and
the review pin may change, both plans computed again and compared, one writer call, the admission
closed in every case; an unknown writer outcome stops everything). The SPL runs once an entry: the
write's session finds the clean DDR diagnostic the check's SPL left and runs none (a second SPL's
DDR training fails too often). A write that stopped before the writer ran
(`failed-before-writer`) goes on with `install.py --resume <run> --history …`:
that run's card stands, the same package is prepared again, and in a fresh entry the first blocks
are checked again and the write follows. The player then starts the new system once
and the owner answers yes or no. The card carries the written image's SHA-256
(`.disc/boot/expected-rootfs.json`); with the cable connected again while the new system runs,
the installer reads its boot layer's check of the root device over the USB console
(`.disc/boot/rootfs-check.json`, a minute or two after its start) and keeps it with the owner's
word in `usb/first-start/`: a match stands for the readback (the write's journal audited alone,
the history naming that folder, 2026-10-07). Without it, or when it differs, a fresh entry
(`READ`) reads it back and compares every byte by the approved exact plan, both USB journals
are audited, and a yes is kept with its time in the readback's `owner-boot-confirmation.json`,
between the write and the readback, where the next installation's review looks for it. A no takes
the player back to stock (owner, 2026-10-05: the way back is stock, from any state): the player
holds this run's own image, known from the writer's completion, so a fresh entry writes stock's
rootfs straight away (`RESTORE`, no readback of the failed image), the owner looks at stock's start
and a fresh entry reads it back. Each session shows a bar, the
percentage and the time gone and left, counting its journal (two lines a call) against the
plan's call limit (`expected_calls`). The write checks the image region on the player with the `staging-check`
payload (built beside the metadata payload and passed as `--staging-build` to the review, the
transport and the audit), reads the image back, hashes a 1 MiB sample of it on the player, and
holds one request to the ROM until the writer returns instead of waiting 15 minutes
([writer transport](usb-boot/writer-transport.md#the-hash-on-the-player)). The read by digest
no longer runs with an installation (owner, 2026-10-06; [rootfs
collector](nand/rootfs-collector.md#the-read-by-digest-rootfs-digest)). The run writes the next `history.json` (its `previousTarget` says candidate or
restore) and keeps `usb/history-used.json`: `install.py --restore --run <run>`
takes the player back to stock with that run's package whatever it holds now: straight to stock
when the player holds that run's own image (its write observed complete), else after a backup that
is only compared with the images it may be. diskOS's files the tools read (its writer, its SPL and the
sources they are checked by) are those of the revision `firmware/sources/diskos.json` names
(`646212d`), each by the digest the reader, writer and probe profiles pin (`scripts/sources.py`):
`--diskos` gives a checkout, otherwise a copy kept in `work/downloads/diskos-<rev>` or, once, the six
files fetched from that revision (about 15 MB) and checked; this repository keeps none of them.
Without either, the step says how the player is written instead.

`--guest` puts the emulator's guest in the player's place (`scripts/installer/guest.py` over
`scripts/guest.py`, its record in the run folder): with the image (`--image`, or the one it
builds), FiiO's update (`--ota`) and the emulator's checkout at a reviewed revision
(`--emulator`), the card is staged in the run folder, copied over the guest's card with the guest
off (`guest.py put`), and the guest starts with Play held. It is followed (`guest.py status`)
until the menu answered and the service was confirmed, then `.disc/boot/result.json` is read from
its card (`guest.py read`) into the report, and the guest is removed in every case.
`tests/conformance/test_installer.py` runs these on a card folder, a 64-block simulated player
and a stand-in for `guest.py`.

The screen's ground fills the terminal's window; the content keeps a readable width. Ctrl-C or
Ctrl-D at a question stops the run like any refusal, with its report.

## The player's console

`python3 console.py` works the player's USB console (the card's `.disc/dev/usb-console` marker,
the cable to this computer): `find` lists the ports, `run` gives each command's output and exit
status (its end found by a marker of that call), `shell` is interactive (Ctrl-] leaves), `send`
puts a file into `/tmp` gzipped in base64 lines and checks its SHA-256 there, and `facts` reads
a summary (firmware, boot status, the keys' word, the watchdog's holder, the input devices,
memory, `/usr/data`, the partitions). It refuses any command that names the serial number, a
MAC address, a token or a raw partition. `tests/conformance/test_console.py` runs it against a
pseudo-terminal whose other end is a shell. The port runs raw at 115200 8N1, and the player's
shell wants each line ended with `\n`.

## The image and its guest

The review image is built offline from the selected firmware's OTA; it never reaches a player from
here. On the computer, without root (2026-10-08; squashfs-tools 4.6 or later, `brew install
squashfs` on macOS, and openssl), with the emulator's reader of FiiO's update fetched at the revision
`firmware/sources/emulator.json` pins (the installer keeps it in `work/downloads/emulator-<rev>`):

```sh
python3 scripts/deployment/build_candidate.py --ota <firmware>/main_os/ota_v<version> \
  --console build/mips/disc-usb-console --boot build/mips/disc-boot --output work/<run>/build \
  --reader work/downloads/emulator-f1d5e33/firmware/tools/firmware_inventory.py
```

Without root the unpacked tree cannot hold stock's owners or setuid bits: the extraction is checked
for kinds, sizes and link targets, and every stock entry's owner, mode bits and time are packed from
stock's own listing (`mksquashfs -pf`, a folder's definition before its entries'), the additions as
root's; the packed image is then held to stock's listing exactly, as in the container. Stock names that
differ only by case are refused (a case-insensitive file system would unpack them over each other).
Built so on macOS with squashfs-tools 4.7.5, 2.57.4's release gave the very image the container builds
(`4f8d68b6…`). In the emulator's image, with its checkout on `PYTHONPATH`:

```sh
bash scripts/build.sh mips
docker run --rm --network none -e PYTHONPATH=/repo -v <emulator>:/repo:ro -v <firmware>/main_os/ota_v<version>:/ota:ro \
  -v "$PWD:/src:ro" -v "$PWD/work/<run>:/out" --entrypoint python3 snowsky-disc-qemu-ci:<revision> \
  -B /src/scripts/deployment/build_candidate.py --ota /ota --console /src/build/mips/disc-usb-console \
  --boot /src/build/mips/disc-boot --output /out/build
```

The same update and the same boot programs give the same image: what is added takes the stock
file system's own time (its superblock's), the stock folders it goes into keep theirs, and the
superblock is packed with that time (`-mkfs-time`); two builds of 2.57.4's release file gave one
image (`4f8d68b6…`).

The builder unpacks, changes and packs stock's tree in a scratch folder of
the container's own file system (`DISC_IMAGE_SCRATCH` names another): a
share from macOS keeps neither owners nor every mode bit, and the first two
images written to the owner's player lost 453 of stock's (setuid of
`/bin/busybox` among them). It refuses a folder whose file system changed
stock's tree on extraction and compares the packed image's own listing
(`unsquashfs -lln`) with stock's: every stock entry exactly (type, every
mode bit, owner, size, link target), nothing but the boot layer's additions.
Both listings go to the output (`stock-listing.txt`, `candidate-listing.txt`)
with the images, `report.json` and `verified-tree`, the packed tree for the
integration tests.

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
python3 scripts/guest.py put --tree <folder>          # with the guest off: a staged card's files over the card
python3 scripts/guest.py read .disc/boot/result.json  # with the guest off: a file of the card
python3 scripts/guest.py power on --hold play         # also reboot, off, cut [--unsynced], status
python3 scripts/guest.py power on --network isolated  # a player without a network (only loopback)
python3 scripts/guest.py status | down
```

`boot_guest.py` is the guest acceptance: about 40 minutes,
since a version is confirmed after 180 s of running. It checks stock's pair
the way the player's `pgrep -x` finds it, by `argv[0]` (the third field of
`cmdline` under qemu-user): `pgrep` in the guest falls back to the process
name and cannot show a program started by its path (contract, "Process
names"). The server repository
drives the same wrapper with a record of its own (`--state`).

`roles_guest.py` (about 20 minutes) accepts the roles of boot API 2 on a fresh
guest: the move of API 1's `service/` to `controller/`, the old server's
update to a controller, two services installed with Play (one failing),
autostart off and stock mode. qemu-user ignores a guest's `RLIMIT_AS`, so a
service's memory bound is recorded there and takes effect on the player:

```sh
python3 scripts/guest.py run -- python3 -B /boot/tests/integration/roles_guest.py --output /work/roles-guest.json
```

`menu_guest.py` drives `disc-menu` on the guest the way a player is driven: Play installs it
with probe UIs, the emulator's buttons and touch panel make the choice, and each screen is read
from the framebuffer page the menu panned to and kept as raw pages in `/work`, turned back 180°
(the emulator serves a static program's screen and input only since `690a55c`, snowsky-disc-qemu
#54, #55). Its steps wait for the UI's confirmation where the next start would otherwise be the
fourth short one, which the boot-loop guard takes. `health_guest.py` and `network_guest.py`
install the services' packages with Play and check them there.

Where the guest differs from the player:

- Its kernel ring is the host's (the container's): the boot log's fatal-signal lines and
  disc-health's counts are only recorded there. A start logs only the last 8 new fatal-signal
  lines and a count (logging and syncing every line held the launcher up by a second).
- Its key device is a file that each new reader reads from the start: a key the menu took would
  be taken again by the player that starts after it, so `menu_guest.py` empties the file once
  the menu has answered. On the player, a later reader sees only later presses.
- Its boot id is the Docker VM's and repeats, while the uptime starts again at each boot: tell
  the boot log's sections apart by the uptime. Files in `/proc` and `/sys` give 0 or a page as
  their size, so they are read to their end. qemu-user ignores `RLIMIT_AS`.
- The emulator has no Wi-Fi, and it puts a guard of its own in `wpa_cli`'s place at each
  power-on: disc-network's tests put a stand-in there after it. Its clock never steps, so
  disc-health's first readings have no time there.
- `boot_guest.py` reads the pair twice, and again until both readings agree, for at most two
  seconds: a child of the pair caught on its way to exec shows in only one of them. The
  emulator's setup primes `sysconfig.db` and waits for the file, not for its table; under load it
  was once killed early (2026-10-05), and a second run went clean.

The two-package acceptance ([contract](boot/contract.md#acceptance-two-independent-packages-owner-2026-10-02))
runs our server beside diskOS's UI. diskOS is built locally from a checkout with its own
toolchain image (GCC 11.2 with musl, hard-float FP64/NaN2008, static; diskOS's UI at `0edcfba`
is 3,945,212 bytes)
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

## Releases

A release is named after the FiiO firmware it is for and our number for it (owner, 2026-10-03):
`2.57.1` and `2.57.2` were recorded but never tagged: the image built from 2.57.1's files looped
on the owner's player, and 2.57.2's menu broke in its first starts
([observations](observations/first-write-observation.md)); `v2.57.3` is the first published
release. A recorded number is never used again, and each repository numbers its own releases (the
boot layer's 2.57.8 offers the server's 2.57.7). A tag goes on only after the release files ran
on the player in a system that stayed up (owner, 2026-10-05), on the commit the release was
recorded from: a later tree may build other bytes, and the release workflow refuses files that
differ from the record.
Its files are built from
`build/mips` by `scripts/release.py`: the boot menu's package `disc-menu-<version>.zip`, the
services' `disc-health-<version>.zip` (from 2.57.7) and `disc-network-<version>.zip` (from 2.57.8),
`disc-boot-<version>-mips.tar.gz` (`disc-boot` and `disc-usb-console` for the image
`install.py` builds from FiiO's update), from 2.57.5 `disc-usb-payloads-<version>.tar.gz` (the
programs the installer runs from the player's RAM in USB Boot: the metadata read, the readback, the
read by digest, the staging check, the identity probe, the restart (from 2.57.6) and the boot
evidence's two, each with its
`build.json`, built with the boot layer's own toolchain, `disc-native-toolchain`, whose compiler gives
diskOS's toolchain's payloads byte for byte; `build.json` names the compiler, not the image's id, so a
build is the same on any computer; diskOS's pinned files are fetched for it unless `--diskos` names
them) and `SHA256SUMS`. The installer takes the payloads from the release by its record's digest and
compiles nothing; with `--boot-build`, or for a release before 2.57.5, it builds them with Docker. The
image is never a release file, and debug builds stay local.

From 2.57.5 a release also holds the installer for users, `disc-installer-<version>.tar.gz`
(`release.py installer`): `install.py` with what it reads from this repository at
the release's commit (the scripts, the profiles, the catalogs, the earlier releases' records, the
payloads' sources and the files the image takes; no tests or documents but the README), this
release's files (the boot programs, the USB payloads, the menu, disc-health and disc-network) and
the catalog's default server and its default apps (`packages/`, found by
their digests with `--from` or downloaded), and `installer.json`, this release as its record will
name it. The record names the archive's digest, so the archive never holds its own record; it is
the same bytes from the same commit and files, and the release workflow builds it again and checks
it like the others. Local changes to its files refuse the build, and so does a catalog that does
not offer each package this release carries by its address and digest, by default: update
`catalog/packages.json` before the archive. The release workflow runs `release.py installer` by
itself, and that step downloads the catalog's default server and apps from their published
addresses: publish the server's release, and any new default app's, before pushing this
repository's tag (2.57.8 was tagged after the server's 2.57.7, 2026-10-10). When a release's
image is built again after a player was written with an earlier build, add the earlier image to
`earlier` in `firmware/images/v<firmware>.json` with its digest and its first blocks' digest;
otherwise the installer does not know what that player holds and offers only the way back
(2.57.7, 2026-10-09/10). Run
from where it was unpacked, `install.py` takes its own packages first and keeps the user's runs
and downloads outside it (macOS: `~/Library/Application Support/SNOWSKY DISC`; Linux:
`$XDG_DATA_HOME` or `~/.local/share`, `snowsky-disc`), so a newer archive replaces the folder
without losing them.

```sh
bash scripts/build.sh mips
python3 scripts/release.py build --version 2.57.5 --output work/release-2.57.5/dist
# catalog/packages.json offers disc-menu-2.57.5.zip by its address and digest (committed), then:
python3 scripts/release.py installer --version 2.57.5 --dist work/release-2.57.5/dist --from <server and apps>
# the guest accepts these files from the unpacked archive (install.py --guest), the player runs them, then:
python3 scripts/release.py record --version 2.57.5 --dist work/release-2.57.5/dist --accepted "<what ran>" \
  --image work/<run>/image   # from 2.57.5: the image the guest ran, so a player holding it is known
```

`record` writes `releases/<version>.json` once; the catalog's entries for the release name its
download address (`https://github.com/eudj1n/snowsky-disc-boot/releases/download/v<version>/<file>`)
with the same digests. The catalog also names snowsky-disc-server's release (`disc-server`, its
address on that repository and the digest its own record keeps) and diskOS's recipe. Pushing the tag `v<version>` runs `.github/workflows/release.yml`: the
synthetic tests, the toolchain from its recipe, the MIPS build, `release.py build`, `installer` and `check`
(the files must be the recorded ones and the catalog must agree), then a draft release with
the files and notes; the owner publishes it. `.github/workflows/ci.yml` builds the same files for
each pull request and each merge into `2.x` (not for a change of documents alone) as a 14-day artifact (`<firmware>.0-ci.<commit>`, never a release). The workflows
use no secrets; their actions are pinned by commit.

## Related repositories

- snowsky-disc-server: the gateway (the `controller` package), its catalogs
  and the emulator wrapper (`scripts/emulator.py`) used for guest acceptance
  today.
- snowsky-disc-web: the frozen history of the gateway and the combined
  images; the running emulator stack still mounts it as `/platform`. This
  repository was ported from it at `faaf502` (2026-10-02): the NAND and USB
  Boot tooling, the USB console and the boot report, the firmware profiles the
  tooling reads, their tests, and the documents of the installation and its
  evidence; the image builder followed, reduced to the boot layer. Links to
  documents that stayed there name snowsky-disc-web; its `docs/plan.md`
  ("Boot layer", "Public installer") keeps the decisions and evidence from
  before 2026-10-02. The default branch here is `2.x`, after FiiO's firmware
  2.x (owner, 2026-10-02).
- snowsky-disc-qemu: the external emulator; never modified from here.
- snowsky-disc-player: the page, a card app of the server.
