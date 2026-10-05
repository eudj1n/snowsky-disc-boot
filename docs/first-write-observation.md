# The first two writes to the owner's player — 2026-10-04/05

Both writes of the boot layer to the owner's player were exact, and both systems restarted
stock's UI without end at their first start. The cause, found 2026-10-05 and reproduced on a
native kernel with the player's BusyBox: the image's wrappers started stock's UI and player by
their paths, and stock's watch loop, which looks for them with BusyBox's `pgrep -x`, never
found them ([The cause](#the-cause)). Runtime evidence stays in the ignored
`work/install-20261004-185637/`, `work/install-20261005-122816/` and `work/first-write/`.

## The first write (image b43034b)

The owner authorized the first write of the boot layer (image b43034b, `6410b080…`, built
2026-10-03 and accepted on the guest: `boot_guest.py` 15 of 15, `two_packages.py` 6 of 6,
`menu_guest.py` and `install.py --guest`) to the player that carried combined-010. The
installer ran in the owner's terminal with the first write's local catalog (the debug server
c502ce6, the menu 2.57.1, the page) and the player's history (combined-010's review, image,
write `7bdf470c` and readback `06cd95a6`).

| Step | Observed |
| --- | --- |
| Installation package | write plan `fcfb053b…`, read plan `45d3a174…`; the proposal changed only the admission and the review pin (`0fbc7b1f…`) |
| Backup | the primary rootfs read in its own session (794 batches), `saved-logical-readback-matches` against combined-010 |
| Write | session `b938abee`, 2026-10-04 14:37:11–15:03:18Z, `writer-completion-observed`, 768 logical blocks through physical block 849, bad blocks 383 and 716, no retries; admission closed |

Leaving USB Boot started the new system. Stock's logo and UI appeared and, a few seconds
later, the logo again, without end, with the card and without it. The power key did nothing,
and Volume Down held with the cable did not reach USB Boot.

The first analysis (2026-10-04/05) found a real defect and took it for the cause: the player's
wrapper marked the player's start with `{ : > /run/disc-boot/player-ran; } 2>/dev/null` before
`exec /usr/bin/mq_player`, and a POSIX shell (BusyBox ash, dash) ends a script on a failed
redirection of a special built-in such as `:`; without a run folder the player never started.
That explanation needed the boot program to have made no run folder on the player, which
nothing showed, and the second write, whose wrappers failed open, looped the same way.

## The way back after the first write

The owner let the battery run down with the cable unplugged. FiiO's own recovery (the update
on a clean card, Volume+ and power held) left a lit, empty screen and did not restore the
player. With the battery empty, Volume Down held while connecting the cable started the boot
ROM's USB Boot (2026-10-05: one `Ingenic USB BOOT DEVICE`, VID 41224, PID 60143).

The way back to stock used the restore plan the installation package approved (stock's
rootfs, `e75d85bd…`, the restore image of every combined installation), through the same
reviewed tools (`work/first-write/restore.py`).

| Step (2026-10-05, one USB Boot entry for the first two) | Observed |
| --- | --- |
| Backup | 794 batches, `saved-logical-readback-matches` against b43034b: the first write had been exact and FiiO's recovery had not touched the rootfs |
| Write, first try | stopped at the re-plan, before the writer, admission closed: `Installation review sources changed` (the image builder had been edited while the package pins it; set aside, the reviewed file back, digest matching the pin) |
| Write | session `e4a19c47`, 05:17:35–05:43:49Z, `writer-completion-observed`, 768 blocks, bad blocks 383 and 716, no retries; admission closed |
| Readback in the same entry | stopped at `SPL DDR diagnostic failed`, nothing read: a session after the writer needs a fresh entry |
| The owner's look | stock started, volume and playback work (05:47:55Z), then the owner re-entered USB Boot |
| Readback | session `2105747b`, 05:48:03–06:26:27Z, all 50,816 records `saved-logical-readback-matches` against stock (`e75d85bd…`) |
| Audits | `saved-restore-write-trace-matches`, `saved-postwrite-trace-matches` (both audits learned the restore target that day) |

## The second write (image fc47ae4)

Image fc47ae4 (`e88ff73b…`, the wrappers failing open, the early hook's exit status in its
log; the boot binaries of b43034b) passed the guest acceptance (`boot_guest.py` 16 of 16 with
stock's pair watched for 45 s after four boots, `two_packages.py` 6 of 6, `menu_guest.py` 3 of
3, `install.py --guest`). The installer ran it from the restore
(`work/install-20261005-122816/`), the card carrying the packages:

| Step (2026-10-05, one USB Boot entry) | Observed |
| --- | --- |
| Backup | session `3b9455ed`, 07:30:18–08:08:02Z, 794 batches, stock (`e75d85bd…`) |
| Read by digest, beside it | session `578a5a3b`, 08:08:03–08:35:25Z, every page agreeing with the full backup and with stock |
| Write | session `46444d85`, 08:35:54–09:01:36Z, `writer-completion-observed`, 768 blocks, bad blocks 383 and 716, no retries; admission closed |

The first start looped as the first write's had: logo, stock's UI, again about every 5 s.
Taking the card out changed nothing. The player was left to run down (about 12 h); the way
back is the restore of the same package (`install.py --restore --run`).

## The cause

Stock's watch loop (`/usr/project/fiio_init.sh`) checks every 5 s with
`pgrep -x mq_ui` and `pgrep -x mq_player` and, missing either, kills both and starts
`mq_ui`, then 2 s later `mq_player`, by name. BusyBox 1.31.1, the player's, matches a
pattern against `argv[0]` first and against the process name only when the pattern is nowhere
in `argv[0]`; with `-x` the match must cover the whole string. Stock's debug mode shows the
same rule: it checks `pgrep -x /usr/data/mq_ui`, a name no process name can hold.

Both images' `/sbin/mq_ui` and `/sbin/mq_player` ended in `exec /usr/bin/mq_ui "$@"` and
`exec /usr/bin/mq_player "$@"`. A shell passes the path it executes as `argv[0]`, so the
running UI was `/usr/bin/mq_ui`: the pattern is in it, the anchored match fails, the process
name is never tried, and `pgrep -x mq_ui` finds nothing. The loop killed the pair and started
it again, forever; the player was killed every few seconds, before it handled the power key.
Nothing of this depends on the card or on the boot program, which is why the card's removal
changed nothing; whether `disc-boot early` made its run folder on the player is still not
known and no longer needed to explain the loop.

Reproduced 2026-10-05 on a native Linux kernel (Docker's VM) with BusyBox 1.31.1
(`busybox:1.31.1`): a loop like stock's over compiled stand-ins named `mq_ui` and
`mq_player`, started through each image's wrappers. With fc47ae4's wrappers every check
restarted the pair (`pgrep -x` found 0 of 2 running processes whose names were right); started
with the bare name the pair stayed and both were found.

**Why the guest did not show it.** Under qemu-user a guest program's `cmdline` begins with
the interpreter (`qemu-mipsel-static`, then the file, then `argv[0]`), so `pgrep` in the guest
never finds the pattern in `argv[0]` and always falls back to the process name, which is right.
The emulator's notes say `pgrep -x` behaves the same on the player and the guest; for programs
started by their path it does not. The guest acceptance now reads `argv[0]` from `cmdline` and
checks the pair the way the player's `pgrep` does: run against fc47ae4 it refuses the image
(`mq_ui` and `mq_player` running, found by neither check of the player's).

**The image builder lost stock's modes.** Found while looking for the cause: both images were
built with the output folder on a macOS share mounted into Docker, which kept neither owners
nor every mode bit, and the builder unpacked, changed and packed stock's tree there. Against
stock's squashfs, 453 entries differed in both images: 292 files `0775` → `0755`, 157 files
`0664` → `0644`, the setuid bit gone from `/bin/busybox` and
`/usr/libexec/dbus-daemon-launch-helper`, `/run/dbus` (1001) and `/var/www` (33) owned by root.
The builder's round trip compared the packed image with that same damaged tree. It is not the
loop's cause (everything that would notice runs as root, `/run` is a tmpfs at runtime, D-Bus
has no activated services), but an image must hold stock exactly. combined-010, built in the
container's own file system, had none of it.

## What changes

- The wrappers start the launcher or stock's program from its own path with `exec -a` and the
  bare name stock's loop looks for (BusyBox ash, the player's shell, has `exec -a`; a shell
  without it starts by the path) (contract, "Process names").
  `tests/conformance/test_wrappers.py` checks the `argv[0]` they give with compiled stand-ins
  under dash, BusyBox 1.31.1 ash and bash in POSIX mode and requires the bare name from BusyBox
  and bash; fc47ae4's wrappers fail it. A first attempt through folders of links named as stock's
  programs gave the right `argv[0]` too, but changed the path the emulator finds stock's player
  by, and the guest's card events stopped: `exec -a` keeps the path.
- A `ui` package's `player` launcher ends in stock's player started as `mq_player`
  (`exec -a mq_player /usr/bin/mq_player`), not by its path alone (contract); the guest's probe
  packages do so.
- `boot_guest.py` checks the pair the way the player's `pgrep` sees it after each steady boot,
  with a ui package and after the menu's hand-over.
- The image builder unpacks, changes and packs in a scratch folder of the container's own file
  system, refuses a folder whose file system changed stock's tree on extraction, and compares
  the packed image's own listing (`unsquashfs -lln`) with stock's: every stock entry exactly,
  nothing but the boot layer's additions. Both listings go to the output.
- A boot log that survives a reset: `/usr/data/disc-boot/boot.log`, each boot's id and
  uptime, the early decision's output, exit status and `boot.json`, each start of the pair by
  the wrappers and whether the boot layer chose a package, the start hook's status; 256 KiB,
  then `boot.log.1`. With stock's own `/usr/data/fiio/log/process_failed.txt` it says what a
  system that never stays up did, once `/usr/data` can be read.
- The next write goes in steps: the fixed image first with no package on the card (the
  console's marker only), checked on the player (stock runs, the power key works, the console
  answers, the boot log), then the packages with Play.
- While a package is in use (from its review to its last audit), the sources it pins are not
  edited; the tools refused the plan, as they should, but the rule is now explicit.
- Release 2.57.1 (`releases/2.57.1.json`) is not tagged: its boot binaries have not run on a
  device in a system that stayed up. The next release takes the next number.
