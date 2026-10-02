# Corrected-image boot report and ACM diagnosis — 2026-09-24

The corrected-controller image passed [physical writing, exact full readback
and owner-confirmed normal UI boot](udc-installation-observation.md). The owner
switched to USB storage following the proposed fresh-report procedure, preserving
the original report before another export. No firmware change is part of this
procedure.

## Previous report preserved

The stock storage interface exposed the same external PLAY volume, UUID
`C42F22D8-EFD8-3C81-809C-358185EC8AA2`, exFAT, 30,936,170,496 bytes. Only the known
report/marker paths and proposed archive name were inspected. Both markers
retained their exact expected contents. The existing 2,547-byte report was still
the [original image's snapshot](boot-report-observation.md), SHA-256
`ae6daaa4c4fc051ff6609f64eeefcee8ffa99f0699c75c9e55de430d96155566`.
Exact copies were retained under ignored `work/udc-report-export-001/`.

The free archive name was `DISC_WEB_BOOT_REPORT.before-udc-fix.txt`. exFAT refused
Darwin's exclusive rename operation with `ENOTSUP`, leaving the original intact.
The fallback created the archive exclusively, flushed it, and compared every
byte before removing the verified original path. Source identity/content were
rechecked immediately before removal. Archive contents and both unchanged
markers were then checked again. The output name `DISC_WEB_BOOT_REPORT.txt` is
now free for the next export; no existing destination was replaced.

Archiving completed at `2026-09-24T13:06:43.329295+00:00`. Fresh volume identity
checks preceded successful `diskutil eject`. `archive-result.json`, volume
observations and `eject.txt` remain ignored. This qualifies archive preparation,
not export or native/USB health. No production behavior changed; existing
report-preservation and marker tests remain the software validation baseline.

## Requested observation sequence

Exit storage mode and reboot normally **with the USB cable already connected**,
leaving the card installed and not selecting storage or DAC. Wait at least two
minutes after the stock screen appears before selecting storage to read the
report. This sequence captures console startup with a cable; unlike the first
cable-free report, it can retain errors from a cabled attempt. The exporter still
refuses a foreign stock gadget; lack of a report is not proof of service failure.

Inspect the new report's complete footer, new boot ID and installed USB-profile
fingerprint `25fe6c616db3c15bdf0fcce9300bcde98922078a0362792ccbddf5f74d448b2d`.
Preserve the archive and new output. The resulting report was inspected below;
USB enumeration and live native health/read-only protocol acceptance remain open.

## Fresh report inspected

The owner requested inspection after this sequence. The same card was validated
again; both markers and the archived original report were unchanged. Only known
diagnostic paths were read, with regular-file/no-follow checks and a 16 KiB cap.
New evidence remains ignored in `work/udc-report-read-001/`. No card files were
modified during inspection.

| Evidence | Observation |
| --- | --- |
| New report | 2,637 bytes, complete V1 header/footer |
| SHA-256 | `d7a09aafbe977373005a0529a090efdfa3b390ca047dd1f5eb90b50fbffb68be` |
| Boot ID | `f71b6559-52dc-4ee0-abbd-b74cdd583309` |
| Snapshot uptime | 49.61 seconds |
| Profile | Matches the installed corrected-controller image's archived build report |
| Native process | PID 1019, `/opt/disc-web/disc-service`, six threads, RSS 388 KiB |
| Listener | `127.0.0.1:7870`, TCP LISTEN |
| Boot root | `root=/dev/mtdblock_bbt_ro2`; `/dev/root` mounted read-only squashfs |
| Card/controller readiness | `mounted=1 controller=1` |
| Setup failure | `ACM link: No such file or directory`, then `gadget setup failed` |
| Snapshot USB state | `13500000.otg_new`, `not attached`, no gadgets listed |

The profile correction resolved the earlier controller-name rejection. This
helper now reaches gadget setup but stops before its UDC bind. Cleanup also logs
`unbind failed; inspect USB state before retry`; this follows the failed setup
and does not establish that an active console had been bound. The empty gadget
list is a later snapshot, not complete USB lifecycle acceptance.

Native process/listener presence remains separate from `/api/health` and stock
protocol round trips. A saved snapshot does not prove socket ownership or live
diagnostic access.

## ACM link correction

The helper passed `../../functions/acm.0` as the symlink target. That string works
for ordinary filesystem links when interpreted relative to `configs/c.1`, but
configfs resolves the input target immediately from the calling process's working
directory before constructing its own link. Upstream
[Linux v4.4 configfs source](https://raw.githubusercontent.com/torvalds/linux/v4.4/fs/configfs/symlink.c)
shows `get_target()` calling `kern_path()` and then validating the target's
configfs superblock/item. This is an upstream reference, not a claim that the
vendor kernel has been independently rebuilt or fully compared.
The retrieved 7,350-byte upstream file is retained in ignored work storage with
SHA-256 `0cc9122e017840cc7a94987ba54158368eaf08a62fe90500df13d2ec0f4d74d4`.

The pinned stock `usr/project/serial_config.sh` first changes into the gadget
directory, then links `functions/acm.0`. Our native helper does not change into
that directory. Its relative target therefore names the wrong path at creation
time, explaining the observed ENOENT. The implementation now passes the absolute
path of its own ACM function; it remains independent of the caller's cwd and
does not select another controller or change stock USB ownership rules.

The regression starts the helper from an unrelated directory and checks that
the supplied target resolves to the actual function from that cwd. It failed
against the old binary (`work/acm-target-regression-before.log`). The fixture
also now models configfs's immediate target-existence lookup, because a regular
filesystem otherwise accepts a dangling target and hides this error. Production
has no fixture option. No physical configfs operation was performed for tests.

The installed image is still the previously verified `76cc1ee9…` image. Applying
the ACM fix requires a newly reviewed installation package and separate concrete
write authorization; none is implied by reading this report.

## Validation and offline image

- All 14 host USB fixture tests passed (`work/acm-target-host-tests.log`),
  including the regression that failed before the fix.
- The same 14 tests passed against rebuilt static MIPS fixture/production
  binaries in Linux/QEMU (`work/acm-target-mips-tests.log`). These synthetic tests
  require no firmware, physical USB, private credentials or sibling checkout
  and remain part of GitHub Actions discovery.
- Full host conformance passed: 285 Python tests, eight JavaScript tests and
  native C assertions (`work/acm-target-conformance.log`). No hosted CI run is
  claimed.
- The builder preserved all 3,492 stock objects and passed full content/metadata
  pack/extract comparison. The independent offline image review passed with
  `flashReady: false`.
- Packed-image integration passed companion loopback health, disabled start,
  native SIGTERM/relaunch and unrelated-PID preservation; USB duplicate-start,
  explicit stop and no-card timeout passed at 30.09 seconds
  (`work/acm-link-packed-boot.log`). QEMU's existing argv limitation leaves
  hook-managed companion stop/idempotency unqualified. This test does not expose
  real USB/configfs and cannot qualify physical ACM binding or enumeration.

Ignored artifacts: `work/deployment-acm-link-001/`, with disposable build trees
in `/work/disc-deployment-acm-link-001/`. Pinned toolchain:
`sha256:68e50109b799b49e4ba1acf53ca906ffb50d9d1fd27499f1a22056abc89c516b`.
The candidate remains 100,663,296 padded bytes; squashfs is 81,043,456 bytes.

| Artifact | SHA-256 |
| --- | --- |
| New offline candidate | `d6ca62fb84795f89aaac3e2d31d2359bccf2af4629e960ad7a478a1633b9939e` |
| Fixed native USB helper, 170,036 bytes | `682b8d105c2eb4f10818ecee8c632e15922029ce010c76bc781cb751f0f2a0a6` |
| Unchanged stock restore | `e75d85bddb7b9fe304dfe334058693fb522a93cb1048ddfb5417f136f204eae0` |

The USB profile, companion and rendered report script are unchanged. The previous
physical write authorization and installation review belong to the older image;
they cannot authorize this new candidate. The installer admission flag remains
closed. Existing reports and markers were preserved on the card.
