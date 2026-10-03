#!/usr/bin/env python3
"""The SNOWSKY DISC installer (plan, stage 4): a guided run in the boot menu's colours.

It checks this computer, builds the image from FiiO's update with the boot layer (or takes
one built before), offers the packages of catalog/packages.json and the apps of the chosen
server's catalog, and puts them on the card with the USB console's marker. The player's
write through USB Boot and the first boot follow in the next parts.

    python3 install.py                      # guided, in the terminal
    python3 install.py --dry-run            # stages into work/install-*/card, writes nothing else
    python3 install.py --yes --ota DIR --card /Volumes/PLAY --from work/packages   # without questions

Packages are taken from local files with their catalog digest (--from) or, once published,
downloaded (--download). The run's report is work/install-*/report.json.
"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent/'scripts'))
from installer import flow, tui  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dry-run', action='store_true', help='Stage into the run folder instead of a card')
    parser.add_argument('--yes', action='store_true', help='No questions: the defaults and the options given')
    parser.add_argument('--plain', action='store_true', help='Plain text, no colours')
    parser.add_argument('--ota', help="The folder of FiiO's update (main_os/ota_v...)")
    parser.add_argument('--image', help='An image built before (skips the build)')
    parser.add_argument('--emulator', help="The emulator checkout whose image builds the image")
    parser.add_argument('--card', help="Where the player's card is mounted")
    parser.add_argument('--package', action='append', help='A package to install (default: the catalog\'s defaults)')
    parser.add_argument('--app', action='append', help='An app of the server to stage (default: its defaults)')
    parser.add_argument('--from', dest='packages_from', action='append', default=[], help='A file or folder of local archives')
    parser.add_argument('--download', action='store_true', help='Download published archives that are not local')
    parser.add_argument('--work', help='The run folder (default: work/install-<time>)')
    parser.add_argument('--catalog', help='Another catalog of packages (tests)')
    args = parser.parse_args()
    screen = tui.Screen(look='plain') if args.plain else None
    return flow.Installer(args, screen).run()


if __name__ == '__main__':
    sys.exit(main())
