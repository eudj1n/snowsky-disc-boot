# Provenance

- Protocol/UI reference: https://github.com/eudj1n/snowsky-disc-qemu,
  baseline `992d156c16746be5a25ab43c7cda5fcef09b8863`.
- Inspected reference checkout: `a0cf54dee06dfb126c6f0485f1f506e3dc02ac03`;
  its only changes from the baseline are the handoff document and index entry.
- Local reference path supplied by the owner:
  `/Users/zhek/IdeaProjects/snowsky-disc-qemu`.
- diskOS build reference: `/Users/zhek/IdeaProjects/diskos`, revision
  `646212d57425bd437468ab8896f63b16bae8f744`. Its ui/Dockerfile pins the musl
  toolchain archive SHA-256. No diskOS screen UI source is reused.
- Firmware input supplied by the owner:
  `/Users/zhek/Downloads/SNOWSKY_DISC_update_20260909_v257/main_os/ota_v257`.
  Firmware remains external; upstream fingerprint validation is mandatory.
- CivetWeb source: https://github.com/civetweb/civetweb, revision
  `d7ba35bbb649209c66e582d5a0244ba988a15159 (v1.16)`, fetched 2026-09-23.
  Source/license are vendored; the local frame-limit patch is documented beside it.

FiiO framing semantics and ownership rules derive from the MIT-licensed reference
Controller. Preserve its copyright notice in LICENSE. Historical diskOS V2.40
warm-start evidence does not establish V2.57 installation or cold boot.

The original host ROM probe uses the CPU-info request shape observed in diskOS
`src/usbboot/usbboot.c` (source hash pinned by the probe profile). No GPL usbboot
implementation was copied or bundled. Optional physical acquisition uses a
separately installed libusb library through its public C ABI; that library is
not distributed here. Synthetic CI substitutes the ABI without loading libusb.
