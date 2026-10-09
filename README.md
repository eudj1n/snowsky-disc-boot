# SNOWSKY DISC boot layer

**A small base layer for your SNOWSKY DISC: it lets the player run new
interfaces and services next to FiiO's own software, installs them from the
memory card, and always lets you go back.**

An unofficial project, not affiliated with FiiO or SNOWSKY. Installing it
writes the player's system memory over USB; read [the risk](#the-risk) and
[the way back](#the-way-back-to-stock) before you start.

[What you get](#what-you-get) · [What you need](#what-you-need) ·
[Install](#install) · [The risk](#the-risk) ·
[The way back](#the-way-back-to-stock) · [The keys at power-on](#the-keys-at-power-on) ·
[Releases](https://github.com/eudj1n/snowsky-disc-boot/releases)

<p align="center">
  <img src="docs/assets/menu/choose.png" width="240" alt="The boot menu on the player's round screen: Start with diskOS 1.2.0 or FiiO 2.57, starting in 4 seconds">
  &nbsp;&nbsp;
  <img src="docs/assets/menu/installing.png" width="240" alt="The boot menu installing disc-menu from the card, 2 of 3">
</p>

_The boot menu, drawn by its own code: choosing the interface at power-on,
and installing packages from the card._

## What you get

| | |
| --- | --- |
| **FiiO's player, unchanged** | The image keeps FiiO's own system and adds only the boot layer; music, settings and the library stay as they are. |
| **Packages from the card** | Interfaces, a menu and services come as packages on the memory card; a start with Play installs them, each checked before it runs. |
| **The boot menu** | With more than one interface installed, the player asks at power-on which one to start, FiiO's own among them. |
| **[DISC server](https://github.com/eudj1n/snowsky-disc-server)** | Offered by default: the player serves [Disc Player](https://github.com/eudj1n/snowsky-disc-player) and other web apps over its Wi-Fi. |
| **Safe starts** | A package that fails at its start gives way to the previous one by itself; three failed starts in a row bring FiiO's interface back. |
| **A way back, always** | FiiO's own update, or the installer through USB Boot, returns the player to stock. |

## What you need

- A SNOWSKY DISC with FiiO's firmware **2.57** (Settings → About), and its
  battery charged to more than 30%.
- FiiO's 2.57 update for the player (the `SNOWSKY_DISC_update_…_v257.zip`
  from FiiO), unpacked into a folder on the computer. The installer builds the
  new system from it, on your computer; FiiO's files are never part of a
  release.
- A computer with macOS or Linux and:
  - Python 3.11 or later;
  - libusb (macOS: `brew install libusb`; Debian or Ubuntu:
    `apt install libusb-1.0-0`);
  - squashfs-tools 4.6 or later and openssl (macOS:
    `brew install squashfs openssl`; Debian or Ubuntu:
    `apt install squashfs-tools openssl`).

  The installer checks each of them first and names what is missing.
- A USB cable and a card reader for the player's memory card.

## Install

1. Download `disc-installer-<version>.tar.gz` from the
   [latest release](https://github.com/eudj1n/snowsky-disc-boot/releases),
   unpack it and, in a terminal, in its folder:

   ```sh
   python3 install.py
   ```

2. The installer asks for the folder of FiiO's update and builds the new
   system (about a minute). It offers the packages: the menu, the server and
   Disc Player are ticked; they come with the installer.
3. **The card.** Put the player's card into the computer, name where it is
   mounted and type `CARD`. Then put the card back into the player.
4. **The player.** Switch the player off, hold **Volume Down** and connect
   the cable to the computer (USB Boot). Type `CHECK`: in about a minute the
   installer reads what the player holds and checks it. It writes nothing to
   a player it does not know.
5. Type `WRITE`. The new system is written in about 10 minutes; keep the
   cable connected.
6. Disconnect the cable: the player leaves USB Boot and starts the new system
   by itself. Answer whether it started normally, then connect the cable again
   while it runs: the new system has checked itself (every byte written, by
   its SHA-256), and the installer reads that.
7. To install the packages from the card, switch the player off, then switch
   it on holding **Play** (let Play go once the logo shows). The menu shows
   the installation, then the player starts as usual.

All together it takes about 15 minutes with the player. Each run is kept,
with its report, in `~/Library/Application Support/SNOWSKY DISC/runs`
(macOS) or `~/.local/share/snowsky-disc/runs` (Linux).

## The risk

The installer replaces the player's system partition (FiiO's root file
system) with the image it builds from FiiO's update and the boot layer. It
does not touch the bootloader, the kernel, FiiO's recovery or your data.
Before it writes, it checks in the same connection that the player's
bootloader starts that partition and that the partition holds FiiO's 2.57 or
one of this project's images; it checks the player's memory and the image in
it before the write, and the new system checks the written bytes at its first
start.

A write cut short (the cable pulled, the battery empty) leaves the system
partition incomplete, and the player will not start it. USB Boot still works
in that state: it is the player's own, below any system, so the installation
can be run again or the player taken back to stock. This is an unofficial
modification of your player: use it at your own risk.

## The way back to stock

- **FiiO's own update.** Put FiiO's update zip, as FiiO publishes it, at the
  card's root, and choose Settings → System updates → Local upgrade. It
  writes FiiO's system again in a few minutes (checked on a player,
  2026-10-08), even over the same version.
- **The installer, through USB Boot**, when the player does not start:

  ```sh
  python3 install.py --restore
  ```

  It reads what the player holds as for an installation and writes FiiO's
  own system partition. Over a system it does not know (a write cut short) it
  asks you to type `STOCK` first: it does not read the player's kernel, so on
  a player with another FiiO version use FiiO's own update instead.

Either way, the packages' files in the player's data partition and the
card's `.disc` folder stay; FiiO's software does not use them.

## The keys at power-on

| Held at power-on | What starts |
| --- | --- |
| Nothing | The boot layer with your packages (the menu asks when there is a choice) |
| Volume Up | FiiO's own interface, no package started, for this start only |
| Play | The installation of the packages staged on the card, then the boot layer |
| Volume Down, with the cable to a computer | USB Boot, for the installer |

## For developers

How the layer works, how to build, test and release it:
[development](docs/development.md), the [contract](docs/contract.md) the
packages rely on, and the [plan](docs/plan.md).

License: MIT (see [LICENSE](LICENSE)).
