# disc-network

Several Wi-Fi networks for the player (owner, 2026-10-08/09): a
service of boot API 2 ([contract](../boot/contract.md#roles)) that keeps the networks
stock connected to and, when stock's network is out of reach, puts a kept one
in range in its place in stock's wpa_supplicant, so the player joins whichever
is in range without its password again. Source: `device/network/network.c`;
its package (`disc-network-<version>.zip`, a memory bound of 32 MiB: it runs
stock's dynamic `wpa_cli`) is a release file from 2.57.8, offered ticked, after
this design ran on the owner's player (2026-10-10).

## Why stock keeps one

From stock's programs (V2.57, wpa_supplicant 2.9): its connection
(`connect_wifi`) asks wpa_supplicant to `remove_network all` before it adds the
new network, selects it and saves, so `/usr/data/wpa_supplicant.conf` holds
one network; its Wi-Fi screen asks the password every time;
`fix_wpa_conf_ssid` rewrites the file's first `ssid=` line through a 4 KiB
buffer, so stock's network stays first. Its "Forget" and "Reconnect" send
nothing to wpa_supplicant.

## On the owner's player (2026-10-09)

Release 2.57.7 carried this design. With the networks given back (two in
stock's configuration), the player switched by itself between them, but stock's
UI (`mq_ui`) crashed twice while a password was being typed in its Wi-Fi
screen (SIGSEGV; stock's loop restarted the UI and the player): in the cJSON
code that builds its list of networks, a string pointer held the bytes
`EF 87 AB`, LVGL's Wi-Fi symbol (U+F1EB), so it took a wrong element of its
list. In about 35 earlier boots with one saved network, password entry
included, it never crashed. Stock's UI is written for the one network stock
keeps. So disc-network is no release file of 2.57.7 and was turned off on the
player (its second network removed); its next version keeps one network in
stock's configuration and changes it itself (owner, 2026-10-09).

## On the owner's player (2026-10-10)

With one network in stock's configuration, stock joined a new network with
its password typed in its Wi-Fi screen, and its UI ran on; the configuration
held one network throughout. When that network went away, disc-network put a
kept one in range in its place 36 s later (a minute after stock's
connection), and the player joined it.

## On the owner's player (2026-10-11)

Release 2.57.9's packages run on the player: the menu, disc-health and
disc-network 2.57.9 beside the server 2.57.8 as the controller, on the image
`9e6448dd…` (build `8eb15a998d85`), as the server's `/api/about` reported
them; disc-network connected, its report naming two kept networks, each with
its id. The owner reported that everything works correctly.

## What it does

While Wi-Fi is on (stock's wpa_supplicant answers on
`/var/run/wpa_supplicant/wlan0`), every 5 s, through `wpa_cli -i wlan0 --`
(the `--` ends wpa_cli's options: glibc's getopt takes any later argument that
begins with `-` for one):

- **When it acts.** Only when wpa_supplicant's networks (`list_networks`) are
  those the file saves, in the same order, and both stayed so for two looks,
  and not while a connection is being made: never in the middle of stock's own
  sequence (remove all, add, set, select, save).
- **It keeps** the network stock is connected to: its block of the file
  (`ssid`, `psk`, `key_mgmt`, `scan_ssid`) as wpa_supplicant wrote it, in its
  store `$DISC_BOOT_DATA/networks.json` (mode 0600, on the player only), at
  most 8, the least recently used dropped first. A network with other settings
  (EAP and the like) is not kept.
- **Stock's configuration keeps one network.** When Wi-Fi has had no
  connection for 20 s, the configuration last changed at least a minute ago
  (stock's connection: a network the owner has just chosen gets its time), and
  a kept network other than stock's is in range (`scan_results`; it asks a
  `scan` at most every 30 s while it waits), the strongest of them takes the
  place of stock's network by stock's own sequence through wpa_supplicant:
  `remove_network all`, `add_network`, `set_network` (`ssid`, `psk` or
  `key_mgmt`, `scan_ssid`), `select_network`, `save_config`. One that does not
  connect within a minute after that waits 10 minutes. It never edits the file
  (owner's decisions of 2026-10-09: the one exception to "a package does not
  change FiiO's files", through stock's own wpa_supplicant only).
- **What an earlier version added** (2.57.7 gave the kept networks back beside
  stock's) goes: kept networks beside stock's one are removed, all but the
  connected one or else the first.
- **It forgets** its networks when Wi-Fi comes on with a file that holds no
  network (stock's reset; `S43wifi` writes a new one; owner, 2026-10-08:
  before the player is sold, its networks go with the rest of its settings);
  a file emptied while Wi-Fi is on gets one back as above. Removing
  disc-network with its data (the menu's Packages screen), or everything
  ours, forgets them all; the menu forgets one (below).

`$DISC_BOOT_RUN/status.json` (at most 4 KiB): `{"schema": 1, "wifi": "off" |
"searching" | "connecting" | "connected", "connected": <name> | null,
"networks": [{"id", "name", "open", "held", "inRange"}], "lastChange": {"what",
"t"} | null}`: `id` 8 hex digits, FNV-1a over the SSID's bytes, `held` when it
is the network in stock's configuration, `inRange` when the last scan heard
it; names only, never a key (a PSK in hex works as the password, so neither
form may appear).

## Forgetting one network

The boot menu's Services screen lists the networks of the report under "Wi-Fi
networks" and, on a second Play, asks disc-network to forget one (owner,
2026-10-11): it writes `{"forget": "<id>"}` to `$DISC_BOOT_RUN/forget.json`.
disc-network takes the ask at once (it looks for the file each second between
its looks), removes the file, and forgets the one kept network with that id,
if there is exactly one:

- it leaves the store at once (`lastChange` "forgot <name>");
- when stock's configuration holds it, it leaves that too, by stock's own
  sequence, at the next look where Wi-Fi is on and steady: another kept
  network takes its place (the strongest in range, else the most recently
  used), or, with none kept, `remove_network all` and `save_config`. Until
  then it waits in the store's `forget` list, so Wi-Fi off at the time or a
  restart loses nothing; the file itself is never edited.

The menu shows the font's ASCII and a question mark for any other character
of a name; the id, not the name, says which network goes.

## Tests

`tests/integration/wpa_cli_stand_in.sh` stands in for stock's `wpa_cli` and
wpa_supplicant over files (the commands disc-network and stock use, options
parsed as glibc's getopt does, saving the file as wpa_supplicant 2.9 does, a
range with signals, and the tests' own `_up`, `_down`, `_stock_connect`,
`_range`, `_fail`). `tests/conformance/test_network.py` runs the fixture build
(`-DDISC_NETWORK_FIXTURE`: the player under `DISC_BOOT_FIXTURE_ROOT`,
`DISC_NETWORK_INTERVAL`, the looks above scaled to it) against it;
`tests/integration/network_guest.py` puts it in the guest's tree in stock's
`wpa_cli` place for the run (the emulator has no Wi-Fi), installs the
package and the menu with Play, and at a later start forgets a network from
the menu's Wi-Fi networks screen with the emulator's buttons. The player is the acceptance that counts: two networks
joined in turn, each joined again by itself when the other is out of reach,
and a password typed in stock's Wi-Fi screen without its UI failing.
