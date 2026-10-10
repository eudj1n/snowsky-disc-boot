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
3. **The card.** Put the player's card into the computer, or connect the
   player with its cable and choose **Working mode → USB Storage** on it: the
   card shows as a drive either way. Name where it is mounted and type `CARD`. Then eject it,
   and put the card back into the player (or leave USB storage).
4. **The player.** Switch the player off, hold **Volume Down** and connect
   the cable to the computer (USB Boot). Type `CHECK`: in about a minute the
   installer reads what the player holds and checks it. It writes nothing to
   a player it does not know.
5. Type `WRITE`. The new system is written in about 10 minutes; keep the
   cable connected.
6. The installer restarts the player into the new system itself; keep the
   cable connected. Answer whether it started normally: the new system has
   checked itself (every byte written, by its SHA-256), and the installer
   reads that over the cable. (Should the restart not come, the installer
   asks you to disconnect the cable and connect it again while the new system
   runs.)
7. **The packages from the card.** The first time, switch the player off,
   then switch it on holding **Play** (let Play go once the logo shows). The
   menu shows the installation, then the player starts as usual. Once the
   boot menu is installed, it offers new packages on the card itself: its
   row "on the card" opens Packages, where Play installs each one; nothing
   is held at power-on.

All together it takes about 15 minutes with the player. Each run is kept,
with its report, in `~/Library/Application Support/SNOWSKY DISC/runs`
(macOS) or `~/.local/share/snowsky-disc/runs` (Linux).

## Updating the packages

When the player has the boot layer and its menu already, new versions of the
packages need no USB Boot: from the newest release's archive,

```sh
python3 install.py --packages
```

offers the packages and apps as above and puts the chosen ones on the card
(in a card reader, or the player itself in Working mode → USB Storage);
nothing else is needed on the computer (no FiiO update, no libusb). Eject
the card, put it back into the player (or leave USB storage), then switch
the player off and on: the menu's row "on the card" opens Packages, where
Play installs each one. A new version of a service or the server starts at
once, a new menu at the next start; each keeps its previous version, and one
that fails gives way to it.

## What is installed

- **On the player:** the system partition (FiiO's root file system) becomes
  the image the installer built from FiiO's update and the boot layer's
  programs: FiiO's own system with the boot layer added. The bootloader, the
  kernel, FiiO's recovery, your settings and your library stay as they are.
- **In the player's data partition:** the packages installed from the card
  (`/usr/data/disc-boot`), each with its previous version kept.
- **On the card:** the packages waiting to be installed
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
