# Changelog

What changes for you in the DISC boot layer, release by release. A release is
named after FiiO's firmware and our number for it: 2.57.5 is the fifth boot
layer for FiiO's 2.57. Each published release is a `v<version>` tag with its
files on the [releases page](https://github.com/eudj1n/snowsky-disc-boot/releases).
Entries stay short; how and why it was done is in the [plan](docs/plan.md).

## [2.57.5] — unreleased

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
