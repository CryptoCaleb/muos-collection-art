#!/bin/sh
# Collection Art watcher: rebuilds collection art whenever a collection changes.
APP="$(cd "$(dirname "$0")" && pwd)"
PIDF=/tmp/collectionart_watch.pid
if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then exit 0; fi
echo $$ > "$PIDF"
PY="$(command -v python3 || command -v python)"
[ -z "$PY" ] && exit 1
COL=""
for i in $(seq 1 60); do
    for c in /run/muos/storage/info/collection /mnt/sdcard/MUOS/info/collection /mnt/mmc/MUOS/info/collection; do
        [ -d "$c" ] && COL="$c" && break
    done
    [ -n "$COL" ] && break
    sleep 5
done
[ -z "$COL" ] && exit 1
sig() { find "$COL" -name '*.cfg' 2>/dev/null | sort | md5sum; }
LAST=""
while :; do
    NOW="$(sig)"
    if [ "$NOW" != "$LAST" ]; then
        [ -n "$LAST" ] && sleep 3
        nice -n 19 "$PY" "$APP/app.py" --auto >/dev/null 2>&1
        LAST="$(sig)"
    fi
    sleep 15
done
