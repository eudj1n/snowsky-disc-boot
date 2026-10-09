# Installing the DISC boot layer

How to put the boot layer on your SNOWSKY DISC with the installer, what it
installs, and the risk it carries. If something goes wrong, [the way
back](way-back.md) returns the player to stock.

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

## Step by step

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

## What is installed

- **On the player:** the system partition (FiiO's root file system) becomes
  the image the installer built from FiiO's update and the boot layer's
  programs: FiiO's own system with the boot layer added. The bootloader, the
  kernel, FiiO's recovery, your settings and your library stay as they are.
- **In the player's data partition:** the packages installed from the card
  (`/usr/data/disc-boot`), each with its previous version kept.
- **On the card:** the packages staged for a start with Play
  (`.disc/boot/install`), the apps (`Apps/`), the boot layer's status and the
  USB console's marker (`.disc`); your music is not touched.

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
