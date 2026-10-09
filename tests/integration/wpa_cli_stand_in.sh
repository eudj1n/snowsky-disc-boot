#!/bin/sh
# A stand-in for stock's wpa_cli (wpa_supplicant 2.9) over files, for disc-network's tests on the host
# and on the guest (the emulator has no Wi-Fi). Installed as <root>/usr/sbin/wpa_cli, it keeps
# wpa_supplicant's networks in <root>/run/wpa-stand-in/net/<id>/<key> and saves them as wpa_supplicant
# writes <root>/usr/data/wpa_supplicant.conf (beside, then renamed). wpa_cli's commands: ping, status,
# list_networks, add_network, set_network, enable_network, select_network, remove_network, save_config.
# The tests' own begin with _: _up and _down (wpa_supplicant started, with the networks of the file, or
# stopped), _state <wpa_state> [id], _stock_connect <ssid> <psk|NONE> (stock's connect_wifi: remove
# all, add, set, select, save; then connected to it), _scan <id>... (those networks in range: the
# first enabled one by priority connects).
set -u
here=$(cd "$(dirname "$0")" && pwd)
base=$(cd "$here/../.." && pwd)
S="$base/run/wpa-stand-in"
conf="$base/usr/data/wpa_supplicant.conf"
mkdir -p "$S/net"
[ "${1:-}" = -i ] && shift 2
# As glibc's getopt in stock's wpa_cli: without "--", an argument beginning with "-" anywhere is an option.
if [ "${1:-}" = -- ]; then shift; else
    for a in "$@"; do
        case $a in -?*) echo "wpa_cli: invalid option -- '${a#-}'"; echo "wpa_cli [-p<path to ctrl sockets>] [-i<ifname>] [-hvB] ..."; exit 255;; esac
    done
fi
cmd=${1:-}
[ $# -gt 0 ] && shift
echo "$cmd $*" >> "$S/log"

ids() { ls "$S/net" 2>/dev/null | sort -n; }
next_id() { last=-1; for i in $(ids); do last=$i; done; echo $((last + 1)); }
field() { cat "$S/net/$1/$2" 2>/dev/null; }
# A configuration's SSID as wpa_cli prints it: the text of a quoted one, \xNN for each byte of a hex one.
printed() {
    case $1 in
        \"*\") v=${1#\"}; printf '%s' "${v%\"}" ;;
        *) printf '%s' "$1" | sed 's/../\\x&/g' ;;
    esac
}
save() {
    {
        printf 'ctrl_interface=/var/run/wpa_supplicant\nupdate_config=1\ncountry=GB\n'
        for i in $(ids); do
            printf '\nnetwork={\n\tssid=%s\n' "$(field "$i" ssid)"
            [ -n "$(field "$i" scan_ssid)" ] && printf '\tscan_ssid=%s\n' "$(field "$i" scan_ssid)"
            [ -n "$(field "$i" psk)" ] && printf '\tpsk=%s\n' "$(field "$i" psk)"
            [ -n "$(field "$i" key_mgmt)" ] && printf '\tkey_mgmt=%s\n' "$(field "$i" key_mgmt)"
            [ -n "$(field "$i" eap)" ] && printf '\teap=%s\n' "$(field "$i" eap)"
            p=$(field "$i" priority); [ -n "$p" ] && [ "$p" != 0 ] && printf '\tpriority=%s\n' "$p"
            [ "$(field "$i" disabled)" = 1 ] && printf '\tdisabled=1\n'
            printf '}\n'
        done
    } > "$conf.tmp" && mv "$conf.tmp" "$conf"
}
# wpa_supplicant's start: the networks of the file.
load() {
    rm -rf "$S/net" && mkdir -p "$S/net"
    id=-1
    [ -f "$conf" ] || return 0
    while IFS= read -r line; do
        line=$(printf '%s' "$line" | sed 's/^[[:space:]]*//')
        case $line in
            'network={') id=$((id + 1)); mkdir -p "$S/net/$id"; echo 0 > "$S/net/$id/disabled" ;;
            '}') ;;
            *=*) [ "$id" -ge 0 ] && printf '%s' "${line#*=}" > "$S/net/$id/${line%%=*}" ;;
        esac
    done < "$conf"
}
need_up() { [ -e "$S/up" ] || { echo "Failed to connect to non-global ctrl_ifname: wlan0  error: No such file or directory"; exit 255; }; }

case $cmd in
    ping) need_up; echo PONG ;;
    status)
        need_up
        state=$(cat "$S/state" 2>/dev/null || echo DISCONNECTED)
        if [ "$state" = COMPLETED ]; then
            c=$(cat "$S/current")
            echo "bssid=02:00:00:00:00:01"; echo "ssid=$(printed "$(field "$c" ssid)")"; echo "id=$c"
        fi
        echo "wpa_state=$state" ;;
    list_networks)
        need_up
        printf 'network id / ssid / bssid / flags\n'
        c=$(cat "$S/current" 2>/dev/null); state=$(cat "$S/state" 2>/dev/null)
        for i in $(ids); do
            flags=''
            [ "$(field "$i" disabled)" = 1 ] && flags='[DISABLED]'
            [ "$state" = COMPLETED ] && [ "$i" = "$c" ] && flags='[CURRENT]'
            printf '%s\t%s\tany\t%s\n' "$i" "$(printed "$(field "$i" ssid)")" "$flags"
        done ;;
    add_network) need_up; id=$(next_id); mkdir -p "$S/net/$id"; echo 1 > "$S/net/$id/disabled"; echo "$id" ;;
    set_network)
        need_up
        id=$1 key=$2; shift 2
        [ -d "$S/net/$id" ] || { echo FAIL; exit 0; }
        printf '%s' "$*" > "$S/net/$id/$key"; echo OK ;;
    enable_network) need_up; [ -d "$S/net/$1" ] && echo 0 > "$S/net/$1/disabled"; echo OK ;;
    select_network) need_up; for i in $(ids); do if [ "$i" = "$1" ]; then echo 0; else echo 1; fi > "$S/net/$i/disabled"; done; echo OK ;;
    remove_network) need_up; if [ "$1" = all ]; then rm -rf "$S/net" && mkdir -p "$S/net"; else rm -rf "$S/net/$1"; fi; echo OK ;;
    save_config) need_up; save; echo OK ;;
    _up) load; mkdir -p "$base/var/run/wpa_supplicant"; : > "$base/var/run/wpa_supplicant/wlan0"; : > "$S/up"; echo DISCONNECTED > "$S/state" ;;
    _down) rm -f "$S/up" "$base/var/run/wpa_supplicant/wlan0" ;;
    _state) echo "$1" > "$S/state"; [ $# -gt 1 ] && echo "$2" > "$S/current"; true ;;
    _stock_connect)
        rm -rf "$S/net" && mkdir -p "$S/net/0"
        printf '%s' "$1" > "$S/net/0/ssid"; printf 1 > "$S/net/0/scan_ssid"; echo 0 > "$S/net/0/disabled"
        if [ "$2" = NONE ]; then printf NONE > "$S/net/0/key_mgmt"; else printf '%s' "$2" > "$S/net/0/psk"; fi
        save; echo COMPLETED > "$S/state"; echo 0 > "$S/current" ;;
    _scan)
        best='' bestp=''
        for i in "$@"; do
            [ -d "$S/net/$i" ] && [ "$(field "$i" disabled)" != 1 ] || continue
            p=$(field "$i" priority); p=${p:-0}
            if [ -z "$best" ] || [ "$p" -gt "$bestp" ]; then best=$i bestp=$p; fi
        done
        if [ -n "$best" ]; then echo COMPLETED > "$S/state"; echo "$best" > "$S/current"; else echo SCANNING > "$S/state"; fi ;;
    *) echo "Unknown command '$cmd'"; exit 1 ;;
esac
