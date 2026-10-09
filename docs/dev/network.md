# disc-network

Several Wi-Fi networks for the player (plan, stage 7; owner, 2026-10-08/09): a
service of boot API 2 ([contract](contract.md#roles)) that keeps the networks
stock connected to and gives them back to stock's wpa_supplicant, so the player
joins whichever is in range without its password again. Source:
`device/network/network.c`; its package is a release file
(`disc-network-<version>.zip`, a memory bound of 32 MiB: it runs stock's
dynamic `wpa_cli`), named by the catalog but not ticked until it has run on
the owner's player.

## Why stock keeps one

From stock's programs (V2.57, wpa_supplicant 2.9): its connection
(`connect_wifi`) asks wpa_supplicant to `remove_network all` before it adds the
new network, selects it and saves, so `/usr/data/wpa_supplicant.conf` holds
one network; its Wi-Fi screen asks the password every time;
`fix_wpa_conf_ssid` rewrites the file's first `ssid=` line through a 4 KiB
buffer, so stock's network stays first.

## What it does

While Wi-Fi is on (stock's wpa_supplicant answers on
`/var/run/wpa_supplicant/wlan0`), every 5 s, through `wpa_cli -i wlan0`:

- **When it acts.** Only when wpa_supplicant's networks (`list_networks`) are
  those the file saves, in the same order, and both stayed so for two looks,
  and not while a connection is being made: never in the middle of stock's own
  sequence (remove all, add, set, select, save), which would put another
  network first in the file.
- **It keeps** the network stock is connected to: its block of the file
  (`ssid`, `psk`, `key_mgmt`, `scan_ssid`) as wpa_supplicant wrote it, in its
  store `$DISC_BOOT_DATA/networks.json` (mode 0600, on the player only), at
  most 8, the least recently used dropped first. A network with other settings
  (EAP and the like) is not kept.
- **It adds back** each kept network wpa_supplicant does not hold, after
  stock's own and below it (`add_network`, `set_network` with `priority=-1`,
  `enable_network`, then `save_config`), and enables a kept one that stock's
  selection disabled. It never edits the file and never interrupts the
  connection (owner's decision of 2026-10-09: the one exception to "a package
  does not change FiiO's files", through stock's own wpa_supplicant only).
- **It forgets** its networks when Wi-Fi comes on with a file that holds no
  network (stock's reset; `S43wifi` writes a new one); a file emptied while
  Wi-Fi is on gets them back. Forgetting one network comes with the menu's
  services screen; until then, removing the boot layer's packages and data
  removes them.

`$DISC_BOOT_RUN/status.json` (at most 4 KiB): `{"schema": 1, "wifi": "off" |
"searching" | "connecting" | "connected", "connected": <name> | null,
"networks": [{"name", "open", "held"}], "lastChange": {"what", "t"} | null}`:
names only, never a key.

## Tests

`tests/integration/wpa_cli_stand_in.sh` stands in for stock's `wpa_cli` and
wpa_supplicant over files (the commands disc-network and stock use, saving the
file as wpa_supplicant 2.9 does, and the tests' own `_up`, `_down`,
`_stock_connect`, `_scan`). `tests/conformance/test_network.py` runs the
fixture build (`-DDISC_NETWORK_FIXTURE`: the player under
`DISC_BOOT_FIXTURE_ROOT`, `DISC_NETWORK_INTERVAL`) against it;
`tests/integration/network_guest.py` puts it in the guest's tree in stock's
`wpa_cli` place for the run (the emulator has no Wi-Fi) and installs the
release's package with Play. The player is the acceptance that counts: two
networks joined in turn, then the player joining either without its password.
