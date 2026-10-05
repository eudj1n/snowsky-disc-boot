# The first write to the owner's player — 2026-10-04/05

The owner authorized the first write of the boot layer (image b43034b, `6410b080…`, built
2026-10-03 and accepted on the guest: `boot_guest.py` 15 of 15, `two_packages.py` 6 of 6,
`menu_guest.py` and `install.py --guest`) to the player that carried combined-010. The
installer ran in the owner's terminal with the first write's local catalog (the debug server
c502ce6, the menu 2.57.1, the page) and the player's history (combined-010's review, image,
write `7bdf470c` and readback `06cd95a6`). Runtime evidence stays in the ignored
`work/install-20261004-185637/` and `work/first-write/`.

## The sessions

| Step | Observed |
| --- | --- |
| Installation package | write plan `fcfb053b…`, read plan `45d3a174…`; the proposal changed only the admission and the review pin (`0fbc7b1f…`) |
| Backup | the primary rootfs read in its own session (794 batches), `saved-logical-readback-matches` against combined-010 |
| Write | session `b938abee`, 2026-10-04 14:37:11–15:03:18Z, `writer-completion-observed`, 768 logical blocks through physical block 849, bad blocks 383 and 716, no retries; admission closed |

## The new system's first start

Leaving USB Boot started the new system. Stock's logo and UI appeared and, a few seconds
later, the logo again, without end, with the card and without it. The power key did nothing,
and Volume Down held with the cable did not reach USB Boot.

What it was, reconstructed offline and reproduced on the guest (2026-10-04, the same image):
the system did not reboot. Stock's player never started, stock's watch loop
(`/usr/project/fiio_init.sh`) found no `mq_player` every 5 s, killed the UI and started the
pair again; the logo was the UI's own splash. The power key is handled by stock's player, and
without a reset the boot ROM never looks at Volume Down. On the guest, with `/run/disc-boot`
removed and the player stopped, the loop logged `mq_player Restarting` every 7–8 s, the UI got
a new process each time and no player came back.

- **The wrapper did not fail open.** `/sbin/mq_player` marked the player's start with
  `{ : > /run/disc-boot/player-ran; } 2>/dev/null` before `exec /usr/bin/mq_player`. A POSIX
  shell (BusyBox ash on the player, dash) ends a script on a failed redirection of a special
  built-in such as `:`: without the run folder the wrapper ended there and the player never
  started. The contract asked the wrappers to start stock on any failure of the boot program;
  this line broke it. The same construct had failed the tests on GitHub's Linux runner the day
  before (`1662230`), where it was fixed in the tests only.
- **The boot program made no run folder on the player.** `disc-boot early` (S22) creates
  `/run/disc-boot` first thing; `/run` is a tmpfs mounted at sysinit and no stock script
  clears or remounts it. Why it did not run, or did not get that far, on the device is not
  known: its log was in `/run`, and the player could not be reached. Open until an image whose
  wrappers fail open boots on the player with its console.
- **Why the guest did not see it.** On the guest the boot program always ran, so the wrapper's
  line never failed; its watchdog is a stub; and no step watched the pair over time.

## The way back

The owner let the battery run down with the cable unplugged. FiiO's own recovery (the update
on a clean card, Volume+ and power held) left a lit, empty screen and did not restore the
player. With the battery empty, Volume Down held while connecting the cable started the boot
ROM's USB Boot (2026-10-05: one `Ingenic USB BOOT DEVICE`, VID 41224, PID 60143).

The way back to stock uses the restore plan the installation package approved (stock's
rootfs, `e75d85bd…`, the restore image of every combined installation), through the same
reviewed tools, in three sessions: a backup of what the player holds, the write with the
admission for one call, then the owner's look at stock's start and a readback compared with
stock (`work/first-write/restore.py`).

| Step (2026-10-05, one USB Boot entry for the first two) | Observed |
| --- | --- |
| Backup | 794 batches, `saved-logical-readback-matches` against b43034b: the first write had been exact and FiiO's recovery had not touched the rootfs |
| Write, first try | stopped at the re-plan, before the writer, admission closed: `Installation review sources changed` (the image builder had been edited for the fix below while the package pins it; set aside, the reviewed file back, digest matching the pin) |
| Write | session `e4a19c47`, 05:17:35–05:43:49Z, `writer-completion-observed`, 768 blocks, bad blocks 383 and 716, no retries; admission closed |
| Readback in the same entry | stopped at `SPL DDR diagnostic failed`, nothing read: a session after the writer needs a fresh entry |
| The owner's look | stock started, volume and playback work (05:47:55Z), then the owner re-entered USB Boot |
| Readback | session `2105747b`, 05:48:03–06:26:27Z, all 50,816 records `saved-logical-readback-matches` against stock (`e75d85bd…`) |
| Audits | `saved-restore-write-trace-matches`, `saved-postwrite-trace-matches` (both audits learned the restore target that day: they had read only the candidate's plan and image) |

The player runs stock 2.57 again. Its history for the next installation is the restore
(`work/player-history.json`).

## What changes

- Both wrappers fail open: before the `exec` of stock's binary only plain commands whose
  failure is ignored; the boot layer's branch is taken only on `ui-launch`, written by the boot
  program in that very boot. `tests/conformance/test_wrappers.py` runs the image's wrappers
  under dash, BusyBox ash where present and bash in POSIX mode with no run folder, a run folder
  that cannot be written and a launcher that is missing; the old line fails it.
- The guest acceptance watches stock's pair for 45 s after each boot that ends in stock's UI or
  a running service (no restart in stock's log, the same processes), and boots once with the
  boot program unable to run.
- The next write goes in steps: the fixed image first with no package on the card (the
  console's marker only), checked on the player (stock runs, the power key works, the
  console answers, the boot program's log), then the packages with Play.
- While a package is in use (from its review to its last audit), the sources it pins are not
  edited; the tools refused the plan, as they should, but the rule is now explicit.
- Release 2.57.1 (`releases/2.57.1.json`) is not tagged: its boot binaries have not run on a
  device. The next release takes the next number.
