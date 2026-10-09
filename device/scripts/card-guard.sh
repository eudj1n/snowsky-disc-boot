#!/bin/sh
# The boot layer's card guard (docs/dev/contract.md, "The card guard"), installed as rm first
# in the PATH of stock's player and UI. Stock's player prepares the card with
# "umount <mount point>" and then "rm -rf <mount point>" without checking the unmount:
# while the card is busy that deletes the mounted card. An rm that names the card's mount
# point, or a folder above it, is refused; the mount point itself is only removed when it
# is an empty folder (rmdir, which a mounted card refuses). Everything else, files and
# folders on the card included, goes to the real rm unchanged.
card='@SD@'
refused=
options=1
for operand in "$@"; do
  if [ "$options" = 1 ]; then
    case "$operand" in --) options=0; continue ;; -?*) continue ;; esac
  fi
  # The operand as given and as the file system resolves it (relative paths, ., .. and links).
  resolved=$(readlink -f -- "$operand" 2>/dev/null)
  for path in "$operand" "$resolved"; do
    [ -n "$path" ] || continue
    while :; do
      case "$path" in
        */) path=${path%/} ;;
        *//*) path=${path%%//*}/${path#*//} ;;
        *) break ;;
      esac
    done
    case "$card/" in
      "$path"/*)
        refused="$refused $operand"
        [ "$path" = "$card" ] && rmdir -- "$card" 2>/dev/null
        ;;
    esac
  done
done
if [ -n "$refused" ]; then
  log=/run/disc-boot/guard.log
  if [ ! -f "$log" ] || [ "$(wc -c < "$log")" -lt 65536 ]; then
    echo "$(date '+%F %T') refused rm $*" 2>/dev/null >> "$log"
  fi
  exit 0
fi
exec /bin/rm "$@"
