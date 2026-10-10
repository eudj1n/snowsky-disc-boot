# Changelog

What changes for you in the DISC boot layer, release by release. A release is
named after FiiO's firmware and our number for it: 2.57.5 is the fifth boot
layer for FiiO's 2.57. Each published release is a `v<version>` tag with its
files on the [releases page](https://github.com/eudj1n/snowsky-disc-boot/releases).
Entries stay short; how and why it was done is in the [plan](docs/dev/plan.md).

## [2.57.8] — in preparation

- disc-network, offered by default: the player remembers up to 8 Wi-Fi
  networks it joined. When the network FiiO's interface keeps is out of reach,
  it puts a remembered one in range in its place, so the player joins it
  without asking the password again. FiiO's interface keeps one network, as
  it expects. The passwords stay on the player; a reset of the player clears
  them too.
- `install.py --packages` updates the packages without USB Boot: it puts them
  on the card, and the boot menu installs them.
- The server 2.57.6 by default: its page lists the services beside it and
  FiiO's own interface with the firmware's version.
- The installer and the guide say what is true since the boot menu's
  Packages screen: once the menu is installed, it offers the packages on the
  card at a start; Play at power-on is needed only the first time.

## [2.57.7] — 2026-10-09

- Besides the server, the player can run services: small background programs,
  each started and watched on its own. A service that fails stops alone and
  never sends the player back to FiiO's interface. The server you installed
  moves to its new place at the first start by itself: nothing to reinstall.
- disc-health, the first service, offered ticked by the installer: every 10
  minutes it notes the battery and its temperature, the free space, card errors
  and crashes, keeps a few days of them on the player and reports the latest
  for the server's diagnostics. It works offline and changes nothing.
- The boot menu has two more screens, at the end of its list: Services turns
  each service on or off, and Packages installs what waits on the card and
  removes an interface or a service with its data, or everything of ours
  before the player changes hands. While a package waits on the card, the
  menu waits for you instead of counting down.
- The installer's report says whether the player restarted after the write.
- The boot menu waits while you use it: a minute after your last key, not
  after it appeared.
- A service or the server installed from the menu starts at once, in its
  new version, and is kept only once that version has run for three
  minutes.

## [2.57.6] — 2026-10-09

- After writing the player, the installer restarts it into the new system
  itself: the cable stays connected, and you only answer whether it started
  normally.
- The boot menu lists FiiO's own interface first, then the interfaces you
  installed.

## [2.57.5] — 2026-10-09

### One archive to install from

- Download `disc-installer-2.57.5.tar.gz`, unpack it and run
  `python3 install.py`: nothing to clone and no Docker. It brings the menu,
  [DISC server](https://github.com/eudj1n/snowsky-disc-server) and
  [Disc Player](https://github.com/eudj1n/snowsky-disc-player), and keeps its
  runs in your user folder, so a newer archive replaces the old one.
- The new system is built on your computer from FiiO's update with
  squashfs-tools and openssl.
- The installer reads the player in the same connection it writes in, knows
  what it holds (FiiO's own system or one of these releases) and writes
  nothing to a player it does not know. It keeps no history between runs.
- You answer only yes or no about the first start; the new system checks the
  written image itself.

### The way back

- FiiO's own update (Settings → System updates → Local upgrade, its zip at
  the card's root) returns the player to stock, checked on a player.
- `python3 install.py --restore` returns it through USB Boot from any state,
  even after a write cut short (you type `STOCK` first).

The boot layer's programs are 2.57.4's, the same build.

## [2.57.4] — 2026-10-08

- The installation needs one connection in USB Boot: a short check of the
  player's first blocks instead of a full backup, and the write right after
  it, about 15 minutes instead of an hour.
- The new system checks the written image at its first start (its system
  partition, every byte by its SHA-256), in place of reading it back.
- A start with Play leaves a package that already runs as it is, and the
  release of Play no longer answers the menu.
- After an update, the count of failed starts clears as soon as the packages
  are ready, not three minutes later.

## [2.57.3] — 2026-10-07

The first published release.

- The boot layer in FiiO's own system, built on your computer from FiiO's
  update: packages installed from the card with a start with Play, each
  checked before it runs, and the previous version kept for a failed start.
- The boot menu: with several interfaces, the player asks at power-on which
  one to start (keys or touch), FiiO's own among them, and starts your last
  answer after its countdown; it shows the installation from the card.
- Volume Up at power-on starts FiiO's interface for that start; three failed
  starts in a row bring it back by themselves.
