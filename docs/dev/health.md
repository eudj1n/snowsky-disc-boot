# disc-health

The health journal (plan, stage 7; owner, 2026-10-07): a service of boot
API 2 ([contract](contract.md#roles)) that reads, offline and read-only, what
the player shows of itself, keeps a short journal of it on the player and
reports the latest reading for the server's diagnostics. It opens no socket
and writes nothing outside its own two folders. Source: `device/health/health.c`;
its package is a release file (`disc-health-<version>.zip`), offered ticked by
the installer.

## What it reads

At its start and then every 10 minutes:

| Part | Where | In the reading |
| --- | --- | --- |
| Battery | the first power supply with a `capacity`, by name (`cw221X-bat` on the player: `capacity` %, `voltage_now` µV, `current_now` µA, `temp` 0.1 °C, `cycle_count`) | `battery`: `name`, `percent`, `mV`, `mA`, `celsius`, `cycles`; null without one |
| Temperatures | `/sys/class/thermal/thermal_zone<N>/temp` (m°C) and `type`, at most four | `thermal`: `[{zone, celsius}]` |
| Time | the clock (null before it was set), `/proc/sys/kernel/random/boot_id`, `/proc/uptime`, `/proc/loadavg` | `t`, `boot` (its first 8 characters), `uptime` (s), `load` (1, 5, 15 min) |
| Memory | `/proc/meminfo` | `memory`: `totalKB`, `availableKB` |
| Free space | `statvfs` of `/usr/data`, and of the card where boot says it is (`DISC_BOOT_CARD`) when the mount table has it there | `space`: `data`, `card`: `{freeKB, totalKB}` or null |
| Card errors, crashes | the kernel's ring since the last line read: lines of `mmc`/`mmcblk` naming an error, a timeout or a failure; the fatal signals boot has the kernel print in platform mode | `kernel`: `cardErrors`, `fatalSignals` (new in this reading) |
| Stock's restarts | `Restarting` lines in `/usr/data/fiio/log/process_failed.txt`, which stock's watch loop writes | `pairRestarts` (the file's count) |

A source the player does not have is null (or `[]`), never a guess. Files of
`/proc` and `/sys` are read to their end: they say 0 or a page as their size,
never their text's (the guest's first reading, 2026-10-09, took nothing by the
size). At a start the card comes later (stock mounts it once its player runs),
so a reading without it is taken again as soon as the mount table has it. The
battery's attributes are those of a V2.57 player as snowsky-disc-qemu's
`device` profile models them; the thermal zones are not known yet on the
player (the first reading there says).

## The journal and the report

- `$DISC_BOOT_DATA/journal.jsonl`: one reading a line, appended and synced;
  at 128 KiB it becomes `journal.1.jsonl` (replacing the one before), so
  both stay within 256 KiB, about five days of readings.
- `$DISC_BOOT_RUN/status.json` (at most 4 KiB), the report the server shows:
  `{"schema": 1, "interval": 600, "samples", "started", "journalBytes",
  "sinceStart": {"cardErrors", "fatalSignals", "pairRestarts"}, "latest":
  <the last reading>}`.
- `$DISC_BOOT_RUN/ready` after the first reading; SIGTERM ends it.

## Tests

`tests/conformance/test_health.py` runs the fixture build
(`-DDISC_HEALTH_FIXTURE`: the player's files under `DISC_BOOT_FIXTURE_ROOT`,
the kernel's ring as `fixture/kmsg`, `DISC_HEALTH_INTERVAL`) and, as a
service package, under the boot program's fixture.
`tests/integration/health_guest.py` installs the release's package on the
guest with Play and reads the emulator's gauge. Under qemu-user the kernel's
ring is the container's, so its counts there are recorded only, and a
service's memory bound does not apply (contract, "Tests").
