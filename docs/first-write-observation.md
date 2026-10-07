# The writes of the boot layer to the owner's player — 2026-10-04/05

The first two writes of the boot layer to the owner's player were exact, and both systems
restarted stock's UI without end at their first start. The cause, found 2026-10-05 and
reproduced on a native kernel with the player's BusyBox: the image's wrappers started stock's
UI and player by their paths, and stock's watch loop, which looks for them with BusyBox's
`pgrep -x`, never found them ([The cause](#the-cause)). The third write, with the fix and the
release files, started steadily; the menu then broke twice after the packages' installation
([The third write](#the-third-write-release-2572)). Runtime evidence stays in the ignored
`work/install-20261004-185637/`, `work/install-20261005-122816/`,
`work/install-20261005-222707/`, `work/menu-repro/` and `work/first-write/`.

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
Taking the card out changed nothing. The player was left to run down, then Volume Down with the
cable reached USB Boot, and `install.py --restore --run work/install-20261005-122816` took it
back to stock with the same package (run `install-20261005-180404`):

| Step (2026-10-05) | Observed |
| --- | --- |
| Write, straight away (no readback of the failed image) | session `37846365`, 13:04:13–13:30:10Z, `writer-completion-observed`, 768 blocks, bad blocks 383 and 716, no retries; admission closed |
| The owner's look | stock started, the interface steady, volume, playback and the power key working (13:31:28Z) |
| Readback, a fresh entry | session `8e4fd642`, 13:32:21–14:10:26Z, all 50,816 records `saved-logical-readback-matches` against stock (`e75d85bd…`) |
| Audits | `saved-restore-write-trace-matches`, `saved-postwrite-trace-matches` |

The installer profile keeps that package's review pin (`0e1f262a…`); the player's history for
the next installation is this restore (`work/install-20261005-122816/usb/history.json`).

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

## The third write (release 2.57.2)

The owner authorized the third write on 2026-10-05, with the files of release 2.57.2 (the menu
2.57.2, snowsky-disc-server 2.57.1, the player page 2026.10.02-05a1422), all taken from local
files because nothing was published yet. The installation package started from the restore
(`install-20261005-122816`'s readback and the owner's answer). Before the session, the same
package was prepared offline on the player's real history. `install.py` built its own image
(`0da9a217…`, every stock entry exact) and staged the packages and the page on the card.

| Step (2026-10-05) | Observed |
| --- | --- |
| Backup | session `17:29:31–18:07:49Z`, 794 batches, stock (`e75d85bd…`) |
| Read by digest, beside it | stopped after 12 calls at the second SPL's DDR diagnostic `(0xd1a6c0de, 9, 30, 0x12, 1)`, before any RAM test or NAND access (below) |
| Write, after a fresh entry | session `6df65b78`, 18:10:15–18:36:24Z, `writer-completion-observed`, 768 blocks, bad blocks 383 and 716, no retries; admission closed |
| The owner's look, no key held | steady, the keys and the power key working |
| Readback, a fresh entry | finished 19:17:59Z, all 50,816 records `saved-logical-readback-matches` against `0da9a217…` |
| Audits | `saved-candidate-write-trace-matches`, `saved-postwrite-trace-matches` |

**The boot layer's fix held on the player.** The first start without Play kept stock's pair
steady. The boot log recorded each start of the pair by its bare name, and the console showed
`argv0 mq_ui`, `argv0 mq_player`, with `pgrep -x` finding both.

### The menu after the installation

The owner's next three starts (boot log, stock's `process_failed.txt`):

1. **Play held.** The boot layer installed the menu and the server, stopped stock's UI and ran
   the menu at 14.69 s. A key chose stock, and stock's UI hung on its logo. The pair was never
   restarted.
2. **No key held.** The menu ran at the pair's first start, and the countdown chose stock.
   Stock ran normally.
3. **No key held.** The menu ran at 4.51 s, and a key chose stock after the countdown had
   stopped. From 19.70 s stock's watch loop restarted the pair every 7.1 s
   (`mq_ui Restarting`, the player logging a fresh start each time). Stock's UI died at every
   start until the owner rebooted.

The next three boots ran in stock mode (`boot-loop`). The server stayed tentative until it had
been ready for 180 s, and none of these boots lasted that long, so `unconfirmed` reached 3.
That is the guard working as designed. Nothing that acts on the pair depends on the server's
confirmation: the boot program stops stock's UI only after an installation from the card, and
the menu only when it does not answer within 60 s (`t_menu`, not 30 s as first said).

**Through the console** (read-only, except one write the owner approved):
- The counter was reset to 0 (`state.json`, written atomically).
- The menu came back. While it waited with its countdown stopped, the console never appeared:
  it waits at most 30 s for the card's mount, which stock's player does, and the player waits
  for the menu's choice. The menu's watcher ended it at 60 s, and stock ran.
- In that boot the server became ready, stayed up past 180 s and was confirmed.
- Two more starts did not reproduce the failure: a key pressed at once, then a key pressed about
  ten seconds after the countdown was stopped. In both the menu answered `stock`, and stock's
  pair ran under the names stock's loop looks for.
- 31 samples over 13.5 min showed the same pair, no restart and the server ready.
- Over Wi-Fi, the page on port 7870 and the manager on 7871 answered.

**Not established:** why stock's UI hung, or died at each start, after the menu's choice in
two of three starts. Stock's UI keeps no log. The kernel showed no fault. `/run`, which held the
boot program's state, was gone after the reboot.

### The second SPL of a USB Boot entry

The read by digest failed at the SPL, which brings DDR up before any payload.

**Where it fails.** In diskOS's SPL source (`spl-src/`, GPL-2.0, `ddr_innophy.c` with patch
0003's breadcrumbs), failure 30 is `ddrp_hardware_calibration()`. It starts the PHY's hardware
training and polls `CALIB_DONE` until its low nibble is `0x3`. The poll ended at `0x12`: one of
the two byte lanes never finished training. The same 20 bytes (`86eb682f…`) stopped a readback
after the writer on 2026-09-24.

**Why only a re-run.** Each SPL run repeats the whole bring-up on DRAM that is already running:
the PHY reset through `dfi_reset_n`, the PLL, the DFI initialisation, the mode registers
(MR63 first) and the training. Auto self-refresh is not the cause: the SPL takes it from the
`ginfo` block (`ginfo_w63ah6nkb.bin`), whose `ddr_change_param` is all zero, so it is off.
diskOS's own patch 0008 calls the training's result marginal. Their SPL records a failure but
does not retry it.

| SPL run within a USB Boot entry | Diagnostic |
| --- | --- |
| The first | clean in all 11 recorded |
| A second, after a read | clean 3 times, failed once (this one) |
| A second, after the writer | failed both times |

The plan's remedy is one SPL per entry. A later session skips the SPL when this entry's SPL left
a clean diagnostic and its RAM pattern passes succeed. A session stopped at the diagnostic asks
for a fresh entry and runs again; it never got as far as the NAND. Not filed with diskOS
(owner).

## The fourth write (diagnostics and status links)

The owner wrote the image of boot `ef056a6` on 2026-10-06 (build `ef056a613a3f`, image
`c460b12e…`, accepted on the guest the same day): the third write's diagnostics, the menu's
hand-over in stock's order and the status links. The card took the menu 2.57.2, disc-server
2.57.2 (the player already ran it, updated through the manager) and the player page.

| Step (2026-10-06) | Observed |
| --- | --- |
| Backup | session `352463b7`, 15:37:38–16:15:43Z, `0da9a217…` as the history said |
| Read by digest, beside it | `42a25650`, 16:15:45–16:43:08Z, every page agreeing; each page's CP0 Count ticks 0 |
| Write, same entry | `40df245d`, stopped in 3 s at the third SPL's DDR diagnostic `(9, 30, 0x12, 1)`, before any page or writer: `failed-before-writer`, the player untouched |
| Write, a fresh entry (`install.py --resume`) | `2a00f7cf`, 16:55:56–17:22:08Z, `writer-completion-observed`, a clean diagnostic; admission closed |
| The owner's look, no key held | "it ok": the menu, the choice of a system, its skip and a manual switch |
| Readback, a fresh entry | `9efdc040`, 17:34:24–18:12:43Z, `saved-logical-readback-matches` against `c460b12e…` |
| Audits | `saved-candidate-write-trace-matches`, `saved-postwrite-trace-matches` |

The installer had said to stay connected for the write after the backup. That was the third SPL
of one entry, and the known DDR failure stopped it; the owner lost an hour to a failure known
since the third write. The installer now asks for a fresh entry before each write, and
`--resume` went on from that run's backup (compared again offline, the same package prepared
again). The read by digest no longer goes with an installation: three runs brought page ticks of
zero (plan, stage 4b).

### Play after the write

The status named the new build, the server's page (`service.json`) and no page for diskOS (its
package predates the field). Two starts with Play held (`reason: recovery`) ended about a second
after the menu's first answer, which was diskOS by its countdown (5 s), before stock's player
had run and mounted the card: no installation, the staged menu and server left on the card,
`result.json` from diskOS's installation. The start after each began at a power-on reset
(CPM `RSR` `0x1`, not the watchdog), and the boot log does not reach the disk line by line, so
its last seconds may be lost. The owner's account: the keys held too long. A power key held for
about ten seconds switches the player off in hardware, and a Play still held may answer the
menu. A third start with Play, the first answer stock's UI, installed both packages (40 s),
stopped stock's UI for them, asked again and started diskOS. To do with the faster write
(owner): the boot log synced line by line; the menu takes only a fresh Play press; the
installer says to hold Play until the logo and press the power key briefly; the installation
finishes before the menu offers anything.

## The fifth write (boot release 2.57.3, the faster sessions)

The owner wrote boot release 2.57.3 on 2026-10-07 (build `2506f166236f`, image `69d82c9d…`, the
guest acceptance of PRs #10–#12): the installation with Play before the pair, the menu's
progress and power key, its last answer, and the USB sessions of #15. The day's first tries
stopped before any write: the review refused the asks' setting in the transport profile (#13
moved it out), and the first backup with repeated asks stopped after its first batch (#14, #15).

| Step (2026-10-07, UTC) | Observed |
| --- | --- |
| Backup with repeated asks | `80db9bc4`, from 16:13:58: after the first batch 18 asks of 50 ms timed out, the 19th was answered (967 ms), the next request (the result's address) timed out; nothing written |
| Probe read, one held ask | `c1d49c2a`, 16:35:22–16:35:31, 2 blocks, 297 calls, batches of 185, 957 and 964 ms, no timeout |
| Stage only, no writer | `b805faeb`, 16:37:29–16:43:10, 9,447 calls, no timeout: the image region's check 111 s, the 1 MiB sample's hash 4.2 s |
| Backup | `2130dde8`, 16:52:19–17:16:30 (24 min 11 s; 38 before), 794 batches, median 959 ms, the history's image |
| Write, a fresh entry | `5096608b`, 17:18:09–17:27:56 (9 min 47 s; 26 before): region check 110.7 s, sample hash 4.24 s, the writer 245.7 s (measured, not a blind 15 min); 768 blocks, bad blocks 383 and 716, no retry |
| The owner's look | "it is ok": the new build ran, the menu and the server 2.57.3; Play installed the menu 2.57.3 and the server again (below) |
| Readback, a fresh entry | `dae42fc7`, 17:48:51–18:12:54 (24 min 3 s), `saved-logical-readback-matches` against `69d82c9d…` |
| Audits | `saved-candidate-write-trace-matches`, `saved-postwrite-trace-matches` |

The installation's three sessions took 58 minutes on the player against about 1 h 45 min.

### The first starts after it

The boot log over the USB console told the rest. The first start with Play (`9fd15eab`) mounted
the exFAT card itself and installed disc-server 2.57.3 and disc-menu 2.57.3 in 1.2 s, while the
menu installed before (2.57.2), which knows nothing of the installation, was on the screen. The
server came from the day's first try, which had staged it and stopped at its review; the later
runs chose only the menu but left it on the card. Installing the server that ran already made
it tentative, and the boot-loop count clears only at a confirmation, three minutes into every
start: six quick restarts in search of the menu reached five and the player started in stock
(`boot-loop`). The old menu's answer at the first start, stock, had become the default UI, and
the release of the recovery's Play, reported by stock's key driver when the key is let go
(`0x10d`, `0xfa`), answered the new menu's default. A start with Play and three minutes of
running brought the menu back. The fixes are on branch `next-image` (plan, stage 4c).
