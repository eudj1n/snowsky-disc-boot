# DISC boot layer

Communicate in Russian; write source documentation and comments in English.
This repository is **snowsky-disc-boot**, the boot layer of the SNOWSKY DISC
platform: what the user's own V2.57 rootfs gains once, through USB Boot (boot
modes, the package loader with slots and rollback, recovery from the card,
the USB console), and the tooling that builds, writes and verifies that image.
Packages run on top of it: snowsky-disc-server (the gateway, a sibling
repository) and third-party ones such as diskOS's UI. snowsky-disc-web is the
frozen history of both. Read
docs/dev/contract.md and docs/dev/plan.md before extending it.

- The contract (docs/dev/contract.md) is what packages build on. Change it only
  with the owner's decision, and raise `bootApi` when packages can tell.
- Boot never runs or installs anything from the card without the physical
  gesture, adds no network listener, and requires no signature of ours:
  packages run at the installer's own risk. The image carries no package.
- Keep the external emulator repository unchanged. Reuse its runtime scripts;
  do not copy firmware, proprietary binaries, private catalogs or captures.
  FiiO's rootfs is never redistributed: users build the image from their own
  OTA file.
- Tests and documentation accompany each behavior change. Synthetic tests
  first, then the disposable V2.57 guest, then a packaged, reviewed image.
  Only an image that passed those checks is written to a player.
- No physical connections, flashing, boot-hook writes, public deployment or
  paid services without a separately authorized concrete step.
- Commit each completed stage, including its tests and documentation. Keep
  generated runtime evidence out of commits.
- Keep docs/dev/plan.md as the canonical plan; mark completed checklist items
  with evidence after each stage.
- Documentation for users: README.md, CHANGELOG.md and the pages in docs/
  (the installation guide, the way back), which the installer's archive
  carries. For developers: docs/dev/ (development.md first, the contract, the
  plan, reviews and procedures) and docs/dev/observations/ (the records of
  sessions on players). Put build, test and architecture instructions there.
- Keep on-device identifiers (paths, hook and marker names) stable across
  images unless a stage explicitly renames them; installation evidence pins
  them.
- Select firmware through reviewed profiles, never hard-coded version checks.
  Re-run compatibility acceptance for each new release before promoting it.
- Keep synthetic conformance tests runnable in GitHub Actions without
  proprietary firmware, a physical player, sibling checkouts or private
  credentials.
- Never print or commit a player's serial number, MAC address or tokens.
