# The player restarted from USB Boot

The installer leaves USB Boot by itself (owner, 2026-10-09: FiiO's
update restarts the player itself): after the write, in the same entry, a small
program run from the player's RAM starts the watchdog, the chip restarts and,
Volume Down no longer held, starts the system it holds from NAND with the cable
still connected. The installer then reads the new system's own check over its USB
console, so the user neither unplugs nor plugs the cable.

## The payload

`device/usbboot/restart.c`, built as `build_identity.py --mode restart` (344 bytes;
memory and the TCU's registers only, no SFC or NAND code), writes the sequence of
the pinned SPL source's `_machine_restart` (diskOS's u-boot-xburst,
`arch/mips/cpu/xburst2/cpu.c` with `asm/arch-x2000/wdt.h` and `base.h`):

| Register (kseg1) | Value | Meaning |
| --- | --- | --- |
| `0xb000203c` TCU `TSCR` | `0x10000` | the watchdog's clock no longer stopped |
| `0xb0002008` WDT `TCNT` | `0` | the counter from zero |
| `0xb0002000` WDT `TDR` | `2` | 32768 / 64 × 4 ms (`RESET_DELAY_MS`) |
| `0xb000200c` WDT `TCSR` | `0x1a` | prescale 64, the RTC's 32768 Hz |
| `0xb0002004` WDT `TCER` | `0`, then `1` | enabled: the reset about 4 ms later |

Then it waits until CP0 Count has advanced 2^28 ticks (a fraction of a second at
the SPL's clocks) or the loop has turned 2^26 times, whichever comes first: Count
stands still in the payloads' context on the player (the read by digest found
every page's ticks to be 0: [rootfs
collector](../nand/rootfs-collector.md#the-read-by-digest-rootfs-digest)), so the turns bound
the wait on their own there, a fraction of a second to some seconds (owner,
2026-10-11). Should no restart come, it stops the watchdog and returns to the ROM, so
the player is never left looping and the host can tell (`device/tests/restart_test.c`
checks the sequence and the wait with a running and a standing Count; the other
payloads' bytes do not change with it).

## The session and its audit

```sh
python3 scripts/deployment/build_identity.py --mode restart --diskos <diskOS files> --output build/restart
python3 scripts/deployment/restart_player.py plan --build build/restart --diskos <diskOS files> > plan.json
python3 scripts/deployment/restart_player.py acquire --build build/restart --diskos <diskOS files> \
  --libusb <libusb> --approved-plan-sha256 <plan.json's> --output work/<run>
python3 scripts/deployment/audit_usb_restart.py --run work/<run> --plan plan.json --build build/restart \
  --diskos <diskOS files> --output work/<run>/offline-review.json
```

The session reads the DDR diagnostic (the SPL runs only in a fresh entry), uploads and
compares the payload, executes it and holds one request for the ROM: the ROM leaving
the bus is `player-restarted` (the interface then cannot be released, which is no
failure; the system's start and its USB console are the proof), the ROM answering is
`restart-not-observed`, silence is `restart-uncertain`. The audit reconstructs every
call, the held request's outcome included (`usb_trace_audit.ask`). `test_restart_player`
runs the three outcomes on a fake ROM.

## On the player

2026-10-09, the session alone, without a write, by the owner from another computer
(the branch's clone, the payload's build sent to it, plan `c5937231…` computed there
the same): a fresh entry, so the SPL once; the payload compared in RAM and executed;
the held request ended with an I/O error, the ROM gone from the bus
(`player-restarted`, 23 calls, 2.1 s, its audit matching there and here). The player
started its system by itself with the cable connected: the menu, then stock.

## In the installer

After every write (the candidate's and both ways back to stock) the installer runs the
session in the write's entry (`usbboot.Reviewed.restart`, the payload from the release
or built with the others) and audits it. When the player left the bus it says so and
the cable stays connected: the first start's check is read over that cable, and the
user is asked about the start only when it does not come back. With the check in,
the packages the card step staged go in over the same console (owner, 2026-10-19;
`Installer.install_over_console`): `/opt/disc-boot/disc-boot install <folder>` for
each, controller, menu, services, UIs, the menu's own command with its `{"ok",
"note"}` answer, the card's `result.json` kept in the run's `packages/` folder, then
one line that removes the console's marker from the card and runs `reboot` (the
console revokes its session as soon as the marker is gone, so no answer comes
back: the port's leaving the computer within 90 s is the restart's proof). The
report's `packages` step holds the outcome (`installed`, `partly installed`, `no
console`, `console lost` with the error, `nothing staged`), each folder's answer,
`marker` and `restart`; the first-boot screen names what went in and, when the
console did not answer, keeps the start with Play. Any other outcome, or a release without the payload, is
the cable as before (unplug, then plug while the new system runs); the write stands
either way and nothing is retried (`test_usbboot`). The run's `report.json` names
the outcome in the player's step: its `restart` field holds `outcome`
(`player-restarted`, `restart-not-observed`, `restart-uncertain`, `failed` with
`why`, or `not available` for a release without the payload) and the session's
folder.
