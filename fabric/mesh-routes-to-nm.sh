#!/bin/bash
# mesh-routes-to-nm.sh <mesh-up-rkN.sh> : copy the mesh's opposite-node /32 routes into the NM ring profiles
# (NetworkManager flushes non-NM routes when a link bounces, e.g. when a neighbour reboots).
set -e
grep -oE 'ip route add [0-9./]+ via [0-9.]+ dev [^ ]+ src [0-9.]+' "$1" | while read -r _ _ _ dst _ gw _ dev _ src; do
  cur=$(nmcli -g ipv4.routes con show ring-$dev | sed 's/\\//g')
  echo "$cur" | grep -q "$dst" && { echo "ring-$dev already has $dst"; continue; }
  nmcli con mod ring-$dev +ipv4.routes "$dst $gw src=$src"
  ip route replace $dst via $gw dev $dev src $src
  echo "ring-$dev += $dst via $gw src $src"
done
