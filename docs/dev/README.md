# Developer documents

The documents of snowsky-disc-boot for developers, by area. The README at the
repository's root and `docs/install.md` and `docs/way-back.md` are for users.

## Start here

- [Development](development.md): the layout, building and testing, packages,
  the menu, the installer, the console, the guest, releases.
- [Plan](plan.md): the open work only (owner, 2026-10-10).

## The boot layer on the player — `boot/`

- [Contract](boot/contract.md): modes and keys, roles and packages, slots,
  the controller's and services' lifecycle, the `ui` role and the menu, the
  card guard, recovery from the card, the first start's check, status, the
  facts it rests on.
- [Boot selection](boot/boot-selection.md): what the SPL selects and how it
  is read.
- [Boot report](boot/boot-report.md): the report the image writes to the
  card.

## Packages — `packages/`

- [disc-health](packages/health.md): the health journal.
- [disc-network](packages/network.md): several Wi-Fi networks, one in stock's
  configuration.

The menu's design and screens are in [development](development.md#the-boot-menu)
and the [contract](boot/contract.md#the-menus-screens).

## Installing — `installer/`

- [Build and flash](installer/build-and-flash.md): building, reviewing,
  writing and verifying the image.
- [Installation procedure](installer/installation-procedure.md) and
  [installation review](installer/installation-review.md): with and without
  a history, the known image.
- [Pre-install review](installer/preinstall-review.md), [writer
  review](installer/deployment-writer-review.md): stock's acceptance, the
  writer's behaviour, diskOS's `my_write6`.
- The installation packages of 2026-09-24:
  [boot report](installer/boot-report-installation.md),
  [corrected controller](installer/udc-update.md).

## USB Boot — `usb-boot/`

- [RAM transport](usb-boot/ram-transport.md): the ROM's requests, the SPL
  once an entry, held asks, the completion profile.
- [Writer transport](usb-boot/writer-transport.md): staging, the region's
  check, the writer's single invocation.
- [Restart](usb-boot/restart.md): leaving USB Boot by itself after a write.
- [USB console](usb-boot/usb-diagnostics.md): the engineering image's
  console.

## NAND — `nand/`

The chip, the kernel's view of it, and reading it through USB Boot:
[chip review](nand/nand-chip-review.md),
[kernel review](nand/nand-kernel-review.md),
[identity payload](nand/nand-identity.md),
[metadata page](nand/nand-metadata.md),
[reader core](nand/nand-reader-core.md),
[OOB review](nand/oob-review.md),
[bootloader review](nand/bootloader-review.md),
[rootfs collector](nand/rootfs-collector.md) (with the read by digest),
[logical readback](nand/logical-readback.md),
[acquisition](nand/native-acquisition.md).

## Firmware — `firmware/`

- [Firmware compatibility](firmware/firmware-compatibility.md): selecting
  and accepting a firmware through reviewed profiles.
- [Provenance](firmware/provenance.md): the references and inputs.
- [Deployment preparation for V2.57](firmware/native-deployment.md).

## Records — `observations/`

What happened in sessions on players: the writes to the owner's player
([first-write-observation](observations/first-write-observation.md), from
2026-10-04) and the combined images' sessions of 2026-09-24.

Comments in the image's own sources (`device/boot`, `device/console`,
`device/common`, `device/menu`, `device/scripts`) and in the scripts
`scripts/deployment/build_candidate.py` writes into the image keep these
documents' earlier paths (`docs/dev/<name>.md`): a change there would change
the image.
