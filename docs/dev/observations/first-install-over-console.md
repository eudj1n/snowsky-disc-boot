# The first installation over the console — 2026-10-19

The owner's second DISC (HeYangTek HYF2GQ4U, [its acceptance](second-player-acceptance.md))
took the first installation whose packages go in over the USB console instead
of a start with Play ([restart](../usb-boot/restart.md#in-the-installer),
`Installer.install_over_console`, branch `packages-over-console`, PR #63).
One run of `install.py --boot-build` from that branch, the image and the
payloads built on the owner's computer; run `install-20261019-222715`, its
evidence in `work/install-20261019-222715/` (ignored).

## The state before

The player held release 2.57.10 with its packages. To reach a true first
installation the owner chose "Everything ours" in the menu's Packages screen
and restarted the player once: boot removed `/usr/data/disc-boot` and the
card's `.disc` at that start, and the player started stock's interface. This
is the first time that removal ran on a player (it had run on the guest);
nothing else on the card was touched. The player still held our rootfs, so
the installer's review found a known boot layer and admitted the write
without a way back to stock first.

## The run

| Step | Outcome |
| --- | --- |
| Card | disc-menu 2.57.10, disc-server 2.57.8, disc-health 2.57.10, disc-network 2.57.10 staged, Disc Player 1.0.0 in `Apps/`, the console's marker written |
| Player | Chip `hyf2gq4u`; the review admitted the write; the image (build `8eb15a998d85`, the same bytes as 2.57.10's) written once (session `37058f8d-dc8b-4102-b9e2-90304320d2c1`); the restart payload ran and the player left the bus (17:40:24 UTC) |
| First start's check | Fetched over the console 33 s after the player left the bus; `/dev/mtdblock_bbt_ro2` matched the image (100,663,296 bytes, 18.3 s on the player) |
| Packages | Four `disc-boot install` calls over the console, all answered `ok`: controller (`installed disc-server 2.57.8`), menu, service/disc-health, service/disc-network; the card's `result.json` read; all four within 5 s of the check |
| Marker and restart | The line `sync; rm -f /tmp/sdcard/.disc/dev/usb-console; reboot` sent; the port left the computer within 5 s (`player-restarted`); the console's revocation did not get ahead of `reboot` |
| First boot | The installer's screen named the four folders and said the player was restarting with them; `report.json`: `packages.outcome` `installed`, `first boot.packages` `installed` |

The whole console phase, from the player's leaving the bus after the write to
its leaving again with the packages installed, took 43 s against the
console's 900 s session; nothing came near a budget. The player started with
the menu afterwards (the owner's look).

## What it settles

- The first installation needs no Play at power-on: what the user chose on
  the computer is installed by the installer's own session.
- `disc-boot install` over the console behaves as from the menu: the answers
  and `result.json` are the menu's.
- Removing the marker and `reboot` in one line is safe on the player: the
  console's session loop (100 ms ticks) did not stop the shell before
  `reboot` ran. The installer still treats a port that stays as
  `restart-not-observed` and tells the user to restart by hand.
- "Everything ours" works on a player: the data and the card's `.disc` go at
  the next start, stock's interface comes up, and the installer can write the
  player again from that state.
