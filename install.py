#!/usr/bin/env python3
"""The SNOWSKY DISC installer (plan, stage 4): a guided run in the boot menu's colours.

It checks this computer, builds the image from FiiO's update with the boot layer (or takes
one built before), offers the packages of catalog/packages.json and the apps of the chosen
server's catalog, puts them on the card with the USB console's marker, writes the player
through USB Boot with the reviewed tools (--history) and records its first boot.

    python3 install.py                      # guided, in the terminal
    python3 install.py --dry-run            # stages into work/install-*/card, writes nothing else
    python3 install.py --simulate           # the player's step against a simulated NAND
    python3 install.py --guest --image FILE --ota DIR --from work/packages   # the emulator's guest as the player
    python3 install.py --yes --ota DIR --card /Volumes/PLAY --from work/packages   # without questions

Packages are taken from local files with their catalog digest (--from) or downloaded from
their published address and checked by that digest (not with --offline). The run's report is
work/install-*/report.json; from the installer's archive (disc-installer-<version>.tar.gz) its
own packages come first, and the runs are kept outside it (macOS: ~/Library/Application
Support/SNOWSKY DISC/runs; Linux: ~/.local/share/snowsky-disc/runs).
"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent/'scripts'))
from installer import flow, tui  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dry-run', action='store_true', help='Stage into the run folder instead of a card')
    # The player's step: a simulated one, the emulator's guest, or the player through the reviewed tools.
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--simulate', nargs='?', const='run', help='Write a simulated player (a NAND file; default in the run folder)')
    modes.add_argument('--guest', action='store_true', help="The emulator's guest of the image as the player (the card staged in the run folder)")
    modes.add_argument('--history', help="A developer's player: its installation history (history.json of its last "
                       "installation). Without it the player is read and written in one entry into USB Boot, by the image it holds")
    parser.add_argument('--yes', action='store_true', help='No questions: the defaults and the options given')
    parser.add_argument('--plain', action='store_true', help='Plain text, no colours')
    parser.add_argument('--ota', help="The folder of FiiO's update (main_os/ota_v...)")
    parser.add_argument('--image', help='An image built before (skips the build)')
    parser.add_argument('--boot-build', action='store_true',
                        help="The boot layer's programs from build/mips, a local build (development), not its release file")
    parser.add_argument('--emulator', help="The emulator checkout whose image builds the image")
    parser.add_argument('--card', help="Where the player's card is mounted")
    parser.add_argument('--package', action='append', help='A package to install (default: the catalog\'s defaults)')
    parser.add_argument('--app', action='append', help='An app of the server to stage (default: its defaults)')
    parser.add_argument('--from', dest='packages_from', action='append', default=[], help='A file or folder of local archives')
    parser.add_argument('--offline', dest='download', action='store_false',
                        help='Never download: only local files with the catalog\'s digests (by default a published archive '
                             'that is not local is downloaded and checked by its digest)')
    parser.add_argument('--work', help='The run folder (default: work/install-<time>)')
    parser.add_argument('--catalog', help='Another catalog of packages (tests)')
    parser.add_argument('--simulate-small', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--fault', action='append', help='A simulated fault: no-device, bad-blocks=N,M, write-stops=N, readback-flip=N')
    parser.add_argument('--restore', action='store_true', help="Back to stock: the restore image, the same path to the player")
    parser.add_argument('--run', help="With --restore: back to stock with that installer run's package, whatever the player holds")
    parser.add_argument('--resume', help="Go on with a run whose write stopped before the writer: its card and backup stand, "
                                         'the write follows in a fresh entry (with --history)')
    parser.add_argument('--diskos', help="diskOS's checkout at the writer's pinned revision (default: its pinned files, "
                                         "fetched once and kept in work/downloads)")
    parser.add_argument('--libusb', help='The libusb library the reviewed tools load (default: where Homebrew or the '
                                         'system packages put it)')
    args = parser.parse_args()
    if args.run and not args.restore:
        parser.error('--run names the run whose package takes the player back to stock: give --restore too')
    if args.resume and (args.restore or args.guest or args.simulate is not None or args.dry_run or not args.history):
        parser.error('--resume goes on with a player\'s installation: give --history, without --restore, --guest, --simulate or --dry-run')
    if args.guest and args.restore:
        parser.error('--restore writes a player; a guest starts fresh from each image')
    screen = tui.Screen(look='plain') if args.plain else None
    return flow.Installer(args, screen).run()


if __name__ == '__main__':
    sys.exit(main())
