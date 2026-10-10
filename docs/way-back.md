# Back to stock

How to return a SNOWSKY DISC with the boot layer to FiiO's own system, and
what stays afterwards.

## Two ways back

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
card's `.disc` folder stay; FiiO's software does not use them. To have the
boot layer again, install it over stock as the first time: the packages it
finds in the data partition run again. The way back is always FiiO's own
system, built from your update; the installer never writes an earlier copy of
the player back.

## USB Boot

USB Boot is the player's own, below any system, so it works even when the
installed system does not start: switch the player off (if it cannot be
switched off, let its battery run down), hold **Volume Down** and connect the
cable to the computer. After a write the installer restarts the player into
the system it holds; otherwise, the cable disconnected, the player leaves USB
Boot and restarts into it by itself.
